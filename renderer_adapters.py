"""renderer_adapters.py — typed dispatch adapters and the DispatchContext
each one receives (Task 2B-B2a).

No adapter reads a module-level global (no CONTEXT, no project_dir, no
asset_base free variable). Every dependency an adapter needs comes from its
three parameters: the schema-validated `route`, the pre-resolved `target`
path, and an immutable `DispatchContext` the canonical dispatcher assembles
once per route, carrying only already-validated state. No adapter accepts a
provider/model/backend/prompt override parameter — everything
execution-affecting comes from `route` or `ctx.renderer_entry`
(`renderers.RENDERERS[route["renderer_id"]]`, and only that one entry — an
adapter has no way to look up a different renderer's configuration).

Nothing in this module is invoked by anything live yet. Wiring an actual
dispatcher to call these functions is Task 2B-B2b, not this one — B2a only
defines and tests the adapters themselves, against mocked provider/subprocess
boundaries.

Every adapter — including the free/local ones, which can still write
canonical episode artwork — calls
generation_gate.require_canonical_visual_execution_ready() as its first
executable operation, before any read, subprocess, download, mkdir, copy,
provider-client construction, or write. That guard is, for the whole of
Task 2B-B2a, an unconditional refusal: it always raises. `generation_gate`
is imported as a module (not `from generation_gate import ...`) specifically
so a unit test can patch `generation_gate.require_canonical_visual_execution_ready`
directly and have every adapter observe the patched version — there is no
locally-bound alias anywhere in this file that could go stale against a
patch. The import itself is local to each function, not module-level:
channel_context.py imports renderers.py (for capability validation), and
renderers.py imports this module for its adapter callables — a module-level
`import generation_gate` here would close that into a real import cycle,
since generation_gate.py itself imports channel_context.py. A local import
still resolves to the identical, single `sys.modules["generation_gate"]`
object every other importer sees, so a test patching
`generation_gate.require_canonical_visual_execution_ready` through its own
top-level import affects every adapter's lookup exactly the same way a
module-level import here would have.
"""

import base64
import io
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

import prompt_policy
import route_failures


class DispatchIntegrityError(RuntimeError):
    """A dispatch-integrity violation: a route/renderer-entry/context
    mismatch, a malformed canonical entry, or an atomic-output invariant
    broken by code rather than by a provider. Never converted into an
    ordinary route_failures record and a `False` return — there is no route
    content that could excuse it, and disguising it as an ordinary provider
    failure would let dispatch continue as though nothing structural were
    wrong. A subclass of RuntimeError, so every existing `except
    RuntimeError` boundary in this module and in callers still catches it."""


def _verify_dispatch_entry(route, ctx: "DispatchContext", expected_adapter, *,
                           renderer_field: str = "renderer_id") -> None:
    """Every adapter calls this immediately after the universal refusal
    guard, before any other meaningful operation (final boundary micro-fix,
    item 1; extended for Task 2B-B2b-2a's snapshot-binding requirement).

    Proves two things, both by object identity, never by equal-looking
    content:

      1. `route` IS `ctx.route` — the exact route object the dispatcher
         built this context for. A mutable dict, an equal copied dict, an
         independently frozen mapping, another route from the same
         snapshot, or a route from another snapshot/project all fail this —
         copying or re-freezing a route would only prove the *values* match
         at one instant, never that this call is still operating on the one
         route object the dispatcher's preflight and snapshot validation
         actually covered.
      2. `ctx.renderer_entry` IS the canonical, deeply-frozen entry
         `renderers.get(route[renderer_field])` returns for THIS route, and
         that entry's registered `adapter` is the function currently
         executing. `renderer_field` is `"renderer_id"` for every primary
         adapter and `"host_renderer_id"` for `adapt_host_composite` —
         binding against the wrong field (a host adapter reading
         `renderer_id`, or vice versa) is exactly the "primary/host
         renderer-field confusion" this parameter exists to prevent.

    Raises DispatchIntegrityError (a RuntimeError) on either failure — this
    is a dispatch-integrity violation, not an ordinary per-route failure.

    `renderers` is imported locally: renderers.py imports this module for
    its adapter callables, so a module-level `import renderers` here would
    close the same import cycle documented for `generation_gate` above."""
    import renderers

    if route is not ctx.route:
        raise DispatchIntegrityError(
            "route argument is not the exact object ctx.route governs "
            "(identity check failed) — a mutable dict, an equal copy, an "
            "independently frozen mapping, another route from the same "
            "snapshot, or a route from another snapshot/project is never "
            "trusted")

    renderer_id = route.get(renderer_field)
    if not isinstance(renderer_id, str) or not renderer_id.strip():
        raise DispatchIntegrityError(
            f"route has no usable {renderer_field}: {renderer_id!r}")
    try:
        canonical_entry = renderers.get(renderer_id)
    except renderers.RendererError as e:
        raise DispatchIntegrityError(
            f"route names an unregistered {renderer_field}: {e}") from e
    if ctx.renderer_entry is not canonical_entry:
        raise DispatchIntegrityError(
            f"ctx.renderer_entry is not the canonical registry entry for "
            f"{renderer_field} {renderer_id!r} (identity check failed) — a "
            f"caller-substituted, copied, re-frozen, or mismatched-renderer "
            f"entry is refused, never trusted")
    if canonical_entry.get("adapter") is not expected_adapter:
        raise DispatchIntegrityError(
            f"{renderer_field} {renderer_id!r}'s registered adapter does not "
            f"match the adapter currently being invoked")


