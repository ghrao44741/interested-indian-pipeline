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
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

import prompt_policy
import route_failures


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

def _read_credential(env_var_name: str | None) -> str:
    """Reads a credential by the EXACT env-var name the registry declares
    for this renderer (`ctx.renderer_entry["credential_env_var"]`) — never a
    name hardcoded in this file, and never borrowed from another module's
    own hardcoded default (generate_images_flux.XAI_BASE_URL,
    generate_images_aibmm._get_api_key(), or a literal "XAI_API_KEY" are all
    exactly the kind of un-registry-traceable authority this closes off).
    Checks the environment first, then a `.env` file alongside this module,
    matching the existing convention elsewhere in this codebase — but keyed
    on the registry-declared name, not a name this function assumes."""
    if not env_var_name:
        raise RuntimeError(
            "this renderer's registry entry declares no credential_env_var")
    value = os.environ.get(env_var_name)
    if value:
        return value
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{env_var_name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _client_kwargs(entry) -> dict:
    """api_key + (optionally) base_url, built only from what the registry
    declares — never a module-level default imported from elsewhere."""
    kwargs = {"api_key": _read_credential(entry.get("credential_env_var"))}
    base_url = entry.get("base_url")
    if base_url:
        kwargs["base_url"] = base_url
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
    — comes from ctx.renderer_entry, never from a CLI flag, an environment
    variable chosen at call time, another module's hardcoded default, or a
    route field. No adapter parameter exists for a caller to override any
    of it."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir, operation="adapt_flux (canonical visual execution)")
    entry = ctx.renderer_entry
    if entry.get("provider") != "xai":
        raise RuntimeError(f"adapt_flux only supports provider 'xai', got "
                           f"{entry.get('provider')!r}")

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

    client = OpenAI(**_client_kwargs(entry))
    try:
        response = client.images.generate(
            model=entry["model_id"], prompt=prompt,
            **dict(entry.get("provider_parameters") or {}))
        img_bytes = _extract_image_bytes(
            response, response_format_policy=entry.get("response_format_policy"),
            timeout=entry.get("download_timeout_seconds") or 60)
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"flux/grok generation failed: {e}")
        return False
    if img_bytes is None:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason="flux/grok response carried no usable image data")
        return False

    transform = entry.get("output_transform") or apply_output_transform
    try:
        final_bytes = transform(img_bytes)
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


def adapt_flux_reference_anchor(route: dict, target: Path, ctx: DispatchContext) -> bool:
    """Reference-anchored generation via OpenAI's image-edit endpoint,
    anchored on both approved character masters. No path in this function
    ever calls images.generate() — a missing/invalid reference, an
    edit-call exception, or an empty response are all refusals, never a
    silent fallback to unanchored generation.

    Reference bytes are revalidated here, not trusted from
    ctx.approved_references alone: each reference id is resolved through
    reference_registry.resolve(), which re-verifies status, top-level/master
    agreement, path containment, forbidden (archive/candidate/raw/pending)
    components, symlink escapes, and hash freshness against the CURRENT
    channel — the same shared verifier visual_routes.validate_contract()
    itself uses — before any credential is read or client constructed.
    ctx.approved_references (deep-frozen, but still just a prior snapshot)
    is not consulted for this decision at all."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir,
        operation="adapt_flux_reference_anchor (canonical visual execution)")
    entry = ctx.renderer_entry
    if entry.get("provider") != "openai":
        raise RuntimeError(f"adapt_flux_reference_anchor only supports provider "
                           f"'openai', got {entry.get('provider')!r}")

    ref_ids = route.get("host_reference_asset_ids") or []
    if set(ref_ids) != set(REFERENCE_ORDER):
        route_failures.record_failure(
            ctx.project_dir, route,
            reason=f"reference_anchored_generation requires exactly "
                   f"{sorted(REFERENCE_ORDER)}, got {sorted(ref_ids)}")
        return False

    import reference_registry

    resolved_paths = []
    for rid in REFERENCE_ORDER:               # deterministic: body, then face
        try:
            p = reference_registry.resolve(rid, context=ctx.channel)
        except reference_registry.ReferenceError as e:
            route_failures.record_failure(
                ctx.project_dir, route,
                reason=f"reference {rid!r} failed verification: {e}")
            return False
        resolved_paths.append(p)

    # Read and verify the exact bytes now, before any credential lookup or
    # client construction — resolve() already hash-checked the file; this
    # is the bytes that will actually be sent, read once, right before use.
    try:
        reference_bytes = [p.read_bytes() for p in resolved_paths]
    except OSError as e:
        route_failures.record_failure(
            ctx.project_dir, route, reason=f"could not read a verified reference: {e}")
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

    client = OpenAI(**_client_kwargs(entry))
    file_objs = [io.BytesIO(b) for b in reference_bytes]
    try:
        response = client.images.edit(
            model=entry["model_id"], image=file_objs, prompt=prompt,
            **dict(entry.get("provider_parameters") or {}))
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
        response, response_format_policy=entry.get("response_format_policy"),
        timeout=entry.get("download_timeout_seconds") or 60)
    if not img_bytes:
        route_failures.record_failure(
            ctx.project_dir, route,
            reason="reference-anchored edit returned no usable image data; refused")
        return False

    transform = entry.get("output_transform") or apply_output_transform
    try:
        final_bytes = transform(img_bytes)
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"output transform failed: {e}")
        return False
    target.write_bytes(final_bytes)
    return True
