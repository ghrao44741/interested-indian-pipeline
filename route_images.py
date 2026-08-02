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
from generation_gate import GateBlocked, require_generation_ready

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

        # MAP_ARGS field (only meaningful for MAP type)
        # If a scene is classified as MAP but has no MAP_ARGS (legacy prompts pre-dating
        # the TYPE/MAP_ARGS feature), downgrade to CARTOON so xAI generates an illustrative
        # version rather than a useless blank-highlight GeoJSON map.
        map_args = ""
        if raw_type == "MAP":
            map_args = _field(line, "MAP_ARGS", ["NARRATION", "PROMPT", "OVERLAY"])
            if not map_args:
                raw_type = "CARTOON"  # fallback: no highlight args → AI is better
                print(f"  ⚠  SHOT {m_shot.group(1)}: MAP type but no MAP_ARGS — routing to AI (re-run prompts stage to get proper map args)")

        # CHART_ARGS field (only meaningful for CHART type).
        # CHART_ARGS carries an embedded JSON --data payload on a single line, which is
        # more failure-prone than MAP_ARGS' simple comma list (quoting, or a stray field
        # name inside label/event text confusing _field()'s boundary regex). Validate it
        # here at parse time — if it's missing, unparsable, or the JSON is invalid/empty,
        # downgrade to CARTOON rather than let generate_chart.py fail later as a subprocess.
        chart_args = ""
        if raw_type == "CHART":
            chart_args = _field(line, "CHART_ARGS", ["NARRATION", "PROMPT", "OVERLAY"])
            if not chart_args or not _validate_chart_args(chart_args):
                reason = "no CHART_ARGS" if not chart_args else "invalid CHART_ARGS (bad/missing --type or --data JSON)"
                print(f"  ⚠  SHOT {m_shot.group(1)}: CHART type but {reason} — routing to AI (re-run prompts stage to get proper chart args)")
                raw_type = "CARTOON"
                chart_args = ""

        # HOST_POSE carries a registry id — the router deliberately has no way to
        # name a file. An id that is not registered and approved is left in place
        # rather than silently dropped, so plan_visuals.py can queue it for review
        # instead of the shot quietly turning into a generic AI illustration.
        pose_id = ""
        scene_bound = False
        if raw_type == "HOST":
            pose_id = _field(line, "HOST_POSE",
                             ["SCENE_BOUND", "NARRATION", "PROMPT", "OVERLAY", "CUE"])
            scene_bound = _field(line, "SCENE_BOUND",
                                 ["NARRATION", "PROMPT", "OVERLAY", "CUE"]).lower() \
                in ("true", "yes", "1")
            if not pose_id:
                print(f"  !  SHOT {m_shot.group(1)}: HOST type but no HOST_POSE — "
                      f"needs review, not generating")

        # Parse narration for Pexels keyword extraction
        m_narr = re.search(r'NARRATION:\s*"([^"]+)"', line)
        narration = m_narr.group(1) if m_narr else ""

        shots.append({
            "shot_num": int(m_shot.group(1)),
            "file":     m_file.group(1),
            "type":     raw_type,
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

def run_host(shot: dict, images_dir: Path, script_dir: Path) -> bool:
    """Composite one HOST shot from its pose id.

    In-process rather than a subprocess: the compositor is the code that must be
    shown to resolve through the registry, and a subprocess boundary would make
    that harder to assert, not safer. Any refusal from the registry — unknown id,
    unapproved status, hash mismatch, scene-bound without permission — surfaces
    here as a failed shot rather than a wrong render.
    """
    out_path = images_dir / shot["file"]
    background = out_path if out_path.exists() else None
    if background is None:
        print("!  no background rendered for this shot yet — "
              "generate the background before compositing the host")
        return False
    try:
        rec = composite_character.composite(
            shot["pose_id"], background, out_path,
            scene_bound=shot.get("scene_bound", False))
    except (pose_registry.PoseError, ValueError) as e:
        print(f"x  {e}")
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


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Route image generation by scene type (MAP→GeoJSON, rest→xAI)"
    )
    parser.add_argument("--project",  required=True, help="Project folder (e.g. ep01)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing images")
    parser.add_argument("--dry-run",   action="store_true", help="Show routing plan without generating")
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    project_dir = (
        Path(args.project) if Path(args.project).is_absolute()
        else (script_dir / args.project).resolve()
    )
    if not project_dir.is_dir():
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    prompts_path = project_dir / PROMPTS_FILE
    if not prompts_path.exists():
        print(f"❌ {PROMPTS_FILE} not found in: {project_dir}")
        sys.exit(1)

    # Preflight before anything is created or dispatched. --dry-run still runs it
    # (reporting the verdict is the point of a dry run) but does not fail on it.
    try:
        require_generation_ready(project_dir, "route_images")
    except GateBlocked as e:
        print(f"\n{e}")
        if not args.dry_run:
            print("\nNothing was generated. Resolve the blockers above, then re-run.")
            sys.exit(1)
        print("\n(dry run — reporting only)")

    images_dir = project_dir / "images"
    images_dir.mkdir(exist_ok=True)

    all_shots = parse_shots(prompts_path)

    # ── split by type and skip existing ───────────────────────────────────────
    map_shots     = []
    chart_shots   = []
    photo_shots   = []
    host_shots    = []
    ai_shot_files = []   # filenames for AI batch (flux handles skip-existing itself)
    skipped       = 0

    for shot in all_shots:
        already = any(
            (images_dir / f"{Path(shot['file']).stem}{ext}").exists()
            for ext in (".png", ".jpg", ".jpeg", ".webp")
        )
        # A HOST shot composites onto its own background, so an existing file is
        # the input, not a reason to skip.
        if already and not args.overwrite and shot["type"] != "HOST":
            skipped += 1
            continue
        if shot["type"] == "HOST":
            host_shots.append(shot)
        elif shot["type"] == "MAP":
            map_shots.append(shot)
        elif shot["type"] == "CHART":
            chart_shots.append(shot)
        elif shot["type"] == "PHOTO":
            photo_shots.append(shot)
        else:
            ai_shot_files.append(shot["file"])

    # ── print plan ─────────────────────────────────────────────────────────────
    print(f"\n{'═'*58}")
    print(f"Image Router — {project_dir.name}")
    print(f"  Total shots : {len(all_shots)}")
    print(f"  Skipped     : {skipped} (already exist)")
    print(f"  MAP         : {len(map_shots)} → generate_india_map.py (GeoJSON)")
    print(f"  CHART       : {len(chart_shots)} → generate_chart.py (matplotlib)")
    print(f"  PHOTO       : {len(photo_shots)} → search_pexels.py (Pexels API)")
    print(f"  HOST        : {len(host_shots)} → composite_character.py (approved poses)")
    print(f"  AI (Grok)   : {len(ai_shot_files)} → generate_images_flux.py")
    print(f"{'═'*58}\n")

    if args.dry_run:
        print("── DRY RUN — nothing generated ──")
        for s in map_shots:
            print(f"  MAP   SHOT {s['shot_num']:02d}  {s['file']}  args: {s['map_args'] or '(none)'}")
        for s in chart_shots:
            print(f"  CHART SHOT {s['shot_num']:02d}  {s['file']}  args: {s['chart_args'] or '(none)'}")
        for s in photo_shots:
            print(f"  PHOTO SHOT {s['shot_num']:02d}  {s['file']}  narration: {s['narration'][:60]}...")
        for s in host_shots:
            print(f"  HOST  SHOT {s['shot_num']:02d}  {s['file']}  pose: {s['pose_id'] or '(none)'}"
                  f"{'  scene-bound' if s['scene_bound'] else ''}")
        for f in ai_shot_files:
            print(f"  AI    {f}")
        return

    if not map_shots and not chart_shots and not host_shots and not ai_shot_files:
        print("✓ Nothing to generate — all images already exist.")
        return

    # ── generate MAP shots ─────────────────────────────────────────────────────
    map_ok = map_fail = 0
    if map_shots:
        print(f"Generating {len(map_shots)} map image(s) via GeoJSON...\n")
        for shot in map_shots:
            label = f"[{shot['shot_num']:02d}] {shot['file']}"
            print(f"  {label}", end="  ", flush=True)
            if run_map(shot, images_dir, script_dir):
                size = (images_dir / shot["file"]).stat().st_size // 1024
                print(f"✓  ({size} KB)")
                map_ok += 1
            else:
                map_fail += 1

        print(f"\n  Maps done: {map_ok} ✓  {map_fail} ✗")

    # ── generate CHART shots ───────────────────────────────────────────────────
    chart_ok = chart_fail = 0
    if chart_shots:
        print(f"Generating {len(chart_shots)} chart image(s) via matplotlib...\n")
        for shot in chart_shots:
            label = f"[{shot['shot_num']:02d}] {shot['file']}"
            print(f"  {label}", end="  ", flush=True)
            if run_chart(shot, images_dir, script_dir):
                size = (images_dir / shot["file"]).stat().st_size // 1024
                print(f"✓  ({size} KB)")
                chart_ok += 1
            else:
                chart_fail += 1

        print(f"\n  Charts done: {chart_ok} ✓  {chart_fail} ✗")

    # ── generate PHOTO shots → Pexels ─────────────────────────────────────────
    photo_ok = photo_fail = 0
    if photo_shots:
        pexels_script = script_dir / "search_pexels.py"
        if not pexels_script.exists():
            print(f"⚠  search_pexels.py not found — routing PHOTO shots to AI instead")
            ai_shot_files.extend(s["file"] for s in photo_shots)
            photo_shots = []
        else:
            print(f"Fetching {len(photo_shots)} photo(s) via Pexels...\n")
            for shot in photo_shots:
                label = f"[{shot['shot_num']:02d}] {shot['file']}"
                print(f"  {label}", end="  ", flush=True)
                if run_pexels(shot, images_dir, script_dir):
                    size = (images_dir / shot["file"]).stat().st_size // 1024
                    print(f"✓  ({size} KB)")
                    photo_ok += 1
                else:
                    # Fallback: queue for AI generation
                    print(f"  → falling back to AI")
                    ai_shot_files.append(shot["file"])
                    photo_fail += 1

    # ── composite HOST shots ──────────────────────────────────────────────────
    host_ok = host_fail = 0
    if host_shots:
        print(f"\nCompositing {len(host_shots)} host shot(s) from approved poses...\n")
        for shot in host_shots:
            print(f"  [{shot['shot_num']:02d}] {shot['file']}  pose={shot['pose_id'] or '(none)'}",
                  end="  ", flush=True)
            if shot["pose_id"] and run_host(shot, images_dir, script_dir):
                print(f"✓  ({shot['placement']['side']})")
                host_ok += 1
            else:
                if not shot["pose_id"]:
                    print("✗  no HOST_POSE id")
                host_fail += 1
        print(f"\n  Host shots done: {host_ok} ✓  {host_fail} ✗")

    # ── generate AI shots ─────────────────────────────────────────────────────
    ai_batch_ok = True
    if ai_shot_files:
        print(f"\nGenerating {len(ai_shot_files)} AI image(s) via xAI Grok...\n")
        ai_batch_ok = run_ai_batch(project_dir, script_dir, args.overwrite)

    # ── summary ────────────────────────────────────────────────────────────────
    print(f"\n{'═'*58}")
    print(f"Routing complete.")
    if ai_shot_files and not ai_batch_ok:
        print(f"  ❌ AI image batch ({len(ai_shot_files)} shots) FAILED — see error above. "
              f"None of these images were generated or updated.")
    if map_fail:
        print(f"  ⚠ {map_fail} map(s) failed — check GeoJSON state names")
        print(f"    Tip: python generate_india_map.py --list-states")
    if chart_fail:
        print(f"  ⚠ {chart_fail} chart(s) failed — check --type/--data JSON")
        print(f"    Tip: python generate_chart.py --type bar --example")
    if photo_shots:
        print(f"  Photos : {photo_ok} ✓  {photo_fail} ✗ (failed → AI fallback)")
    if host_fail:
        print(f"  ⚠ {host_fail} host shot(s) failed — the pose was refused by the "
              f"registry or had no id. Nothing was rendered for them.")
        print(f"    Tip: python composite_character.py --list-poses")
    print(f"\nNext step:")
    print(f"  python add_text_overlays.py --project {project_dir.name}")
    print(f"{'═'*58}\n")


if __name__ == "__main__":
    main()