def _require_execution_settings(entry) -> dict:
    """Every execution-affecting setting must be explicitly, validly
    declared on the canonical entry — no `or <default>` fallback anywhere
    (final boundary micro-fix, item 3). Missing, null, wrong-type, or
    otherwise malformed settings refuse (raise RuntimeError) before any
    credential lookup or client construction. Returns a plain dict of the
    validated values so callers never re-read `entry.get(...)` themselves
    and risk reintroducing a silent default."""
    credential_env_var = entry.get("credential_env_var")
    if not isinstance(credential_env_var, str) or not credential_env_var.strip():
        raise RuntimeError("registry entry declares no valid credential_env_var")

    model_id = entry.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise RuntimeError("registry entry declares no valid model_id")

    response_format_policy = entry.get("response_format_policy")
    if response_format_policy not in _KNOWN_RESPONSE_FORMAT_POLICIES:
        raise RuntimeError(
            f"registry entry declares an unknown response_format_policy "
            f"{response_format_policy!r}; known: {_KNOWN_RESPONSE_FORMAT_POLICIES}")

    timeout = entry.get("download_timeout_seconds")
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
            or timeout <= 0):
        raise RuntimeError(
            f"registry entry declares an invalid download_timeout_seconds "
            f"{timeout!r} — must be a positive number")

    provider_parameters = entry.get("provider_parameters")
    if not isinstance(provider_parameters, Mapping):
        raise RuntimeError("registry entry's provider_parameters is not a mapping")

    transform = entry.get("output_transform")
    if not callable(transform):
        raise RuntimeError("registry entry declares no callable output_transform")

    transform_version = entry.get("output_transform_version")
    if (not isinstance(transform_version, int) or isinstance(transform_version, bool)
            or transform_version <= 0):
        raise RuntimeError(
            f"registry entry declares an invalid output_transform_version "
            f"{transform_version!r} — must be a positive int")

    base_url = entry.get("base_url")
    if base_url is not None and (not isinstance(base_url, str) or not base_url.strip()):
        raise RuntimeError(f"registry entry declares an invalid base_url {base_url!r}")

    return {
        "credential_env_var": credential_env_var,
        "model_id": model_id,
        "response_format_policy": response_format_policy,
        "timeout": timeout,
        "provider_parameters": dict(provider_parameters),
        "transform": transform,
        "base_url": base_url,
    }


def _deep_freeze(obj):
    """Recursive deep-freeze, matching renderers._freeze()'s exact shape —
    duplicated here (rather than imported) because renderers.py imports
    THIS module for its adapter callables; importing renderers.py back
    would recreate the same import-cycle class documented above for
    generation_gate. A dict becomes a MappingProxyType of recursively-frozen
    values, a list becomes a tuple."""
    if isinstance(obj, dict):
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_deep_freeze(v) for v in obj)
    return obj


