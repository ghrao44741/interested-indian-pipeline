"""
generation_gate.py — one preflight gate in front of every paid generation.

Two things live here, and they are deliberately together:

  PAID_ENTRY_POINTS         the registry of every code path that can spend money
  require_generation_ready  the check each of those paths must run first

Keeping them apart is how the earlier failures happened: checks existed but
nothing called them, because the only caller was an orchestrator no project ever
used. A registry that sits next to the gate can be tested against it — every
registered entry point must invoke the gate, and no unregistered paid path may
exist (tests/test_generation_gate.py asserts both).

The gate fails BEFORE a client is constructed, before references are opened,
before an output file is created and before any cost record is incremented, so a
blocked project cannot spend anything and cannot leave half-written artwork
behind. It raises rather than warns: generating against uncertain identity is
how approved artwork ends up attached to the wrong words.

    from generation_gate import require_generation_ready, GateBlocked
    require_generation_ready("pilot_neet_scandal", "xai image batch")

CLI:
    python generation_gate.py --project test_2min --operation "image batch"
    python generation_gate.py --list-entry-points
"""

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pose_registry
import source_ids

PIPELINE_DIR = Path(__file__).parent
SPEC_PATH = PIPELINE_DIR / "character" / "character_spec.json"
VISUAL_PLAN_NAME = "visual_plan.json"

# Directories whose contents are, by definition, not approved output. A pose or
# reference resolving into any of these must never reach a render: raw holds
# pre-alpha generator output, pose_candidates holds unreviewed replacements,
# archive holds superseded identities (the v1 child face lives there).
NON_RENDERABLE_DIRS = ("pose_candidates", "pose_sources", "archive", "raw", "pending")


# ── the registry of paid entry points ────────────────────────────────────────
#
# scope:
#   project    — needs a clean manifest for a specific project
#   character  — channel-level asset work; no project manifest exists
# `gate` names the function whose body must contain the require_generation_ready
# call. Entries with implemented=False name work that does not exist yet; the
# registry test asserts those modules are genuinely absent, so an entry cannot
# quietly stay unimplemented once someone writes the file.

PAID_ENTRY_POINTS = [
    {
        "id": "images.flux_batch",
        "module": "generate_images_flux.py",
        "gate": "main",
        "provider": "xai | replicate",
        "operation": "episode image batch (CARTOON/REENACTMENT shots)",
        "scope": "project",
        "retry_paths": ["--from-report", "--shot", "--overwrite"],
        "implemented": True,
    },
    {
        "id": "images.aibmm_batch",
        "module": "generate_images_aibmm.py",
        "gate": "main",
        "provider": "openai",
        "operation": "episode image batch via gpt-image-2 (mascot/general scenes)",
        "scope": "project",
        "retry_paths": ["--overwrite", "--test"],
        "implemented": True,
    },
    {
        "id": "images.router",
        "module": "route_images.py",
        "gate": "main",
        "provider": "delegated (xai, pexels) + local",
        "operation": "route shots to MAP/CHART/PHOTO/HOST/AI generators",
        "scope": "project",
        "retry_paths": ["--overwrite"],
        "implemented": True,
    },
    {
        "id": "images.pexels",
        "module": "search_pexels.py",
        "gate": "main",
        "provider": "pexels (free tier, rate limited)",
        "operation": "stock photo fetch",
        "scope": "project",
        "retry_paths": [],
        "implemented": True,
    },
    {
        "id": "qa.review_images",
        "module": "review_images.py",
        "gate": "main",
        "provider": "anthropic (vision)",
        "operation": "per-image QA rubric pass",
        "scope": "project",
        "retry_paths": ["repeated review rounds in _stage_images"],
        "implemented": True,
    },
    {
        "id": "character.masters",
        "module": "generate_character.py",
        "gate": "main",
        "provider": "openai",
        "operation": "canonical masters, expression and view packages",
        "scope": "character",
        "retry_paths": ["--force"],
        "implemented": True,
    },
    {
        "id": "character.pose_batch",
        "module": "generate_poses.py",
        "gate": "generate_batch",
        "provider": "openai",
        "operation": "authorized pose batch",
        "scope": "character",
        "retry_paths": ["--force (rejected over an approved batch)"],
        "implemented": True,
    },
    {
        "id": "character.pose_replacement",
        "module": "generate_poses.py",
        "gate": "generate_replacement_candidate",
        "provider": "openai",
        "operation": "single replacement candidate for one approved pose",
        "scope": "character",
        "retry_paths": ["repeat invocation creates vNN+1"],
        "implemented": True,
    },
    {
        "id": "orchestrator.stage_images",
        "module": "pipeline_agents.py",
        "gate": "_stage_images",
        "provider": "delegated",
        "operation": "orchestrated images stage (router + review rounds)",
        "scope": "project",
        "retry_paths": ["review round re-route loop"],
        "implemented": True,
    },
    {
        "id": "images.reenactment",
        "module": "generate_reenactment.py",
        "gate": "main",
        "provider": "openai (planned)",
        "operation": "illustrated reenactment of an unobservable historical event",
        "scope": "project",
        "retry_paths": [],
        "implemented": False,
        "note": "not written yet; reenactments currently route through images.flux_batch",
    },
]

