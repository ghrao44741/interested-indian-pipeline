"""
route_images.py — Scene type router for image generation

Reads image_prompts_one_line_per_prompt.md, classifies each scene by TYPE,
and calls the appropriate generator:

  MAP    → generate_india_map.py   (accurate GeoJSON — never AI-generated)
  CARTOON → generate_images_flux.py (xAI Grok)
  CHART  → generate_images_flux.py  (fallback; future: generate_chart.py)
  PHOTO  → generate_images_flux.py  (fallback; future: search_pexels.py)

Scenes without a TYPE field are classified by keyword matching (legacy prompts).

SETUP:
    No extra dependencies — calls existing pipeline scripts as subprocesses.

USAGE:
    python route_images.py --project test_script
    python route_images.py --project ep01 --overwrite
    python route_images.py --project ep01 --dry-run   (shows plan, no writes)
"""

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path

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
    """Fallback classification for prompts without a TYPE field."""
    text = line.lower()
    if any(re.search(kw, text) for kw in _MAP_KEYWORDS):
        return "MAP"
    if any(re.search(kw, text) for kw in _CHART_KEYWORDS):
        return "CHART"
    return "CARTOON"


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
        raw_type = _field(line, "TYPE", ["MAP_ARGS", "NARRATION", "PROMPT", "OVERLAY", "CUE"])
        raw_type = raw_type.upper() if raw_type else ""
        if raw_type not in ("CARTOON", "MAP", "CHART", "PHOTO"):
            raw_type = _classify_by_keywords(line)

        # MAP_ARGS field (only meaningful for MAP type)
        map_args = ""
        if raw_type == "MAP":
            map_args = _field(line, "MAP_ARGS", ["NARRATION", "PROMPT", "OVERLAY"])

        # Parse narration for Pexels keyword extraction
        m_narr = re.search(r'NARRATION:\s*"([^"]+)"', line)
        narration = m_narr.group(1) if m_narr else ""

        shots.append({
            "shot_num": int(m_shot.group(1)),
            "file":     m_file.group(1),
            "type":     raw_type,
            "map_args": map_args,
            "narration": narration,
        })

    return shots


# ── Generators ─────────────────────────────────────────────────────────────────

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

    result = subprocess.run(cmd, capture_output=True, text=True)
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
           "--out", str(out_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()[:200]
        print(f"✗  {err}")
        return False
    # Print any output from pexels script (photographer credit etc.)
    if result.stdout:
        for ln in result.stdout.strip().splitlines():
            print(f"    {ln}")
    return True


def run_ai_batch(project_dir: Path, script_dir: Path, overwrite: bool):
    """Call generate_images_flux.py for all non-MAP shots (it skips existing)."""
    flux_script = script_dir / "generate_images_flux.py"
    cmd = [sys.executable, str(flux_script), "--project", str(project_dir)]
    if overwrite:
        cmd.append("--overwrite")
    subprocess.run(cmd, check=False)


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

    images_dir = project_dir / "images"
    images_dir.mkdir(exist_ok=True)

    all_shots = parse_shots(prompts_path)

    # ── split by type and skip existing ───────────────────────────────────────
    map_shots     = []
    photo_shots   = []
    ai_shot_files = []   # filenames for AI batch (flux handles skip-existing itself)
    skipped       = 0

    for shot in all_shots:
        already = any(
            (images_dir / f"{Path(shot['file']).stem}{ext}").exists()
            for ext in (".png", ".jpg", ".jpeg", ".webp")
        )
        if already and not args.overwrite:
            skipped += 1
            continue
        if shot["type"] == "MAP":
            map_shots.append(shot)
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
    print(f"  PHOTO       : {len(photo_shots)} → search_pexels.py (Pexels API)")
    print(f"  AI (Grok)   : {len(ai_shot_files)} → generate_images_flux.py")
    print(f"{'═'*58}\n")

    if args.dry_run:
        print("── DRY RUN — nothing generated ──")
        for s in map_shots:
            print(f"  MAP   SHOT {s['shot_num']:02d}  {s['file']}  args: {s['map_args'] or '(none)'}")
        for s in photo_shots:
            print(f"  PHOTO SHOT {s['shot_num']:02d}  {s['file']}  narration: {s['narration'][:60]}...")
        for f in ai_shot_files:
            print(f"  AI    {f}")
        return

    if not map_shots and not ai_shot_files:
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

    # ── generate AI shots ─────────────────────────────────────────────────────
    if ai_shot_files:
        print(f"\nGenerating {len(ai_shot_files)} AI image(s) via xAI Grok...\n")
        run_ai_batch(project_dir, script_dir, args.overwrite)

    # ── summary ────────────────────────────────────────────────────────────────
    print(f"\n{'═'*58}")
    print(f"Routing complete.")
    if map_fail:
        print(f"  ⚠ {map_fail} map(s) failed — check GeoJSON state names")
        print(f"    Tip: python generate_india_map.py --list-states")
    if photo_shots:
        print(f"  Photos : {photo_ok} ✓  {photo_fail} ✗ (failed → AI fallback)")
    print(f"\nNext step:")
    print(f"  python add_text_overlays.py --project {project_dir.name}")
    print(f"{'═'*58}\n")


if __name__ == "__main__":
    main()