@dataclass(frozen=True)
class DispatchContext:
    """Assembled once per project by the canonical dispatcher (B2b), from an
    already-validated ProjectRoutesLoad. Every field here is already-verified
    state — an adapter never re-derives or re-resolves any of it itself.

    `approved_poses` and `approved_references` are deep-frozen in
    `__post_init__` regardless of what the caller passes in — a mutable
    dict handed to this constructor is never sufficient evidence on its own
    (corrective follow-up, item 4): an adapter reading `ctx.approved_references`
    must be reading a snapshot nothing can still be mutating underneath it,
    not a live, caller-owned dict a concurrent or later caller could still
    change. `object.__setattr__` is required here because the dataclass
    itself is frozen — this is the standard, narrow escape hatch for a
    frozen dataclass's own `__post_init__`, not a bypass of immutability."""
    project_dir: Path
    channel: object                       # channel_context.ChannelContext
    approved_poses: dict
    approved_references: dict
    poses_asset_base: Path | None
    poses_root: Path | None
    references_asset_base: Path | None
    references_root: Path | None
    output_root: Path                     # images_dir, already confirmed to exist
    renderer_entry: MappingProxyType      # RENDERERS[route[<renderer_field>]] — this route's entry only
    # The exact snapshot route this context governs (Task 2B-B2b-2a). Every
    # adapter's _verify_dispatch_entry() call requires its `route` parameter
    # to be THIS object, by identity. Defaults to None (last field, so every
    # other field keeps its no-default position) — a context built without
    # one can never pass that check, which is correct: there is no dispatch
    # without a bound route.
    route: object = None

    def __post_init__(self):
        object.__setattr__(self, "approved_poses", _deep_freeze(dict(self.approved_poses or {})))
        object.__setattr__(self, "approved_references",
                          _deep_freeze(dict(self.approved_references or {})))


def build_dispatch_context(snapshot, route, *, output_root: Path,
                           renderer_field: str = "renderer_id") -> "DispatchContext":
    """THE only factory that produces a trustworthy DispatchContext (Task
    2B-B2b-2a) — the canonical dispatcher (route_images.py) must build every
    context through this function, never through `DispatchContext(...)`
    directly with caller-assembled pieces.

    `route` must be the exact object (by identity) already present in
    `snapshot.routes` — not merely equal to one — or this raises
    DispatchIntegrityError before anything else. `approved_poses`/
    `approved_references` and their asset roots are read from `snapshot`
    itself, never accepted as separate parameters here: a caller cannot
    substitute its own registry dict by calling this factory with different
    arguments, because there is no argument that would let it.

    `renderer_field` selects which of the route's two renderer-id fields the
    resolved `renderer_entry` is bound against — `"renderer_id"` for every
    primary adapter (the default), `"host_renderer_id"` for the host-
    composite stage. Raises DispatchIntegrityError for a missing/blank/
    unregistered id at that field, exactly like `_verify_dispatch_entry()`
    will independently re-check when the adapter itself runs.

    `visual_routes`/`renderers` are imported locally: this module is itself
    imported by renderers.py (for its adapter callables), and visual_routes.py
    is imported by channel_context.py, which renderers.py also imports — a
    module-level import here would risk the same import-cycle class already
    documented above for `generation_gate`/`renderers`.
    """
    import renderers as _renderers
    import visual_routes as _visual_routes

    if not isinstance(snapshot, _visual_routes.DispatchSnapshot):
        raise DispatchIntegrityError(
            f"build_dispatch_context() requires a visual_routes.DispatchSnapshot, "
            f"got {type(snapshot).__name__}")
    if not any(route is r for r in snapshot.routes):
        raise DispatchIntegrityError(
            "route is not a member (by identity) of snapshot.routes — a "
            "mutable dict, an equal copy, an independently frozen mapping, "
            "or a route from another snapshot/project is never trusted")

    renderer_id = route.get(renderer_field)
    if not isinstance(renderer_id, str) or not renderer_id.strip():
        raise DispatchIntegrityError(
            f"route has no usable {renderer_field}: {renderer_id!r}")
    try:
        renderer_entry = _renderers.get(renderer_id)
    except _renderers.RendererError as e:
        raise DispatchIntegrityError(
            f"route names an unregistered {renderer_field}: {e}") from e

    return DispatchContext(
        project_dir=snapshot.project_dir,
        channel=snapshot.channel,
        approved_poses=snapshot.approved_poses,
        approved_references=snapshot.approved_references,
        poses_asset_base=snapshot.poses_asset_base,
        poses_root=snapshot.poses_root,
        references_asset_base=snapshot.references_asset_base,
        references_root=snapshot.references_root,
        output_root=output_root,
        renderer_entry=renderer_entry,
        route=route,
    )


