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
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import prompt_policy
import route_failures


@dataclass(frozen=True)
class DispatchContext:
    """Assembled once per project by the canonical dispatcher (B2b), from an
    already-validated ProjectRoutesLoad. Every field here is already-verified
    state — an adapter never re-derives or re-resolves any of it itself."""
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


# ── paid renderer: xAI/Grok illustration & reenactment ───────────────────────

def _extract_image_bytes(response) -> bytes | None:
    """Handles both response shapes (url or b64_json) — same contract
    generate_images_flux.generate_image_grok() already uses, duplicated here
    rather than calling that function directly: it bakes the obsolete
    STYLE_PREFIX/STYLE_SUFFIX into every prompt unconditionally, which would
    double-wrap prompt_policy.build_effective_prompt()'s own, corrected
    output. generate_images_flux.py is a direct CLI, out of this task's
    scope to modify (Task 2B-B2a amendment 6) — so this adapter builds the
    provider call itself instead of routing through that function."""
    img_data = response.data[0]
    url = getattr(img_data, "url", None)
    if url:
        import requests
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        return r.content
    b64 = getattr(img_data, "b64_json", None)
    if b64:
        return base64.b64decode(b64)
    return None


def adapt_flux(route: dict, target: Path, ctx: DispatchContext) -> bool:
    """xAI/Grok illustration or reenactment. Provider and model come only
    from ctx.renderer_entry — never from a CLI flag, environment variable
    chosen at call time, or route field. No adapter parameter exists for a
    caller to override either."""
    import generation_gate
    generation_gate.require_canonical_visual_execution_ready(
        ctx.project_dir, operation="adapt_flux (canonical visual execution)")
    entry = ctx.renderer_entry
    if entry.get("provider") != "xai":
        raise RuntimeError(f"adapt_flux only supports provider 'xai', got "
                           f"{entry.get('provider')!r}")
    prompt = prompt_policy.build_effective_prompt(route)

    import generate_images_flux as gif
    try:
        from openai import OpenAI
    except ImportError:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason="openai package not installed")
        return False

    api_key = gif.load_env_key(Path(__file__).parent, "XAI_API_KEY")
    client = OpenAI(api_key=api_key, base_url=gif.XAI_BASE_URL)
    try:
        response = client.images.generate(
            model=entry["model_id"], prompt=prompt,
            **dict(entry.get("provider_parameters") or {}))
        img_bytes = _extract_image_bytes(response)
    except Exception as e:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason=f"flux/grok generation failed: {e}")
        return False
    if img_bytes is None:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason="flux/grok response carried no usable image data")
        return False
    target.write_bytes(img_bytes)
    gif.ensure_png(target)
    return True


# ── paid renderer: reference-anchored generation ─────────────────────────────

# Deterministic order every reference-anchored call passes assets in — body
# identity establishes the figure, face identity refines it, matching the
# order this codebase's own pose records already describe
# ("images.edit (dual reference: body_master v4 + face_master v3)").
REFERENCE_ORDER = ("body_master", "face_master")


def adapt_flux_reference_anchor(route: dict, target: Path, ctx: DispatchContext) -> bool:
    """Reference-anchored generation via OpenAI images.edit(), anchored on
    both approved character masters. No path in this function ever calls
    images.generate() — a missing reference, an edit-call exception, or an
    empty response are all refusals, never a silent fallback to unanchored
    generation."""
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

    resolved_paths = []
    for rid in REFERENCE_ORDER:               # deterministic: body, then face
        record = (ctx.approved_references or {}).get(rid)
        if record is None:
            route_failures.record_failure(
                ctx.project_dir, route,
                reason=f"reference {rid!r} is not in the approved reference set")
            return False
        resolved_paths.append((Path(ctx.references_asset_base) / record["path"]).resolve())

    prompt = prompt_policy.build_effective_prompt(route)

    try:
        from openai import OpenAI
    except ImportError:
        route_failures.record_failure(ctx.project_dir, route,
                                       reason="openai package not installed")
        return False

    from generate_images_aibmm import _extract_image, _get_api_key

    api_key = _get_api_key()
    client = OpenAI(api_key=api_key)
    opened = []
    try:
        for p in resolved_paths:
            opened.append(open(p, "rb"))
        try:
            response = client.images.edit(
                model=entry["model_id"], image=opened, prompt=prompt,
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
    finally:
        for fh in opened:
            fh.close()

    img_bytes = _extract_image(response)
    if not img_bytes:
        route_failures.record_failure(
            ctx.project_dir, route,
            reason="reference-anchored edit returned no usable image data; refused")
        return False
    target.write_bytes(img_bytes)
    return True
