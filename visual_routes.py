"""visual_routes.py — the canonical `visual_routes.json` contract (Task 2B-B1).

This is contract-foundation code only: a schema, a data model, a pure
validator, hashing/atomicity helpers, and a deterministic adapter renderer.
Nothing in this module is wired into the live pipeline yet — no producer
writes this artifact, no dispatcher reads it, no gate hashes it. That cutover
is a separately reviewed phase.

Three artifacts this module knows about, kept deliberately distinct:

  - `visual_routes.json`   — the canonical, schema-validated routing artifact.
    Carries no independently mutable summary (mix, paid totals, needs-review
    counts): those are always derived from `routes` at render time, never
    stored, so they cannot drift out of sync with the data they summarise.
  - `visual_routes.md`     — a generated, human-readable adapter, produced
    solely from the JSON. A future dispatcher must never read it.
  - the renderer-registry projection — a canonical hash over every
    execution-affecting field (module, entry, cost_category, implemented,
    supports_reference_input) of every renderer a route references, so a
    code-only change to renderers.py is detectable even when the governing
    Channel Pack's own hash never moves.

Three hashes, kept separate on purpose (see routes_id / routes_content_sha256
/ the exact file-byte hash a future approval step would bind to):

  - `routes_id`              — a fresh nonce per build. Anti-resurrection,
    the same role `plan_id` already plays in generation_gate today.
  - `routes_content_sha256`  — deterministic hash over meaningful route
    content only (channel/manifest bindings, the renderer-registry
    projection, route order, and every route's executable/review fields).
    Excludes only routes_id, generated_at, and itself.
  - the exact SHA-256 of the file's bytes on disk — what a future approval
    step would bind to. Computed the same way generation_gate/
    approve_checkpoint already compute it elsewhere; this module does not
    duplicate that helper, callers hash the file themselves.

    python visual_routes.py --validate <project>/visual_routes.json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from channel_context import canonical_sha256, _write_atomic_text

PIPELINE_DIR = Path(__file__).parent
SCHEMA_PATH = PIPELINE_DIR / "channels" / "schema" / "visual_routes.schema.json"
ROUTES_NAME = "visual_routes.json"
ROUTES_MD_NAME = "visual_routes.md"
SCHEMA_VERSION = 1

CANONICAL_VISUAL_TYPES = (
    "MAP", "CHART", "TIMELINE", "DOCUMENT", "PHOTO", "ILLUSTRATION", "REENACTMENT",
)
HOST_METHODS = ("approved_pose_composite", "reference_anchored_generation")
COST_CATEGORIES = ("free_local", "free_api", "paid_api", "derived")
STATUS_READY = "READY"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"

REVIEW_REASON_CODES = (
    "AMBIGUOUS_LEGACY_CARTOON",
    "AMBIGUOUS_LEGACY_HOST",
    "LEGACY_UNCLASSIFIED",
    "INCOMPLETE_ROUTE_ARGS",
    "LOW_CONFIDENCE",
    "MANUAL_OVERRIDE",
    "RENDERER_UNAVAILABLE",
    "MANIFEST_IDENTITY_UNRESOLVED",
    "OTHER",
)

_ROUTE_ARG_KEYS = (
    "map", "chart", "timeline", "document", "photo", "illustration", "reenactment",
)


class VisualRoutesError(RuntimeError):
    """The artifact, or an operation on it, could not be honoured."""


# ── schema ───────────────────────────────────────────────────────────────────

def _validator():
    """Build the validator, checking the schema itself first.

    A malformed schema silently accepts everything, which is worse than no
    schema at all: it reports success. check_schema() turns that into a loud
    failure before any document is looked at.
    """
    try:
        from jsonschema import Draft202012Validator
    except ImportError as e:  # pragma: no cover - environment problem, not logic
        raise VisualRoutesError(
            "jsonschema is required to validate visual_routes.json "
            "(pip install -r requirements.txt)") from e
    if not SCHEMA_PATH.is_file():
        raise VisualRoutesError(f"schema not found at {SCHEMA_PATH}")
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise VisualRoutesError(f"schema at {SCHEMA_PATH} is not valid JSON: {e}") from e
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as e:
        raise VisualRoutesError(f"schema at {SCHEMA_PATH} is itself invalid: {e}") from e
    return Draft202012Validator(schema)


def schema_errors(doc: dict) -> list[str]:
    """Every schema violation, as human-readable strings. Empty means valid."""
    if not isinstance(doc, dict):
        return ["document must be a JSON object"]
    if doc.get("schema_version") != SCHEMA_VERSION:
        return [f"schema_version {doc.get('schema_version')!r} is not supported "
                f"by this build (expected {SCHEMA_VERSION})"]
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in errors]


def validate_schema(doc: dict, *, source: str) -> None:
    errors = schema_errors(doc)
    if errors:
        detail = "\n".join(f"  - {e}" for e in errors[:20])
        raise VisualRoutesError(f"{source}: does not satisfy the schema:\n{detail}")


# ── hashing ──────────────────────────────────────────────────────────────────

def renderer_registry_projection(renderer_ids, registry: dict) -> dict:
    """The execution-affecting subset of every referenced renderer's entry.

    module and entry are included specifically so a code change that
    redirects a renderer id to a different module/function is detected even
    though it changes neither cost_category nor implemented. Only
    descriptive fields (e.g. `note`) are excluded.
    """
    proj = {}
    for rid in sorted(set(renderer_ids)):
        entry = registry.get(rid)
        if entry is None:
            raise VisualRoutesError(f"unregistered renderer {rid!r}; "
                                    f"registered: {sorted(registry)}")
        proj[rid] = {
            "module": entry["module"],
            "entry": entry["entry"],
            "cost_category": entry["cost_category"],
            "implemented": bool(entry["implemented"]),
            "supports_reference_input": bool(entry.get("supports_reference_input", False)),
        }
    return proj


def compute_renderer_registry_sha256(renderer_ids, registry: dict) -> str:
    return canonical_sha256(renderer_registry_projection(renderer_ids, registry))


def referenced_renderer_ids(routes: list[dict]) -> set[str]:
    ids = set()
    for r in routes:
        if r.get("renderer_id"):
            ids.add(r["renderer_id"])
        if r.get("host_renderer_id"):
            ids.add(r["host_renderer_id"])
    return ids


def compute_routes_content_sha256(doc: dict) -> str:
    """Deterministic hash over meaningful content only.

    Excludes exactly routes_id, generated_at, and this field itself — nothing
    else. Route order matters (it is part of the projection, not sorted away),
    formatting and key order do not (canonical_sha256 sorts keys and uses a
    compact separator).
    """
    projection = {k: v for k, v in doc.items()
                  if k not in ("routes_id", "generated_at", "routes_content_sha256")}
    return canonical_sha256(projection)


def check_renderer_registry_drift(doc: dict, registry: dict) -> str | None:
    """None if the stored renderer_registry_sha256 still matches the live
    registry's projection for every renderer this artifact references;
    otherwise a message naming the mismatch."""
    ids = referenced_renderer_ids(doc.get("routes", []))
    try:
        live = compute_renderer_registry_sha256(ids, registry)
    except VisualRoutesError as e:
        return str(e)
    stored = doc.get("renderer_registry_sha256")
    if live != stored:
        return (f"renderer registry drift: stored {stored} != live {live} for "
                f"referenced renderers {sorted(ids)}")
    return None


# ── atomic write / adapter ──────────────────────────────────────────────────

def render_routes_md(doc: dict) -> str:
    """Deterministic human-readable adapter. Derives mix/paid/needs-review
    summaries at render time — none of them are stored in the JSON."""
    routes = doc.get("routes", [])
    mix: dict[str, int] = {}
    needs_review = []
    paid_shots = 0
    for r in routes:
        vt = r.get("visual_type")
        if vt:
            mix[vt] = mix.get(vt, 0) + 1
        if r.get("status") == STATUS_NEEDS_REVIEW:
            needs_review.append(r)
        if r.get("paid"):
            paid_shots += 1

    lines = [
        f"# Visual routes — {doc.get('project_id', '?')}",
        "",
        f"- schema_version: {doc.get('schema_version')}",
        f"- routes_id: {doc.get('routes_id')}",
        f"- generated_at: {doc.get('generated_at')}",
        f"- routes_content_sha256: {doc.get('routes_content_sha256')}",
        f"- channel: {doc.get('channel', {}).get('channel_id')}",
        "",
        "## Mix",
        "",
    ]
    for vt in CANONICAL_VISUAL_TYPES:
        if vt in mix:
            lines.append(f"- {vt}: {mix[vt]}")
    lines += ["", f"paid shots: {paid_shots}", "", f"## Needs review ({len(needs_review)})", ""]
    for r in needs_review:
        reasons = "; ".join(f"{rr['code']}: {rr['detail']}" for rr in r.get("review_reasons", []))
        lines.append(f"- {r.get('visual_asset_id')} ({r.get('output_file')}): {reasons}")
    lines += ["", "## Routes", ""]
    lines.append("| visual_asset_id | scene | type | status | file | narration |")
    lines.append("|---|---|---|---|---|---|")
    for r in routes:
        lines.append(f"| {r.get('visual_asset_id')} | {r.get('scene_id')} | "
                     f"{r.get('visual_type') or '(none)'} | {r.get('status')} | "
                     f"{r.get('output_file')} | {(r.get('narration') or '').replace(chr(10), ' ')} |")
    return "\n".join(lines) + "\n"


def adapter_drift(project_dir) -> str | None:
    """None if visual_routes.md on disk matches a fresh render of
    visual_routes.json on disk; otherwise a message describing the mismatch.
    Distinct from "routing artifact changed since approval" — this only ever
    compares the pair to each other, never to a stored/approved hash."""
    project_dir = Path(project_dir)
    json_path = project_dir / ROUTES_NAME
    md_path = project_dir / ROUTES_MD_NAME
    if not json_path.is_file():
        return f"{ROUTES_NAME} is missing at {project_dir}"
    if not md_path.is_file():
        return f"{ROUTES_MD_NAME} is missing at {project_dir}"
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    fresh = render_routes_md(doc)
    on_disk = md_path.read_text(encoding="utf-8")
    if on_disk != fresh:
        return (f"{ROUTES_MD_NAME} does not match a fresh render of {ROUTES_NAME} — "
                f"it is generated, not maintained, and does not match live content")
    return None


def write_atomic(doc: dict, project_dir) -> None:
    """Stage and validate both files, then commit adapter first, JSON last.

    Both temp files are fully validated before either live file is touched.
    The adapter is replaced first; the canonical JSON is the commit point and
    is replaced last. If the process crashes between the two replacements,
    the on-disk pair is genuinely inconsistent (new adapter, old JSON) — not
    a disguised-fine prior state. That mismatch is exactly what
    adapter_drift() detects, and the next successful build (a fresh call to
    this function) always repairs it by re-staging and re-replacing both.
    """
    validate_schema(doc, source=f"{Path(project_dir).name}/{ROUTES_NAME}")
    md_text = render_routes_md(doc)
    json_text = json.dumps(doc, indent=2, ensure_ascii=False)

    project_dir = Path(project_dir)
    _write_atomic_text(project_dir / ROUTES_MD_NAME, md_text)
    _write_atomic_text(project_dir / ROUTES_NAME, json_text)


# ── route_args helpers ───────────────────────────────────────────────────────

def empty_route_args() -> dict:
    return {k: None for k in _ROUTE_ARG_KEYS}


# ── pure contract validator (library only — not wired into any dispatcher) ──

def validate_contract(
    doc: dict,
    *,
    manifest: dict,
    governing_channel_binding: dict,
    renderer_capabilities: dict,
    renderer_registry: dict,
    approved_poses: dict | None = None,
    approved_references: dict | None = None,
    live_pose_sha256: dict | None = None,
    live_reference_sha256: dict | None = None,
) -> list[str]:
    """Every check a future dispatcher/gate would need, run here as a pure
    function against explicit, caller-supplied inputs. Returns a list of
    problem strings (empty = the artifact is fully honourable against the
    supplied governing state). Never touches disk, never imports a live
    channel or registry itself — the caller decides what "current" means.
    """
    problems: list[str] = []
    approved_poses = approved_poses or {}
    approved_references = approved_references or {}
    live_pose_sha256 = live_pose_sha256 or {}
    live_reference_sha256 = live_reference_sha256 or {}

    if doc.get("channel") != governing_channel_binding:
        problems.append(
            f"channel binding {doc.get('channel')} does not match the governing "
            f"Channel Pack {governing_channel_binding}")

    scenes_by_id = {s.get("id"): s for s in manifest.get("scenes", [])}

    for route in doc.get("routes", []):
        rid = route.get("visual_asset_id", "<unknown>")

        scene = scenes_by_id.get(route.get("scene_id"))
        if scene is None:
            problems.append(f"{rid}: scene_id {route.get('scene_id')!r} not found in manifest")
        else:
            manifest_output = Path(scene.get("image", "")).name
            if scene.get("visual_asset_id") != route.get("visual_asset_id"):
                problems.append(
                    f"{rid}: visual_asset_id {route.get('visual_asset_id')!r} != "
                    f"manifest {scene.get('visual_asset_id')!r}")
            if scene.get("source_ids") != route.get("source_ids"):
                problems.append(
                    f"{rid}: source_ids {route.get('source_ids')!r} != "
                    f"manifest {scene.get('source_ids')!r}")
            if scene.get("shot_instance_id") != route.get("shot_instance_id"):
                problems.append(
                    f"{rid}: shot_instance_id {route.get('shot_instance_id')!r} != "
                    f"manifest {scene.get('shot_instance_id')!r}")
            if manifest_output != route.get("output_file"):
                problems.append(
                    f"{rid}: output_file {route.get('output_file')!r} != "
                    f"manifest-derived {manifest_output!r}")

        vt = route.get("visual_type")
        renderer_id = route.get("renderer_id")
        renderer_entry = None
        if vt is not None:
            expected_renderer = renderer_capabilities.get(vt)
            if expected_renderer != renderer_id:
                problems.append(
                    f"{rid}: renderer_id {renderer_id!r} != current capability "
                    f"{expected_renderer!r} for {vt}")
            if renderer_id is not None:
                renderer_entry = renderer_registry.get(renderer_id)
                if renderer_entry is None:
                    problems.append(f"{rid}: renderer {renderer_id!r} is not in the "
                                    f"code-owned registry")
                else:
                    if not renderer_entry.get("implemented"):
                        problems.append(f"{rid}: renderer {renderer_id!r} is not implemented")
                    if renderer_entry.get("cost_category") != route.get("cost_category"):
                        problems.append(
                            f"{rid}: cost_category {route.get('cost_category')!r} != "
                            f"registry {renderer_entry.get('cost_category')!r} for "
                            f"{renderer_id!r}")
                    expected_paid = renderer_entry.get("cost_category") == "paid_api"
                    if bool(route.get("paid")) != expected_paid:
                        problems.append(
                            f"{rid}: paid={route.get('paid')!r} disagrees with cost_category "
                            f"{renderer_entry.get('cost_category')!r}")

        host_method = route.get("host_method")
        if route.get("host_present") and host_method == "approved_pose_composite":
            expected_host_renderer = renderer_capabilities.get("HOST_COMPOSITE")
            host_renderer_id = route.get("host_renderer_id")
            if expected_host_renderer != host_renderer_id:
                problems.append(
                    f"{rid}: host_renderer_id {host_renderer_id!r} != current "
                    f"HOST_COMPOSITE capability {expected_host_renderer!r}")
            if host_renderer_id is not None:
                hentry = renderer_registry.get(host_renderer_id)
                if hentry is None or not hentry.get("implemented"):
                    problems.append(f"{rid}: HOST_COMPOSITE renderer "
                                    f"{host_renderer_id!r} is unavailable")
            pose_id = route.get("host_pose_id")
            record = approved_poses.get(pose_id)
            if record is None:
                problems.append(f"{rid}: pose {pose_id!r} is not an approved active pose")
            else:
                if record.get("state") != "active":
                    problems.append(
                        f"{rid}: pose {pose_id!r} is {record.get('state')!r}, not active")
                if record.get("channel_id") != governing_channel_binding.get("channel_id"):
                    problems.append(
                        f"{rid}: pose {pose_id!r} belongs to {record.get('channel_id')!r}, "
                        f"not {governing_channel_binding.get('channel_id')!r}")
                if pose_id in live_pose_sha256 and live_pose_sha256[pose_id] != record.get("sha256"):
                    problems.append(f"{rid}: pose {pose_id!r} hash mismatch")

        elif route.get("host_present") and host_method == "reference_anchored_generation":
            if vt in ("MAP", "CHART", "PHOTO", "DOCUMENT"):
                problems.append(
                    f"{rid}: reference_anchored_generation is not permitted for "
                    f"deterministic visual_type {vt} — it must never substitute for "
                    f"deterministic MAP/CHART/PHOTO/DOCUMENT rendering")
            if renderer_entry is not None and not renderer_entry.get("supports_reference_input"):
                problems.append(
                    f"{rid}: renderer {renderer_id!r} does not declare "
                    f"supports_reference_input; reference_anchored_generation is not "
                    f"permitted through it")
            elif renderer_id is not None and renderer_entry is None:
                problems.append(
                    f"{rid}: reference_anchored_generation requires a resolvable "
                    f"renderer declaring supports_reference_input")
            for ref_id in (route.get("host_reference_asset_ids") or []):
                record = approved_references.get(ref_id)
                if record is None:
                    problems.append(
                        f"{rid}: reference {ref_id!r} is not an approved active reference")
                else:
                    if record.get("state") != "active":
                        problems.append(
                            f"{rid}: reference {ref_id!r} is {record.get('state')!r}, "
                            f"not active")
                    if record.get("channel_id") != governing_channel_binding.get("channel_id"):
                        problems.append(
                            f"{rid}: reference {ref_id!r} belongs to "
                            f"{record.get('channel_id')!r}, not "
                            f"{governing_channel_binding.get('channel_id')!r}")
                    if (ref_id in live_reference_sha256
                            and live_reference_sha256[ref_id] != record.get("sha256")):
                        problems.append(f"{rid}: reference {ref_id!r} hash mismatch")

    return problems


# ── provenance sidecar (structural validation only — no writer, no I/O) ─────

_PROVENANCE_REQUIRED = (
    "visual_asset_id", "routes_id", "routes_file_sha256", "channel_id",
    "output_asset_sha256", "source_url", "provider_asset_id", "creator",
    "license", "retrieved_at",
)


def validate_provenance_sidecar(data: dict) -> list[str]:
    """Structural validation only, for the future PHOTO provenance sidecar
    contract. No writer exists in this module — no dispatcher writes this
    file yet, and none should until the cutover phase defines crash-recovery
    semantics for a missing/invalid sidecar (it must never be treated as
    'already generated' or skipped on a later run)."""
    if not isinstance(data, dict):
        return ["provenance sidecar must be a JSON object"]
    problems = []
    for key in _PROVENANCE_REQUIRED:
        if key not in data:
            problems.append(f"missing required field {key!r}")
        elif not isinstance(data[key], str) or not data[key]:
            problems.append(f"{key!r} must be a non-empty string")
    return problems


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_routes_id() -> str:
    return uuid.uuid4().hex


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--validate", metavar="PATH", help="Path to a visual_routes.json to "
                    "schema-validate")
    args = ap.parse_args()
    if args.validate:
        try:
            doc = json.loads(Path(args.validate).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot read {args.validate}: {e}", file=sys.stderr)
            return 1
        errors = schema_errors(doc)
        if errors:
            print(f"INVALID — {len(errors)} problem(s):")
            for e in errors:
                print(f"  - {e}")
            return 1
        print("valid")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