# ── atomic output — every adapter's sole write path ─────────────────────────
#
# The canonical episode-artwork frame — 16:9, matching the pipeline's fixed
# output size for every generated shot. Defined here (not beside
# apply_output_transform() below) because _validate_canonical_png() needs it
# and every adapter's atomic_commit() call runs before that section.
CANONICAL_WIDTH, CANONICAL_HEIGHT = 1280, 720
OUTPUT_TRANSFORM_VERSION = 1


def _validate_canonical_png(path: Path) -> None:
    """Raises RuntimeError unless `path` is a regular, non-symlink, nonempty
    file that decodes as a PNG exactly CANONICAL_WIDTH x CANONICAL_HEIGHT.
    Called on the TEMPORARY file, before atomic_commit()'s os.replace() —
    a caller must never commit a final asset that fails this."""
    if path.is_symlink():
        raise RuntimeError(f"{path} is a symlink — refusing to treat it as adapter output")
    if not path.is_file():
        raise RuntimeError(f"{path} does not exist or is not a regular file")
    if path.stat().st_size == 0:
        raise RuntimeError(f"{path} is empty")
    from PIL import Image
    try:
        with Image.open(path) as img:
            img.load()
            fmt, size = img.format, img.size
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"{path} does not decode as a valid image: {e}") from e
    if fmt != "PNG":
        raise RuntimeError(f"{path} is not a PNG (decoded format: {fmt})")
    if size != (CANONICAL_WIDTH, CANONICAL_HEIGHT):
        raise RuntimeError(
            f"{path} is {size[0]}x{size[1]}, expected "
            f"{CANONICAL_WIDTH}x{CANONICAL_HEIGHT}")


def atomic_commit(target: Path, producer) -> None:
    """THE shared atomic-output transaction every adapter uses — including
    the two subprocess-driven ones (map/chart), which pass the temporary
    path to their subprocess rather than `target` directly.

    `producer(tmp_path)` is called with a unique temporary path inside
    `target`'s own directory, created via tempfile.mkstemp() with `target`'s
    own suffix preserved (some subprocess/library callees infer format from
    the extension). `producer` must write/render/download its ENTIRE output
    to `tmp_path` and ONLY `tmp_path` — never `target` directly, and never
    partial bytes anywhere `target` could later be mistaken for.

    On return, `tmp_path` is validated as a genuine canonical PNG
    (`_validate_canonical_png`), then committed onto `target` via
    `os.replace()` — an atomic rename, so any other process ever sees either
    the complete old `target` bytes or the complete new ones, never a
    partial file.

    On ANY failure — `producer` raising, validation failing, or
    `os.replace()` itself failing — the temporary file is removed and
    `target` (if it already existed) is left byte-for-byte untouched. The
    original exception (or the RuntimeError from validation) propagates;
    callers convert that into a route_failures record themselves — this
    function has no `route`/`project_dir` to record against.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=target.suffix or ".png")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        producer(tmp_path)
        _validate_canonical_png(tmp_path)
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


# ── free/local renderers ─────────────────────────────────────────────────────

def adapt_map(route, target: Path, ctx: DispatchContext) -> bool:
    """Deterministic GeoJSON map render. No paid call, no credentials — still
    canonical-execution-gated, because it writes approved episode artwork.

    Writes exclusively through atomic_commit(): the subprocess is given the
    unique temporary path, never `target` directly, so a crash mid-render or
    a non-zero exit can never leave a partial or stale-but-successful-looking
    file at `target`."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir, operation="adapt_map (canonical visual execution)")
    _verify_dispatch_entry(route, ctx, adapt_map)
    args = (route.get("route_args") or {}).get("map") or {}
    map_script = Path(__file__).parent / "generate_india_map.py"

    def _producer(tmp_path: Path) -> None:
        cmd = [sys.executable, str(map_script), "--out", str(tmp_path)]
        if args.get("regions"):
            cmd += ["--highlight", ",".join(args["regions"])]
        if args.get("secondary_regions"):
            cmd += ["--highlight2", ",".join(args["secondary_regions"])]
        if args.get("callout"):
            cmd += ["--callout", str(args["callout"])]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as e:
            raise RuntimeError(f"could not launch generate_india_map.py: {e}") from e
        if result.returncode != 0:
            raise RuntimeError(f"map render failed: {result.stderr[-500:]}")

    try:
        atomic_commit(target, _producer)
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"map render failed: {e}")
        return False
    return True


