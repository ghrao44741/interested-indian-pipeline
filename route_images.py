"""
route_images.py — Scene type router for image generation

Reads image_prompts_one_line_per_prompt.md, classifies each scene by TYPE,
and calls the appropriate generator:

  MAP    → generate_india_map.py   (accurate GeoJSON — never AI-generated)
  CARTOON → generate_images_flux.py (xAI Grok)
  CHART  → generate_chart.py        (matplotlib)
  PHOTO  → search_pexels.py         (Pexels API, falls back to generate_images_flux.py)
  HOST   → composite_character.py   (approved pose composited over a background)

Scenes without a TYPE field are classified by keyword matching (legacy prompts).

HOST shots carry `HOST_POSE: <pose_id>` — an id, never a file path. The router
records the id and hands it to the compositor, which resolves it through
pose_registry. Nothing here ever touches character/poses/ directly, so an
unapproved, raw or tampered asset has no route into a render.

Routing runs behind require_generation_ready(), so a project whose identity is
blocked cannot reach any generator, paid or free.

SETUP:
    No extra dependencies — calls existing pipeline scripts as subprocesses.

USAGE:
    python route_images.py --project test_script
    python route_images.py --project ep01 --overwrite
    python route_images.py --project ep01 --dry-run   (shows plan, no writes)
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import channel_context
import composite_character
import generation_gate
import pose_registry
import renderer_adapters
import renderers
import route_failures
import visual_routes
from generation_gate import (GateBlocked, VISUAL_PLAN_NAME,
                             PLAN_SCHEMA_VERSION,
                             require_generation_ready,
                             require_identity_ready)

PIPELINE_DIR = Path(__file__).parent
PROMPTS_FILE = "image_prompts_one_line_per_prompt.md"

# The complete set of routes this code knows how to execute. Anything else —
# missing, misspelled, or introduced by a newer prompt writer — is UNCLASSIFIED
# and needs a human. There is deliberately no "everything else" route: that is
# what turned unknown types into paid AI generations.
SUPPORTED_TYPES = frozenset({"CARTOON", "MAP", "CHART", "PHOTO", "HOST"})
UNCLASSIFIED = "UNCLASSIFIED"

# Only these may reach the AI generator, and only by name.
AI_TYPES = frozenset({"CARTOON"})


class RouteError(RuntimeError):
    """A plan entry cannot be executed. Raised before any generator runs."""

# Keywords for legacy prompts that don't have a TYPE field
_MAP_KEYWORDS = [
    "color-coded political map", "india map", "map of india",
    "map showing", "highlighted.*state", "state.*highlight",
    "union territor", "northeastern india", "southern india map",
    "political map illustration",
]
_CHART_KEYWORDS = [
    "infographic cartoon chart", "bar chart", "pie chart",
    "timeline", "stat card", "data visualization",
]


# ── Parsing ────────────────────────────────────────────────────────────────────

def _field(line: str, name: str, until: list[str] | None = None) -> str:
    """Extract a named field value from a one-line prompt entry."""
    if until:
        end_pat = "|".join(re.escape(u) for u in until)
        m = re.search(rf"\b{re.escape(name)}:\s*(.+?)\s*(?:{end_pat}|$)", line, re.IGNORECASE)
    else:
        m = re.search(rf"\b{re.escape(name)}:\s*(.+?)$", line, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _keyword_candidates(line: str) -> list[str]:
    """Route *suggestions* for a prompt with no usable TYPE. Never a decision.

    Only searches the PROMPT field, NOT the full line. Narration frequently
    mentions 'union territory', 'India map' and the like — searching the whole
    line mis-classified cartoon scenes as MAP.

    This used to return "CARTOON" when nothing matched, which meant a missing,
    misspelled or simply newer TYPE silently became a paid AI generation. It now
    returns candidates for a human to choose between, and the caller marks the
    shot NEEDS_REVIEW regardless of what comes back.
    """
    m = re.search(r'\bPROMPT:\s*(.+?)(?:\s+OVERLAY:|\s+CUE:|$)', line, re.IGNORECASE)
    text = (m.group(1) if m else line).lower()
    out = []
    if any(re.search(kw, text) for kw in _MAP_KEYWORDS):
        out.append("MAP")
    if any(re.search(kw, text) for kw in _CHART_KEYWORDS):
        out.append("CHART")
    return out


def _validate_chart_args(chart_args: str) -> bool:
    """Verify a CHART_ARGS string shlex-splits cleanly and contains a valid
    --type (one of bar/stat/timeline/pie) and a --data value that parses as
    non-empty JSON array. This is the code-level backstop for the LLM's
    CHART_ARGS output — if this returns False, the caller must downgrade the
    shot to CARTOON rather than pass a broken --data string to generate_chart.py.
    """
    try:
        tokens = shlex.split(chart_args)
    except ValueError:
        return False
    if "--type" not in tokens or "--data" not in tokens:
        return False
    try:
        chart_type = tokens[tokens.index("--type") + 1]
        data_str   = tokens[tokens.index("--data") + 1]
    except IndexError:
        return False
    if chart_type not in ("bar", "stat", "timeline", "pie"):
        return False
    try:
        data = json.loads(data_str)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(data, list) and len(data) > 0


def parse_shots(prompts_path: Path, context=None) -> list[dict]:
    """Parse every shot from the one-line prompts file."""
    shots = []
    for line in prompts_path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("**SHOT"):
            continue

        m_shot = re.search(r"\*\*SHOT\s+(\d+)\*\*", line)
        m_file = re.search(r"`([^`]+\.png)`", line)
        if not m_shot or not m_file:
            continue
        m_scene = re.search(r"·\s*(SCENE-\d+)\s*·", line)

        # TYPE field (new prompts)
        raw_type = _field(line, "TYPE", ["MAP_ARGS", "CHART_ARGS", "HOST_POSE",
                                         "SCENE_BOUND", "NARRATION", "PROMPT",
                                         "OVERLAY", "CUE"])
        raw_type = raw_type.upper() if raw_type else ""
        candidates = []
        unsupported = ""
        if raw_type not in SUPPORTED_TYPES:
            # Missing, misspelled, or a type from a future version of the prompt
            # writer. All three are the same situation: this code cannot say what
            # the shot should be, so a human must. Keyword matching contributes
            # suggestions and nothing more.
            unsupported = raw_type or "(none)"
            candidates = _keyword_candidates(line)
            raw_type = UNCLASSIFIED

        # A route that cannot be executed becomes NEEDS_REVIEW, keeping the route
        # that was planned and why it failed. It never silently becomes something
        # else. The previous behaviour turned an unusable MAP or CHART into
        # CARTOON, which converted a free, accurate, local render into a paid AI
        # guess — the exact substitution nobody would approve if asked, made
        # silently at parse time. It is also how the pilot ended up with zero maps.
        planned_type = raw_type
        needs_review = False
        review_reason = ""

        def flag(reason: str):
            nonlocal needs_review, review_reason
            needs_review, review_reason = True, reason
            print(f"  !  SHOT {m_shot.group(1)}: {planned_type} — {reason}. "
                  f"NEEDS_REVIEW, not rerouted.")

        if unsupported:
            flag(f"TYPE {unsupported!r} is not one of {sorted(SUPPORTED_TYPES)}"
                 + (f"; keyword suggestions: {candidates}" if candidates
                    else "; no keyword suggestion either"))

        map_args = ""
        if raw_type == "MAP":
            map_args = _field(line, "MAP_ARGS", ["NARRATION", "PROMPT", "OVERLAY"])
            if not map_args:
                flag("no MAP_ARGS, so the map has nothing to highlight")

        # CHART_ARGS carries an embedded JSON --data payload on a single line,
        # which is more failure-prone than MAP_ARGS' simple comma list. Validated
        # here so the failure is visible at planning time rather than as a
        # subprocess error midway through a run.
        chart_args = ""
        if raw_type == "CHART":
            chart_args = _field(line, "CHART_ARGS", ["NARRATION", "PROMPT", "OVERLAY"])
            if not chart_args:
                flag("no CHART_ARGS")
            elif not _validate_chart_args(chart_args):
                flag("invalid CHART_ARGS (bad or missing --type / --data JSON)")

        # HOST_POSE carries a registry id — the router deliberately has no way to
        # name a file.
        pose_id = ""
        scene_bound = False
        if raw_type == "HOST":
            pose_id = _field(line, "HOST_POSE",
                             ["SCENE_BOUND", "NARRATION", "PROMPT", "OVERLAY", "CUE"])
            scene_bound = _field(line, "SCENE_BOUND",
                                 ["NARRATION", "PROMPT", "OVERLAY", "CUE"]).lower() \
                in ("true", "yes", "1")
            if not pose_id:
                flag("HOST shot with no HOST_POSE id")
            elif pose_id not in approved_pose_ids(context):
                flag(f"HOST_POSE {pose_id!r} is not an approved registry id")

        # Parse narration for Pexels keyword extraction
        m_narr = re.search(r'NARRATION:\s*"([^"]+)"', line)
        narration = m_narr.group(1) if m_narr else ""

        shots.append({
            "shot_num": int(m_shot.group(1)),
            "file":     m_file.group(1),
            "scene_id": m_scene.group(1) if m_scene else "",   # display order only
            "type":     raw_type,
            "planned_type": planned_type,
            "route_candidates": candidates,
            "needs_review": needs_review,
            "review_reason": review_reason,
            "map_args": map_args,
            "chart_args": chart_args,
            "pose_id":  pose_id,        # id only — never a filesystem path
            "scene_bound": scene_bound,
            "narration": narration,
        })

    return shots


def approved_pose_ids(context=None) -> set[str]:
    """Pose ids the router may reference, from the registry — never from disk.

    Scoped to the episode's channel: an identically named pose in another
    channel's pack is a different asset and must be unreachable from here.
    """
    return set(pose_registry.list_poses(context=context))


# ── Generators ─────────────────────────────────────────────────────────────────

def run_host(shot: dict, project_dir: Path, images_dir: Path) -> bool:
    """Composite one HOST shot from its pose id.

    Goes through the compositor's production path, which re-runs the generation
    gate with the project in hand. That is deliberate duplication: the router
    already checked approval before dispatch, but the compositor is also directly
    invocable, and a production write must never depend on its caller having
    checked.
    """
    out_path = images_dir / shot["file"]
    if not out_path.exists():
        print("no background rendered for this shot yet", end="")
        return False
    try:
        rec = composite_character.render_production(
            project_dir, shot["pose_id"], out_path, out_path,
            scene_bound=shot.get("scene_bound", False))
    except (pose_registry.PoseError, GateBlocked, ValueError) as e:
        print(f"{e}", end="")
        return False
    shot["placement"] = rec
    return True


def run_map(shot: dict, images_dir: Path, script_dir: Path) -> bool:
    """Call generate_india_map.py for one MAP shot. Returns True on success."""
    out_path = images_dir / shot["file"]
    map_script = script_dir / "generate_india_map.py"

    cmd = [sys.executable, str(map_script), "--out", str(out_path)]
    if shot["map_args"]:
        try:
            cmd.extend(shlex.split(shot["map_args"]))
        except ValueError:
            # Malformed quoted string — pass as single arg, map script will error clearly
            cmd.append(shot["map_args"])

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()[:200]
        print(f"✗  {err}")
        return False
    return True


def run_chart(shot: dict, images_dir: Path, script_dir: Path) -> bool:
    """Call generate_chart.py for one CHART shot. Returns True on success."""
    out_path = images_dir / shot["file"]
    chart_script = script_dir / "generate_chart.py"

    cmd = [sys.executable, str(chart_script), "--out", str(out_path)]
    if shot["chart_args"]:
        try:
            cmd.extend(shlex.split(shot["chart_args"]))
        except ValueError:
            # Malformed quoted string — pass as single arg, chart script will error clearly
            cmd.append(shot["chart_args"])

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()[:200]
        print(f"✗  {err}")
        return False
    return True


def run_pexels(shot: dict, images_dir: Path, script_dir: Path) -> bool:
    """Call search_pexels.py for one PHOTO shot. Returns True on success."""
    out_path = images_dir / shot["file"]
    pexels_script = script_dir / "search_pexels.py"

    # Use narration as query; search_pexels extracts keywords from it internally
    query = shot.get("narration", "") or shot["file"]
    cmd = [sys.executable, str(pexels_script),
           "--query", query,
           "--project", str(images_dir.parent),
           "--out", str(out_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()[:200]
        print(f"✗  {err}")
        return False
    # Print any output from pexels script (photographer credit etc.)
    if result.stdout:
        for ln in result.stdout.strip().splitlines():
            print(f"    {ln}")
    return True


def run_ai_batch(project_dir: Path, script_dir: Path, overwrite: bool) -> bool:
    """Call generate_images_flux.py, which filters to CARTOON-type shots only
    in a full-batch run (MAP/CHART/PHOTO are handled by their own scripts and
    skipped here — see generate_images_flux.py's own filter for why that
    matters). Returns True on success. Deliberately doesn't capture_output — this is a
    long-running batch (can be 100+ shots) that prints per-shot progress live,
    and capturing would hide that until the whole batch finished. But the
    exit code must still be checked: this used to be a bare
    subprocess.run(cmd, check=False) with the result silently discarded, which
    let a total failure (e.g. generate_images_flux.py crashing on import before
    generating anything) print nothing and get reported as a normal, silent
    'Routing complete' — confirmed on a real run where all 102 shots in a
    batch failed this way with zero visible error."""
    flux_script = script_dir / "generate_images_flux.py"
    cmd = [sys.executable, str(flux_script), "--project", str(project_dir)]
    if overwrite:
        cmd.append("--overwrite")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"\n❌ generate_images_flux.py exited with code {result.returncode} — "
              f"AI image batch did NOT complete. See output above for the real error.")
        return False
    return True


# --- Two-phase entry ----------------------------------------------------------
#
# Phase 1 (classification, dry run) needs identity only: it reads scene identity
# and decides routes, and must be runnable before anyone has approved anything.
# Phase 2 (dispatch) spends money and writes approved episode artwork, so it runs
# behind Checkpoint 3. Splitting them is what lets a plan be produced and reviewed
# without the review itself being the thing that authorises the spend.

def classify(project_dir: Path) -> tuple[list[dict], list[dict]]:
    """Identity-gated. Returns (shots, review_items). Writes nothing."""
    require_identity_ready(project_dir, "route classification")
    # Pose ids are validated against the episode's own channel, so a HOST_POSE
    # naming another channel's pose is flagged rather than accepted.
    shots = parse_shots(project_dir / PROMPTS_FILE,
                        channel_context.load_channel_for_project(project_dir))
    review = [{"shot": s["shot_num"], "file": s["file"],
               "planned_route": s["planned_type"], "reason": s["review_reason"]}
              for s in shots if s["needs_review"]]
    return shots, review


def validate_plan(project_dir: Path, plan: dict) -> list[dict]:
    """Check the WHOLE approved plan before anything is created or generated.

    Raises RouteError on the first problem found, having written nothing. This is
    deliberately not per-shot-as-we-go: validating lazily means an invalid entry
    halfway down the plan is discovered only after the shots above it have already
    been paid for and written.
    """
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise RouteError(f"visual plan schema_version is "
                         f"{plan.get('schema_version')!r}, expected "
                         f"{PLAN_SCHEMA_VERSION} — re-run plan_visuals.py")

    # A plan carries a channel; so does the episode. If they disagree, the plan
    # was built for different artwork under different rules, and executing it
    # here would put one channel's character and palette into another's episode.
    context = channel_context.load_channel_for_project(project_dir)
    binding = plan.get("channel") or {}
    if binding.get("channel_id") != context.channel_id:
        raise RouteError(
            f"the plan was built for channel {binding.get('channel_id')!r} but this "
            f"episode belongs to {context.channel_id!r} — a plan is not portable "
            f"between channels")
    if binding.get("channel_json_sha256") != context.channel_json_sha256:
        raise RouteError(
            f"the channel pack has changed since this plan was built — re-plan and "
            f"re-approve before dispatching")

    shots = plan.get("shots", [])
    if not shots:
        raise RouteError("the approved plan contains no shots")

    if plan.get("needs_review"):
        raise RouteError(f"the plan carries {len(plan['needs_review'])} unresolved "
                         f"review item(s); it should never have been approved")

    outstanding = route_failures.unresolved(project_dir)
    if outstanding:
        raise RouteError(
            f"{len(outstanding)} unresolved route failure(s): "
            + ", ".join(f["visual_asset_id"] for f in outstanding[:6])
            + ". Resolve them with route_failures.py, re-plan and re-approve.")

    seen = set()
    manifest = json.loads((project_dir / "manifest.json").read_text(encoding="utf-8"))
    known_ids = {s.get("visual_asset_id") for s in manifest.get("scenes", [])}
    images_dir = (project_dir / "images").resolve()
    approved_poses = approved_pose_ids(context)

    for s in shots:
        where = f"shot {s.get('shot')} ({s.get('file')})"
        vid = s.get("visual_asset_id")
        if not vid:
            raise RouteError(f"{where} has no visual_asset_id — approved work must "
                             f"be identified by persistent artwork identity, not by "
                             f"shot number or filename")
        if vid in seen:
            raise RouteError(f"{where}: duplicate visual_asset_id {vid!r} — two plan "
                             f"entries claim the same artwork")
        seen.add(vid)
        if vid not in known_ids:
            raise RouteError(f"{where}: visual_asset_id {vid!r} is not in the "
                             f"manifest — the plan and the manifest disagree")

        route = s.get("visual_type")
        if route not in SUPPORTED_TYPES:
            raise RouteError(f"{where}: route {route!r} is not one of "
                             f"{sorted(SUPPORTED_TYPES)}. Nothing may be inferred "
                             f"from an unrecognised route.")
        if route == "MAP" and not s.get("map_args"):
            raise RouteError(f"{where}: MAP with no map_args")
        if route == "CHART" and not _validate_chart_args(s.get("chart_args", "")):
            raise RouteError(f"{where}: CHART arguments do not validate")
        if route == "HOST":
            pid = s.get("pose_id")
            if pid not in approved_poses:
                raise RouteError(f"{where}: HOST pose {pid!r} is not approved")
            meta = pose_registry.metadata(pid, context=context)
            if meta.get("includes_geometry") and not s.get("scene_bound"):
                raise RouteError(f"{where}: {pid!r} is a scene-bound tableau and the "
                                 f"plan does not grant scene_bound permission")
            # Resolving here proves the bytes exist and still hash correctly,
            # before any of the cheaper routes have written anything.
            pose_registry.resolve(pid, scene_bound=bool(s.get("scene_bound")),
                                  context=context)

        out = (images_dir / s["file"]).resolve()
        if not out.is_relative_to(images_dir):
            raise RouteError(f"{where}: output {s['file']!r} escapes the project's "
                             f"images directory")
    return shots


def dispatch_routes_legacy_v2(project_dir: Path, script_dir: Path, plan: dict,
                              overwrite: bool) -> int:
    """The legacy, schema-v2, visual_plan.json-driven dispatcher.

    Renamed from `dispatch_routes()` (Task 2B-B2b-2a): that name is now the
    CANONICAL, visual_routes.json-only dispatcher below, which accepts no
    plan/route-list argument at all. This function's behavior is otherwise
    completely unchanged — it still requires the legacy v2
    require_generation_ready() approval and still executes the APPROVED
    PLAN, never the prompts file. Kept, not retired: plan_visuals.py and the
    legacy provider CLIs remain functional through B2b-2a, per that task's
    explicit scope.

    The prompts markdown is mutable and is re-read on every run;
    dispatching from it meant an edit made after approval changed what got
    generated while both approved hashes stayed intact. The plan is the
    contract, and the gate has already verified it is the one that was
    approved.
    """
    require_generation_ready(project_dir, "route dispatch")
    shots = validate_plan(project_dir, plan)

    images_dir = project_dir / "images"
    images_dir.mkdir(exist_ok=True)

    runners = {"MAP": run_map, "CHART": run_chart, "PHOTO": run_pexels}
    done = skipped = 0

    # Non-AI routes first, one at a time, stopping at the first failure. A failure
    # means the approved plan no longer describes what can be produced, so nothing
    # further may be spent under that approval.
    for s in shots:
        route = s["visual_type"]
        if route in AI_TYPES:
            continue
        target = images_dir / s["file"]
        if target.exists() and not overwrite and route != "HOST":
            skipped += 1
            continue

        print(f"  {route:6s} [{s['shot']:02d}] {s['file']}", end="  ", flush=True)
        if route == "HOST":
            ok = run_host(s, project_dir, images_dir)
            reason = "pose refused by the registry, or no background rendered yet"
        else:
            ok = runners[route](s, images_dir, script_dir)
            reason = {"MAP": "map render failed - check GeoJSON state names",
                      "CHART": "chart render failed - check --type/--data",
                      "PHOTO": "Pexels returned nothing usable - a PHOTO shot must "
                               "not be replaced by a generated image"}[route]
        if not ok:
            print("FAILED")
            rec = route_failures.record_failure(
                project_dir, s, reason,
                digest_version=route_failures.LEGACY_DIGEST_VERSION)
            print(f"\n  Dispatch stopped at {rec['visual_asset_id']} ({route}).")
            print(f"  {reason}")
            print(f"\n  Recorded in {route_failures.FAILURES_NAME}. Nothing further "
                  f"was generated, and this approval no longer permits a retry:")
            print(f"    1. python route_failures.py --project {project_dir.name}")
            print(f"    2. fix the route, or resolve it explicitly")
            print(f"    3. python plan_visuals.py --project {project_dir.name}")
            print(f"    4. review, then approve_checkpoint.py")
            return 1
        side = (s.get("placement") or {}).get("side", "")
        print("ok" + (f" ({side})" if side else ""))
        done += 1

    # AI last: it is the only paid-per-shot route, so every free route that could
    # have invalidated the plan has already been proven to work.
    ai = [s for s in shots if s["visual_type"] in AI_TYPES]
    if ai:
        pending = [s for s in ai
                   if overwrite or not (images_dir / s["file"]).exists()]
        if pending:
            print(f"\n  AI batch: {len(pending)} shot(s) via generate_images_flux.py\n")
            if not run_ai_batch(project_dir, script_dir, overwrite):
                for s in pending:
                    route_failures.record_failure(
                        project_dir, s, "AI image batch did not complete",
                        digest_version=route_failures.LEGACY_DIGEST_VERSION)
                print(f"\n  AI batch failed; recorded in "
                      f"{route_failures.FAILURES_NAME}. Re-plan and re-approve.")
                return 1
            done += len(pending)
        else:
            skipped += len(ai)

    print(f"\n{'=' * 58}")
    print(f"  Routing complete - {done} produced, {skipped} already present.")
    print(f"\n  Next: python add_text_overlays.py --project {project_dir.name}")
    print(f"{'=' * 58}\n")
    return 0


# ── canonical dispatch (Task 2B-B2b-2a) ──────────────────────────────────────
#
# dispatch_routes() below is canonical-only: it reads visual_routes.json
# through visual_routes.require_executable_routes(), never a caller-supplied
# plan or route list, never image_prompts_one_line_per_prompt.md or
# visual_plan.json/.md during dispatch, and never invokes a legacy provider
# CLI through subprocess. The universal canonical-execution guard is its
# first meaningful operation — before route loading, target resolution,
# mkdir, adapter lookup, credentials, clients, subprocesses, downloads, or
# writes — so this whole function is currently unreachable in normal
# runtime (Task 2B-B2a/B2b-1's guard always refuses). It is exercised only
# in tests with that guard explicitly patched.

def _create_working_target(images_dir: Path, final_target: Path, taken: set) -> Path:
    """Creates the route-level working target via tempfile.mkstemp() — a
    unique, collision-safe path in `images_dir`, using `final_target`'s own
    suffix (corrective follow-up, item 5). Never the predictable
    `.{name}.working` scheme this replaces: that name is itself a route's
    own future output_file candidate, or a leftover from an interrupted
    prior run, in a way a fresh mkstemp() name structurally cannot be.

    Verifies the created path is a regular, non-symlink file, and that it
    is not already present in `taken` (every final target already known
    from preflight, plus every working target created so far this
    dispatch) — a check that mkstemp's own uniqueness guarantee makes
    unreachable in practice, asserted explicitly rather than assumed away.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(images_dir),
                                    suffix=final_target.suffix or ".png",
                                    prefix=".route-work-")
    os.close(fd)
    working_target = Path(tmp_name)
    if working_target.is_symlink():
        raise RouteError(f"working target {working_target} was created as a symlink — refused")
    if not working_target.is_file():
        raise RouteError(f"working target {working_target} is not a regular file — refused")
    if working_target in taken:
        raise RouteError(
            f"working target {working_target} collides with an existing final or "
            f"working target — refused")
    taken.add(working_target)
    return working_target


