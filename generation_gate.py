"""
generation_gate.py — preflight gates in front of every paid operation.

Three gates, because "may this run?" has three different answers:

  require_character_ready()    channel asset work: masters + pose registry
  require_identity_ready()     planning on an episode: content identity is sound
  require_generation_ready()   identity AND an explicit Checkpoint 3 approval

The middle one is what prompt authoring, route classification and dry runs need.
They read scene identity and decide routing, so identity must be current — but
they run before the plan a human approves, so demanding approval would make the
checkpoint unreachable. The last one is what spending needs.

Alongside them lives PAID_ENTRY_POINTS, the registry of every code path that can
spend money, each recording which gate it must call. Keeping the registry next to
the gates is what makes them testable against each other: every registered entry
point must invoke its declared gate, and no unregistered paid path may exist
(tests/test_generation_gate.py asserts both).

A gate fails BEFORE a client is constructed, before references are opened, before
an output file is created and before any cost record is incremented, so a blocked
project cannot spend anything and cannot leave half-written artwork behind.

    from generation_gate import require_identity_ready, require_generation_ready
    require_identity_ready("pilot_neet_scandal", "prompt authoring")
    require_generation_ready("pilot_neet_scandal", "xai image batch")

CLI:
    python generation_gate.py --project test_2min --gate identity
    python generation_gate.py --project test_2min --gate generation
    python generation_gate.py --list-entry-points
"""

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pose_registry
import route_failures
import source_ids

PIPELINE_DIR = Path(__file__).parent
SPEC_PATH = PIPELINE_DIR / "character" / "character_spec.json"
VISUAL_PLAN_NAME = "visual_plan.json"
VISUAL_PLAN_MD_NAME = "visual_plan.md"
# Bumped whenever the executable plan gains or changes a field that
# dispatch relies on, so an older plan cannot be executed by newer code.
PLAN_SCHEMA_VERSION = 1
APPROVAL_NAME = "checkpoint_3_approval.json"
PROMPTS_NAME = "image_prompts_one_line_per_prompt.md"
APPROVAL_SCHEMA_VERSIONS = {1}
APPROVAL_REQUIRED_FIELDS = ("schema_version", "project", "plan_id",
                            "manifest_sha256", "visual_plan_sha256",
                            "visual_plan_md_sha256", "prompts_sha256",
                            "failure_revision", "approved_at", "approved_by",
                            "confirmation", "paid_generation")

# Directories whose contents are, by definition, not approved output. A pose or
# reference resolving into any of these must never reach a render: raw holds
# pre-alpha generator output, pose_candidates holds unreviewed replacements,
# archive holds superseded identities (the v1 child face lives there).
NON_RENDERABLE_DIRS = ("pose_candidates", "pose_sources", "archive", "raw", "pending")


# ── the registry of paid entry points ────────────────────────────────────────
#
# Each entry declares which gates its code must call and in which function.
#   character   — channel asset work, no episode involved
#   identity    — reads scene identity; must not require Checkpoint 3
#   generation  — spends on episode artwork; requires Checkpoint 3 approval
# An entry may declare both, when one function plans and a later one dispatches.
# Entries with implemented=False name work that does not exist yet; the registry
# test asserts those modules are genuinely absent, so an entry cannot quietly
# stay unimplemented once someone writes the file.