def adapt_chart(route, target: Path, ctx: DispatchContext) -> bool:
    """Deterministic matplotlib chart render. No paid call, no credentials —
    still canonical-execution-gated, for the same reason as adapt_map. Same
    atomic_commit() discipline: the subprocess only ever sees the temporary
    path."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir, operation="adapt_chart (canonical visual execution)")
    _verify_dispatch_entry(route, ctx, adapt_chart)
    import json as _json
    args = (route.get("route_args") or {}).get("chart") or {}
    chart_script = Path(__file__).parent / "generate_chart.py"

    def _producer(tmp_path: Path) -> None:
        cmd = [sys.executable, str(chart_script), "--out", str(tmp_path),
               "--type", str(args.get("chart_type")),
               "--data", _json.dumps(args.get("data"))]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as e:
            raise RuntimeError(f"could not launch generate_chart.py: {e}") from e
        if result.returncode != 0:
            raise RuntimeError(f"chart render failed: {result.stderr[-500:]}")

    try:
        atomic_commit(target, _producer)
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"chart render failed: {e}")
        return False
    return True


def adapt_host_composite(route, target: Path, ctx: DispatchContext) -> bool:
    """Composites an already-approved pose asset over `target`'s CURRENT
    content, treated as the base image (Task 2B-B2b-2a: `target` here is the
    dispatcher's route-level working target — the primary renderer's output
    — never the project's live final asset; the dispatcher alone decides
    when a working target becomes final).

    Binds against `route['host_renderer_id']`, not `route['renderer_id']` —
    the whole reason `_verify_dispatch_entry()` takes a `renderer_field`
    parameter. Deliberately does NOT call
    composite_character.render_production(): that public entry point is
    gated by the legacy v2 require_generation_ready(), which does not (and
    must not be made to) authorize canonical execution. Instead this calls
    composite_character's private `_composite()` core directly — the same
    rendering logic, with none of render_production()'s own authorization
    layered on top, since this adapter's own canonical guard and identity
    checks are the authorization here.

    host_placement carries free-text `position`/`framing` fields (used only
    to build the AI-generation prompt for the background render, in
    prompt_policy.py) with no controlled vocabulary the compositor's
    left/right/center placement understands — there is no deterministic
    mapping from arbitrary text to a placement side, so it is never passed
    to the compositor at all. The compositor's own canonical default (the
    pose's own negative_space metadata) always governs; this adapter never
    invents a side from text no schema constrains."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir, operation="adapt_host_composite (canonical visual execution)")
    _verify_dispatch_entry(route, ctx, adapt_host_composite, renderer_field="host_renderer_id")

    pose_id = route.get("host_pose_id")
    if not isinstance(pose_id, str) or not pose_id.strip():
        route_failures.record_failure(
            ctx.project_dir, route,
            reason=f"host composite route has no usable host_pose_id: {pose_id!r}")
        return False
    if not target.exists():
        route_failures.record_failure(
            ctx.project_dir, route,
            reason="host composite has no base image to composite onto — the "
                   "primary renderer must succeed first")
        return False

    import composite_character

    def _producer(tmp_path: Path) -> None:
        composite_character._composite(
            pose_id, background=target, out=tmp_path,
            scene_bound=bool(route.get("host_scene_bound")), context=ctx.channel)

    try:
        atomic_commit(target, _producer)
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"host composite failed: {e}")
        return False
    return True