def _cleanup_working(p: Path) -> None:
    """Removes a route-level working target after a stage failure. If
    removal itself fails, raises a DispatchIntegrityError naming the
    leftover path rather than silently swallowing the failure (corrective
    follow-up, item 6) — cleanup is never claimed to have succeeded when it
    did not, and this must propagate: it is never converted into an
    ordinary route_failures record, and dispatch must not continue past it.
    """
    try:
        if p.exists():
            p.unlink()
    except OSError as e:
        raise renderer_adapters.DispatchIntegrityError(
            f"transaction-integrity failure: working target {p} could not be "
            f"removed after a stage failure: {e}") from e


def _preflight_targets(snapshot: "visual_routes.DispatchSnapshot",
                       images_dir: Path, overwrite: bool) -> dict:
    """Validates the COMPLETE output-target set for every route in
    `snapshot` before anything is created — no mkdir, no adapter call, no
    temporary file exists until this returns cleanly. Raises RouteError
    naming every problem found (not just the first) otherwise.

    Returns {visual_asset_id: final_target_path} — the only target mapping
    dispatch_routes() below trusts; nothing else in this module builds one.
    """
    problems: list[str] = []
    targets: dict[str, Path] = {}
    seen_targets: dict[Path, str] = {}

    project_dir = snapshot.project_dir
    try:
        resolved_project = project_dir.resolve()
        images_dir_is_symlink = images_dir.is_symlink()
        resolved_images_dir = images_dir.resolve()
    except OSError as e:
        raise RouteError(f"cannot resolve the images directory {images_dir}: {e}")
    if images_dir_is_symlink:
        problems.append(f"images directory {images_dir} is itself a symlink — refused")
    elif not resolved_images_dir.is_relative_to(resolved_project):
        problems.append(
            f"images directory {images_dir} resolves to {resolved_images_dir}, "
            f"outside the project {project_dir} — refused")

    for route in snapshot.routes:
        vid = route.get("visual_asset_id")
        output_file = route.get("output_file")
        try:
            target = visual_routes.resolve_output_target(images_dir, output_file)
        except visual_routes.VisualRoutesError as e:
            problems.append(f"{vid}: {e}")
            continue
        targets[vid] = target

        if target in seen_targets:
            problems.append(
                f"{vid} and {seen_targets[target]} both resolve to the same final "
                f"target {target} — duplicate/colliding output")
        else:
            seen_targets[target] = vid

        try:
            target_is_symlink = target.is_symlink()
            target_exists = target.exists()
            target_is_dir = target.is_dir() if target_exists else False
        except OSError as e:
            problems.append(f"{vid}: could not inspect existing target {target}: {e}")
            continue
        if target_is_symlink:
            problems.append(f"{vid}: existing target {target} is a symlink — refused")
        elif target_is_dir:
            problems.append(f"{vid}: existing target {target} is a directory — refused")
        elif target_exists and not overwrite:
            problems.append(
                f"{vid}: target {target} already exists and --overwrite was not given")

        renderer_id = route.get("renderer_id")
        if not isinstance(renderer_id, str) or not renderer_id.strip():
            problems.append(f"{vid}: no usable renderer_id ({renderer_id!r})")
        else:
            try:
                fn = renderers.dispatch_adapter(renderer_id)
                if not callable(fn):
                    problems.append(f"{vid}: renderer {renderer_id!r}'s adapter is not callable")
                elif not renderers.get(renderer_id).get("implemented"):
                    problems.append(
                        f"{vid}: renderer {renderer_id!r} is registered but not implemented")
            except renderers.RendererError as e:
                problems.append(f"{vid}: primary renderer problem: {e}")

        host_present = bool(route.get("host_present"))
        host_method = route.get("host_method")
        host_renderer_id = route.get("host_renderer_id")
        if not host_present:
            if host_method is not None or host_renderer_id is not None:
                problems.append(
                    f"{vid}: host_present is false but host_method/host_renderer_id "
                    f"is set — malformed host relationship")
        elif host_method == "approved_pose_composite":
            if not isinstance(host_renderer_id, str) or not host_renderer_id.strip():
                problems.append(
                    f"{vid}: approved_pose_composite route has no usable "
                    f"host_renderer_id ({host_renderer_id!r})")
            else:
                try:
                    hfn = renderers.dispatch_adapter(host_renderer_id)
                    if not callable(hfn):
                        problems.append(
                            f"{vid}: host renderer {host_renderer_id!r}'s adapter is "
                            f"not callable")
                    elif not renderers.get(host_renderer_id).get("implemented"):
                        problems.append(
                            f"{vid}: host renderer {host_renderer_id!r} is registered "
                            f"but not implemented")
                except renderers.RendererError as e:
                    problems.append(f"{vid}: host renderer problem: {e}")
        elif host_method == "reference_anchored_generation":
            if host_renderer_id is not None:
                problems.append(
                    f"{vid}: reference_anchored_generation must not carry a "
                    f"host_renderer_id, got {host_renderer_id!r} — the primary "
                    f"renderer performs the anchored generation itself")
        else:
            problems.append(f"{vid}: unrecognised host_method {host_method!r}")

    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        raise RouteError(
            f"preflight failed for {len(problems)} issue(s) across the routing "
            f"artifact — nothing was created:\n{detail}")
    return targets