# Paid, but not image generation and not identity-scoped. Recorded so the
# inventory is honest about where money goes, not to bring them under this gate:
# narration must be generatable before a manifest exists, and script/prompt
# generation runs before any visual identity is assigned.
OTHER_PAID_APIS = [
    {"module": "generate_source_audio.py", "provider": "gemini | elevenlabs | edge", "operation": "TTS narration"},
    {"module": "generate_image_prompts.py", "provider": "anthropic | gemini", "operation": "prompt authoring"},
    {"module": "review_script.py", "provider": "anthropic", "operation": "script review"},
    {"module": "generate_chapters.py", "provider": "anthropic", "operation": "chapter timestamps"},
    {"module": "review_narration_audio.py", "provider": "anthropic", "operation": "narration QA"},
    {"module": "auto_split_scenes_v1_stage3_export.py", "provider": "local whisperx (GPU, no API)", "operation": "forced alignment"},
]


def entry_point(entry_id: str) -> dict:
    for e in PAID_ENTRY_POINTS:
        if e["id"] == entry_id:
            return e
    raise KeyError(f"unregistered paid entry point {entry_id!r}; "
                   f"registered: {[e['id'] for e in PAID_ENTRY_POINTS]}")


# ── result types ─────────────────────────────────────────────────────────────

class GateBlocked(RuntimeError):
    """Raised before any spend when a preflight condition fails."""

    def __init__(self, operation: str, blockers: list[str]):
        self.operation = operation
        self.blockers = blockers
        detail = "\n".join(f"    - {b}" for b in blockers)
        super().__init__(f"{operation}: blocked by {len(blockers)} condition(s)\n{detail}")


@dataclass
class GateReport:
    operation: str
    project: str | None
    scope: str
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, ok, detail))
        if not ok:
            self.blockers.append(f"{name}: {detail}" if detail else name)
        return ok

    def render(self) -> str:
        head = f"generation gate — {self.operation} [{self.scope}]"
        if self.project:
            head += f" — project {self.project}"
        lines = [head]
        for name, ok, detail in self.checks:
            lines.append(f"  {'PASS' if ok else 'BLOCK'}  {name}"
                         + (f"  <- {detail}" if detail and not ok else ""))
        lines.append("  VERDICT: " + ("ready" if not self.blockers else
                                      f"BLOCKED ({len(self.blockers)})"))
        return "\n".join(lines)


# ── individual checks ────────────────────────────────────────────────────────

def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def _check_masters(rep: GateReport) -> None:
    """Masters exist and still hash to what was approved.

    A drifted master silently changes the character in every asset generated
    afterwards, and nothing downstream would notice — the files would all look
    like valid output.
    """
    if not rep.add("character spec present", SPEC_PATH.exists(), str(SPEC_PATH)):
        return
    try:
        spec = _load_spec()
    except json.JSONDecodeError as e:
        rep.add("character spec parses", False, str(e))
        return
    rep.add("character spec parses", True)

    masters = spec.get("masters", {})
    if not rep.add("masters recorded with provenance", bool(masters),
                   "spec has no masters block"):
        return
    for key, m in masters.items():
        path = PIPELINE_DIR / m["path"]
        if not rep.add(f"master {key} present", path.exists(), str(path)):
            continue
        expected = m.get("sha256")
        if not expected:
            rep.add(f"master {key} has an approved hash", False,
                    "no sha256 recorded — provenance cannot be verified")
            continue
        found = _sha(path)
        rep.add(f"master {key} matches approved hash", found == expected,
                f"expected {expected[:12]}…, found {found[:12]}…")


