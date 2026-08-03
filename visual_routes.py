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

Three hashes, kept separate on purpose:

  - `routes_id`              — a fresh nonce per build. Anti-resurrection,
    the same role `plan_id` already plays in generation_gate today.
  - `routes_content_sha256`  — deterministic hash over meaningful route
    content only (channel/manifest bindings, the renderer-registry
    projection, route order, and every route's executable/review fields).
    Excludes only routes_id, generated_at, and itself. write_atomic()
    refuses to write a document whose stored value has gone stale.
  - the exact SHA-256 of the file's bytes on disk — what a future approval
    step would bind to. Computed the same way generation_gate/
    approve_checkpoint already compute it elsewhere; this module does not
    duplicate that helper, callers hash the file themselves.

**Integrity is not executability.** `validate_contract()` proves an artifact
is internally honest — every hash current, every binding correct, every
manifest identity accounted for exactly once, every READY route's renderer
resolvable and registered. It says nothing about whether the artifact is
*safe to dispatch* — an artifact can be perfectly integral and still contain
NEEDS_REVIEW routes, which are execution blockers by definition. Use
`execution_blockers()` / `is_executable()` for that question; never read
"no integrity problems" as "safe to run."

    python visual_routes.py --validate <project>/visual_routes.json
"""

import argparse
import hashlib
import json
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

import channel_context
from channel_context import canonical_sha256, _write_atomic_text

PIPELINE_DIR = Path(__file__).parent
SCHEMA_PATH = PIPELINE_DIR / "channels" / "schema" / "visual_routes.schema.json"
ROUTES_NAME = "visual_routes.json"
ROUTES_MD_NAME = "visual_routes.md"
SCHEMA_VERSION = 1

CANONICAL_VISUAL_TYPES = (
    "MAP", "CHART", "TIMELINE", "DOCUMENT", "PHOTO", "ILLUSTRATION", "REENACTMENT",
)
# Only these two types may ever carry a host through reference-anchored
# generation. Deterministic renders (MAP/CHART/TIMELINE/DOCUMENT) and
# provider-sourced PHOTO can never be substituted by it.
REFERENCE_ANCHORABLE_TYPES = ("ILLUSTRATION", "REENACTMENT")
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
    otherwise a message naming the mismatch. Also invoked from inside
    validate_contract()'s main path — this standalone form exists for callers
    who only want the registry check in isolation."""
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


# ── output path containment (foundation only — creates nothing) ────────────

def resolve_output_target(images_root, output_file: str) -> Path:
    """Canonically resolve output_file under images_root, or refuse.

    Foundation-only: this never creates a directory or a file. It exists so a
    future dispatcher can ask "where would this actually land, and is that
    safe" before doing anything else. Rejects absolute paths, `..`
    traversal, both `/` and `\\` separators (regardless of host OS — a
    Windows-style traversal must be refused even when this runs on a
    POSIX test box), and resolves symlinks before checking containment so a
    linked directory cannot smuggle the destination outside images_root.
    """
    if not isinstance(output_file, str) or not output_file:
        raise VisualRoutesError("output_file must be a non-empty string")
    if "/" in output_file or "\\" in output_file:
        raise VisualRoutesError(
            f"output_file {output_file!r} contains a path separator — it must be a "
            f"bare, contained filename")
    pw = PureWindowsPath(output_file)
    if pw.is_absolute() or ".." in pw.parts or pw.name in ("", ".", ".."):
        raise VisualRoutesError(
            f"output_file {output_file!r} is not a contained relative filename")

    root = Path(images_root).resolve()
    resolved = (root / output_file).resolve()
    if not resolved.is_relative_to(root):
        raise VisualRoutesError(
            f"output_file {output_file!r} resolves to {resolved}, outside {root}")
    return resolved


# ── atomic write / adapter ──────────────────────────────────────────────────

def _md_escape(s) -> str:
    """Safe for both a markdown table cell and a bullet line: no raw pipes,
    no embedded newlines that would break row/list structure."""
    if s is None:
        return ""
    return str(s).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_routes_md(doc: dict) -> str:
    """Deterministic human-readable adapter. Derives mix/paid/needs-review
    summaries at render time — none of them are stored in the JSON. Exposes
    every field a human reviewer needs to judge a route: typed arguments,
    renderer/cost, host method/assets, prompt, cue, overlay, review reasons."""
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
        f"# Visual routes — {_md_escape(doc.get('project_id', '?'))}",
        "",
        f"- schema_version: {doc.get('schema_version')}",
        f"- routes_id: {doc.get('routes_id')}",
        f"- generated_at: {doc.get('generated_at')}",
        f"- routes_content_sha256: {doc.get('routes_content_sha256')}",
        f"- channel: {_md_escape(doc.get('channel', {}).get('channel_id'))}",
        "",
        "## Mix",
        "",
    ]
    for vt in CANONICAL_VISUAL_TYPES:
        if vt in mix:
            lines.append(f"- {vt}: {mix[vt]}")
    lines += ["", f"paid shots: {paid_shots}", "", f"## Needs review ({len(needs_review)})", ""]
    for r in needs_review:
        reasons = "; ".join(f"{rr['code']}: {_md_escape(rr['detail'])}"
                            for rr in r.get("review_reasons", []))
        lines.append(f"- {r.get('visual_asset_id')} ({_md_escape(r.get('output_file'))}): {reasons}")

    lines += ["", "## Routes", ""]
    lines.append("| visual_asset_id | scene | type | status | file | narration |")
    lines.append("|---|---|---|---|---|---|")
    for r in routes:
        lines.append(f"| {_md_escape(r.get('visual_asset_id'))} | {_md_escape(r.get('scene_id'))} | "
                     f"{r.get('visual_type') or '(none)'} | {r.get('status')} | "
                     f"{_md_escape(r.get('output_file'))} | {_md_escape(r.get('narration'))} |")

    lines += ["", "## Route detail", ""]
    for r in routes:
        args = {k: v for k, v in (r.get("route_args") or {}).items() if v is not None}
        lines.append(f"### {r.get('visual_asset_id')} — {_md_escape(r.get('output_file'))}")
        lines.append(f"- visual_type: {r.get('visual_type')}")
        lines.append(f"- route_args: {_md_escape(json.dumps(args, sort_keys=True))}")
        lines.append(f"- renderer_id: {r.get('renderer_id')}  ·  "
                     f"cost_category: {r.get('cost_category')}  ·  paid: {r.get('paid')}")
        lines.append(f"- host_present: {r.get('host_present')}  ·  host_method: {r.get('host_method')}  ·  "
                     f"host_renderer_id: {r.get('host_renderer_id')}")
        if r.get("host_present"):
            lines.append(f"  - host_pose_id: {r.get('host_pose_id')}  ·  "
                         f"host_scene_bound: {r.get('host_scene_bound')}  ·  "
                         f"host_reference_asset_ids: {r.get('host_reference_asset_ids')}  ·  "
                         f"host_placement: {r.get('host_placement')}")
        lines.append(f"- prompt: {_md_escape(r.get('prompt'))}")
        lines.append(f"- overlay: {_md_escape(r.get('overlay_text'))}  ·  cue: {_md_escape(r.get('visual_cue'))}")
        if r.get("review_reasons"):
            reasons = "; ".join(f"{rr['code']}: {_md_escape(rr['detail'])}" for rr in r["review_reasons"])
            lines.append(f"- review_reasons: {reasons}")
        if r.get("candidate_visual_types"):
            lines.append(f"- candidate_visual_types: {', '.join(r['candidate_visual_types'])}")
        lines.append("")

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
    the on-disk pair is genuinely INCONSISTENT (new adapter, old JSON) — this
    is not claimed to be atomic as a pair, only detectable and recoverable:
    that exact mismatch is what adapter_drift() catches, and the next
    successful call to this function always repairs it by re-staging and
    re-replacing both from scratch.

    Refuses to write a document whose stored routes_content_sha256 has gone
    stale (does not match a fresh recomputation) — a stale content hash is
    never written, not even once.
    """
    validate_schema(doc, source=f"{Path(project_dir).name}/{ROUTES_NAME}")
    fresh_content_hash = compute_routes_content_sha256(doc)
    if doc.get("routes_content_sha256") != fresh_content_hash:
        raise VisualRoutesError(
            f"stale routes_content_sha256: stored {doc.get('routes_content_sha256')} != "
            f"fresh {fresh_content_hash} — refusing to write a document whose recorded "
            f"content hash no longer matches its own content")

    md_text = render_routes_md(doc)
    json_text = json.dumps(doc, indent=2, ensure_ascii=False)

    project_dir = Path(project_dir)
    _write_atomic_text(project_dir / ROUTES_MD_NAME, md_text)
    _write_atomic_text(project_dir / ROUTES_NAME, json_text)


# ── route_args helpers ───────────────────────────────────────────────────────

def empty_route_args() -> dict:
    return {k: None for k in _ROUTE_ARG_KEYS}


# ── manifest coverage (identity-keyed, not scene_id-keyed) ──────────────────

def check_manifest_coverage(routes: list[dict], manifest: dict) -> list[str]:
    """Every manifest scene with a persistent identity must have exactly one
    route, and every route must correspond to exactly one manifest scene.
    Lookup and equality are both keyed on visual_asset_id — the persistent
    identity — never on scene_id, which is display metadata that moves
    whenever narration is re-split."""
    problems: list[str] = []
    scenes = manifest.get("scenes", [])

    manifest_vids = [s.get("visual_asset_id") for s in scenes if s.get("visual_asset_id")]
    seen = set()
    for vid in manifest_vids:
        if vid in seen:
            problems.append(f"manifest identity {vid!r} appears more than once in the manifest")
        seen.add(vid)
    manifest_vid_set = set(manifest_vids)

    if manifest_vid_set and not routes:
        problems.append(
            f"routing document has no routes, but the manifest has "
            f"{len(manifest_vid_set)} scene(s) with a persistent identity")

    route_vids = [r.get("visual_asset_id") for r in routes]
    for vid, n in Counter(route_vids).items():
        if n > 1:
            problems.append(f"duplicate visual_asset_id {vid!r} across {n} routes")
    for sid, n in Counter(r.get("shot_instance_id") for r in routes).items():
        if n > 1:
            problems.append(f"duplicate shot_instance_id {sid!r} across {n} routes")
    for f, n in Counter(r.get("output_file") for r in routes).items():
        if n > 1:
            problems.append(f"duplicate output_file {f!r} across {n} routes")

    for vid in sorted(manifest_vid_set - set(route_vids)):
        problems.append(f"manifest identity {vid!r} has no corresponding route")

    # "Extra route" is only meaningful for a route that CLAIMS to be
    # dispatchable (READY) but names an identity the manifest doesn't have.
    # A NEEDS_REVIEW route carrying a synthetic placeholder id (because its
    # own identity genuinely could not be resolved — see
    # MANIFEST_IDENTITY_UNRESOLVED) already says so honestly; re-flagging it
    # here as a second, differently-worded problem would make it impossible
    # to ever write an artifact that surfaces that exact ambiguity for
    # review, which is the whole reason NEEDS_REVIEW exists.
    ready_vids = {r.get("visual_asset_id") for r in routes if r.get("status") == "READY"}
    for vid in sorted(v for v in (ready_vids - manifest_vid_set) if v):
        problems.append(f"route {vid!r} does not correspond to any manifest identity")

    return problems


# ── approved-asset resolution (real registry vocabulary, real I/O) ─────────

def _verify_approved_asset(asset_id, record, *, asset_base, root, kind: str,
                           require_scene_bound: bool | None) -> list[str]:
    """Mirrors pose_registry.resolve()'s exact contract: status must be
    approved or approved_scene_bound, the registered path must be relative,
    non-traversing and free of NON_RENDERABLE_DIRS components, must resolve
    (after following any symlink) inside `root`, must exist, and its live
    SHA-256 must match the registered one. A missing expected hash is a
    failure in its own right — never silently skipped."""
    problems = []
    if record is None:
        problems.append(f"{kind} {asset_id!r} is not a registered asset in the "
                        f"governing channel")
        return problems
    status = record.get("status")
    if status not in ("approved", "approved_scene_bound"):
        problems.append(f"{kind} {asset_id!r} has status {status!r}, not approved")
        return problems
    if status == "approved_scene_bound" and not require_scene_bound:
        problems.append(
            f"{kind} {asset_id!r} is a scene-bound tableau — the route must pass "
            f"scene_bound=true to use it")
        return problems

    raw = record.get("path")
    if not raw or not isinstance(raw, str):
        problems.append(f"{kind} {asset_id!r} has no registered path")
        return problems
    p = Path(raw)
    if p.is_absolute() or ".." in p.parts:
        problems.append(f"{kind} {asset_id!r} registered path {raw!r} is absolute or "
                        f"traverses upward")
        return problems
    forbidden = [part for part in p.parts if part in channel_context.NON_RENDERABLE_DIRS]
    if forbidden:
        problems.append(f"{kind} {asset_id!r} registered path {raw!r} passes through "
                        f"{forbidden} — not approved output")
        return problems
    if asset_base is None or root is None:
        problems.append(f"{kind} {asset_id!r}: no asset base/containment root supplied "
                        f"to verify against")
        return problems

    resolved = (Path(asset_base) / p).resolve()
    root_resolved = Path(root).resolve()
    if not resolved.is_relative_to(root_resolved):
        problems.append(f"{kind} {asset_id!r} resolved path escapes {root_resolved} "
                        f"(resolved to {resolved})")
        return problems
    if not resolved.exists():
        problems.append(f"{kind} {asset_id!r}: registered asset missing at {resolved}")
        return problems

    expected_hash = record.get("sha256")
    if not expected_hash:
        problems.append(f"{kind} {asset_id!r}: registry record has no sha256 evidence "
                        f"to verify against")
        return problems
    live_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if live_hash != expected_hash:
        problems.append(
            f"{kind} {asset_id!r}: hash mismatch — the file has changed since approval "
            f"(expected {expected_hash[:12]}\u2026, found {live_hash[:12]}\u2026)")
    return problems


# ── pure contract validator (library only — not wired into any dispatcher) ──

def validate_contract(
    doc: dict,
    *,
    manifest: dict,
    manifest_sha256: str,
    governing_channel_binding: dict,
    renderer_capabilities: dict,
    renderer_registry: dict,
    approved_poses: dict | None = None,
    poses_asset_base=None,
    poses_root=None,
    approved_references: dict | None = None,
    references_asset_base=None,
    references_root=None,
) -> list[str]:
    """Every ARTIFACT-INTEGRITY check a future dispatcher/gate would need,
    run here as a pure function against explicit, caller-supplied inputs.
    Returns a list of problem strings (empty = the artifact is internally
    honest against the supplied governing state).

    This answers "is the artifact telling the truth about itself and the
    governing channel" — NOT "is it safe to dispatch." A fully integral
    artifact may still contain NEEDS_REVIEW routes; see execution_blockers().

    Hash/binding checks run in this main path, not only via the standalone
    check_renderer_registry_drift() helper — a stale manifest hash, a stale
    routes_content_sha256, a renderer registry drift, or a channel-binding
    mismatch are all caught here directly.
    """
    problems: list[str] = []
    approved_poses = approved_poses or {}
    approved_references = approved_references or {}

    # ── top-level hashes and bindings ────────────────────────────────────
    if doc.get("inputs", {}).get("manifest_sha256") != manifest_sha256:
        problems.append(
            f"inputs.manifest_sha256 {doc.get('inputs', {}).get('manifest_sha256')!r} != "
            f"the current manifest hash {manifest_sha256!r} — the manifest changed since "
            f"routes were built")

    fresh_content_hash = compute_routes_content_sha256(doc)
    if doc.get("routes_content_sha256") != fresh_content_hash:
        problems.append(
            f"routes_content_sha256 {doc.get('routes_content_sha256')!r} != a fresh "
            f"recomputation {fresh_content_hash!r} — the stored content hash is stale")

    registry_drift = check_renderer_registry_drift(doc, renderer_registry)
    if registry_drift:
        problems.append(registry_drift)

    if doc.get("channel") != governing_channel_binding:
        problems.append(
            f"channel binding {doc.get('channel')} does not match the governing "
            f"Channel Pack {governing_channel_binding}")

    routes = doc.get("routes", [])

    # ── manifest coverage, keyed by persistent identity ─────────────────
    problems.extend(check_manifest_coverage(routes, manifest))
    scenes_by_vid = {s.get("visual_asset_id"): s for s in manifest.get("scenes", [])
                     if s.get("visual_asset_id")}

    for route in routes:
        rid = route.get("visual_asset_id", "<unknown>")

        scene = scenes_by_vid.get(route.get("visual_asset_id"))
        if scene is not None:
            manifest_output = Path(scene.get("image", "")).name
            if scene.get("source_ids") != route.get("source_ids"):
                problems.append(
                    f"{rid}: source_ids {route.get('source_ids')!r} != "
                    f"manifest {scene.get('source_ids')!r}")
            if scene.get("shot_instance_id") != route.get("shot_instance_id"):
                problems.append(
                    f"{rid}: shot_instance_id {route.get('shot_instance_id')!r} != "
                    f"manifest {scene.get('shot_instance_id')!r}")
            if scene.get("id") != route.get("scene_id"):
                problems.append(
                    f"{rid}: scene_id {route.get('scene_id')!r} != "
                    f"manifest {scene.get('id')!r} (display metadata mismatch)")
            if manifest_output != route.get("output_file"):
                problems.append(
                    f"{rid}: output_file {route.get('output_file')!r} != "
                    f"manifest-derived {manifest_output!r}")
        # A route with no matching manifest scene is already reported by
        # check_manifest_coverage() above — not duplicated here.

        vt = route.get("visual_type")
        renderer_id = route.get("renderer_id")
        renderer_entry = renderer_registry.get(renderer_id) if renderer_id else None

        if route.get("status") == STATUS_READY and vt is not None:
            expected_renderer = renderer_capabilities.get(vt)
            if expected_renderer is None:
                # A missing capability is an error even when renderer_id is
                # null — a READY route naming a type the pack cannot render
                # at all is not "unresolved," it is wrong.
                problems.append(
                    f"{rid}: the governing Channel Pack declares no renderer "
                    f"capability for {vt}, but the route is READY")
            elif expected_renderer != renderer_id:
                problems.append(
                    f"{rid}: renderer_id {renderer_id!r} != current capability "
                    f"{expected_renderer!r} for {vt}")

            if renderer_id is None:
                problems.append(f"{rid}: READY {vt} route has no renderer_id")
            elif renderer_entry is None:
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
            if expected_host_renderer is None:
                problems.append(
                    f"{rid}: the governing Channel Pack declares no HOST_COMPOSITE "
                    f"capability")
            elif expected_host_renderer != host_renderer_id:
                problems.append(
                    f"{rid}: host_renderer_id {host_renderer_id!r} != current "
                    f"HOST_COMPOSITE capability {expected_host_renderer!r}")
            if host_renderer_id is not None:
                hentry = renderer_registry.get(host_renderer_id)
                if hentry is None or not hentry.get("implemented"):
                    problems.append(f"{rid}: HOST_COMPOSITE renderer "
                                    f"{host_renderer_id!r} is unavailable")

            problems.extend(_verify_approved_asset(
                route.get("host_pose_id"), approved_poses.get(route.get("host_pose_id")),
                asset_base=poses_asset_base, root=poses_root, kind="pose",
                require_scene_bound=bool(route.get("host_scene_bound"))))

        elif route.get("host_present") and host_method == "reference_anchored_generation":
            if route.get("host_renderer_id") is not None:
                problems.append(
                    f"{rid}: reference_anchored_generation must not have a "
                    f"host_renderer_id — the base renderer performs the anchored "
                    f"generation directly")
            if vt not in REFERENCE_ANCHORABLE_TYPES:
                problems.append(
                    f"{rid}: reference_anchored_generation is only permitted for "
                    f"{'/'.join(REFERENCE_ANCHORABLE_TYPES)} — refused for visual_type "
                    f"{vt!r}, which must never be substituted by anchored generation")
            if renderer_id is not None and renderer_entry is not None:
                if not renderer_entry.get("supports_reference_input"):
                    problems.append(
                        f"{rid}: renderer {renderer_id!r} does not declare "
                        f"supports_reference_input; reference_anchored_generation is "
                        f"not permitted through it")
            elif renderer_id is not None and renderer_entry is None:
                problems.append(
                    f"{rid}: reference_anchored_generation requires a resolvable "
                    f"renderer declaring supports_reference_input")

            for ref_id in (route.get("host_reference_asset_ids") or []):
                problems.extend(_verify_approved_asset(
                    ref_id, approved_references.get(ref_id),
                    asset_base=references_asset_base, root=references_root,
                    kind="reference", require_scene_bound=True))

    return problems


def execution_blockers(doc: dict) -> list[str]:
    """Every route that is not execution-ready, independent of whether the
    artifact is otherwise fully integral. A NEEDS_REVIEW route always blocks
    — that is what the status means. Non-empty means "do not dispatch"."""
    return [f"{r.get('visual_asset_id')}: status {r.get('status')!r} blocks execution "
            f"— NEEDS_REVIEW routes are never dispatchable"
            for r in doc.get("routes", []) if r.get("status") != STATUS_READY]


def is_executable(doc: dict) -> bool:
    return not execution_blockers(doc)


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