def _dispatch_one(route, final_target: Path,
                  snapshot: "visual_routes.DispatchSnapshot", images_dir: Path,
                  taken_targets: set) -> bool:
    """Runs one route's complete stage sequence against a fresh, unique
    route-level working target, committing it onto `final_target` only once
    every required stage has succeeded (Task 2B-B2b-2a item 6, corrective
    follow-up item 5). Each adapter's own atomic_commit() only ever protects
    a SINGLE file replacement — this layers the base+host two-stage
    transaction on top of that, so a route requiring a host composite can
    never expose a base-only or half-composited image as the successful
    final asset.

    On any stage failure the working target is removed and `final_target`
    (if it pre-existed) is left byte-for-byte untouched — os.replace() onto
    `final_target` happens exactly once, only on total success.

    A DispatchIntegrityError (route/renderer-entry/context mismatch, a
    working-target cleanup failure, or any other structural violation)
    propagates immediately rather than being converted into a
    route_failures record — it is a bug in the dispatcher or its inputs,
    never an ordinary provider failure, and must never be disguised as one.
    """
    working_target = _create_working_target(images_dir, final_target, taken_targets)

    def _run_stage(renderer_field: str, fn) -> bool:
        ctx = renderer_adapters.build_dispatch_context(
            snapshot, route, target=working_target, output_root=images_dir,
            renderer_field=renderer_field)
        try:
            return bool(fn(route, working_target, ctx))
        except renderer_adapters.DispatchIntegrityError:
            raise
        except Exception as e:
            # The adapter itself is the sole writer of an ordinary-failure
            # route_failures record; reaching this branch means it raised
            # instead of returning False, so it never got the chance to
            # record one itself — this is the one case the dispatcher
            # records on the adapter's behalf. Canonical digest: this is
            # always a canonical route (never a legacy plan shot).
            route_failures.record_failure(
                snapshot.project_dir, route,
                reason=f"{renderer_field} adapter raised: {e}",
                digest_version=route_failures.CANONICAL_DIGEST_VERSION)
            return False

    primary_fn = renderers.dispatch_adapter(route.get("renderer_id"))
    if not _run_stage("renderer_id", primary_fn):
        _cleanup_working(working_target)
        return False

    if bool(route.get("host_present")) and route.get("host_method") == "approved_pose_composite":
        host_fn = renderers.dispatch_adapter(route.get("host_renderer_id"))
        if not _run_stage("host_renderer_id", host_fn):
            _cleanup_working(working_target)
            return False
    # reference_anchored_generation: the primary renderer already performed
    # the anchored generation above — no second host stage runs, ever.

    try:
        os.replace(str(working_target), str(final_target))
    except OSError as e:
        # This is the dispatcher's OWN final-commit operation, not an
        # adapter's — recording it here (rather than expecting an adapter
        # to) is correct per corrective follow-up item 7.
        route_failures.record_failure(
            snapshot.project_dir, route,
            reason=f"could not commit the final asset (working target -> final): {e}",
            digest_version=route_failures.CANONICAL_DIGEST_VERSION)
        _cleanup_working(working_target)
        return False
    return True