def _check_pose_registry(rep: GateReport) -> None:
    try:
        audit = pose_registry.audit()
    except Exception as e:                                  # unreadable spec, etc.
        rep.add("pose registry audits clean", False, f"{type(e).__name__}: {e}")
        return
    rep.add("pose registry audits clean", not audit["problems"],
            "; ".join(audit["problems"]))


def _check_pose_selection(rep: GateReport, pose_id: str, scene_bound: bool) -> None:
    """The selected pose resolves, and resolves to approved, renderable bytes."""
    try:
        path = pose_registry.resolve(pose_id, scene_bound=scene_bound)
    except pose_registry.PoseError as e:
        rep.add(f"pose {pose_id!r} resolves", False, str(e))
        return
    rep.add(f"pose {pose_id!r} resolves", True)

    rel = path.relative_to(PIPELINE_DIR).as_posix()
    offending = [d for d in NON_RENDERABLE_DIRS if f"/{d}/" in f"/{rel}"]
    rep.add(f"pose {pose_id!r} is not raw/candidate/archived", not offending,
            f"resolves into {offending} via {rel}")

    meta = pose_registry.metadata(pose_id)
    prohibited = set(_load_spec().get("prohibited_anchors") or [])
    rep.add(f"pose {pose_id!r} is not a prohibited anchor",
            pose_id not in prohibited and meta.get("path") not in prohibited)


def _check_manifest_identity(rep: GateReport, project_dir: Path, operation: str) -> dict | None:
    manifest = project_dir / "manifest.json"
    if not rep.add("manifest exists", manifest.exists(), str(manifest)):
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        rep.add("manifest parses", False, str(e))
        return None
    rep.add("manifest parses", True)

    try:
        source_ids.require_clean_identity(manifest, operation)
        rep.add("manifest identity is ok", True)
    except source_ids.IdentityError as e:
        rep.add("manifest identity is ok", False, str(e).split(": ", 1)[-1])

    scenes = data.get("scenes", [])
    rep.add("manifest has scenes", bool(scenes), "no scenes recorded")

    unresolved = [s["id"] for s in scenes
                  if source_ids.NEEDS_REVIEW in (s.get("source_match"),
                                                 s.get("visual_match"),
                                                 s.get("visual_state"))]
    rep.add("no scene awaits identity review", not unresolved,
            f"{len(unresolved)} scene(s): {', '.join(unresolved[:6])}"
            + (" …" if len(unresolved) > 6 else ""))

    missing = [s["id"] for s in scenes if not s.get("source_ids")]
    rep.add("every scene carries source ids", not missing,
            f"{len(missing)} scene(s): {', '.join(missing[:6])}")

    for key, label in (("shot_instance_id", "shot instance ids"),
                       ("visual_asset_id", "visual asset ids")):
        vals = [s.get(key) for s in scenes if s.get(key)]
        dupes = sorted({v for v in vals if vals.count(v) > 1})
        rep.add(f"{label} are unique", not dupes, f"duplicated: {dupes[:6]}")
    return data


def _check_sidecar_currency(rep: GateReport, project_dir: Path) -> None:
    """The sidecar's units are unique and still describe a real current script.

    Anchored on the sidecar rather than on a guessed production script: the
    sidecar is what artwork is keyed to, so the question that matters is whether
    exactly one script in the project still produces it. A project can hold a
    regression fixture alongside the real script — that is not staleness, but two
    scripts producing the same fingerprints, or none, is.
    """
    try:
        side = source_ids.load_sidecar(project_dir)
    except Exception as e:
        rep.add("source-unit sidecar loads", False, f"{type(e).__name__}: {e}")
        return
    units = side.get("units", [])
    if not rep.add("source-unit sidecar loads", bool(units), "sidecar has no units"):
        return

    ids = [u["id"] for u in units]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    rep.add("source unit ids are unique", not dupes, f"duplicated: {dupes[:6]}")

    vis = [v["id"] for u in units for v in u.get("visuals", [])]
    vdupes = sorted({i for i in vis if vis.count(i) > 1})
    rep.add("visual slot ids are unique", not vdupes, f"duplicated: {vdupes[:6]}")

    retired = {u["id"] for u in side.get("retired_units", [])}
    clash = sorted(retired & set(ids))
    rep.add("no active unit reuses a retired id", not clash, f"reused: {clash[:6]}")

    current = [u["fingerprint"] for u in units]
    matches = []
    for cand in source_ids.candidate_scripts(project_dir):
        try:
            fresh = [u["fingerprint"]
                     for u in source_ids.build_source_units(
                         cand.read_text(encoding="utf-8"))]
        except OSError:
            continue
        if fresh == current:
            matches.append(cand.name)
    rep.add("script fingerprints are current", len(matches) == 1,
            "no script in the project reproduces the recorded fingerprints — "
            "the script changed since the last sync, re-run the split stage"
            if not matches else f"ambiguous: {matches} both reproduce them")


