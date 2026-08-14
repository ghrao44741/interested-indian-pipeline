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
import re
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from types import MappingProxyType

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

    Corrective follow-up to Task 2B-B2a: this used to independently
    reconstruct the same field list `renderers.projection_for_hash()` also
    built — two implementations of one fact, free to drift apart. There is
    now exactly one implementation, `renderers.projection_for_hash()`; this
    function is a thin delegator that exists only to keep this module's own
    public name and `VisualRoutesError`-raising contract stable for every
    existing caller (this module never raises `renderers.RendererError`
    directly — that would be a second exception type this module's own
    callers were never contracted to catch).

    `renderers` is imported locally, not at module scope: `renderers.py`
    imports `renderer_adapters.py`, which (for its universal-refusal guard)
    imports `generation_gate.py`, which imports `channel_context.py` — and
    `channel_context.py` itself imports `renderers.py`. A module-level
    `import renderers` here would risk the same import-cycle class Task
    2B-B2a already had to route around once; a local import inside this
    function has no such risk, since by the time any caller actually
    invokes this function, all of those modules have already finished
    importing.
    """
    import renderers
    try:
        return renderers.projection_for_hash(renderer_ids, registry)
    except renderers.RendererError as e:
        raise VisualRoutesError(str(e)) from e


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

def _is_placeholder_identity(value) -> bool:
    """True for missing/null/blank/synthesized identities — never a real,
    persistent identity a route or manifest scene can be honestly matched on.
    A route or scene carrying one of these is excluded from the coverage
    comparison entirely and reported directly — even if the exact same
    placeholder string happens to appear on both sides, since coincidence of
    a synthetic sentinel is not identity.

    Surrounding whitespace is stripped before either test: a padded sentinel
    (" UNRESOLVED-x", "UNRESOLVED-x\\t") is exactly as synthetic as the
    unpadded form, and must not sneak past the prefix check by carrying
    leading/trailing whitespace."""
    if not isinstance(value, str):
        return True
    stripped = value.strip()
    if not stripped:
        return True
    if stripped.startswith("UNRESOLVED-"):
        return True
    return False


def check_manifest_coverage(routes: list[dict], manifest: dict) -> list[str]:
    """Every manifest scene with a persistent identity must have exactly one
    route, and every route must correspond to exactly one manifest scene —
    regardless of route status. Lookup and equality are both keyed on
    visual_asset_id — the persistent identity — never on scene_id, which is
    display metadata that moves whenever narration is re-split.

    Exact coverage does not depend on route status: a NEEDS_REVIEW route
    carrying a synthesized placeholder identity is not exempt — it is
    rejected directly by the placeholder check below, and (independently)
    can never satisfy manifest coverage even if a manifest scene happens to
    carry an identically malformed identity. Duplicates are counted via
    Counter, not detected by comparing set sizes, so they cannot be hidden
    by set collapse."""
    problems: list[str] = []
    scenes = manifest.get("scenes", [])

    manifest_vid_counts: Counter = Counter()
    manifest_sid_counts: Counter = Counter()
    for s in scenes:
        label = s.get("id") or s.get("image") or "<unnamed manifest scene>"
        vid = s.get("visual_asset_id")
        sid = s.get("shot_instance_id")
        if _is_placeholder_identity(vid):
            problems.append(f"manifest scene {label!r} has a missing/empty/placeholder "
                            f"visual_asset_id ({vid!r})")
        else:
            manifest_vid_counts[vid] += 1
        if _is_placeholder_identity(sid):
            problems.append(f"manifest scene {label!r} has a missing/empty/placeholder "
                            f"shot_instance_id ({sid!r})")
        else:
            manifest_sid_counts[sid] += 1

    for vid, n in manifest_vid_counts.items():
        if n > 1:
            problems.append(f"manifest identity {vid!r} appears more than once in the manifest")
    for sid, n in manifest_sid_counts.items():
        if n > 1:
            problems.append(f"manifest shot_instance_id {sid!r} appears more than once in "
                            f"the manifest")
    manifest_vid_set = set(manifest_vid_counts)

    if manifest_vid_set and not routes:
        problems.append(
            f"routing document has no routes, but the manifest has "
            f"{len(manifest_vid_set)} scene(s) with a persistent identity")

    route_vid_counts: Counter = Counter()
    valid_route_vids: set = set()
    for r in routes:
        vid = r.get("visual_asset_id")
        if _is_placeholder_identity(vid):
            problems.append(
                f"route {r.get('output_file')!r} has a missing/empty/placeholder "
                f"visual_asset_id ({vid!r}) — a synthesized identity can never satisfy "
                f"exact manifest coverage, even if the manifest happens to carry the same "
                f"placeholder string")
        else:
            route_vid_counts[vid] += 1
            valid_route_vids.add(vid)

    for vid, n in route_vid_counts.items():
        if n > 1:
            problems.append(f"duplicate visual_asset_id {vid!r} across {n} routes")
    for sid, n in Counter(r.get("shot_instance_id") for r in routes).items():
        if n > 1:
            problems.append(f"duplicate shot_instance_id {sid!r} across {n} routes")
    for f, n in Counter(r.get("output_file") for r in routes).items():
        if n > 1:
            problems.append(f"duplicate output_file {f!r} across {n} routes")

    for vid in sorted(manifest_vid_set - valid_route_vids):
        problems.append(f"manifest identity {vid!r} has no corresponding route")

    # Unlike the previous round, this is no longer scoped to status=="READY":
    # exact coverage means every route — READY or NEEDS_REVIEW — must
    # correspond to a real manifest identity. A route whose own identity
    # could not be resolved is caught above by the placeholder check, not
    # exempted here.
    for vid in sorted(valid_route_vids - manifest_vid_set):
        problems.append(f"route {vid!r} does not correspond to any manifest identity")

    return problems


# ── approved-asset resolution (real registry vocabulary, real I/O) ─────────

_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _verify_asset_path_and_hash(asset_id, record, *, asset_base, root, kind: str) -> list[str]:
    """Path/existence/hash verification shared by pose and reference
    resolution. Assumes the caller already checked status (and, for poses,
    the scene-bound metadata contradictions) -- this only ever verifies that
    the registered path is safe and that the bytes on disk are the approved
    ones. The registered path must be relative, non-traversing and free of
    NON_RENDERABLE_DIRS components, must resolve (after following any
    symlink) inside `root`, must exist, and must be a regular file -- a
    directory, FIFO, socket or other non-file object at that path is a
    blocker, not something `read_bytes()` is ever allowed to be attempted
    against. Its live SHA-256 must match the registered one, and the
    registered sha256 evidence itself must be a well-formed 64-character hex
    string -- missing, blank, or malformed evidence is a failure in its own
    right, never silently skipped. A filesystem error while reading the file
    (permissions, race, transient I/O failure) is reported as a problem, not
    raised -- this function's contract is "return blockers," never "crash
    the caller."""
    problems = []
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
                        f"{forbidden} -- not approved output")
        return problems
    if asset_base is None or root is None:
        problems.append(f"{kind} {asset_id!r}: no asset base/containment root supplied "
                        f"to verify against")
        return problems

    try:
        resolved = (Path(asset_base) / p).resolve()
        root_resolved = Path(root).resolve()
        escapes = not resolved.is_relative_to(root_resolved)
        exists = resolved.exists()
        is_file = exists and resolved.is_file()
    except (OSError, RuntimeError, ValueError) as e:
        # RuntimeError: a symlink loop during resolution. ValueError: an
        # embedded NUL (or other value the OS path APIs reject outright) in
        # the registered path. Either way, the path never gets to prove
        # anything about containment or the file it might point at -- this
        # is a blocker, not a crash.
        problems.append(f"{kind} {asset_id!r}: could not resolve/stat the registered path "
                        f"{raw!r}: {e}")
        return problems

    if escapes:
        problems.append(f"{kind} {asset_id!r} resolved path escapes {root_resolved} "
                        f"(resolved to {resolved})")
        return problems
    if not exists:
        problems.append(f"{kind} {asset_id!r}: registered asset missing at {resolved}")
        return problems
    if not is_file:
        problems.append(f"{kind} {asset_id!r}: registered path resolves to {resolved}, "
                        f"which is not a regular file (directory, symlink target, or other "
                        f"non-file object) -- refusing rather than attempting to read it")
        return problems

    expected_hash = record.get("sha256")
    if not isinstance(expected_hash, str) or not _SHA256_HEX_RE.match(expected_hash):
        problems.append(f"{kind} {asset_id!r}: registry record has no valid sha256 evidence "
                        f"to verify against (expected a 64-character hex string, got "
                        f"{expected_hash!r})")
        return problems
    try:
        live_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as e:
        problems.append(f"{kind} {asset_id!r}: could not read the registered asset at "
                        f"{resolved} to verify its hash: {e}")
        return problems
    if live_hash != expected_hash:
        problems.append(
            f"{kind} {asset_id!r}: hash mismatch -- the file has changed since approval "
            f"(expected {expected_hash[:12]}..., found {live_hash[:12]}...)")
    return problems


def _verify_approved_pose(pose_id, record, *, asset_base, root, host_scene_bound) -> list[str]:
    """Enforces the registry record's own internal honesty as well as the
    route's fit for it -- stricter than pose_registry.resolve(), which only
    checks status against scene_bound. `host_scene_bound` is passed through
    UNCOERCED: a malformed value (None, a string, anything other than the
    exact bool the record's status requires) is itself a failure and must
    never be normalised with bool(...) before this check runs.

    status=approved requires generic_compositing_allowed is exactly True,
    includes_geometry absent or an empty list, and host_scene_bound is
    exactly False. status=approved_scene_bound requires
    generic_compositing_allowed is exactly False, includes_geometry a
    non-empty list, and host_scene_bound is exactly True. Any deviation --
    missing, null, string-valued, or merely truthy/falsy instead of the
    exact required value -- is a contradictory record or a route/record
    mismatch, and is rejected before any path/hash verification runs."""
    if record is None:
        return [f"pose {pose_id!r} is not a registered asset in the governing channel"]
    status = record.get("status")
    if status not in ("approved", "approved_scene_bound"):
        return [f"pose {pose_id!r} has status {status!r}, not approved"]

    gca = record.get("generic_compositing_allowed")
    geometry = record.get("includes_geometry")
    problems: list[str] = []

    if status == "approved":
        if gca is not True:
            problems.append(
                f"pose {pose_id!r} is status=approved but generic_compositing_allowed is "
                f"{gca!r}, not exactly true -- contradictory record")
        if not (geometry is None or (isinstance(geometry, list) and len(geometry) == 0)):
            problems.append(
                f"pose {pose_id!r} is status=approved but includes_geometry is {geometry!r} "
                f"-- contradictory record: must be absent or an empty list; baked-in "
                f"geometry requires approved_scene_bound")
        if host_scene_bound is not False:
            problems.append(
                f"pose {pose_id!r} is a generic approved pose -- the route must pass "
                f"host_scene_bound exactly false to use it (got {host_scene_bound!r})")
    else:  # approved_scene_bound
        if gca is not False:
            problems.append(
                f"pose {pose_id!r} is status=approved_scene_bound but "
                f"generic_compositing_allowed is {gca!r}, not exactly false -- contradictory "
                f"record")
        if not (isinstance(geometry, list) and len(geometry) > 0):
            problems.append(
                f"pose {pose_id!r} is status=approved_scene_bound but includes_geometry is "
                f"{geometry!r} -- must be a non-empty list")
        if host_scene_bound is not True:
            problems.append(
                f"pose {pose_id!r} is a scene-bound tableau -- the route must pass "
                f"host_scene_bound exactly true to use it (got {host_scene_bound!r})")

    if problems:
        return problems
    return _verify_asset_path_and_hash(pose_id, record, asset_base=asset_base, root=root,
                                       kind="pose")


def _verify_approved_reference(ref_id, record, *, asset_base, root) -> list[str]:
    """References carry no generic_compositing_allowed/includes_geometry
    vocabulary -- that is pose-specific. Any approved or approved_scene_bound
    status is accepted equally; only path/existence/hash integrity (via the
    shared helper) is checked beyond the status itself."""
    if record is None:
        return [f"reference {ref_id!r} is not a registered asset in the governing channel"]
    status = record.get("status")
    if status not in ("approved", "approved_scene_bound"):
        return [f"reference {ref_id!r} has status {status!r}, not approved"]
    return _verify_asset_path_and_hash(ref_id, record, asset_base=asset_base, root=root,
                                       kind="reference")


# ── pure contract validator (library only — not wired into any dispatcher) ──

def validate_contract(
    doc: dict,
    *,
    manifest: dict,
    manifest_sha256: str,
    governing_channel_binding: dict,
    expected_project_id: str,
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
    if doc.get("project_id") != expected_project_id:
        problems.append(
            f"project_id {doc.get('project_id')!r} != expected {expected_project_id!r} — "
            f"this routing artifact does not belong to the project being validated")

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
            # A reference-anchored route resolves against a capability key
            # suffixed _REFERENCE (e.g. ILLUSTRATION_REFERENCE), not the
            # plain visual-type key — mirroring the existing HOST_COMPOSITE
            # capability-key precedent below. Every other route (including
            # host_present routes using approved_pose_composite) resolves
            # against the ordinary, unsuffixed key exactly as before — this
            # branch changes nothing for them.
            if (route.get("host_present")
                    and route.get("host_method") == "reference_anchored_generation"):
                capability_key = f"{vt}_REFERENCE"
            else:
                capability_key = vt
            expected_renderer = renderer_capabilities.get(capability_key)
            if expected_renderer is None:
                # A missing capability is an error even when renderer_id is
                # null — a READY route naming a type the pack cannot render
                # at all is not "unresolved," it is wrong.
                problems.append(
                    f"{rid}: the governing Channel Pack declares no renderer "
                    f"capability for {capability_key}, but the route is READY")
            elif expected_renderer != renderer_id:
                problems.append(
                    f"{rid}: renderer_id {renderer_id!r} != current capability "
                    f"{expected_renderer!r} for {capability_key}")

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

            problems.extend(_verify_approved_pose(
                route.get("host_pose_id"), approved_poses.get(route.get("host_pose_id")),
                asset_base=poses_asset_base, root=poses_root,
                host_scene_bound=route.get("host_scene_bound")))

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

            # The canonical route currently carries no reliable framing field
            # for a host reference render, so every reference-anchored host
            # route must name exactly both approved masters — never a single
            # reference chosen ad hoc.
            ref_id_set = set(route.get("host_reference_asset_ids") or [])
            if ref_id_set != {"body_master", "face_master"}:
                problems.append(
                    f"{rid}: reference_anchored_generation requires exactly "
                    f"host_reference_asset_ids == ['body_master', 'face_master'] "
                    f"— got {sorted(ref_id_set)}")

            for ref_id in (route.get("host_reference_asset_ids") or []):
                problems.extend(_verify_approved_reference(
                    ref_id, approved_references.get(ref_id),
                    asset_base=references_asset_base, root=references_root))

    return problems


def _status_only_blockers(doc: dict) -> list[str]:
    """Every route that is not execution-ready by status alone, independent
    of whether the artifact is otherwise fully integral. A NEEDS_REVIEW
    route always blocks — that is what the status means. Private: this is
    only one ingredient of execution_blockers() below, never a complete
    answer to "is this executable" on its own — a status-only check cannot
    see a schema violation or a stale/mismatched binding."""
    return [f"{r.get('visual_asset_id')}: status {r.get('status')!r} blocks execution "
            f"— NEEDS_REVIEW routes are never dispatchable"
            for r in doc.get("routes", []) if r.get("status") != STATUS_READY]


def execution_blockers(
    doc: dict,
    *,
    manifest: dict,
    manifest_sha256: str,
    governing_channel_binding: dict,
    expected_project_id: str,
    renderer_capabilities: dict,
    renderer_registry: dict,
    approved_poses: dict | None = None,
    poses_asset_base=None,
    poses_root=None,
    approved_references: dict | None = None,
    references_asset_base=None,
    references_root=None,
) -> list[str]:
    """The ONLY function allowed to answer "is this artifact safe to
    dispatch." There is deliberately no bare-`doc` variant: a document's
    route statuses alone say nothing about whether those statuses can be
    trusted — a hand-built or corrupted dict claiming `status: "READY",
    renderer_id: null` is schema-invalid, and a status-only check would
    still report it as having zero blockers.

    Runs, in order: schema_errors(doc) (a schema-invalid document is not
    safe to reason about further, so its problems are returned immediately);
    validate_contract(doc, ...) against the full supplied governing state
    (integrity: hashes, bindings, manifest coverage, renderer/host
    resolution); and finally the per-route NEEDS_REVIEW status check. Any
    problem from any of the three blocks execution. Integrity being clean is
    necessary but not sufficient — see the module docstring.
    """
    problems = schema_errors(doc)
    if problems:
        return problems
    problems = list(validate_contract(
        doc,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        governing_channel_binding=governing_channel_binding,
        expected_project_id=expected_project_id,
        renderer_capabilities=renderer_capabilities,
        renderer_registry=renderer_registry,
        approved_poses=approved_poses,
        poses_asset_base=poses_asset_base,
        poses_root=poses_root,
        approved_references=approved_references,
        references_asset_base=references_asset_base,
        references_root=references_root,
    ))
    problems += _status_only_blockers(doc)
    return problems


def is_executable(doc: dict, **kwargs) -> bool:
    return not execution_blockers(doc, **kwargs)


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


# ── prompt authority (Task 2B-B2a) ───────────────────────────────────────────

_TYPE_ROUTE_ARGS_KEY = {"ILLUSTRATION": "illustration", "REENACTMENT": "reenactment"}


def prompt_authority_problems(route: dict) -> list[str]:
    """The schema carries both a top-level `route['prompt']` (required) and,
    for ILLUSTRATION/REENACTMENT, a typed `route['route_args'][...]['prompt']`
    (optional, nullable) — two places prompt text can live. The typed
    route_args prompt is the execution authority (Task 2B-B2a amendment 2):
    for a READY ILLUSTRATION/REENACTMENT route, it must be present (non-null)
    and must equal the top-level prompt exactly. A mismatch, or a null typed
    prompt on an otherwise-READY route, is a problem — never silently
    resolved by picking one field over the other at generation time.

    Layered on top of validate_contract() rather than folded into it: this
    check doesn't touch anything validate_contract() (locked, B1) already
    decides, so it can live entirely in new code."""
    key = _TYPE_ROUTE_ARGS_KEY.get(route.get("visual_type"))
    if key is None or route.get("status") != STATUS_READY:
        return []
    rid = route.get("visual_asset_id", "<unknown>")
    args = (route.get("route_args") or {}).get(key)
    typed = args.get("prompt") if isinstance(args, dict) else None
    if typed is None:
        return [f"{rid}: READY {route.get('visual_type')} route has no typed "
                f"route_args.{key}.prompt — the typed prompt is the execution "
                f"authority and must not be null"]
    if typed != route.get("prompt"):
        return [f"{rid}: top-level prompt does not equal route_args.{key}.prompt "
                f"— {route.get('prompt')!r} != {typed!r}"]
    return []


def prompt_authority_problems_for_doc(doc: dict) -> list[str]:
    problems: list[str] = []
    for route in doc.get("routes", []):
        problems.extend(prompt_authority_problems(route))
    return problems


# ── canonical project loader (Task 2B-B2a) ───────────────────────────────────
#
# Two tiers, deliberately: inspect_project_routes() never raises, so a caller
# that needs to enumerate every problem by name (a GateReport, a diagnostic
# --show) can. require_executable_routes() is the only one a direct
# generator/reviewer/overlay-writer/approval-writer may call before doing
# anything with side effects — it raises unless every bucket is empty, so
# there is no way to hold a "loaded but broken" result without having already
# raised. Nothing in the live pipeline calls either function yet (Task 2B-B2b
# wires that in); both are exercised directly by tests in this task.

@dataclass(frozen=True)
class _LoadSeal:
    """Module-private proof that a ProjectRoutesLoad was produced by
    inspect_project_routes() and passed every validation bucket (Task
    2B-B2b-2a corrective). Constructed ONLY inside inspect_project_routes(),
    on the single success path, after schema/integrity/status are all
    confirmed clean.

    Captures its OWN frozen copy of everything build_dispatch_snapshot()
    needs — routes_bytes, the parsed doc, both hashes, the channel, the
    manifest, and the pose/reference registry state. This is the whole
    point: build_dispatch_snapshot() reads exclusively from this sealed
    copy, never from the ProjectRoutesLoad's own public, mutable fields —
    so mutating `result.doc`, `result.routes_path`, or any other field on
    an already-sealed load AFTER validation has no effect on what a
    snapshot built from it actually contains.

    Not a cryptographic capability. Nothing in Python's object model makes
    an object truly unforgeable to code already running in the same
    process — a caller willing to `import visual_routes` and construct
    `visual_routes._LoadSeal(...)` directly can still do so. What this
    provides is a structural boundary against the realistic failure modes
    this task exists to close: a caller assembling a ProjectRoutesLoad by
    hand, copying one, or mutating a genuinely-validated one after the
    fact — none of those produce a `_LoadSeal` merely by looking right.
    """
    project_dir: Path
    doc: "MappingProxyType"
    routes_bytes: bytes
    routes_file_sha256: str
    routes_content_sha256: str
    context: object
    manifest: "MappingProxyType | None"
    manifest_sha256: str | None
    routes_path: Path
    routes_md_path: Path
    approved_poses: "MappingProxyType"
    poses_asset_base: Path | None
    poses_root: Path | None
    approved_references: "MappingProxyType"
    references_asset_base: Path | None
    references_root: Path | None


@dataclass
class ProjectRoutesLoad:
    project_dir: Path
    operation: str
    doc: dict | None
    context: object | None                     # channel_context.ChannelContext | None
    manifest: dict | None
    manifest_sha256: str | None
    routes_path: Path
    routes_md_path: Path
    load_problems: list = field(default_factory=list)
    schema_problems: list = field(default_factory=list)
    integrity_problems: list = field(default_factory=list)
    status_problems: list = field(default_factory=list)
    # Populated by inspect_project_routes() alongside the contract-validation
    # pass below (Task 2B-B2b-2a) — the SAME pose/reference registry load
    # validate_contract() was already given, kept on the result so
    # build_dispatch_snapshot() can bind a DispatchSnapshot against exactly
    # what was validated, rather than re-loading (and risking a second,
    # possibly different, live read of) the registry a second time.
    approved_poses: dict = field(default_factory=dict)
    poses_asset_base: Path | None = None
    poses_root: Path | None = None
    approved_references: dict = field(default_factory=dict)
    references_asset_base: Path | None = None
    references_root: Path | None = None
    # The dispatch-authority seal (corrective follow-up). None on every
    # load that inspect_project_routes() itself did not carry all the way
    # to a clean pass — including one that is merely reporting
    # execution_blockers == [] because a caller built it that way by hand.
    # Leading underscore: module-private by convention, deliberately not
    # part of this dataclass's ordinary public field set.
    _seal: "_LoadSeal | None" = None

    @property
    def execution_blockers(self) -> list:
        return (list(self.load_problems) + list(self.schema_problems)
                + list(self.integrity_problems) + list(self.status_problems))

    @property
    def is_executable(self) -> bool:
        return not self.execution_blockers


def file_sha256(path) -> str:
    """SHA-256 of a file's exact bytes on disk — the same thing
    generation_gate/approve_checkpoint already compute elsewhere, given one
    shared name so every caller uses the identical function rather than
    hand-rolling hashlib.sha256(path.read_bytes()) a second time."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolve_project_dir(project) -> Path:
    p = Path(project)
    return p if p.is_absolute() else (PIPELINE_DIR / project).resolve()


def inspect_project_routes(project, *, operation: str = "inspect") -> "ProjectRoutesLoad":
    """Read-only. Never raises for ordinary malformed input or an expected
    operational failure — that is the whole point of returning a
    ProjectRoutesLoad rather than letting a caller catch an exception.
    Loads everything a project's visual_routes.json needs to be judged
    against — the artifact itself, the governing channel, the manifest and
    its hash, the approved pose and reference registries — and returns a
    ProjectRoutesLoad whose four problem buckets separate "could not even
    load the inputs" from "loaded but schema-invalid" from "schema-valid but
    internally inconsistent" from "internally honest but has routes needing
    review." No writes, no directory creation, no reading of any legacy
    markdown artifact anywhere in this function.

    Corrective follow-up (item 6): every phase below — path resolution,
    directory/file existence checks, adapter-drift inspection, the routes/
    manifest reads, channel loading, schema loading/validation, pose/
    reference registry loading, and contract validation itself — is wrapped
    so an unexpected failure inside it becomes a blocker in the appropriate
    bucket instead of an uncaught exception. Every `except` below names
    `Exception`, never a bare `except:` and never `BaseException` — so
    KeyboardInterrupt and SystemExit are never swallowed; only genuine
    operational failures are converted into diagnostics.
    """
    import pose_registry
    import reference_registry
    import renderers

    try:
        project_dir = _resolve_project_dir(project)
    except Exception as e:
        result = ProjectRoutesLoad(
            project_dir=Path("."), operation=operation, doc=None, context=None,
            manifest=None, manifest_sha256=None, routes_path=Path("."),
            routes_md_path=Path("."))
        result.load_problems.append(f"could not resolve project path {project!r}: {e}")
        return result

    routes_path = project_dir / ROUTES_NAME
    routes_md_path = project_dir / ROUTES_MD_NAME
    result = ProjectRoutesLoad(
        project_dir=project_dir, operation=operation, doc=None, context=None,
        manifest=None, manifest_sha256=None, routes_path=routes_path,
        routes_md_path=routes_md_path)

    try:
        project_is_dir = project_dir.is_dir()
    except Exception as e:
        result.load_problems.append(f"could not check project directory {project_dir}: {e}")
        return result
    if not project_is_dir:
        result.load_problems.append(f"no such project directory: {project_dir}")
        return result

    try:
        routes_exists = routes_path.is_file()
    except Exception as e:
        result.load_problems.append(f"could not check for {ROUTES_NAME}: {e}")
        return result
    if not routes_exists:
        result.load_problems.append(
            f"canonical routing is unavailable — {ROUTES_NAME} not found at "
            f"{project_dir}. A future canonical rebuild and a fresh approval "
            f"are required before this project can be dispatched.")
        return result

    # Single read: routes_file_sha256 is computed over the EXACT bytes that
    # get decoded and parsed below — never a second, later read of the same
    # path (corrective follow-up, item 1). A file that changes between two
    # separate reads is exactly the TOCTOU gap this closes; hashing and
    # parsing the same in-memory bytes object makes that gap impossible for
    # this artifact.
    try:
        routes_bytes = routes_path.read_bytes()
    except Exception as e:
        result.load_problems.append(f"could not read {ROUTES_NAME}: {e}")
        return result
    routes_file_sha256 = hashlib.sha256(routes_bytes).hexdigest()
    try:
        doc = json.loads(routes_bytes.decode("utf-8"))
    except Exception as e:
        result.load_problems.append(f"could not parse {ROUTES_NAME}: {e}")
        return result
    result.doc = doc

    try:
        drift = adapter_drift(project_dir)
    except Exception as e:
        result.load_problems.append(f"could not check {ROUTES_MD_NAME} for drift: {e}")
        drift = None
    if drift:
        result.load_problems.append(drift)

    context = None
    try:
        context = channel_context.load_channel_for_project(project_dir)
        result.context = context
    except Exception as e:
        result.load_problems.append(f"could not resolve the governing channel: {e}")

    manifest_path = project_dir / "manifest.json"
    manifest = None
    manifest_sha256 = None
    try:
        manifest_exists = manifest_path.is_file()
    except Exception as e:
        result.load_problems.append(f"could not check for manifest.json: {e}")
        manifest_exists = False
    if not manifest_exists:
        result.load_problems.append(f"manifest.json missing at {project_dir}")
    else:
        try:
            manifest_bytes = manifest_path.read_bytes()
        except Exception as e:
            result.load_problems.append(f"could not read manifest.json: {e}")
        else:
            manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
            try:
                manifest = json.loads(manifest_bytes.decode("utf-8"))
                result.manifest = manifest
                result.manifest_sha256 = manifest_sha256
            except Exception as e:
                result.load_problems.append(f"could not parse manifest.json: {e}")

    if result.load_problems:
        return result

    try:
        schema_probs = schema_errors(doc)
    except Exception as e:
        result.schema_problems.append(f"schema validation itself failed: {e}")
        return result
    result.schema_problems.extend(schema_probs)
    if schema_probs:
        return result

    approved_poses = {}
    poses_asset_base = poses_root = None
    approved_references = {}
    references_asset_base = references_root = None
    try:
        if context is not None and context.host_enabled:
            approved_poses = dict(pose_registry.registry(context=context))
            poses_asset_base = pose_registry._asset_base(context)
            poses_root = pose_registry._poses_root(context)
            approved_references = dict(reference_registry.registry(context=context))
            references_asset_base = reference_registry.references_asset_base(context=context)
            references_root = reference_registry.references_root(context=context)
    except Exception as e:
        result.integrity_problems.append(
            f"could not load the approved pose/reference registry: {e}")
        return result
    result.approved_poses = approved_poses
    result.poses_asset_base = poses_asset_base
    result.poses_root = poses_root
    result.approved_references = approved_references
    result.references_asset_base = references_asset_base
    result.references_root = references_root

    try:
        integrity = list(validate_contract(
            doc,
            manifest=manifest, manifest_sha256=result.manifest_sha256,
            governing_channel_binding=context.plan_binding() if context is not None else {},
            expected_project_id=project_dir.name,
            renderer_capabilities=(dict(context.renderer_capabilities)
                                   if context is not None else {}),
            renderer_registry=renderers.RENDERERS,
            approved_poses=approved_poses, poses_asset_base=poses_asset_base,
            poses_root=poses_root,
            approved_references=approved_references,
            references_asset_base=references_asset_base, references_root=references_root,
        ))
    except Exception as e:
        result.integrity_problems.append(f"contract validation itself failed: {e}")
        return result

    try:
        integrity.extend(prompt_authority_problems_for_doc(doc))
    except Exception as e:
        integrity.append(f"prompt-authority validation itself failed: {e}")
    result.integrity_problems = integrity
    if integrity:
        return result

    try:
        result.status_problems = _status_only_blockers(doc)
    except Exception as e:
        result.integrity_problems.append(f"status evaluation itself failed: {e}")
        return result
    if result.status_problems:
        return result

    # Every one of the four buckets is genuinely clean — seal the load.
    # Everything the seal captures is deep-frozen HERE, from THIS validated
    # doc/manifest/registries, over the SAME routes_bytes hashed above —
    # never re-read from disk and never re-derived from result's own
    # (still mutable, still public) fields afterward. build_dispatch_
    # snapshot() reads exclusively from this seal.
    try:
        result._seal = _LoadSeal(
            project_dir=project_dir,
            doc=_deep_freeze_json(doc),
            routes_bytes=routes_bytes,
            routes_file_sha256=routes_file_sha256,
            routes_content_sha256=compute_routes_content_sha256(doc),
            context=context,
            manifest=_deep_freeze_json(manifest) if manifest is not None else None,
            manifest_sha256=manifest_sha256,
            routes_path=routes_path,
            routes_md_path=routes_md_path,
            approved_poses=_deep_freeze_json(approved_poses),
            poses_asset_base=poses_asset_base,
            poses_root=poses_root,
            approved_references=_deep_freeze_json(approved_references),
            references_asset_base=references_asset_base,
            references_root=references_root,
        )
    except Exception as e:
        result.integrity_problems.append(f"could not seal the validated load: {e}")
    return result


def require_executable_routes(project, *, operation: str = "dispatch") -> "ProjectRoutesLoad":
    """Calls inspect_project_routes() and raises VisualRoutesError, carrying
    every blocker, unless the result is fully executable. This is the only
    function a direct generator, reviewer, overlay writer, downloader, or
    approval writer may call before doing anything with side effects — a
    caller that gets a ProjectRoutesLoad back from this function has an
    artifact already proven executable; there is no way to hold a broken one
    without having already raised."""
    result = inspect_project_routes(project, operation=operation)
    if result.execution_blockers:
        detail = "\n".join(f"  - {b}" for b in result.execution_blockers)
        raise VisualRoutesError(
            f"{operation}: the canonical routing artifact is not executable:\n{detail}")
    return result


# ── the immutable dispatch snapshot (Task 2B-B2b-2a, corrective follow-up) ──
#
# A mutable dict copied from visual_routes.json is never sufficient dispatch
# authority — nothing stops a caller from mutating it after validation, or
# handing a route from one project's snapshot to another project's dispatch.
# DispatchSnapshot is the one immutable, fully-bound, SEALED object a
# canonical dispatcher (route_images.py) is allowed to iterate; it is built
# only by build_dispatch_snapshot(), and only from a ProjectRoutesLoad
# carrying a genuine _LoadSeal — never merely one reporting an empty
# execution_blockers list, which a caller can produce by hand.

def _deep_freeze_json(obj):
    """dict -> MappingProxyType (recursively), list -> tuple (recursively) —
    genuinely immutable structures, not dict/list SUBCLASSES (corrective
    follow-up, item 2). MappingProxyType has no mutating method at all;
    tuple is immutable by construction — dict.__setitem__/list.append and
    every other base-class mutator is simply absent from the result's type,
    not merely overridden to raise.

    Because the result is NOT a dict/list subclass, isinstance(x, dict) and
    isinstance(x, list) do not hold for it. Every consumer that needs to
    check shape checks collections.abc.Mapping instead — see
    prompt_policy.py's narrow update, the one place in this codebase that
    used to check isinstance(..., dict) on a route-derived value.

    Anything else (str, int, float, bool, None) is returned unchanged —
    already immutable."""
    if isinstance(obj, dict):
        return MappingProxyType({k: _deep_freeze_json(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_deep_freeze_json(v) for v in obj)
    return obj


@dataclass(frozen=True)
class _SnapshotSeal:
    """Module-private proof that a DispatchSnapshot was produced by
    build_dispatch_snapshot() from a genuinely sealed ProjectRoutesLoad.
    Carries the _LoadSeal it was built from, so a caller cannot construct a
    DispatchSnapshot and merely poke a truthy value into `_seal` without
    also having a real _LoadSeal to reference — which itself is only ever
    produced by inspect_project_routes()'s own success path. Same non-
    cryptographic caveat as _LoadSeal: this is a structural boundary
    against realistic misuse, not a guarantee against a determined caller
    willing to import and call this module's private names directly."""
    load_seal: _LoadSeal


@dataclass(frozen=True)
class DispatchSnapshot:
    """The complete, immutable dispatch authority for one project's
    canonical visual_routes.json — the only thing a canonical dispatcher may
    execute against. Built only by build_dispatch_snapshot(). `_seal` is
    None for anything else, including a directly-constructed instance —
    build_dispatch_context() (renderer_adapters.py) refuses any snapshot
    whose `_seal` is not a genuine _SnapshotSeal."""
    project_dir: Path
    routes_id: str
    routes_file_sha256: str
    routes_content_sha256: str
    channel: object                      # channel_context.ChannelContext
    routes: tuple                        # tuple of MappingProxyType, canonical document order
    approved_poses: MappingProxyType
    poses_asset_base: Path | None
    poses_root: Path | None
    approved_references: MappingProxyType
    references_asset_base: Path | None
    references_root: Path | None
    _seal: "_SnapshotSeal | None" = None


def build_dispatch_snapshot(result: "ProjectRoutesLoad") -> DispatchSnapshot:
    """THE only way to obtain a DispatchSnapshot.

    Requires `result` to carry a genuine `_LoadSeal` — installed only by
    inspect_project_routes(), only on its single clean-pass success path,
    after schema/integrity/status are all confirmed empty (corrective
    follow-up, item 1). This is deliberately NOT `isinstance(result,
    ProjectRoutesLoad)` plus `execution_blockers == []`: a caller-built
    ProjectRoutesLoad, a copy of a genuinely sealed one, or a genuinely
    sealed one whose fields were mutated after the fact all fail this check
    — none of them carry a real seal, and even one that still does has
    every value below read from the SEAL's own frozen copy, never from
    `result`'s own (still mutable, still public) fields. A route or the
    routes file changing after validation, or being swapped out from under
    an already-sealed load, has no way to reach this function's output at
    all.
    """
    if not isinstance(result, ProjectRoutesLoad):
        raise VisualRoutesError(
            f"build_dispatch_snapshot() requires a ProjectRoutesLoad, got "
            f"{type(result).__name__}")
    seal = result._seal
    if not isinstance(seal, _LoadSeal):
        detail = ("\n".join(f"  - {b}" for b in result.execution_blockers)
                  if result.execution_blockers
                  else "no seal present — this load was never sealed by "
                       "inspect_project_routes()'s own clean-pass success path")
        raise VisualRoutesError(
            f"cannot build a dispatch snapshot: this ProjectRoutesLoad is "
            f"not genuinely sealed dispatch authority:\n{detail}")

    return DispatchSnapshot(
        project_dir=seal.project_dir,
        routes_id=seal.doc.get("routes_id"),
        routes_file_sha256=seal.routes_file_sha256,
        routes_content_sha256=seal.routes_content_sha256,
        channel=seal.context,
        routes=tuple(seal.doc.get("routes") or ()),
        approved_poses=seal.approved_poses,
        poses_asset_base=seal.poses_asset_base,
        poses_root=seal.poses_root,
        approved_references=seal.approved_references,
        references_asset_base=seal.references_asset_base,
        references_root=seal.references_root,
        _seal=_SnapshotSeal(load_seal=seal),
    )


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