def dispatch_routes(project_dir: Path, *, overwrite: bool = False) -> int:
    """The canonical dispatcher (Task 2B-B2b-2a). Executes ONLY a validated
    `visual_routes.json` artifact — never a caller-supplied plan or route
    list, never the prompts markdown, never a legacy provider CLI through
    subprocess.

    The universal canonical-execution gate is the first meaningful
    operation, before route loading, target resolution, mkdir, adapter
    lookup, credentials, clients, subprocesses, downloads, or writes. It
    currently always refuses (Task 2B-B2a/B2b-1); this function's body
    below it is exercised only in tests with the guard explicitly patched,
    and goes live only when a later, separately authorized checkpoint
    (Task 2B-B2b-3) activates that guard — no code here changes when that
    happens.

    A thin public wrapper: performs its own gate check and loads its own
    snapshot, then delegates to `dispatch_snapshot_routes()`. A caller that
    already holds a sealed snapshot for this same project (Task 2B-B2b-2b's
    in-process orchestration, which must stay bound to ONE snapshot across
    dispatch/review/overlay stages rather than re-deriving one per stage)
    calls `dispatch_snapshot_routes()` directly instead of this function.
    """
    generation_gate.require_canonical_visual_execution_ready(
        project_dir, operation="route dispatch (canonical visual execution)")

    result = visual_routes.require_executable_routes(project_dir, operation="dispatch")
    snapshot = visual_routes.build_dispatch_snapshot(result)
    return dispatch_snapshot_routes(snapshot, overwrite=overwrite)


