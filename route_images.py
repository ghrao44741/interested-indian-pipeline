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
import re
import shlex
import subprocess
import sys
from pathlib import Path

import composite_character
import pose_registry
from generation_gate import (GateBlocked, require_generation_ready,
                             require_identity_ready)

PIPELINE_DIR = Path(__file__).parent
PROMPTS_FILE = "image_prompts_one_line_per_prompt.md"

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


def _classify_by_keywords(line: str) -> str:
    """Fallback classification for prompts without a TYPE field.

    Only searches the PROMPT field, NOT the full line.  Narration text frequently
    mentions 'union territory', 'India map', etc. — searching the whole line causes
    cartoon scenes to be mis-classified as MAP, generating generic geography images
    with no highlights instead of the intended illustration.
    """
    # Extract only the PROMPT field (between PROMPT: and OVERLAY: / CUE: / end)
    m = re.search(r'\bPROMPT:\s*(.+?)(?:\s+OVERLAY:|\s+CUE:|$)', line, re.IGNORECASE)
    text = (m.group(1) if m else line).lower()
    if any(re.search(kw, text) for kw in _MAP_KEYWORDS):
        return "MAP"
    if any(re.search(kw, text) for kw in _CHART_KEYWORDS):
        return "CHART"
    return "CARTOON"


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


def parse_shots(prompts_path: Path) -> list[dict]:
    """Parse every shot from the one-line prompts file."""
    shots = []
    for line in prompts_path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("**SHOT"):
            continue

        m_shot = re.search(r"\*\*SHOT\s+(\d+)\*\*", line)
        m_file = re.search(r"`([^`]+\.png)`", line)
        if not m_shot or not m_file:
            continue

        # TYPE field (new prompts)
        raw_type = _field(line, "TYPE", ["MAP_ARGS", "CHART_ARGS", "HOST_POSE",
                                         "SCENE_BOUND", "NARRATION", "PROMPT",
                                         "OVERLAY", "CUE"])
        raw_type = raw_type.upper() if raw_type else ""
        if raw_type not in ("CARTOON", "MAP", "CHART", "PHOTO", "HOST"):
            raw_type = _classify_by_keywords(line)

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
            elif pose_id not in approved_pose_ids():
                flag(f"HOST_POSE {pose_id!r} is not an approved registry id")

        # Parse narration for Pexels keyword extraction
        m_narr = re.search(r'NARRATION:\s*"([^"]+)"', line)
        narration = m_narr.group(1) if m_narr else ""

        shots.append({
            "shot_num": int(m_shot.group(1)),
            "file":     m_file.group(1),
            "type":     raw_type,
            "planned_type": planned_type,
            "needs_review": needs_review,
            "review_reason": review_reason,
            "map_args": map_args,
            "chart_args": chart_args,
            "pose_id":  pose_id,        # id only — never a filesystem path
            "scene_bound": scene_bound,
            "narration": narration,
        })

    return shots


def approved_pose_ids() -> set[str]:
    """Pose ids the router may reference, from the registry — never from disk."""
    return set(pose_registry.list_poses())


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
    shots = parse_shots(project_dir / PROMPTS_FILE)
    review = [{"shot": s["shot_num"], "file": s["file"],
               "planned_route": s["planned_type"], "reason": s["review_reason"]}
              for s in shots if s["needs_review"]]
    return shots, review


