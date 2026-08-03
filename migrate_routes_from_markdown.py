"""migrate_routes_from_markdown.py — one-time bridge from legacy prompt
markdown to the canonical `visual_routes.json` contract (Task 2B-B1).

This is the ONLY place legacy `image_prompts_one_line_per_prompt.md`-style
markdown is read after cutover. It is never invoked automatically by any
pipeline stage, script, or hook — a human runs it deliberately, once, per
project, and reviews the result.

It preserves every legacy ambiguity honestly rather than guessing:

  - a legacy `CARTOON` route becomes NEEDS_REVIEW with candidates
    ["ILLUSTRATION", "REENACTMENT"] — never silently resolved to either.
  - a legacy `HOST` route becomes NEEDS_REVIEW with host_present=true and
    host_method=null — a base visual_type and host method are both left for
    a human to assign.
  - anything the legacy router itself could not classify, or flagged
    needs_review, carries that forward as a structured reason — never
    silently repaired.

For an unambiguous MAP, CHART or PHOTO route it resolves and stores a real
renderer binding from the *governing Channel Pack*: renderer_id, cost_category
and paid are set from the pack's own capability and the code-owned registry,
and the route is marked READY only when that renderer is both registered and
implemented. A route whose visual_type would otherwise be clean but whose
pack declares no usable renderer for it is downgraded to NEEDS_REVIEW with a
RENDERER_UNAVAILABLE reason — migration never writes a READY route with a
null renderer_id or cost_category.

Before writing anything, the complete built artifact is run through
visual_routes.validate_contract() against the real manifest, the real
manifest hash, the governing channel's binding and capabilities, and the
current renderer registry — a migration that would produce an
integrity-broken artifact refuses to write it at all.

Never modifies the legacy input, the project's manifest, any approval, or
any other project file. Never creates a missing project directory. Never
overwrites an existing visual_routes.json/.md (there is no --force). Refuses
a legacy input that is external to the pipeline root, unmanifested, or
belongs to a different channel than the target project — before any temp
file or output is created.

    python migrate_routes_from_markdown.py \\
        --legacy-md ep01/image_prompts_one_line_per_prompt.md --project ep01
    python migrate_routes_from_markdown.py \\
        --legacy-md ep01/image_prompts_one_line_per_prompt.md --project ep01 --dry-run
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import channel_context
import renderers
import route_images
import visual_routes as vr

PIPELINE_DIR = Path(__file__).parent


class MigrationError(vr.VisualRoutesError):
    """The requested migration could not be performed."""


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _extract_prompt_overlay_cue(line: str) -> tuple[str | None, str | None, str | None]:
    """Fields route_images.parse_shots() does not carry (it only classifies).

    Read-only, best-effort extraction directly from the raw markdown line —
    the same regex shape already used by review_images.py/match_images.py
    for the same three fields.
    """
    if not line:
        return None, None, None
    m_prompt = re.search(r'PROMPT:\s*(.+?)\s*(?:OVERLAY:|CUE:|$)', line)
    m_overlay = re.search(r'OVERLAY:\s*(.+?)\s*(?:CUE:|$)', line)
    m_cue = re.search(r'CUE:\s*(.+)$', line)
    return (m_prompt.group(1).strip() if m_prompt else None,
            m_overlay.group(1).strip() if m_overlay else None,
            m_cue.group(1).strip() if m_cue else None)


def _parse_legacy_map_args(raw: str) -> dict:
    import shlex
    regions, secondary, callout = [], [], None
    try:
        tokens = shlex.split(raw) if raw else []
    except ValueError:
        tokens = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "--highlight" and i + 1 < len(tokens):
            regions = [s.strip() for s in tokens[i + 1].split(",") if s.strip()]
            i += 2
        elif tokens[i] == "--highlight2" and i + 1 < len(tokens):
            secondary = [s.strip() for s in tokens[i + 1].split(",") if s.strip()]
            i += 2
        elif tokens[i] == "--callout" and i + 1 < len(tokens):
            callout = tokens[i + 1]
            i += 2
        else:
            i += 1
    return {"regions": regions, "secondary_regions": secondary, "callout": callout}


def _parse_legacy_chart_args(raw: str):
    """Returns a canonical chart_args dict, the sentinel "TIMELINE_CANDIDATE",
    or None (unparseable / incomplete)."""
    import shlex
    if not raw:
        return None
    try:
        tokens = shlex.split(raw)
    except ValueError:
        return None
    if "--type" not in tokens or "--data" not in tokens:
        return None
    try:
        chart_type = tokens[tokens.index("--type") + 1]
        data_str = tokens[tokens.index("--data") + 1]
    except IndexError:
        return None
    title = None
    if "--title" in tokens:
        try:
            title = tokens[tokens.index("--title") + 1]
        except IndexError:
            title = None
    if chart_type == "timeline":
        # TIMELINE is now its own canonical visual_type, not a CHART variant.
        # Converting event data reliably needs a human, not a guess.
        return "TIMELINE_CANDIDATE"
    if chart_type not in ("bar", "stat", "pie"):
        return None
    try:
        raw_data = json.loads(data_str)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw_data, list) or not raw_data:
        return None
    points = []
    for item in raw_data:
        if not isinstance(item, dict):
            return None
        label = item.get("label") or item.get("name")
        value = item.get("value", item.get("y"))
        if label is None or not isinstance(value, (int, float)):
            return None
        points.append({"label": str(label), "value": float(value)})
    return {"chart_type": chart_type, "data": points, "title": title}


def migrate_legacy_shot(shot: dict, manifest_scene: dict | None,
                        prompt: str | None, overlay: str | None, cue: str | None) -> dict:
    """One route_images.parse_shots() shot + its manifest scene (if resolved)
    -> one canonical route dict. Never invents an identity, a base visual
    type for HOST, or a resolution for CARTOON's ILLUSTRATION/REENACTMENT
    ambiguity."""
    review_reasons: list[dict] = []
    candidate_visual_types: list[str] = []
    visual_type = None
    host_present = False
    host_method = None
    route_args = vr.empty_route_args()
    status = vr.STATUS_READY

    def mark(code: str, detail: str):
        nonlocal status
        status = vr.STATUS_NEEDS_REVIEW
        review_reasons.append({"code": code, "detail": detail})

    scene = manifest_scene or {}
    visual_asset_id = scene.get("visual_asset_id")
    source_ids = list(scene.get("source_ids") or [])
    shot_instance_id = scene.get("shot_instance_id")
    if not (visual_asset_id and shot_instance_id):
        mark("MANIFEST_IDENTITY_UNRESOLVED",
             f"no visual_asset_id/shot_instance_id resolved from the manifest for "
             f"{shot['file']!r}")
        visual_asset_id = visual_asset_id or f"UNRESOLVED-{shot['file']}"
        shot_instance_id = shot_instance_id or f"UNRESOLVED-{shot['file']}"

    raw_type = shot["type"]

    if raw_type == "CARTOON":
        mark("AMBIGUOUS_LEGACY_CARTOON",
             "legacy CARTOON route requires a manual choice between ILLUSTRATION "
             "and REENACTMENT")
        candidate_visual_types = ["ILLUSTRATION", "REENACTMENT"]

    elif raw_type == "HOST":
        host_present = True
        mark("AMBIGUOUS_LEGACY_HOST",
             "legacy HOST route has no base visual_type or host_method recorded — "
             "assign both manually")

    elif raw_type == "MAP":
        visual_type = "MAP"
        parsed = _parse_legacy_map_args(shot["map_args"])
        route_args["map"] = parsed
        if not parsed["regions"]:
            mark("INCOMPLETE_ROUTE_ARGS", "legacy MAP_ARGS had no --highlight regions")
        if shot["needs_review"]:
            mark("INCOMPLETE_ROUTE_ARGS",
                 shot["review_reason"] or "flagged needs_review by the legacy router")

    elif raw_type == "CHART":
        parsed = _parse_legacy_chart_args(shot["chart_args"])
        if parsed is None:
            mark("INCOMPLETE_ROUTE_ARGS",
                 "legacy CHART_ARGS could not be parsed into a canonical chart shape")
        elif parsed == "TIMELINE_CANDIDATE":
            mark("LEGACY_UNCLASSIFIED",
                 "legacy CHART_ARGS declared --type timeline, which is now the "
                 "separate TIMELINE visual_type — a human must re-author the "
                 "structured events")
            candidate_visual_types = ["TIMELINE"]
        else:
            visual_type = "CHART"
            route_args["chart"] = parsed
        if shot["needs_review"] and visual_type:
            mark("INCOMPLETE_ROUTE_ARGS",
                 shot["review_reason"] or "flagged needs_review by the legacy router")

    elif raw_type == "PHOTO":
        visual_type = "PHOTO"
        query = shot["narration"] or None
        route_args["photo"] = {"query": query, "constraints": None}
        if not query:
            mark("INCOMPLETE_ROUTE_ARGS",
                 "legacy PHOTO route had no narration to use as a search query")

    else:  # UNCLASSIFIED — missing, misspelled, or unrecognised TYPE
        mark("LEGACY_UNCLASSIFIED",
             shot["review_reason"] or f"legacy TYPE {raw_type!r} is not recognised")
        candidate_visual_types = list(shot.get("route_candidates") or [])

    return {
        "visual_asset_id": visual_asset_id,
        "source_ids": source_ids,
        "shot_instance_id": shot_instance_id,
        "scene_id": shot["scene_id"] or "",
        "output_file": shot["file"],
        "status": status,
        "review_reasons": review_reasons,
        "candidate_visual_types": candidate_visual_types,
        "routing_confidence": None,
        "manual_override": None,
        "visual_type": visual_type,
        "route_args": route_args,
        "narration": shot["narration"],
        "prompt": prompt,
        "visual_cue": cue,
        "overlay_text": overlay,
        "renderer_id": None,
        "host_renderer_id": None,
        "cost_category": None,
        "paid": False,
        "host_present": host_present,
        "host_method": host_method,
        "host_pose_id": None,
        "host_scene_bound": None,
        "host_reference_asset_ids": None,
        "host_placement": None,
    }


def _resolve_renderer_or_downgrade(route: dict, renderer_capabilities: dict,
                                   registry: dict) -> None:
    """Mutates route in place. A route that is still READY after
    migrate_legacy_shot() and names a canonical visual_type gets a real
    renderer_id/cost_category/paid resolved from the governing Channel Pack —
    never left null on a READY route. If the pack declares no capability for
    the type, or the capability names an unregistered/unimplemented
    renderer, the route is downgraded to NEEDS_REVIEW with a structured
    reason rather than written READY with nothing to actually dispatch."""
    vt = route["visual_type"]
    if vt is None or route["status"] != vr.STATUS_READY:
        return
    renderer_id = renderer_capabilities.get(vt)
    entry = registry.get(renderer_id) if renderer_id else None
    if renderer_id is None or entry is None or not entry.get("implemented"):
        route["status"] = vr.STATUS_NEEDS_REVIEW
        route["review_reasons"].append({
            "code": "RENDERER_UNAVAILABLE",
            "detail": f"the governing Channel Pack has no implemented renderer for "
                     f"{vt} (capability: {renderer_id!r})",
        })
        return
    route["renderer_id"] = renderer_id
    route["cost_category"] = entry["cost_category"]
    route["paid"] = entry["cost_category"] == "paid_api"


def migrate(legacy_md, project, *, dry_run: bool = False) -> dict:
    """Full migration, all refusal checks before any directory/temp-file/output
    creation. Returns {"routes": n, "needs_review": n, "doc": doc, "written": bool}."""
    legacy_path = Path(legacy_md)
    if not legacy_path.is_file():
        raise MigrationError(f"legacy markdown not found: {legacy_path}")

    legacy_resolved = legacy_path.resolve()
    if not legacy_resolved.is_relative_to(PIPELINE_DIR.resolve()):
        raise MigrationError(
            f"{legacy_path} resolves to {legacy_resolved}, outside the pipeline root — "
            f"refusing an external legacy input")
    legacy_project_dir = legacy_resolved.parent
    if not (legacy_project_dir / "manifest.json").is_file():
        raise MigrationError(
            f"{legacy_path} does not live inside a project with a manifest.json — "
            f"refusing an unmanifested legacy input")

    project_dir = Path(project)
    if not project_dir.is_absolute():
        project_dir = (PIPELINE_DIR / project).resolve()
    else:
        project_dir = project_dir.resolve()

    if not project_dir.is_dir():
        raise MigrationError(
            f"target project does not exist: {project_dir} — migration never "
            f"creates a project directory")
    if not project_dir.is_relative_to(PIPELINE_DIR.resolve()):
        raise MigrationError(f"target project {project_dir} is outside the pipeline root")

    manifest_path = project_dir / "manifest.json"
    if not manifest_path.is_file():
        raise MigrationError(
            f"no manifest.json at {manifest_path}; migration requires an existing "
            f"project with a manifest")

    routes_path = project_dir / vr.ROUTES_NAME
    routes_md_path = project_dir / vr.ROUTES_MD_NAME
    if routes_path.exists() or routes_md_path.exists():
        raise MigrationError(
            f"{routes_path.name} or {routes_md_path.name} already exists in "
            f"{project_dir} — migration refuses to overwrite an existing canonical "
            f"artifact")

    def _blank(v) -> bool:
        return v is None or (isinstance(v, str) and not v.strip())

    def _valid_dna_version(v) -> bool:
        """True only for a real, positive integer channel_dna_version. bool
        is rejected even though Python's bool is technically an int subclass
        -- True/False are never a legitimate DNA version. Strings (even
        digit-only ones like "1"), floats, zero and negative numbers are all
        refused too: a version number that needed coercion was never really
        validated in the first place."""
        return isinstance(v, int) and not isinstance(v, bool) and v >= 1

    try:
        src_channel_id, src_dna_version = channel_context.read_manifest_channel(
            legacy_project_dir)
    except channel_context.ChannelError as e:
        raise MigrationError(
            f"cannot read {legacy_project_dir.name}'s manifest channel binding: {e}") from e
    try:
        target_channel_id, target_dna_version = channel_context.read_manifest_channel(
            project_dir)
    except channel_context.ChannelError as e:
        raise MigrationError(
            f"cannot read {project_dir.name}'s manifest channel binding: {e}") from e

    if _blank(src_channel_id) or not _valid_dna_version(src_dna_version):
        raise MigrationError(
            f"{legacy_path} has no channel_id, or no valid (positive integer) "
            f"channel_dna_version, recorded in its manifest (channel_id={src_channel_id!r}, "
            f"channel_dna_version={src_dna_version!r}) — a missing or malformed source "
            f"channel binding is not evidence of a matching channel, refusing before any "
            f"parsing or output creation")
    if _blank(target_channel_id) or not _valid_dna_version(target_dna_version):
        raise MigrationError(
            f"{project_dir.name} has no channel_id, or no valid (positive integer) "
            f"channel_dna_version, recorded in its manifest (channel_id={target_channel_id!r}, "
            f"channel_dna_version={target_dna_version!r}) — cannot verify a channel match "
            f"without a complete target binding")
    if src_channel_id != target_channel_id or src_dna_version != target_dna_version:
        raise MigrationError(
            f"{legacy_path} belongs to channel {src_channel_id!r} v{src_dna_version} but "
            f"{project_dir.name} belongs to {target_channel_id!r} v{target_dna_version} — "
            f"refusing a cross-channel or cross-version migration target")

    try:
        ctx = channel_context.load_channel_for_project(project_dir)
    except channel_context.ChannelError as e:
        raise MigrationError(f"cannot load the governing Channel Pack: {e}") from e

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha256 = _sha_file(manifest_path)

    scenes_by_id = {s.get("id"): s for s in manifest.get("scenes", [])}
    scenes_by_file = {Path(s.get("image", "")).name: s for s in manifest.get("scenes", [])}

    raw_lines: dict[int, str] = {}
    for line in legacy_path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("**SHOT"):
            continue
        m_shot = re.search(r"\*\*SHOT\s+(\d+)\*\*", line)
        if m_shot:
            raw_lines[int(m_shot.group(1))] = line

    shots = route_images.parse_shots(legacy_path, context=ctx)

    renderer_capabilities = dict(ctx.renderer_capabilities)
    routes = []
    for shot in shots:
        scene = scenes_by_id.get(shot["scene_id"]) or scenes_by_file.get(shot["file"])
        prompt, overlay, cue = _extract_prompt_overlay_cue(raw_lines.get(shot["shot_num"], ""))
        route = migrate_legacy_shot(shot, scene, prompt, overlay, cue)
        _resolve_renderer_or_downgrade(route, renderer_capabilities, renderers.RENDERERS)
        routes.append(route)

    doc = {
        "schema_version": vr.SCHEMA_VERSION,
        "project_id": project_dir.name,
        "routes_id": vr.new_routes_id(),
        "generated_at": vr._now(),
        "inputs": {"manifest_sha256": manifest_sha256},
        "channel": ctx.plan_binding(),
        "renderer_registry_sha256": vr.compute_renderer_registry_sha256(
            vr.referenced_renderer_ids(routes), renderers.RENDERERS),
        "routes_content_sha256": "",
        "routes": routes,
    }
    doc["routes_content_sha256"] = vr.compute_routes_content_sha256(doc)

    vr.validate_schema(doc, source=f"migrated {project_dir.name}")

    integrity_problems = vr.validate_contract(
        doc,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        governing_channel_binding=ctx.plan_binding(),
        expected_project_id=project_dir.name,
        renderer_capabilities=renderer_capabilities,
        renderer_registry=renderers.RENDERERS,
    )
    if integrity_problems:
        detail = "\n".join(f"  - {p}" for p in integrity_problems[:20])
        raise MigrationError(
            f"migrated output for {project_dir.name} failed artifact-integrity "
            f"validation — refusing to write it:\n{detail}")

    if not dry_run:
        vr.write_atomic(doc, project_dir)

    return {
        "routes": len(routes),
        "needs_review": sum(1 for r in routes if r["status"] == vr.STATUS_NEEDS_REVIEW),
        "doc": doc,
        "written": not dry_run,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--legacy-md", required=True, metavar="PATH",
                    help="Legacy image_prompts_one_line_per_prompt.md to migrate")
    ap.add_argument("--project", required=True,
                    help="Existing project directory to write visual_routes.json/.md into")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build and schema-validate the artifact but do not write it")
    args = ap.parse_args()

    try:
        result = migrate(args.legacy_md, args.project, dry_run=args.dry_run)
    except vr.VisualRoutesError as e:
        print(f"\nmigration refused: {e}", file=sys.stderr)
        return 1

    print(f"\n  {result['routes']} route(s), {result['needs_review']} needs review")
    if result["written"]:
        print(f"  wrote {vr.ROUTES_NAME} and {vr.ROUTES_MD_NAME}")
    else:
        print("  --dry-run: nothing written")
    for r in result["doc"]["routes"]:
        if r["status"] == vr.STATUS_NEEDS_REVIEW:
            reasons = "; ".join(f"{rr['code']}" for rr in r["review_reasons"])
            print(f"    NEEDS_REVIEW  {r['visual_asset_id']}  ({r['output_file']})  {reasons}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