def dispatch_snapshot_routes(snapshot: "visual_routes.DispatchSnapshot", *,
                             overwrite: bool = False) -> int:
    """The narrow, trusted internal dispatcher (Task 2B-B2b-2b) — executes
    an ALREADY-SEALED DispatchSnapshot the caller obtained itself. This is
    what lets in-process orchestration (pipeline_agents.py) and
    `dispatch_routes()` above share the identical dispatch logic while
    staying bound to exactly one snapshot per orchestrated pass, instead of
    each stage re-deriving its own (a second load could in principle see a
    changed routes file mid-pass).

    Still performs its OWN universal canonical-execution gate check first —
    every entry point into paid/writing behavior in this codebase checks
    its own gate rather than trusting a caller to have checked, and this is
    no exception, even though every realistic caller already has. Requires
    a genuinely sealed snapshot (`isinstance(snapshot._seal, ...)`,
    enforced deeper down by build_dispatch_context() on every route) —
    never accepts a caller-assembled route list or a directly-constructed
    DispatchSnapshot as dispatch authority.
    """
    import visual_routes as _visual_routes
    if not isinstance(snapshot, _visual_routes.DispatchSnapshot):
        raise RouteError(
            f"dispatch_snapshot_routes() requires a visual_routes.DispatchSnapshot, "
            f"got {type(snapshot).__name__}")
    generation_gate.require_canonical_visual_execution_ready(
        snapshot.project_dir, operation="route dispatch (canonical visual execution)")

    images_dir = snapshot.project_dir / "images"
    targets = _preflight_targets(snapshot, images_dir, overwrite)

    # Only now, after the WHOLE target set has passed preflight, may
    # anything be created — this mkdir is the ONLY place in the entire
    # canonical dispatch path a directory is ever created; atomic_commit()
    # itself refuses if this directory is not already present (corrective
    # follow-up, item 4).
    images_dir.mkdir(parents=True, exist_ok=True)

    taken_targets = set(targets.values())
    produced = 0
    for route in snapshot.routes:
        vid = route.get("visual_asset_id")
        final_target = targets[vid]
        print(f"  {route.get('visual_type', '?'):12s} {vid}  {final_target.name}",
              end="  ", flush=True)
        ok = _dispatch_one(route, final_target, snapshot, images_dir, taken_targets)
        if not ok:
            print("FAILED")
            print(f"\n  Dispatch stopped at {vid}. Nothing further was generated.")
            print(f"\n  Recorded in {route_failures.FAILURES_NAME}. Nothing further "
                  f"was generated, and this approval no longer permits a retry.")
            return 1
        print("ok")
        produced += 1

    print(f"\n{'=' * 58}")
    print(f"  Canonical routing complete — {produced} route(s) produced.")
    print(f"{'=' * 58}\n")
    return 0