PAID_ENTRY_POINTS = [
    {
        "id": "prompts.author",
        "module": "generate_image_prompts.py",
        "gates": [{"kind": "identity", "function": "generate_prompts"}],
        "provider": "anthropic",
        "operation": "author per-shot image prompts and routing fields",
        "retry_paths": ["--overwrite", "per-batch retry loop"],
        "implemented": True,
        "note": "identity-gated, not approval-gated: it decides routing and so "
                "runs before the plan a human approves",
    },
    {
        "id": "images.router",
        "module": "route_images.py",
        "gates": [{"kind": "identity", "function": "classify"},
                  {"kind": "generation", "function": "dispatch_routes"}],
        "provider": "delegated (xai, pexels) + local",
        "operation": "classify shots, then dispatch to generators",
        "retry_paths": ["--overwrite"],
        "implemented": True,
        "note": "two phases: classification and --dry-run need identity only; "
                "the first dispatch needs approval",
    },
    {
        "id": "images.flux_batch",
        "module": "generate_images_flux.py",
        "gates": [{"kind": "generation", "function": "main"}],
        "provider": "xai | replicate",
        "operation": "episode image batch (illustration/reenactment shots)",
        "retry_paths": ["--from-report", "--shot", "--overwrite"],
        "implemented": True,
    },
    {
        "id": "images.aibmm_batch",
        "module": "generate_images_aibmm.py",
        "gates": [{"kind": "generation", "function": "main"}],
        "provider": "openai",
        "operation": "episode image batch via gpt-image-2",
        "retry_paths": ["--overwrite", "--test"],
        "implemented": True,
        "note": "--test has no episode and runs the character gate instead",
    },
    {
        "id": "images.pexels",
        "module": "search_pexels.py",
        "gates": [{"kind": "generation", "function": "main"}],
        "provider": "pexels (free tier, rate limited)",
        "operation": "stock photo download",
        "retry_paths": [],
        "implemented": True,
        "note": "free of charge but writes approved episode artwork, so it is "
                "approval-gated like any other route that produces a shot",
    },
    {
        "id": "images.host_composite",
        "module": "composite_character.py",
        "gates": [{"kind": "generation", "function": "render_production"}],
        "provider": "local (writes approved episode artwork)",
        "operation": "composite an approved pose into an episode shot",
        "retry_paths": [],
        "implemented": True,
    },
    {
        "id": "qa.review_images",
        "module": "review_images.py",
        "gates": [{"kind": "generation", "function": "main"}],
        "provider": "anthropic (vision)",
        "operation": "per-image QA rubric pass",
        "retry_paths": ["repeated review rounds in _stage_images"],
        "implemented": True,
    },
    {
        "id": "character.masters",
        "module": "generate_character.py",
        "gates": [{"kind": "character", "function": "main"}],
        "provider": "openai",
        "operation": "canonical masters, expression and view packages",
        "retry_paths": ["--force"],
        "implemented": True,
    },
    {
        "id": "character.pose_batch",
        "module": "generate_poses.py",
        "gates": [{"kind": "character", "function": "generate_batch"}],
        "provider": "openai",
        "operation": "authorized pose batch",
        "retry_paths": ["--force (rejected over an approved batch)"],
        "implemented": True,
    },
    {
        "id": "character.pose_replacement",
        "module": "generate_poses.py",
        "gates": [{"kind": "character", "function": "generate_replacement_candidate"}],
        "provider": "openai",
        "operation": "single replacement candidate for one approved pose",
        "retry_paths": ["repeat invocation creates vNN+1"],
        "implemented": True,
    },
    {
        "id": "orchestrator.stage_prompts",
        "module": "pipeline_agents.py",
        "gates": [{"kind": "identity", "function": "_stage_prompts"}],
        "provider": "delegated",
        "operation": "orchestrated prompt-authoring stage",
        "retry_paths": [],
        "implemented": True,
    },
    {
        "id": "orchestrator.stage_images",
        "module": "pipeline_agents.py",
        "gates": [{"kind": "generation", "function": "_stage_images"}],
        "provider": "delegated",
        "operation": "orchestrated images stage (router + review rounds)",
        "retry_paths": ["review round re-route loop"],
        "implemented": True,
    },
    {
        "id": "images.reenactment",
        "module": "generate_reenactment.py",
        "gates": [{"kind": "generation", "function": "main"}],
        "provider": "openai (planned)",
        "operation": "illustrated reenactment of an unobservable historical event",
        "retry_paths": [],
        "implemented": False,
        "note": "not written yet; reenactments currently route through images.flux_batch",
    },
]

