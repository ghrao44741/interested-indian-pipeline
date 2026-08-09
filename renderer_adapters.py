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


def _verify_dispatch_entry(route: dict, ctx: "DispatchContext", expected_adapter) -> None:
    """Every adapter calls this immediately after the universal refusal
    guard, before any other meaningful operation (final boundary micro-fix,
    item 1). Proves `ctx.renderer_entry` IS — by object identity, not
    equal-looking content — the exact, canonical, deeply-frozen entry
    `renderers.get(route['renderer_id'])` returns for THIS route, and that
    its registered `adapter` is the function currently executing.

    A caller-constructed dict, a fresh MappingProxyType built to look
    identical, a copy of the real entry, or another renderer's real entry
    all fail the `is` check below — copying or re-freezing an entry would
    only prove the *values* match at one instant, never that dispatch is
    still reading the one canonical object every other reader (the hash
    projection, a future dispatcher) also reads. Raises RuntimeError — this
    is a dispatch-integrity violation, not an ordinary per-route failure,
    so it is never converted into a route_failures record and a `False`
    return; there is no route content that could excuse it.

    `renderers` is imported locally: renderers.py imports this module for
    its adapter callables, so a module-level `import renderers` here would
    close the same import cycle documented for `generation_gate` above."""
    import renderers

    renderer_id = route.get("renderer_id")
    if not isinstance(renderer_id, str) or not renderer_id.strip():
        raise RuntimeError(f"route has no usable renderer_id: {renderer_id!r}")
    try:
        canonical_entry = renderers.get(renderer_id)
    except renderers.RendererError as e:
        raise RuntimeError(f"route names an unregistered renderer_id: {e}") from e
    if ctx.renderer_entry is not canonical_entry:
        raise RuntimeError(
            f"ctx.renderer_entry is not the canonical registry entry for "
            f"renderer_id {renderer_id!r} (identity check failed) — a "
            f"caller-substituted, copied, re-frozen, or mismatched-renderer "
            f"entry is refused, never trusted")
    if canonical_entry.get("adapter") is not expected_adapter:
        raise RuntimeError(
            f"renderer_id {renderer_id!r}'s registered adapter does not match "
            f"the adapter currently being invoked")


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
    renderer_entry: MappingProxyType      # RENDERERS[route["renderer_id"]] — this route's entry only

    def __post_init__(self):
        object.__setattr__(self, "approved_poses", _deep_freeze(dict(self.approved_poses or {})))
        object.__setattr__(self, "approved_references",
                          _deep_freeze(dict(self.approved_references or {})))


# ── free/local renderers ─────────────────────────────────────────────────────

def adapt_map(route: dict, target: Path, ctx: DispatchContext) -> bool:
    """Deterministic GeoJSON map render. No paid call, no credentials — still
    canonical-execution-gated, because it writes approved episode artwork."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir, operation="adapt_map (canonical visual execution)")
    _verify_dispatch_entry(route, ctx, adapt_map)
    args = (route.get("route_args") or {}).get("map") or {}
    cmd = [sys.executable, str(Path(__file__).parent / "generate_india_map.py"),
           "--out", str(target)]
    if args.get("regions"):
        cmd += ["--highlight", ",".join(args["regions"])]
    if args.get("secondary_regions"):
        cmd += ["--highlight2", ",".join(args["secondary_regions"])]
    if args.get("callout"):
        cmd += ["--callout", str(args["callout"])]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"map render failed: {result.stderr[-500:]}")
        return False
    return True


def adapt_chart(route: dict, target: Path, ctx: DispatchContext) -> bool:
    """Deterministic matplotlib chart render. No paid call, no credentials —
    still canonical-execution-gated, for the same reason as adapt_map."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir, operation="adapt_chart (canonical visual execution)")
    _verify_dispatch_entry(route, ctx, adapt_chart)
    import json as _json
    args = (route.get("route_args") or {}).get("chart") or {}
    cmd = [sys.executable, str(Path(__file__).parent / "generate_chart.py"),
           "--out", str(target), "--type", str(args.get("chart_type")),
           "--data", _json.dumps(args.get("data"))]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"chart render failed: {result.stderr[-500:]}")
        return False
    return True


def adapt_host_composite(route: dict, target: Path, ctx: DispatchContext) -> bool:
    """Composites an already-approved pose asset. Spends nothing, but writes
    approved episode artwork, so composite_character.render_production()
    re-checks generation-readiness itself — deliberately, per its own
    contract — rather than trusting this adapter's caller alone."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir, operation="adapt_host_composite (canonical visual execution)")
    _verify_dispatch_entry(route, ctx, adapt_host_composite)
    import composite_character
    try:
        result = composite_character.render_production(
            ctx.project_dir, route["host_pose_id"], background=target, out=target,
            scene_bound=bool(route.get("host_scene_bound")))
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"host composite failed: {e}")
        return False
    return bool(result)


# ── free-api renderer ────────────────────────────────────────────────────────

def adapt_photo(route: dict, target: Path, ctx: DispatchContext) -> bool:
    """Pexels photo fetch. Free of charge, but writes approved episode
    artwork, so it is treated like any other approval-gated route."""
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
    api_key = search_pexels._get_api_key()
    ok = search_pexels.fetch_photo(query, target, api_key)
    if not ok:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"pexels fetch failed for query {query!r}")
    return bool(ok)


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


# The canonical episode-artwork frame — 16:9, matching the pipeline's fixed
# output size for every generated shot.
CANONICAL_WIDTH, CANONICAL_HEIGHT = 1280, 720
OUTPUT_TRANSFORM_VERSION = 1


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
        # failure — nothing has been written to `target` at all yet.
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"output transform failed: {e}")
        return False
    target.write_bytes(final_bytes)
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
    target.write_bytes(final_bytes)
    return True