# ── free-api renderer ────────────────────────────────────────────────────────

def adapt_photo(route, target: Path, ctx: DispatchContext) -> bool:
    """Pexels photo fetch. Free of charge, but writes approved episode
    artwork, so it is treated like any other approval-gated route. Downloads
    and resizes exclusively into the temporary path atomic_commit() gives
    it — never `target` directly."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir, operation="adapt_photo (canonical visual execution)")
    _verify_dispatch_entry(route, ctx, adapt_photo)
    import search_pexels
    args = (route.get("route_args") or {}).get("photo") or {}
    query = args.get("query")
    if not query:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason="photo route has no route_args.photo.query")
        return False
    try:
        api_key = search_pexels._get_api_key()
        if not api_key:
            raise RuntimeError("PEXELS_API_KEY is not configured")
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"pexels credential unavailable: {e}")
        return False

    def _producer(tmp_path: Path) -> None:
        ok = search_pexels.fetch_photo(query, tmp_path, api_key)
        if not ok:
            raise RuntimeError(f"pexels fetch failed for query {query!r}")

    try:
        atomic_commit(target, _producer)
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"pexels fetch failed: {e}")
        return False
    return True


# ── shared: credentials, response extraction, output transform ─────────────

def _read_credential(env_var_name: str) -> str:
    """Reads a credential by the EXACT env-var name the registry declares
    for this renderer (`ctx.renderer_entry["credential_env_var"]`, already
    validated non-blank by `_require_execution_settings`) — never a name
    hardcoded in this file, and never borrowed from another module's own
    hardcoded default (generate_images_flux.XAI_BASE_URL,
    generate_images_aibmm._get_api_key(), or a literal "XAI_API_KEY" are all
    exactly the kind of un-registry-traceable authority this closes off).
    Checks the environment first, then a `.env` file alongside this module,
    matching the existing convention elsewhere in this codebase — but keyed
    on the registry-declared name, not a name this function assumes.

    Raises RuntimeError when the credential is unavailable — final boundary
    micro-fix, item 3: a caller must never construct a client with an
    empty key. There is no empty-string return path left in this function."""
    value = os.environ.get(env_var_name)
    if value:
        return value
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{env_var_name}="):
                found = line.split("=", 1)[1].strip().strip('"').strip("'")
                if found:
                    return found
    raise RuntimeError(
        f"credential {env_var_name!r} is not set (checked the environment and "
        f"a .env file) — refusing to construct a client with an empty key")


def _client_kwargs(settings: dict) -> dict:
    """api_key + (optionally) base_url, built only from an already-validated
    settings dict (`_require_execution_settings()`'s return) — never a
    module-level default imported from elsewhere, and never a raw,
    unvalidated `entry.get(...)` read."""
    kwargs = {"api_key": _read_credential(settings["credential_env_var"])}
    if settings["base_url"]:
        kwargs["base_url"] = settings["base_url"]
    return kwargs


# Response-format policies this codebase knows how to extract bytes from.
# A registry entry declaring anything outside this set is refused rather
# than silently trying every extraction strategy regardless of what was
# actually declared.
_KNOWN_RESPONSE_FORMAT_POLICIES = ("url_or_b64json",)


def _extract_image_bytes(response, *, response_format_policy: str,
                         timeout: int) -> bytes | None:
    """Extracts raw image bytes per the registry-declared response-format
    policy and download timeout — both bound in the hash projection, not
    hardcoded in this function. Handles both response shapes a provider
    following `url_or_b64json` may return."""
    if response_format_policy not in _KNOWN_RESPONSE_FORMAT_POLICIES:
        raise RuntimeError(
            f"unknown response_format_policy {response_format_policy!r}; "
            f"known: {_KNOWN_RESPONSE_FORMAT_POLICIES}")
    img_data = response.data[0]
    url = getattr(img_data, "url", None)
    if url:
        import requests
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    b64 = getattr(img_data, "b64_json", None)
    if b64:
        return base64.b64decode(b64)
    return None


def apply_output_transform(image_bytes: bytes) -> bytes:
    """THE canonical output-transform policy (version 1) every provider
    adapter that writes canonical episode artwork must call — the registry's
    `output_transform` field names exactly this function (by qualified
    name, in the hash projection), so a change to its behavior is a
    hash-visible, approval-invalidating event, and the declaration is never
    just an opaque version int disconnected from real code.

    Deterministic: decodes whatever bytes the provider returned (a Grok
    response is already 16:9-ish; a gpt-image-2 edit response is commonly
    1536x1024, which is NOT 16:9 and must never be written as the final
    canonical asset unchanged), center-crops to a 16:9 aspect ratio without
    ever stretching/distorting the source, resizes to the canonical
    1280x720, and re-encodes as PNG. Raises on any decode/processing
    failure — callers must not write a partial or unconverted file if this
    raises."""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes))
    img.load()
    img = img.convert("RGB")
    w, h = img.size
    target_ratio = CANONICAL_WIDTH / CANONICAL_HEIGHT
    current_ratio = w / h
    if current_ratio > target_ratio:
        new_w = max(1, round(h * target_ratio))
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    elif current_ratio < target_ratio:
        new_h = max(1, round(w / target_ratio))
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    img = img.resize((CANONICAL_WIDTH, CANONICAL_HEIGHT), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ── paid renderer: xAI/Grok illustration & reenactment ───────────────────────

def adapt_flux(route: dict, target: Path, ctx: DispatchContext) -> bool:
    """xAI/Grok illustration or reenactment. Every execution-affecting
    setting — provider, model, credential slot, base URL, request
    parameters, response-format policy, download timeout, output transform
    — comes from ctx.renderer_entry via `_require_execution_settings()`,
    never from a CLI flag, an environment variable chosen at call time,
    another module's hardcoded default, a route field, or an `or <default>`
    fallback. No adapter parameter exists for a caller to override any of
    it."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir, operation="adapt_flux (canonical visual execution)")
    _verify_dispatch_entry(route, ctx, adapt_flux)
    entry = ctx.renderer_entry
    if entry.get("provider") != "xai":
        raise RuntimeError(f"adapt_flux only supports provider 'xai', got "
                           f"{entry.get('provider')!r}")
    settings = _require_execution_settings(entry)

    try:
        prompt = prompt_policy.build_effective_prompt(route)
    except prompt_policy.PromptPolicyError as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"typed prompt is not usable: {e}")
        return False

    try:
        from openai import OpenAI
    except ImportError:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason="openai package not installed")
        return False

    try:
        client = OpenAI(**_client_kwargs(settings))
    except RuntimeError as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"credential unavailable: {e}")
        return False
    try:
        response = client.images.generate(
            model=settings["model_id"], prompt=prompt, **settings["provider_parameters"])
        img_bytes = _extract_image_bytes(
            response, response_format_policy=settings["response_format_policy"],
            timeout=settings["timeout"])
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"flux/grok generation failed: {e}")
        return False
    if img_bytes is None:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason="flux/grok response carried no usable image data")
        return False

    try:
        final_bytes = settings["transform"](img_bytes)
    except Exception as e:
        # Never leave a successful-looking final file behind on a transform
        # failure — nothing has been written to `target` at all yet, and
        # atomic_commit() below is never even reached.
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"output transform failed: {e}")
        return False

    try:
        atomic_commit(target, lambda tmp_path: tmp_path.write_bytes(final_bytes))
    except Exception as e:
        # The transformed bytes are already the registered-transform's own
        # output; a failure here is atomic_commit's own validation or
        # os.replace() failing, never "untransformed bytes" — this adapter
        # never writes final_bytes anywhere except through atomic_commit.
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"could not commit the final asset: {e}")
        return False
    return True