def dispatch_routes(project_dir: Path, script_dir: Path, shots: list[dict],
                    overwrite: bool) -> int:
    """Generation-gated. The first thing that can spend or write episode artwork.

    The gate runs before the images directory is created and before any generator
    is invoked, so a project without approval leaves no trace of having tried.
    """
    require_generation_ready(project_dir, "route dispatch")

    review = [s for s in shots if s["needs_review"]]
    if review:
        # Reaching here would mean a plan was approved and then a shot went bad,
        # or that classification was re-run against edited prompts. Either way the
        # approved plan no longer describes what is about to be generated.
        print(f"\n{len(review)} shot(s) need review - routing stops. The approved "
              f"plan does not cover them:")
        for s in review:
            print(f"    SHOT {s['shot_num']:02d} ({s['planned_type']}): {s['review_reason']}")
        print("\nRe-plan with plan_visuals.py and approve the new plan.")
        return 1

    images_dir = project_dir / "images"
    images_dir.mkdir(exist_ok=True)

    map_shots, chart_shots, photo_shots, host_shots, ai_shots = [], [], [], [], []
    buckets = {"MAP": map_shots, "CHART": chart_shots,
               "PHOTO": photo_shots, "HOST": host_shots}
    skipped = 0
    for shot in shots:
        already = any((images_dir / f"{Path(shot['file']).stem}{ext}").exists()
                      for ext in (".png", ".jpg", ".jpeg", ".webp"))
        # A HOST shot composites onto its own background, so an existing file is
        # the input, not a reason to skip.
        if already and not overwrite and shot["type"] != "HOST":
            skipped += 1
            continue
        buckets.get(shot["type"], ai_shots).append(shot)

    print(f"\n{'=' * 58}")
    print(f"Dispatch - {project_dir.name}   (Checkpoint 3 approved)")
    print(f"  Skipped : {skipped} (already exist)")
    print(f"  MAP {len(map_shots)} - CHART {len(chart_shots)} - PHOTO {len(photo_shots)}"
          f" - HOST {len(host_shots)} - AI {len(ai_shots)}")
    print(f"{'=' * 58}\n")

    failures = []

    for shot in map_shots:
        print(f"  MAP   [{shot['shot_num']:02d}] {shot['file']}", end="  ", flush=True)
        ok = run_map(shot, images_dir, script_dir)
        print("ok" if ok else "FAILED")
        if not ok:
            failures.append((shot, "map render failed - check GeoJSON state names"))

    for shot in chart_shots:
        print(f"  CHART [{shot['shot_num']:02d}] {shot['file']}", end="  ", flush=True)
        ok = run_chart(shot, images_dir, script_dir)
        print("ok" if ok else "FAILED")
        if not ok:
            failures.append((shot, "chart render failed - check --type/--data"))

    for shot in photo_shots:
        print(f"  PHOTO [{shot['shot_num']:02d}] {shot['file']}", end="  ", flush=True)
        ok = run_pexels(shot, images_dir, script_dir)
        print("ok" if ok else "FAILED")
        if not ok:
            # No AI fallback. A PHOTO shot asks for a real photograph of a real
            # place; substituting a generated illustration answers a different
            # question, costs money, and changes the approved cost profile -
            # silently, at the moment of failure, with nobody watching.
            failures.append((shot, "Pexels returned nothing usable - a PHOTO shot "
                                   "must not be replaced by a generated image"))

    for shot in host_shots:
        print(f"  HOST  [{shot['shot_num']:02d}] {shot['file']} pose={shot['pose_id']}",
              end="  ", flush=True)
        ok = run_host(shot, project_dir, images_dir)
        print(f"ok ({shot['placement']['side']})" if ok else "FAILED")
        if not ok:
            failures.append((shot, "pose refused by the registry, or no background"))

    ai_ok = True
    if ai_shots:
        print(f"\n  AI batch: {len(ai_shots)} shot(s) via generate_images_flux.py\n")
        ai_ok = run_ai_batch(project_dir, script_dir, overwrite)

    print(f"\n{'=' * 58}")
    if failures:
        print(f"  {len(failures)} shot(s) FAILED and were not rerouted:")
        for shot, why in failures:
            print(f"    SHOT {shot['shot_num']:02d} ({shot['type']}) {shot['file']}: {why}")
        print("\n  These are NEEDS_REVIEW. Fix the route or its arguments, re-plan, "
              "and approve the new plan.")
    if not ai_ok:
        print("  AI batch failed - none of those images were generated.")
    if not failures and ai_ok:
        print("  Routing complete.")
        print(f"\n  Next: python add_text_overlays.py --project {project_dir.name}")
    print(f"{'=' * 58}\n")
    return 0 if (not failures and ai_ok) else 1


def main():
    parser = argparse.ArgumentParser(
        description="Route image generation by scene type"
    )
    parser.add_argument("--project", required=True, help="Project folder (e.g. ep01)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing images")
    parser.add_argument("--dry-run", action="store_true",
                        help="Classify and report only - identity gate only, no writes")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    project_dir = (
        Path(args.project) if Path(args.project).is_absolute()
        else (script_dir / args.project).resolve()
    )
    if not project_dir.is_dir():
        print(f"Project folder not found: {project_dir}")
        sys.exit(1)
    if not (project_dir / PROMPTS_FILE).exists():
        print(f"{PROMPTS_FILE} not found in: {project_dir}")
        sys.exit(1)

    # -- phase 1: classification (identity only) ------------------------------
    try:
        shots, review = classify(project_dir)
    except GateBlocked as e:
        print(f"\n{e}")
        print("\nNothing was classified, written or generated.")
        sys.exit(1)

    counts = {}
    for s in shots:
        counts[s["type"]] = counts.get(s["type"], 0) + 1
    print(f"\n{'=' * 58}")
    print(f"Image Router - {project_dir.name}")
    print(f"  Total shots  : {len(shots)}")
    for t_ in sorted(counts):
        print(f"  {t_:12s}: {counts[t_]}")
    print(f"  NEEDS_REVIEW : {len(review)}")
    print(f"{'=' * 58}\n")

    if args.dry_run:
        print("-- DRY RUN - nothing generated --")
        for s in shots:
            extra = (s["map_args"] or s["chart_args"] or s["pose_id"] or "")
            flag = ("  NEEDS_REVIEW: " + s["review_reason"]) if s["needs_review"] else ""
            print(f"  {s['type']:8s} SHOT {s['shot_num']:02d}  {s['file']}  "
                  f"{extra[:50]}{flag}")
        return 0

    if review:
        print(f"{len(review)} shot(s) need review before anything can be generated:")
        for r in review:
            print(f"    SHOT {r['shot']:02d} ({r['planned_route']}): {r['reason']}")
        print("\nNo route was substituted. Fix the prompts, re-run plan_visuals.py, "
              "and approve the new plan.")
        return 1

    # -- phase 2: dispatch (requires Checkpoint 3) ----------------------------
    try:
        return dispatch_routes(project_dir, script_dir, shots, args.overwrite)
    except GateBlocked as e:
        print(f"\n{e}")
        print("\nNothing was generated, downloaded or written.")
        return 1


if __name__ == "__main__":
    main()