# Paid, but genuinely pre-manifest. These run before scene identity exists, so
# there is nothing for an identity gate to check: a script must be reviewable
# before it is narrated, and narrated before it can be split into scenes.
# generate_image_prompts.py used to be listed here on the claim that it 'runs
# before any visual identity is assigned'. That was wrong — it reads the
# manifest and writes per-shot routing — so it is now a registered,
# identity-gated entry point above.
OTHER_PAID_APIS = [
    {"module": "generate_source_audio.py", "provider": "gemini | elevenlabs | edge", "operation": "TTS narration"},
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

    rep.add("visual plan schema is supported",
            plan.get("schema_version") == PLAN_SCHEMA_VERSION,
            f"schema_version={plan.get('schema_version')!r}, expected "
            f"{PLAN_SCHEMA_VERSION} — re-run plan_visuals.py")

    review = plan.get("needs_review", [])
    rep.add("visual plan has no unresolved review items", not review,
            f"{len(review)} item(s): "
            + ", ".join(str(r.get('shot', r)) for r in review[:6]))

    # The plan is derived from the prompts file, and dispatch used to re-read that
    # file at generation time. Nothing hashed it, so a PHOTO route could be edited
    # into a paid AI route after approval with both approved hashes untouched.
    prompts = project_dir / PROMPTS_NAME
    recorded = plan.get("inputs", {}).get("prompts_sha256")
    if not rep.add("plan records its routing input", bool(recorded),
                   "plan has no inputs.prompts_sha256 — re-run plan_visuals.py"):
        pass
    elif not prompts.exists():
        rep.add("routing input still present", False, f"{PROMPTS_NAME} is gone")
    else:
        found = _sha(prompts)
        rep.add("routing input is unchanged since planning", recorded == found,
                f"{PROMPTS_NAME} has been edited since the plan was built — "
                f"re-classify, re-plan and approve the new plan "
                f"(planned {str(recorded)[:12]}…, found {found[:12]}…)")

    # Every shot the plan will execute must be identifiable as artwork, not by a
    # position that moves whenever the narration is re-split.
    shots = plan.get("shots", [])
    ids = [s.get("visual_asset_id") for s in shots]
    rep.add("every plan entry carries a visual asset id", all(ids),
            f"{sum(1 for i in ids if not i)} entr(ies) without one")
    dupes = sorted({i for i in ids if i and ids.count(i) > 1})
    rep.add("plan visual asset ids are unique", not dupes, f"duplicated: {dupes[:6]}")
    if manifest is not None:
        known = {s.get("visual_asset_id") for s in manifest.get("scenes", [])}
        orphans = [i for i in ids if i and i not in known]
        rep.add("plan entries reconcile with the manifest", not orphans,
                f"not in the manifest: {orphans[:6]}")

    # Runtime failures move the revision. A plan built before a failure — or
    # before its resolution — is no longer the plan that can be executed.
    try:
        current = route_failures.revision(project_dir)
        rep.add("plan is current with the failure record",
                plan.get("failure_revision") == current,
                f"plan was built at failure revision "
                f"{plan.get('failure_revision')!r}, now {current} — re-plan")
    except route_failures.FailureError as e:
        rep.add("failure record is readable", False, str(e))

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


def _check_route_failures(rep: GateReport, project_dir: Path) -> None:
    """Routes that failed during a previous dispatch, and are still outstanding.

    A failure means the approved plan no longer describes what can be produced.
    Blocking here is what stops the old approval from authorising another attempt
    — previously a Pexels failure printed a line, the run carried on into the paid
    AI batch, and the next plan looked clean.
    """
    try:
        outstanding = route_failures.unresolved(project_dir)
    except route_failures.FailureError as e:
        rep.add("route failure record is readable", False, str(e))
        return
    rep.add("no unresolved route failures", not outstanding,
            f"{len(outstanding)}: "
            + "; ".join(f"{f['visual_asset_id']} ({f['planned_route']}) — {f['reason']}"
                        for f in outstanding[:4])
            + ". Resolve with route_failures.py, then re-plan and re-approve.")


def _check_approval(rep: GateReport, project_dir: Path, manifest: dict | None) -> None:
    """An explicit, human-granted Checkpoint 3 approval, bound to exact bytes.

    This is the check the previous single gate did not have. It verified that a
    visual plan existed, had an empty needs_review list and matched the manifest
    — all three of which plan_visuals.py produces itself. Checking them proved
    only that a program agreed with itself; anyone could clear the checkpoint by
    running a free command.

    Approval is a separate artifact carrying the SHA-256 of both the manifest and
    the plan it approved. Editing either side changes a hash and the approval
    stops applying, which is what makes "approve this exact plan" mean something.
    """
    path = project_dir / APPROVAL_NAME
    if not rep.add("Checkpoint 3 approval exists", path.exists(),
                   f"{APPROVAL_NAME} missing — a human must run "
                   f"approve_checkpoint.py after reviewing {VISUAL_PLAN_NAME}"):
        return
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        rep.add("approval record parses", False, str(e))
        return
    rep.add("approval record parses", True)

    rep.add("approval schema is supported",
            rec.get("schema_version") in APPROVAL_SCHEMA_VERSIONS,
            f"schema_version={rec.get('schema_version')!r}, "
            f"supported: {sorted(APPROVAL_SCHEMA_VERSIONS)}")

    # `in (None, "", ...)` rather than falsiness: failure_revision is legitimately
    # 0 on a project that has never had a route fail.
    missing = [f for f in APPROVAL_REQUIRED_FIELDS
               if rec.get(f) in (None, "", [], {})]
    rep.add("approval record is complete", not missing, f"missing/empty: {missing}")

    rep.add("approval names this project", rec.get("project") == project_dir.name,
            f"approval is for {rec.get('project')!r}, this is {project_dir.name!r}")

    # Four artifacts, because a human reads the markdown, the gate executes the
    # JSON, the JSON is derived from the prompts, and all of it describes one
    # manifest. Leaving any of them unbound leaves a way to change what gets
    # generated without changing anything that was approved.
    for label, fname, key in (
            ("manifest", "manifest.json", "manifest_sha256"),
            ("visual plan", VISUAL_PLAN_NAME, "visual_plan_sha256"),
            ("reviewed plan document", VISUAL_PLAN_MD_NAME, "visual_plan_md_sha256"),
            ("routing input", PROMPTS_NAME, "prompts_sha256")):
        f = project_dir / fname
        if not f.exists():
            rep.add(f"approved {label} still present", False, f"{fname} is gone")
            continue
        found = _sha(f)
        rep.add(f"{label} is unchanged since approval", rec.get(key) == found,
                f"{fname} has been edited since approval — re-run plan_visuals.py "
                f"and approve the new plan (approved {str(rec.get(key))[:12]}…, "
                f"found {found[:12]}…)")

    # The approval names the plan a human actually read. A plan regenerated
    # between review and approval gets a new id, so the old confirmation cannot
    # carry over to it.
    plan_path = project_dir / VISUAL_PLAN_NAME
    if plan_path.exists():
        try:
            plan_id = json.loads(plan_path.read_text(encoding="utf-8")).get("plan_id")
            rep.add("approval names the current plan", rec.get("plan_id") == plan_id,
                    f"approval is for plan {str(rec.get('plan_id'))[:8]}, current "
                    f"plan is {str(plan_id)[:8]}")
        except json.JSONDecodeError:
            pass

    try:
        rep.add("approval is current with the failure record",
                rec.get("failure_revision") == route_failures.revision(project_dir),
                f"approved at failure revision {rec.get('failure_revision')!r}, now "
                f"{route_failures.revision(project_dir)} — a route failed or was "
                f"resolved since approval; re-plan and re-approve")
    except route_failures.FailureError as e:
        rep.add("failure record is readable", False, str(e))

    if rec.get("approved_at"):
        try:
            when = datetime.fromisoformat(str(rec["approved_at"]).replace("Z", "+00:00"))
            rep.add("approval timestamp is UTC", when.tzinfo is not None,
                    "timestamp carries no timezone")
        except ValueError as e:
            rep.add("approval timestamp parses", False, str(e))

    rep.add("approval carries a paid-generation summary",
            isinstance(rec.get("paid_generation"), dict)
            and "shots" in rec["paid_generation"],
            "no approved cost summary — the approver saw no spend figure")


# ── the gates ────────────────────────────────────────────────────────────────
#
# Two gates, because they answer two different questions with different answers,
# different lifetimes and different callers:
#
#   require_identity_ready     is this project's content identity sound?
#   require_generation_ready   ...and has a human approved spending on this exact
#                              manifest and this exact plan?
#
# Collapsing them meant prompt authoring — which reads the manifest and decides
# routing — was grouped with TTS as "pre-manifest", and meant Checkpoint 3 was
# satisfied by a file the planner wrote itself.

def _resolve_project(project) -> Path:
    p = Path(project)
    return p if p.is_absolute() else (PIPELINE_DIR / project).resolve()


def require_character_ready(operation: str = "character asset generation",
                            *,
                            pose_id: str | None = None,
                            scene_bound: bool = False,
                            raise_on_block: bool = True) -> GateReport:
    """Channel-level asset work: masters and pose registry only.

    Deliberately separate from any episode. Making the channel's own artwork has
    no manifest to be current with, and must not be gated on an episode's
    approval — nor may an episode approval stand in for it.
    """
    rep = GateReport(operation=operation, project=None, scope="character")
    _check_masters(rep)
    _check_pose_registry(rep)
    if pose_id is not None:
        _check_pose_selection(rep, pose_id, scene_bound)
    if rep.blockers and raise_on_block:
        raise GateBlocked(operation, rep.blockers)
    return rep


def require_identity_ready(project,
                           operation: str = "planning",
                           *,
                           pose_id: str | None = None,
                           scene_bound: bool = False,
                           raise_on_block: bool = True) -> GateReport:
    """Content identity is sound. No approval required, no spend authorised.

    For planning-only work: prompt authoring, route classification, dry runs and
    plan_visuals.py. These read scene identity and decide routing, so they need
    identity to be current — but they run *before* the plan a human approves, so
    requiring approval would make the checkpoint impossible to reach.
    """
    project_dir = _resolve_project(project)
    rep = GateReport(operation=operation, project=project_dir.name, scope="identity")
    if rep.add("project directory exists", project_dir.is_dir(), str(project_dir)):
        _check_manifest_identity(rep, project_dir, operation)
        _check_sidecar_currency(rep, project_dir)
    _check_masters(rep)
    _check_pose_registry(rep)
    if pose_id is not None:
        _check_pose_selection(rep, pose_id, scene_bound)
    if rep.blockers and raise_on_block:
        raise GateBlocked(operation, rep.blockers)
    return rep


def require_generation_ready(project,
                             operation: str = "paid generation",
                             *,
                             pose_id: str | None = None,
                             scene_bound: bool = False,
                             raise_on_block: bool = True) -> GateReport:
    """Everything identity requires, plus an explicit Checkpoint 3 approval.

    Required before anything that downloads, generates, reviews with a paid
    vision model, or writes approved episode artwork. A project argument is
    mandatory: there is no such thing as approved generation with no episode to
    approve.
    """
    project_dir = _resolve_project(project)
    rep = GateReport(operation=operation, project=project_dir.name, scope="generation")
    manifest = None
    if rep.add("project directory exists", project_dir.is_dir(), str(project_dir)):
        manifest = _check_manifest_identity(rep, project_dir, operation)
        _check_sidecar_currency(rep, project_dir)
        _check_route_failures(rep, project_dir)
        _check_visual_plan(rep, project_dir, manifest)
        _check_approval(rep, project_dir, manifest)
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
    ap.add_argument("--gate", choices=["character", "identity", "generation"],
                    default="generation",
                    help="Which gate to evaluate (default: generation)")
    ap.add_argument("--operation", default="preflight check")
    ap.add_argument("--pose-id", default=None)
    ap.add_argument("--scene-bound", action="store_true")
    ap.add_argument("--list-entry-points", action="store_true",
                    help="Print the paid entry-point registry as JSON")
    args = ap.parse_args()

    if args.list_entry_points:
        print(json.dumps({"paid_entry_points": PAID_ENTRY_POINTS,
                          "pre_manifest_paid_apis": OTHER_PAID_APIS}, indent=2))
        return 0

    kw = dict(pose_id=args.pose_id, scene_bound=args.scene_bound,
              raise_on_block=False)
    if args.gate == "character":
        rep = require_character_ready(args.operation, **kw)
    else:
        if not args.project:
            ap.error("--project is required for the identity and generation gates")
        fn = require_identity_ready if args.gate == "identity" else require_generation_ready
        rep = fn(args.project, args.operation, **kw)
    print(rep.render())
    return 1 if rep.blockers else 0


if __name__ == "__main__":
    sys.exit(main())