def _check_visual_plan(rep: GateReport, project_dir: Path, manifest: dict | None) -> None:
    """A reviewed visual plan, matching this manifest, with nothing outstanding."""
    plan_path = project_dir / VISUAL_PLAN_NAME
    if not rep.add("visual plan exists", plan_path.exists(),
                   f"{plan_path.name} missing — run plan_visuals.py first"):
        return
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        rep.add("visual plan parses", False, str(e))
        return
    rep.add("visual plan parses", True)

    review = plan.get("needs_review", [])
    rep.add("visual plan has no unresolved review items", not review,
            f"{len(review)} item(s): "
            + ", ".join(str(r.get('shot', r)) for r in review[:6]))

    # The plan must describe the manifest that is about to be generated from.
    # A plan built against a previous split would authorise the wrong shots.
    if manifest is not None:
        planned = plan.get("manifest_identity", {})
        want = {"scenes": len(manifest.get("scenes", [])),
                "shot_instance_ids": sorted(s.get("shot_instance_id")
                                            for s in manifest.get("scenes", []))}
        same = (planned.get("scenes") == want["scenes"]
                and planned.get("shot_instance_ids") == want["shot_instance_ids"])
        rep.add("visual plan matches the current manifest", same,
                "plan was built against a different split — re-run plan_visuals.py")


# ── the gate ─────────────────────────────────────────────────────────────────

def require_generation_ready(project=None,
                             operation: str = "paid generation",
                             *,
                             pose_id: str | None = None,
                             scene_bound: bool = False,
                             require_visual_plan: bool = True,
                             raise_on_block: bool = True) -> GateReport:
    """Verify everything that must hold before money is spent.

    project=None runs the character-scope subset (masters + pose registry): there
    is no episode manifest when the channel's own assets are being made.

    Returns a GateReport. Raises GateBlocked unless raise_on_block is False —
    which exists for reporting tools such as plan_visuals.py, never for a
    generator deciding to carry on anyway.
    """
    scope = "character" if project is None else "project"
    project_dir = None
    if project is not None:
        project_dir = Path(project)
        if not project_dir.is_absolute():
            project_dir = (PIPELINE_DIR / project).resolve()

    rep = GateReport(operation=operation,
                     project=project_dir.name if project_dir else None,
                     scope=scope)

    if project_dir is not None:
        if rep.add("project directory exists", project_dir.is_dir(), str(project_dir)):
            manifest = _check_manifest_identity(rep, project_dir, operation)
            _check_sidecar_currency(rep, project_dir)
            if require_visual_plan:
                _check_visual_plan(rep, project_dir, manifest)

    _check_masters(rep)
    _check_pose_registry(rep)
    if pose_id is not None:
        _check_pose_selection(rep, pose_id, scene_bound)

    if rep.blockers and raise_on_block:
        raise GateBlocked(operation, rep.blockers)
    return rep


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=None)
    ap.add_argument("--operation", default="preflight check")
    ap.add_argument("--pose-id", default=None)
    ap.add_argument("--scene-bound", action="store_true")
    ap.add_argument("--no-visual-plan", action="store_true",
                    help="Skip the visual-plan check (identity only)")
    ap.add_argument("--list-entry-points", action="store_true",
                    help="Print the paid entry-point registry as JSON")
    args = ap.parse_args()

    if args.list_entry_points:
        print(json.dumps({"paid_image_entry_points": PAID_ENTRY_POINTS,
                          "other_paid_apis": OTHER_PAID_APIS}, indent=2))
        return 0

    rep = require_generation_ready(args.project, args.operation,
                                   pose_id=args.pose_id,
                                   scene_bound=args.scene_bound,
                                   require_visual_plan=not args.no_visual_plan,
                                   raise_on_block=False)
    print(rep.render())
    return 1 if rep.blockers else 0


if __name__ == "__main__":
    sys.exit(main())