# ── paid renderer: reference-anchored generation ─────────────────────────────

# Deterministic order every reference-anchored call passes assets in — body
# identity establishes the figure, face identity refines it, matching the
# order this codebase's own pose records already describe
# ("images.edit (dual reference: body_master v4 + face_master v3)").
REFERENCE_ORDER = ("body_master", "face_master")


def _exactly_body_then_face(ref_ids) -> bool:
    """True only for a list of exactly two strings that are, in some order,
    body_master and face_master once each — never for a wrong count, a
    duplicate (["body_master", "body_master"] has length 2 but is not this),
    a non-list, or a non-string element. `set()` equality alone cannot tell
    ["body_master", "face_master", "body_master"] apart from the correct
    pair (both produce the same 2-element set) — this checks length AND
    element identity, not set membership alone."""
    return (isinstance(ref_ids, list) and len(ref_ids) == 2
            and all(isinstance(r, str) for r in ref_ids)
            and sorted(ref_ids) == sorted(REFERENCE_ORDER))


def adapt_flux_reference_anchor(route: dict, target: Path, ctx: DispatchContext) -> bool:
    """Reference-anchored generation via OpenAI's image-edit endpoint,
    anchored on both approved character masters. No path in this function
    ever calls images.generate() — a missing/invalid reference, an
    edit-call exception, or an empty response are all refusals, never a
    silent fallback to unanchored generation.

    Reference bytes are revalidated here, not trusted from
    ctx.approved_references alone: each reference id is read through
    reference_registry.read_verified_bytes(), which resolves, verifies
    (status, top-level/master agreement, path containment, forbidden
    archive/candidate/raw/pending components, symlink escapes), reads, and
    re-hashes the exact bytes returned — closing the gap where a separate
    later `path.read_bytes()` call could read a file that changed after an
    earlier check. ctx.approved_references (deep-frozen, but still just a
    prior snapshot) is not consulted for this decision at all."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir,
        operation="adapt_flux_reference_anchor (canonical visual execution)")
    _verify_dispatch_entry(route, ctx, adapt_flux_reference_anchor)
    entry = ctx.renderer_entry
    if entry.get("provider") != "openai":
        raise RuntimeError(f"adapt_flux_reference_anchor only supports provider "
                           f"'openai', got {entry.get('provider')!r}")
    settings = _require_execution_settings(entry)

    ref_ids = route.get("host_reference_asset_ids")
    if not _exactly_body_then_face(ref_ids):
        route_failures.record_failure(
            ctx.project_dir, route,
            reason=f"reference_anchored_generation requires a list of exactly "
                   f"{sorted(REFERENCE_ORDER)} (each exactly once), got {ref_ids!r}")
        return False

    import reference_registry

    reference_bytes = []
    for rid in REFERENCE_ORDER:               # deterministic: body, then face
        try:
            reference_bytes.append(
                reference_registry.read_verified_bytes(rid, context=ctx.channel))
        except reference_registry.ReferenceError as e:
            route_failures.record_failure(
                ctx.project_dir, route,
                reason=f"reference {rid!r} failed verification: {e}")
            return False

    try:
        prompt = prompt_policy.build_effective_prompt(route)
    except prompt_policy.PromptPolicyError as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"typed prompt is not usable: {e}")
        return False

    try:
        from openai import OpenAI
    except ImportError:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason="openai package not installed")
        return False

    try:
        client = OpenAI(**_client_kwargs(settings))
    except RuntimeError as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"credential unavailable: {e}")
        return False
    file_objs = [io.BytesIO(b) for b in reference_bytes]
    try:
        response = client.images.edit(
            model=settings["model_id"], image=file_objs, prompt=prompt,
            **settings["provider_parameters"])
    except Exception as e:
        # Refusal, never a fallback to images.generate() without the
        # reference — that would silently turn an anchored request into
        # an unanchored one.
        route_failures.record_failure(
            ctx.project_dir, route,
            reason=f"reference-anchored edit failed, refusing rather than "
                   f"falling back to unanchored generation: {e}")
        return False

    img_bytes = _extract_image_bytes(
        response, response_format_policy=settings["response_format_policy"],
        timeout=settings["timeout"])
    if not img_bytes:
        route_failures.record_failure(
            ctx.project_dir, route,
            reason="reference-anchored edit returned no usable image data; refused")
        return False

    try:
        final_bytes = settings["transform"](img_bytes)
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"output transform failed: {e}")
        return False

    try:
        atomic_commit(target, lambda tmp_path: tmp_path.write_bytes(final_bytes))
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"could not commit the final asset: {e}")
        return False
    return True
