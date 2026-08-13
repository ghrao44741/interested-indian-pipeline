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

import channel_context
import pose_registry
import renderers
import route_failures
import source_ids
import visual_routes

PIPELINE_DIR = Path(__file__).parent
SPEC_PATH = PIPELINE_DIR / "character" / "character_spec.json"
VISUAL_PLAN_NAME = "visual_plan.json"
VISUAL_PLAN_MD_NAME = "visual_plan.md"
# Bumped whenever the executable plan gains or changes a field that
# dispatch relies on, so an older plan cannot be executed by newer code.
# 2: every plan records the Channel Pack it was built against.
PLAN_SCHEMA_VERSION = 2
APPROVAL_NAME = "checkpoint_3_approval.json"
PROMPTS_NAME = "image_prompts_one_line_per_prompt.md"
# Deliberately not {1, 2}. A v1 record carries no channel binding at all, so
# accepting one would mean reinterpreting an approval as covering something its
# approver never saw — which is the silent reinterpretation this version exists
# to prevent. There are no v1 approvals in existence to migrate.
APPROVAL_SCHEMA_VERSIONS = {2}
APPROVAL_REQUIRED_FIELDS = ("schema_version", "project", "plan_id",
                            "manifest_sha256", "visual_plan_sha256",
                            "visual_plan_md_sha256", "prompts_sha256",
                            "failure_revision", "approved_at", "approved_by",
                            "confirmation", "paid_generation", "channel")

# The v3 approval binds canonical visual_routes.json execution. Deliberately a
# separate schema version, checked first and in isolation from
# APPROVAL_SCHEMA_VERSIONS/APPROVAL_REQUIRED_FIELDS above: a v2 record and a v3
# record describe different artifacts (a legacy visual_plan.json vs. the
# canonical routes document), so accepting one where the other is expected
# would silently reinterpret what was actually approved. There is exactly one
# supported v3 schema_version, not a set, for the same reason APPROVAL_SCHEMA_
# VERSIONS is not {1, 2}: no v3 record predates this task, so there is nothing
# to stay compatible with.
APPROVAL_V3_SCHEMA_VERSION = 3
APPROVAL_V3_REQUIRED_FIELDS = ("schema_version", "project", "routes_id",
                               "routes_file_sha256", "routes_md_sha256",
                               "routes_content_sha256", "renderer_registry_sha256",
                               "manifest_sha256", "channel", "failure_revision",
                               "approved_at", "approved_by", "confirmation",
                               "paid_generation")


def _is_exact_int_schema_version(value, allowed) -> bool:
    """True only if `value` is a plain `int` — never `bool`, which is an
    `int` subclass in Python, so `True == 1` and `False == 0` would
    otherwise leak through — equal to `allowed` (a single int) or a member
    of `allowed` (a set/frozenset/list/tuple of ints).

    Deliberately `type(value) is int` rather than `isinstance(value, int)`:
    a schema version gates what downstream code is willing to execute, and
    Python's own equality is looser than that gate should be — `2.0 == 2`,
    `Decimal(2) == 2`, and `True == 1` are all real, and none of them is an
    integer schema_version 2. A numeric string, `None`, a missing field, or
    any other type simply is not `int` and fails immediately.
    """
    if type(value) is not int:
        return False
    if isinstance(allowed, (set, frozenset, list, tuple)):
        return value in allowed
    return value == allowed