def dry_run_report(project_dir: Path) -> tuple[int, "visual_routes.ProjectRoutesLoad"]:
    """Canonical dry-run (Task 2B-B2b-2a): inspects and reports
    visual_routes.json WITHOUT executing anything and WITHOUT requiring a
    v3 approval — visual_routes.inspect_project_routes() never checks for
    one, only whether the artifact is internally honest and executable.

    No mkdir, no write, no generation, no download — inspect_project_routes()
    is read-only by its own contract. The printed report is informational
    only: it is never itself a dispatch authority. dispatch_routes() never
    calls this function and never consults its return value; it always
    re-derives its own snapshot independently through
    require_executable_routes().

    Returns (exit_code, result) — exit_code is 0 iff the artifact is
    currently executable, 1 otherwise; `result` is returned for callers
    (tests) that want the full ProjectRoutesLoad without re-inspecting.
    """
    result = visual_routes.inspect_project_routes(project_dir, operation="dry-run inspect")
    print(f"\n{'=' * 58}")
    print(f"Canonical Route Inspection (dry run) — {project_dir.name}")
    if isinstance(result.doc, dict):
        routes = result.doc.get("routes", [])
        print(f"  Total routes : {len(routes)}")
        counts: dict[str, int] = {}
        for r in routes:
            t = r.get("visual_type", "?") if isinstance(r, dict) else "?"
            counts[t] = counts.get(t, 0) + 1
        for t in sorted(counts):
            print(f"  {t:12s}: {counts[t]}")
    print(f"  Executable   : {result.is_executable}")
    if result.execution_blockers:
        print(f"\n  {len(result.execution_blockers)} blocker(s):")
        for b in result.execution_blockers:
            print(f"    - {b}")
    print(f"{'=' * 58}\n")
    print("-- DRY RUN — nothing was generated, downloaded, or written; this report "
         "authorizes nothing --")
    return (0 if result.is_executable else 1), result


def main():
    parser = argparse.ArgumentParser(
        description="Canonical image route dispatch (Task 2B-B2b-2a). "
                    "--dry-run inspects visual_routes.json only; non-dry-run "
                    "dispatch requires the canonical execution gate, which "
                    "currently always refuses (Task 2B-B2a/B2b-1)."
    )
    parser.add_argument("--project", required=True, help="Project folder (e.g. ep01)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Permit atomic replacement of an existing final asset")
    parser.add_argument("--dry-run", action="store_true",
                        help="Inspect/report visual_routes.json only — no v3 approval "
                             "required, no writes, no dispatch authority granted")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    project_dir = (
        Path(args.project) if Path(args.project).is_absolute()
        else (script_dir / args.project).resolve()
    )
    if not project_dir.is_dir():
        print(f"Project folder not found: {project_dir}")
        sys.exit(1)

    if args.dry_run:
        code, _ = dry_run_report(project_dir)
        return code

    try:
        return dispatch_routes(project_dir, overwrite=args.overwrite)
    except GateBlocked as e:
        print(f"\n{e}")
        print("\nNothing was generated, downloaded or written.")
        return 1
    except renderer_adapters.DispatchIntegrityError as e:
        print(f"\nINTERNAL DISPATCH-INTEGRITY VIOLATION — stopped immediately, "
              f"nothing further was attempted: {e}")
        return 1
    except (RouteError, visual_routes.VisualRoutesError) as e:
        print(f"\nThe canonical routing artifact cannot be executed: {e}")
        print("\nNothing was generated, downloaded or written.")
        return 1


if __name__ == "__main__":
    # The return value used to be discarded, so a dispatch failure or an
    # unresolved review item exited 0 and any caller — a shell script, CI, the
    # orchestrator — read it as success.
    sys.exit(main())