# Directories whose contents are, by definition, not approved output. A pose or
# reference resolving into any of these must never reach a render: raw holds
# pre-alpha generator output, pose_candidates holds unreviewed replacements,
# archive holds superseded identities (the v1 child face lives there).
# Re-exported from channel_context, which is the lower-level module and is the
# one place this list is actually defined now — a master path and a preview
# directory both need the same "underneath an approved subtree is not enough"
# check, so it lives where both channel_context._resolve_asset() and this
# module's _check_masters() can share it without one importing the other's
# private state.
NON_RENDERABLE_DIRS = channel_context.NON_RENDERABLE_DIRS


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
    {
        "id": "images.canonical_adapters",
        "module": "renderer_adapters.py",
        "gates": [
            {"kind": "canonical_visual_execution", "function": "adapt_map"},
            {"kind": "canonical_visual_execution", "function": "adapt_chart"},
            {"kind": "canonical_visual_execution", "function": "adapt_photo"},
            {"kind": "canonical_visual_execution", "function": "adapt_flux"},
            {"kind": "canonical_visual_execution", "function": "adapt_host_composite"},
            {"kind": "canonical_visual_execution", "function": "adapt_flux_reference_anchor"},
        ],
        "provider": "delegated (local, pexels, xai, openai)",
        "operation": "typed dispatch adapters for canonical visual_routes.json execution "
                     "(Task 2B-B2a) — not wired into any live dispatcher yet",
        "retry_paths": [],
        "implemented": True,
        "note": "every adapter, not only the two containing a provider-API marker, is "
                "gated by require_canonical_visual_execution_ready() — a free/local "
                "adapter (adapt_map, adapt_chart) can still write canonical episode "
                "artwork, so it must be just as fail-closed as a paid one. This gate "
                "is an intentional, temporary universal refusal (Task 2B-B2a); it does "
                "not accept require_generation_ready()'s v2 approval semantics, which "
                "do not bind canonical visual_routes execution and must never be able "
                "to authorize it. A later, separately authorized B2b checkpoint "
                "replaces this guard with the complete current-v3 binding validator.",
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


def _load_spec(context=None) -> dict:
    return json.loads(_spec_path(context).read_text(encoding="utf-8"))


def _spec_path(context=None) -> Path:
    """Where this channel's character specification lives.

    Falls back to the module constant only when there is no channel to ask —
    character-setup work, which has no episode. See the narrowly scoped legacy
    exception documented in pose_registry.
    """
    return SPEC_PATH if context is None else Path(context.character_spec_path)


def _asset_base(context=None) -> Path:
    return (PIPELINE_DIR if context is None
            else Path(context.character_spec_path).parent.parent)


def _check_channel(rep: GateReport, project_dir: Path):
    """The episode names a loadable Channel Pack. Returns the context, or None.

    Everything downstream — which character, which poses, which renderers, which
    voice — is a property of the channel, so an episode that cannot say which
    channel it belongs to cannot be checked at all, let alone generated for.
    """
    try:
        ctx = channel_context.load_channel_for_project(project_dir)
    except channel_context.ChannelError as e:
        rep.add("episode names a loadable channel", False, str(e))
        return None
    rep.add("episode names a loadable channel", True)
    return ctx


def narration_binding_problems(project_dir: Path, manifest: dict, context) -> list[str]:
    """Human-readable problems with this episode's recorded narration binding.

    Pure and side-effect-free so both `require_generation_ready()` and
    the approval writer (`approve_checkpoint.py`) can call the exact same check —
    approval must not be grantable over unverifiable audio only to fail later,
    at dispatch, with the check having lived in one place but not the other.

    Verifies, in order: the four narration fields are present (their absence
    means this manifest predates binding enforcement — treated as
    unverifiable, not silently passed); `narration_audio_file` is a safe,
    project-relative path actually inside `source_audio/`; the on-disk file at
    that path still hashes to `narration_audio_sha256`; and — the missing link
    a hash-only check would otherwise leave open — that the recorded hash
    genuinely describes the recorded settings and that both still match the
    channel's current approval:

        canonical_sha256(narration_effective_profile)
            == narration_voice_profile_sha256 == context.voice_profile_sha256

    and `narration_effective_profile` equals the channel's current
    `approved_profile` dict exactly, not merely its hash. Without this, a
    sidecar or manifest could name the current approved hash correctly while
    `narration_effective_profile` had been edited to describe a different
    voice — the hash would look right only because nothing ever recomputes it
    from the dict it is supposed to describe.
    """
    if context is None:
        return []          # _check_channel already reports the missing channel

    problems: list[str] = []
    required = ("narration_audio_file", "narration_voice_profile_sha256",
               "narration_audio_sha256", "narration_effective_profile")
    missing = [f for f in required if not manifest.get(f)]
    if missing:
        return [f"no verified narration binding recorded ({', '.join(missing)} "
                f"missing) — this manifest predates binding enforcement or was "
                f"never split with a verified sidecar; re-split with a channel-"
                f"bound production narration file"]

    raw = manifest["narration_audio_file"]
    p = Path(raw)
    if p.is_absolute() or ".." in p.parts:
        problems.append(f"narration_audio_file {raw!r} is absolute or traverses "
                        f"upward — refusing to resolve it at all")
    else:
        audio_root = (project_dir / "source_audio").resolve()
        resolved = (project_dir / p).resolve()
        if not resolved.is_relative_to(audio_root):
            problems.append(f"narration_audio_file {raw!r} resolves to {resolved}, "
                            f"outside this project's source_audio/ ({audio_root})")
        elif not resolved.exists():
            problems.append(f"recorded narration audio file is missing: {resolved}")
        else:
            found = _sha(resolved)
            if found != manifest["narration_audio_sha256"]:
                problems.append(
                    f"narration audio has changed since it was split — recorded "
                    f"{manifest['narration_audio_sha256'][:12]}…, found "
                    f"{found[:12]}… — someone replaced the file after the fact")

    recorded_hash = manifest["narration_voice_profile_sha256"]
    if recorded_hash != context.voice_profile_sha256:
        problems.append(
            f"this episode was narrated against voice profile "
            f"{str(recorded_hash)[:12]}…, the channel's approved profile is now "
            f"{str(context.voice_profile_sha256)[:12]}… — re-narrate and re-split")

    effective = manifest["narration_effective_profile"]
    self_hash = channel_context.canonical_sha256(effective)
    if self_hash != recorded_hash:
        problems.append(
            f"narration_effective_profile does not hash to its own recorded "
            f"narration_voice_profile_sha256 — the settings and the hash "
            f"claiming to describe them disagree "
            f"(hash of settings: {self_hash[:12]}…, recorded: "
            f"{str(recorded_hash)[:12]}…)")
    current_profile = channel_context._thaw(
        context.config.get("voice", {}).get("approved_profile") or {})
    if effective != current_profile:
        problems.append(
            "narration_effective_profile no longer matches the channel's current "
            "approved_profile exactly, even though a hash looked current — "
            "re-narrate and re-split against the current approval")

    return problems


def _check_narration_binding(rep: GateReport, project_dir: Path, manifest: dict,
                             context) -> None:
    if context is None or manifest is None:
        return
    problems = narration_binding_problems(project_dir, manifest, context)
    rep.add("narration binding is verified", not problems, "; ".join(problems))


def _check_voice_approved(rep: GateReport, context) -> None:
    """A recorded voice decision, not merely a configured one.

    Defense in depth: approve_checkpoint refuses to grant approval while the
    voice is pending, and this refuses to act on an approval that somehow
    predates the selection being reopened. Either check alone would be one edit
    away from being the only thing standing between an unapproved voice and a
    published episode.
    """
    if context is None:
        return
    rep.add("channel has an approved voice profile", context.voice_approved,
            f"{context.channel_id} voice selection is "
            f"{context.voice_selection_status!r} with no approved profile — record "
            f"the decision as voice.approved_profile in the channel pack, then "
            f"re-narrate and re-plan")


def _character_root(context=None) -> Path:
    """This channel's own approved character directory.

    Narrower than _asset_base(context): for a legacy-kind channel, that
    resolves to the pipeline root itself, which every episode folder and every
    other channel pack also sits beneath. A master path escaping the actual
    character directory but still nominally "under the pipeline root" must not
    pass just because the hash also happens to match.
    """
    return SPEC_PATH.parent if context is None else Path(context.character_spec_path).parent


def _resolve_active_asset(raw, *, context, label: str) -> tuple[Path | None, str]:
    """Validate and resolve a path stored in character_spec.json.

    Used for both `masters.*.path` and the top-level `references.*` pointers —
    both are runtime-resolved (unlike `masters.*.references_used`, which is
    provenance/history and deliberately exempt, since it legitimately points
    into archive/). Returns (resolved_path, "") or (None, problem_description).
    """
    if not isinstance(raw, str) or not raw:
        return None, f"{label}: no path recorded"
    p = Path(raw)
    if p.is_absolute() or ".." in p.parts:
        return None, f"{label}: path {raw!r} is absolute or traverses upward"
    forbidden = [part for part in p.parts if part in NON_RENDERABLE_DIRS]
    if forbidden:
        return None, (f"{label}: path {raw!r} passes through {forbidden} — not "
                      f"approved output, even underneath an otherwise-approved dir")
    resolved = (_asset_base(context) / p).resolve()
    root = _character_root(context).resolve()
    if not resolved.is_relative_to(root):
        return None, (f"{label}: path {raw!r} resolves to {resolved}, which is "
                      f"outside this channel's own character directory ({root}) — "
                      f"being underneath the pipeline root is not sufficient")
    return resolved, ""


def _check_masters(rep: GateReport, context=None) -> None:
    """Masters exist, still hash to what was approved, and are actually
    reachable only from this channel's own approved character directory.

    A drifted master silently changes the character in every asset generated
    afterwards, and nothing downstream would notice — the files would all look
    like valid output. An uncontained master path is the same failure with an
    attacker or corruption controlling both the path and the recorded hash.

    A channel with no host has no character package to check at all — nothing
    to skip past, nothing to report.
    """
    if context is not None and not context.host_enabled:
        return
    spec_path = _spec_path(context)
    if not rep.add("character spec present", spec_path.exists(), str(spec_path)):
        return
    try:
        spec = _load_spec(context)
    except json.JSONDecodeError as e:
        rep.add("character spec parses", False, str(e))
        return
    rep.add("character spec parses", True)

    masters = spec.get("masters", {})
    if not rep.add("masters recorded with provenance", bool(masters),
                   "spec has no masters block"):
        return

    resolved_masters: dict[str, Path] = {}
    for key, m in masters.items():
        resolved, problem = _resolve_active_asset(m.get("path"), context=context,
                                                   label=f"master {key}")
        if not rep.add(f"master {key} path is contained", resolved is not None, problem):
            continue
        resolved_masters[key] = resolved
        if not rep.add(f"master {key} present", resolved.exists(), str(resolved)):
            continue
        expected = m.get("sha256")
        if not expected:
            rep.add(f"master {key} has an approved hash", False,
                    "no sha256 recorded — provenance cannot be verified")
            continue
        found = _sha(resolved)
        rep.add(f"master {key} matches approved hash", found == expected,
                f"expected {expected[:12]}…, found {found[:12]}…")

    # character_spec.json carries a second, independent pointer to the same
    # assets at the top level (references.body_master / references.face_master).
    # generate_poses.py and friends read THAT key; this gate reads masters.*.
    # Nothing previously required them to agree — recreating exactly the
    # two-sources-of-truth problem this task exists to close.
    refs = spec.get("references", {})
    for key in ("body_master", "face_master"):
        if key not in masters:
            continue
        ref_resolved, problem = _resolve_active_asset(refs.get(key), context=context,
                                                       label=f"reference {key}")
        if not rep.add(f"reference {key} path is contained", ref_resolved is not None,
                       problem):
            continue
        master_resolved = resolved_masters.get(key)
        agrees = master_resolved is not None and ref_resolved == master_resolved
        if not rep.add(f"reference {key} agrees with the approved master", agrees,
                       f"references.{key} resolves to {ref_resolved}, "
                       f"masters.{key}.path resolves to {master_resolved}"):
            continue
        expected = masters[key].get("sha256")
        if ref_resolved.exists() and expected:
            found = _sha(ref_resolved)
            rep.add(f"reference {key} hash matches the approved master",
                    found == expected,
                    f"expected {expected[:12]}…, found {found[:12]}…")


def _check_pose_registry(rep: GateReport, context=None) -> None:
    if context is not None and not context.host_enabled:
        return          # no host, no pose registry to audit
    try:
        audit = pose_registry.audit(context=context)
    except Exception as e:                                  # unreadable spec, etc.
        rep.add("pose registry audits clean", False, f"{type(e).__name__}: {e}")
        return
    rep.add("pose registry audits clean", not audit["problems"],
            "; ".join(audit["problems"]))


def _check_pose_selection(rep: GateReport, pose_id: str, scene_bound: bool,
                          context=None) -> None:
    """The selected pose resolves, and resolves to approved, renderable bytes."""
    try:
        path = pose_registry.resolve(pose_id, scene_bound=scene_bound, context=context)
    except pose_registry.PoseError as e:
        rep.add(f"pose {pose_id!r} resolves", False, str(e))
        return
    rep.add(f"pose {pose_id!r} resolves", True)

    base = _asset_base(context).resolve()
    rel = (path.resolve().relative_to(base).as_posix()
           if path.resolve().is_relative_to(base) else path.as_posix())
    offending = [d for d in NON_RENDERABLE_DIRS if f"/{d}/" in f"/{rel}"]
    rep.add(f"pose {pose_id!r} is not raw/candidate/archived", not offending,
            f"resolves into {offending} via {rel}")

    meta = pose_registry.metadata(pose_id, context=context)
    prohibited = set(_load_spec(context).get("prohibited_anchors") or [])
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


def _check_plan_channel(rep: GateReport, plan: dict, context, label: str) -> None:
    """A plan or approval describes the exact Channel Pack still in force.

    Changing Channel DNA or the character specification changes what the artwork
    is supposed to be, so it must invalidate work approved under the old one. And
    a plan built for one channel must never be executable under another, however
    similar the two look.
    """
    block = plan.get("channel")
    if not rep.add(f"{label} records its channel", isinstance(block, dict) and block,
                   f"no channel block — re-run plan_visuals.py"):
        return
    if context is None:
        return

    rep.add(f"{label} names this episode's channel",
            block.get("channel_id") == context.channel_id,
            f"{label} is for {block.get('channel_id')!r}, this episode belongs to "
            f"{context.channel_id!r}")
    rep.add(f"{label} channel pack is unchanged",
            block.get("channel_json_sha256") == context.channel_json_sha256,
            f"the channel pack has changed since the {label} was made "
            f"(recorded {str(block.get('channel_json_sha256'))[:12]}…, now "
            f"{context.channel_json_sha256[:12]}…) — re-plan and re-approve")
    rep.add(f"{label} channel DNA version is current",
            block.get("channel_dna_version") == context.channel_dna_version,
            f"recorded v{block.get('channel_dna_version')}, pack is now "
            f"v{context.channel_dna_version}")
    if context.host_enabled:
        rep.add(f"{label} character specification is unchanged",
                block.get("character_spec_sha256") == context.character_spec_sha256,
                f"the character specification has changed since the {label} was made "
                f"— every host shot it authorised would be a different character")
    rep.add(f"{label} voice binding matches the channel",
            block.get("voice_profile_sha256") == context.voice_profile_sha256,
            f"recorded voice profile {str(block.get('voice_profile_sha256'))[:12]}, "
            f"channel now has {str(context.voice_profile_sha256)[:12]}")


def _check_visual_plan(rep: GateReport, project_dir: Path, manifest: dict | None,
                       context=None) -> None:
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

    _check_plan_channel(rep, plan, context, "visual plan")

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


def _check_approval_v2(rep: GateReport, project_dir: Path, manifest: dict | None,
                       context=None) -> None:
    """An explicit, human-granted Checkpoint 3 approval, bound to exact bytes.

    This is the check the previous single gate did not have. It verified that a
    visual plan existed, had an empty needs_review list and matched the manifest
    — all three of which plan_visuals.py produces itself. Checking them proved
    only that a program agreed with itself; anyone could clear the checkpoint by
    running a free command.

    Approval is a separate artifact carrying the SHA-256 of both the manifest and
    the plan it approved. Editing either side changes a hash and the approval
    stops applying, which is what makes "approve this exact plan" mean something.

    Renamed from `_check_approval()` (Task 2B-B2b-1): this is now explicitly the
    v2/legacy validator, called only from `require_generation_ready()`. It binds
    `visual_plan.json`, never `visual_routes.json` — a v3 approval must never
    satisfy this function, and this function must never be asked to evaluate one.
    See `_check_approval_v3()` for the canonical-routes counterpart, and
    `schema_version in APPROVAL_SCHEMA_VERSIONS` below for where the two part
    company: a v3 record fails that check immediately, before any v2-shaped
    field is even read as though it meant something.
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
    if not rep.add("approval record parses", isinstance(rec, dict),
                   f"expected a JSON object, found {type(rec).__name__}"):
        return

    # Corrective follow-up (Task 2B-B2b-1 micro-fix): schema_version is now
    # checked and returned on immediately, exactly like _check_approval_v3()
    # already does — an unsupported schema (a v3 record, or anything else)
    # must never have its fields interpreted as though they meant something
    # v2-shaped, even if that interpretation would itself fail harmlessly.
    if not rep.add("approval schema is supported",
                   _is_exact_int_schema_version(rec.get("schema_version"),
                                                APPROVAL_SCHEMA_VERSIONS),
                   f"schema_version={rec.get('schema_version')!r}, "
                   f"supported: {sorted(APPROVAL_SCHEMA_VERSIONS)} (exact int only — "
                   f"not a float, bool, or numeric string)"):
        return

    # `in (None, "", ...)` rather than falsiness: failure_revision is legitimately
    # 0 on a project that has never had a route fail.
    missing = [f for f in APPROVAL_REQUIRED_FIELDS
               if rec.get(f) in (None, "", [], {})]
    rep.add("approval record is complete", not missing, f"missing/empty: {missing}")

    rep.add("approval names this project", rec.get("project") == project_dir.name,
            f"approval is for {rec.get('project')!r}, this is {project_dir.name!r}")

    # An approval granted under one channel's DNA, character and voice must not
    # authorise a dispatch under another's.
    _check_plan_channel(rep, rec, context, "approval")

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


# ── the v3 approval (canonical visual_routes.json execution) ───────────────────
#
# Task 2B-B2b-1: foundation only. Neither `canonical_confirmation_phrase()`,
# `canonical_paid_generation_summary()`, `_check_approval_v3()`, nor
# `_canonical_execution_problems()` below is called by anything live yet.
# `require_canonical_visual_execution_ready()` — the function every canonical
# adapter actually calls — still unconditionally refuses (Task 2B-B2a) and is
# untouched by this task. This section exists so that composition can be built
# and unit-tested in isolation now, and so a later, separately authorized
# checkpoint (Task 2B-B2b-3) can activate it with a single, reviewable line —
# swapping require_canonical_visual_execution_ready()'s body from "always
# raise" to "raise iff _canonical_execution_problems(...).blockers" — rather
# than writing and activating this logic in the same change.

def canonical_confirmation_phrase(project: str, routes_id: str) -> str:
    """Names the project AND the exact routes document being approved.

    Deliberate counterpart to approve_checkpoint.confirmation_phrase(), which
    names a plan_id — this names a routes_id instead, because a v3 approval
    binds visual_routes.json, never visual_plan.json. Project-specific so a
    phrase cannot be pasted from one episode into another by habit;
    routes-specific so a confirmation typed against the routes document a
    human reviewed cannot be reused for a routes document rebuilt afterward.
    Lives here, not in approve_checkpoint.py, so the v3 writer (Task 2B-B2b-3)
    computes the identical phrase this validator checks against — one
    implementation, not two that could drift apart.
    """
    return f"I approve canonical visual execution for {project} routes {routes_id}"


class CanonicalSummaryError(RuntimeError):
    """canonical_paid_generation_summary() could not honestly describe the
    routes document it was given — a malformed route collection, a missing
    or duplicate visual_asset_id, an unregistered renderer_id, or a renderer
    entry with a cost_category outside renderers.COST_CATEGORIES. Raised
    rather than silently omitted or approximated: a spend figure that quietly
    drops the route it could not classify is worse than no figure at all."""


def canonical_paid_generation_summary(doc: dict) -> dict:
    """A deterministic paid-SHOT summary derived from a canonical routes
    document — never independently authored, so it cannot describe spend the
    routes document itself does not contain.

    `shots` and `paid_count` both mean the number of PAID routes — matching
    the legacy v2 approval's use of `shots` for the approver's confirmed
    spend figure. Neither field is the total route count: a routes document
    that is entirely free/local (maps, charts, composites) must report 0, not
    "every route," which would tell an approver they are paying for
    everything even when they are paying for nothing.

    A route counts as paid by consulting the CURRENT renderer registry's
    cost_category for its renderer_id, never by trusting the route's own
    `paid`/`cost_category` fields — those are validated elsewhere
    (visual_routes.validate_contract), but this summary exists specifically so
    an approver's confirmed spend figure is checked against something neither
    the routes document nor the approval record asserts about itself.

    Raises CanonicalSummaryError — never returns a partial, approximate, or
    "treat the unreadable part as empty/free" result — for anything that
    would otherwise force a silent guess:

      - `doc` is `None` or not a JSON object;
      - `doc` has no `routes` key, `routes` is `None`, or `routes` is not a
        list;
      - a route is not a JSON object;
      - a route's `visual_asset_id` is missing, non-string, blank/whitespace-
        only, or a duplicate of an earlier (already-validated) route's;
      - a route's `renderer_id` is missing, non-string, or blank/whitespace-
        only;
      - a route's `renderer_id` is absent from the live renderer registry;
      - the matched renderer registry entry is not itself a mapping;
      - the matched entry's `cost_category` is missing or not one of
        `renderers.COST_CATEGORIES`.

    Validating a route's own shape *before* checking it against `seen_ids`
    means a malformed route is always reported on its own terms — it never
    gets blamed for "colliding" with an id that was itself never validated.

    A genuinely empty, genuinely valid `routes` list returns
    `{"shots": 0, "paid_count": 0, "paid_route_ids": []}` — emptiness is only
    ever the answer when the input said so explicitly, never when the input
    could not be read. `_check_approval_v3()` calls this inside its own
    try/except and turns any raise into a named blocker; this function itself
    never downgrades a raise into an incomplete result, and never raises
    anything other than CanonicalSummaryError for a validation failure — an
    unhashable or wrong-type `renderer_id`, for instance, is caught by the
    `isinstance(renderer_id, str)` check before it ever reaches a dict
    lookup, so it cannot surface as an incidental TypeError.
    """
    if not isinstance(doc, dict):
        raise CanonicalSummaryError(
            f"routes document must be a JSON object, got {type(doc).__name__}")
    if "routes" not in doc:
        raise CanonicalSummaryError("routes document has no 'routes' field")
    routes = doc["routes"]
    if not isinstance(routes, list):
        raise CanonicalSummaryError(
            f"routes must be a list, got {type(routes).__name__}")

    seen_ids: set = set()
    paid_ids: list = []
    for i, r in enumerate(routes):
        if not isinstance(r, dict):
            raise CanonicalSummaryError(
                f"route [{i}] is not a JSON object: {type(r).__name__}")

        vid = r.get("visual_asset_id")
        if not isinstance(vid, str) or not vid.strip():
            raise CanonicalSummaryError(
                f"route [{i}] has a missing, non-string, or blank "
                f"visual_asset_id ({vid!r})")
        if vid in seen_ids:
            raise CanonicalSummaryError(f"duplicate visual_asset_id {vid!r} across routes")
        seen_ids.add(vid)

        renderer_id = r.get("renderer_id")
        if not isinstance(renderer_id, str) or not renderer_id.strip():
            raise CanonicalSummaryError(
                f"route {vid!r} has a missing, non-string, or blank "
                f"renderer_id ({renderer_id!r})")
        entry = renderers.RENDERERS.get(renderer_id)
        if entry is None:
            raise CanonicalSummaryError(
                f"route {vid!r} names unregistered renderer_id {renderer_id!r}")
        try:
            cost_category = entry.get("cost_category")
        except AttributeError:
            raise CanonicalSummaryError(
                f"route {vid!r}: renderer registry entry for {renderer_id!r} is "
                f"malformed (not a mapping): {type(entry).__name__}") from None
        if cost_category not in renderers.COST_CATEGORIES:
            raise CanonicalSummaryError(
                f"route {vid!r}: renderer {renderer_id!r} declares a missing or "
                f"unsupported cost_category {cost_category!r}")
        if cost_category == "paid_api":
            paid_ids.append(vid)

    paid_ids = sorted(paid_ids)
    return {"shots": len(paid_ids), "paid_route_ids": paid_ids, "paid_count": len(paid_ids)}


def _bound_file_check(rep: GateReport, path: Path, recorded_hash, *,
                      present_check: str, bound_check: str) -> None:
    """One v3-binding file check: `present_check` fails on missing OR
    inaccessible (permission error, disappearing file, unreadable bytes —
    every operational failure collapses into "not present" rather than an
    uncaught exception, because either way this binding cannot be verified
    and must not be treated as satisfied); `bound_check` fails on a hash
    mismatch once the file was read successfully.
    """
    try:
        exists = path.exists()
    except Exception as e:
        rep.add(present_check, False,
                f"could not check for {path.name}: {type(e).__name__}: {e}")
        return
    if not exists:
        rep.add(present_check, False, f"{path.name} is gone")
        return
    try:
        found = visual_routes.file_sha256(path)
    except Exception as e:
        rep.add(present_check, False,
                f"could not read/hash {path.name}: {type(e).__name__}: {e}")
        return
    rep.add(bound_check, recorded_hash == found,
            f"approved {str(recorded_hash)[:12]}…, found {found[:12]}…")


def _check_approval_v3(rep: GateReport, project_dir: Path,
                       routes_load: "visual_routes.ProjectRoutesLoad | None",
                       context=None) -> None:
    """The v3 Checkpoint-3 approval binding canonical visual_routes.json
    execution — the counterpart to `_check_approval_v2()` above, and never a
    shared implementation with it.

    The first substantive check, after the record merely parsing, is its own
    schema_version: a missing, malformed, v2, boolean, string, float or any
    other schema_version value refuses right there and returns, before a
    single v3-specific field is read as though it meant something. That is
    what makes cross-version confusion structural rather than a matter of this
    function's internal discipline — the same property `_check_approval_v2()`
    already has via `APPROVAL_SCHEMA_VERSIONS`.

    Every hash bound here is recomputed fresh from what is on disk right now —
    `routes_content_sha256` and `renderer_registry_sha256` are never trusted
    from either the approval record or the routes document's own self-reported
    fields, exactly as `visual_routes.validate_contract()` already refuses to
    trust a document's self-reported `routes_content_sha256`.

    `routes_load` is expected to be the result of
    `visual_routes.inspect_project_routes()` for this same project — reused
    rather than re-loaded so this function and its caller agree on exactly
    which bytes were read. A caller with no such result yet (routes_load is
    None, or its `.doc` is None or not a JSON object) still gets every check
    that does not require the routes document itself; those become named
    blockers instead of a crash.

    Corrective follow-up (Task 2B-B2b-1 micro-fix): every step below that
    touches a file, recomputes a hash, or asks another module for a live
    value is wrapped in its own `try/except Exception` — never `except
    BaseException`, so KeyboardInterrupt and SystemExit are never swallowed —
    and turns a genuine operational failure (permission error, a file
    disappearing mid-check, a malformed routes document, an unregistered
    renderer) into a named GateReport blocker instead of an uncaught
    exception. No failed computation is ever silently treated as a passing or
    default value; every one of them either produces its own named blocker or
    is skipped entirely, never both silently.
    """
    path = project_dir / APPROVAL_NAME
    try:
        exists = path.exists()
    except Exception as e:
        rep.add("v3 approval exists", False,
                f"could not check for {APPROVAL_NAME}: {type(e).__name__}: {e}")
        return
    if not rep.add("v3 approval exists", exists,
                   f"{APPROVAL_NAME} missing — a human must run "
                   f"approve_checkpoint.py after reviewing visual_routes.json"):
        return
    try:
        raw_text = path.read_text(encoding="utf-8")
    except Exception as e:
        rep.add("v3 approval record parses", False,
                f"could not read {APPROVAL_NAME}: {type(e).__name__}: {e}")
        return
    try:
        rec = json.loads(raw_text)
    except json.JSONDecodeError as e:
        rep.add("v3 approval record parses", False, str(e))
        return
    if not rep.add("v3 approval record parses", isinstance(rec, dict),
                   f"expected a JSON object, found {type(rec).__name__}"):
        return

    if not rep.add("v3 approval schema_version is exactly 3",
                   _is_exact_int_schema_version(rec.get("schema_version"),
                                                APPROVAL_V3_SCHEMA_VERSION),
                   f"schema_version={rec.get('schema_version')!r} — a v2 record, "
                   f"a float/bool/numeric-string 3, or any other value, is never "
                   f"interpreted as v3"):
        return

    missing = [f for f in APPROVAL_V3_REQUIRED_FIELDS
               if rec.get(f) in (None, "", [], {})]
    rep.add("v3 approval record is complete", not missing, f"missing/empty: {missing}")

    rep.add("v3 approval names this project", rec.get("project") == project_dir.name,
            f"approval is for {rec.get('project')!r}, this is {project_dir.name!r} — "
            f"a copied project tree carrying otherwise-matching hashes must not "
            f"inherit another project's approval")

    doc = routes_load.doc if routes_load is not None else None
    if not isinstance(doc, dict):
        doc = None          # a non-object doc (e.g. malformed JSON) is unusable here;
                             # visual_routes.inspect_project_routes() already reports
                             # it as an execution blocker in its own right.
    routes_path = (routes_load.routes_path if routes_load is not None
                  else project_dir / visual_routes.ROUTES_NAME)
    routes_md_path = (routes_load.routes_md_path if routes_load is not None
                      else project_dir / visual_routes.ROUTES_MD_NAME)

    current_routes_id = doc.get("routes_id") if doc is not None else None
    rep.add("v3 approval names the current routes_id",
            rec.get("routes_id") == current_routes_id,
            f"approval is for routes {str(rec.get('routes_id'))[:8]}, current "
            f"routes document is {str(current_routes_id)[:8]}")

    _bound_file_check(rep, routes_path, rec.get("routes_file_sha256"),
                      present_check="visual_routes.json still present",
                      bound_check="visual_routes.json is unchanged since approval")
    _bound_file_check(rep, routes_md_path, rec.get("routes_md_sha256"),
                      present_check="visual_routes.md still present",
                      bound_check="visual_routes.md is unchanged since approval")
    _bound_file_check(rep, project_dir / "manifest.json", rec.get("manifest_sha256"),
                      present_check="manifest still present",
                      bound_check="manifest is unchanged since approval")

    if doc is not None:
        try:
            fresh_content = visual_routes.compute_routes_content_sha256(doc)
        except Exception as e:
            rep.add("routes_content_sha256 is freshly recomputed and matches", False,
                    f"could not recompute routes_content_sha256: {type(e).__name__}: {e}")
        else:
            rep.add("routes_content_sha256 is freshly recomputed and matches",
                    rec.get("routes_content_sha256") == fresh_content,
                    f"approved {str(rec.get('routes_content_sha256'))[:12]}…, "
                    f"recomputed {fresh_content[:12]}… from the current document — "
                    f"neither document's own self-reported field is trusted")

        try:
            rids = visual_routes.referenced_renderer_ids(doc.get("routes", []))
            fresh_registry = visual_routes.compute_renderer_registry_sha256(
                rids, renderers.RENDERERS)
        except Exception as e:
            rep.add("renderer_registry_sha256 is freshly recomputed and matches", False,
                    f"could not recompute renderer_registry_sha256: {type(e).__name__}: {e}")
        else:
            rep.add("renderer_registry_sha256 is freshly recomputed and matches",
                    rec.get("renderer_registry_sha256") == fresh_registry,
                    f"approved {str(rec.get('renderer_registry_sha256'))[:12]}…, "
                    f"recomputed {fresh_registry[:12]}… against the current live "
                    f"renderer registry projection")

    if context is not None:
        try:
            binding = context.plan_binding()
        except Exception as e:
            rep.add("v3 approval channel binding matches the current channel", False,
                    f"could not compute the current channel binding: "
                    f"{type(e).__name__}: {e}")
        else:
            rep.add("v3 approval channel binding matches the current channel",
                    rec.get("channel") == binding,
                    "approval was granted under a different channel binding "
                    "(channel pack, character specification or voice profile has "
                    "changed since approval) — re-plan and re-approve")

    try:
        current_rev = route_failures.revision(project_dir)
    except Exception as e:
        rep.add("failure record is readable", False, f"{type(e).__name__}: {e}")
    else:
        rep.add("v3 approval is current with the failure record",
                rec.get("failure_revision") == current_rev,
                f"approved at failure revision {rec.get('failure_revision')!r}, "
                f"now {current_rev} — a route failed or was resolved since "
                f"approval; re-plan and re-approve")

    approved_by = rec.get("approved_by")
    rep.add("v3 approval names a human approver",
            isinstance(approved_by, str) and approved_by.strip() != "",
            "approved_by is blank")

    approved_at = rec.get("approved_at")
    if approved_at:
        try:
            when = datetime.fromisoformat(str(approved_at).replace("Z", "+00:00"))
        except Exception as e:
            rep.add("v3 approval timestamp parses", False, f"{type(e).__name__}: {e}")
        else:
            rep.add("v3 approval timestamp is timezone-aware", when.tzinfo is not None,
                    "timestamp carries no timezone")
    else:
        rep.add("v3 approval timestamp parses", False, "approved_at missing")

    try:
        expected_confirmation = canonical_confirmation_phrase(
            project_dir.name, str(current_routes_id or ""))
    except Exception as e:
        rep.add("v3 approval confirmation names this project and routes_id", False,
                f"could not derive the expected confirmation phrase: "
                f"{type(e).__name__}: {e}")
    else:
        rep.add("v3 approval confirmation names this project and routes_id",
                rec.get("confirmation") == expected_confirmation,
                f"expected {expected_confirmation!r}, found {rec.get('confirmation')!r}")

    if doc is not None:
        try:
            expected_summary = canonical_paid_generation_summary(doc)
        except Exception as e:
            rep.add("v3 approval paid-generation summary matches the approved routes",
                    False,
                    f"could not derive the expected paid-generation summary: "
                    f"{type(e).__name__}: {e}")
        else:
            rep.add("v3 approval paid-generation summary matches the approved routes",
                    rec.get("paid_generation") == expected_summary,
                    f"expected {expected_summary}, found {rec.get('paid_generation')}")


def _canonical_execution_problems(project, operation: str = "canonical visual execution",
                                  *, pose_id: str | None = None,
                                  scene_bound: bool = False) -> GateReport:
    """The complete v3 canonical-execution validation, composed but NOT wired.

    Task 2B-B2b-1: this function is directly callable and directly testable,
    but nothing in this commit calls it — in particular,
    `require_canonical_visual_execution_ready()` does not call it, and keeps
    its unconditional refusal. Activating it is Task 2B-B2b-3's one-line swap.

    Deliberately never reads or interprets `visual_plan.json` and never calls
    `_check_approval_v2()` — those describe the legacy pipeline's Checkpoint 3,
    which does not authorize canonical visual_routes.json execution and must
    never be conflated with it, even provisionally. Narration is verified via
    the same `narration_binding_problems()` both this function and (from Task
    2B-B2b-3 onward) the v3 approval writer call explicitly — it is not left
    to be an implicit side effect of `manifest_sha256` matching.

    Side-effect-free like every other check in this module: every step here
    reads a file or computes a hash. Nothing constructs a client, reads a
    credential, opens a subprocess, downloads anything, creates a directory,
    or writes.
    """
    project_dir = _resolve_project(project)
    rep = GateReport(operation=operation, project=project_dir.name,
                     scope="canonical_visual_execution_v3")
    if not rep.add("project directory exists", project_dir.is_dir(), str(project_dir)):
        return rep

    ctx = _check_channel(rep, project_dir)
    manifest = _check_manifest_identity(rep, project_dir, operation)
    _check_sidecar_currency(rep, project_dir)
    _check_voice_approved(rep, ctx)
    _check_narration_binding(rep, project_dir, manifest, ctx)
    _check_route_failures(rep, project_dir)

    routes_load = visual_routes.inspect_project_routes(project_dir, operation=operation)
    blockers = routes_load.execution_blockers
    if blockers:
        for i, b in enumerate(blockers, 1):
            rep.add(f"canonical routing artifact is executable [{i}]", False, b)
    else:
        rep.add("canonical routing artifact is executable", True)

    _check_approval_v3(rep, project_dir, routes_load, ctx)

    _check_masters(rep, ctx)
    _check_pose_registry(rep, ctx)
    if pose_id is not None:
        _check_pose_selection(rep, pose_id, scene_bound, ctx)

    return rep


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
    ctx = None
    if rep.add("project directory exists", project_dir.is_dir(), str(project_dir)):
        ctx = _check_channel(rep, project_dir)
        _check_manifest_identity(rep, project_dir, operation)
        _check_sidecar_currency(rep, project_dir)
    # Deliberately no voice check here: planning must stay reachable while the
    # voice is still being chosen, or the checkpoint that records the choice
    # could never be prepared for.
    _check_masters(rep, ctx)
    _check_pose_registry(rep, ctx)
    if pose_id is not None:
        _check_pose_selection(rep, pose_id, scene_bound, ctx)
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
    manifest = ctx = None
    if rep.add("project directory exists", project_dir.is_dir(), str(project_dir)):
        ctx = _check_channel(rep, project_dir)
        manifest = _check_manifest_identity(rep, project_dir, operation)
        _check_sidecar_currency(rep, project_dir)
        _check_voice_approved(rep, ctx)
        _check_narration_binding(rep, project_dir, manifest, ctx)
        _check_route_failures(rep, project_dir)
        _check_visual_plan(rep, project_dir, manifest, ctx)
        _check_approval_v2(rep, project_dir, manifest, ctx)
    _check_masters(rep, ctx)
    _check_pose_registry(rep, ctx)
    if pose_id is not None:
        _check_pose_selection(rep, pose_id, scene_bound, ctx)

    if rep.blockers and raise_on_block:
        raise GateBlocked(operation, rep.blockers)
    return rep


def require_canonical_visual_execution_ready(project=None,
                                             operation: str = "canonical visual execution"
                                             ) -> GateReport:
    """Temporary, unconditional refusal (Task 2B-B2a).

    Every adapter in renderer_adapters.py calls this, as its first executable
    operation, before any credential read, client construction, reference
    open, subprocess, download, directory creation, or write. It always
    raises GateBlocked. There is no parameter, environment variable, or
    branch anywhere in this function that can make it return successfully —
    the only way past it is to replace it, in a later, separately authorized
    B2b checkpoint, with the complete current-v3 approval/artifact binding
    validator.

    Deliberately does none of the following:
      - accept an approval of any schema version (v2 or otherwise) — no
        approval file is read at all;
      - consult or reinterpret a legacy visual plan — none is read;
      - consult visual_routes.json's validity at all — an artifact being
        internally honest is not the same question as an execution being
        approved to run, and this function does not conflate them even
        provisionally;
      - perform any I/O — no file is opened, no directory is created, no
        client is constructed, no credential is read.

    `project` is accepted only so a caller can label the blocked operation
    with the project it was attempted against; it is never resolved,
    inspected, or used to decide anything.
    """
    rep = GateReport(operation=operation,
                     project=str(project) if project is not None else None,
                     scope="canonical_visual_execution")
    rep.add(
        "canonical visual execution is enabled", False,
        "canonical visual execution remains disabled until the Task 2B-B2b "
        "approval-v3 cutover lands and replaces this guard — this is an "
        "intentional, temporary, universal refusal, not a partial or "
        "artifact-dependent check")
    raise GateBlocked(operation, rep.blockers)


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
