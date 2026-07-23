"""
generate_images_aibmm.py — Scene image generator using AIBMM (GPT Image 2)

Replaces/complements generate_images_flux.py for mascot and explanation scenes.
Uses the locked mascot reference image so the character stays consistent
across all episodes.

Scene type routing:
  mascot    → AIBMM with mascot_reference.png as style anchor
  map       → generate_india_map.py  (geopandas — NOT this script)
  chart     → generate_chart.py      (matplotlib — NOT this script)
  photo     → search_pexels.py       (Pexels API — NOT this script)
  default   → AIBMM without reference (general illustration)

Usage:
    python generate_images_aibmm.py --project ep01
    python generate_images_aibmm.py --project ep01 --scene-type mascot
    python generate_images_aibmm.py --project ep01 --overwrite

Reads:  ep01/image_prompts_one_line_per_prompt.md
Writes: ep01/images/SCENE-XXX.png

Prerequisites:
    pip install requests pillow --break-system-packages
    Set AIBMM_API_KEY in .env (or environment)
    Run Invoke-WebRequest to save mascot_reference.png (see README)
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("❌ requests not found. Run: pip install requests --break-system-packages")
    sys.exit(1)

PIPELINE_DIR = Path(__file__).parent

# ── Mascot reference ───────────────────────────────────────────────────────────

# Public URL of the locked mascot reference sheet (GPT Image 2, 2026-07-23)
# This is passed as a style anchor on every mascot scene generation.
MASCOT_REFERENCE_URL = (
    "https://rqkumunldqvmynqxibca.supabase.co/storage/v1/object/public/"
    "generated-images/adhoc-1784824975374.png"
)

# Local copy (downloaded once via PowerShell — see module docstring)
MASCOT_REFERENCE_LOCAL = PIPELINE_DIR / "mascot_reference.png"

# ── AIBMM API ─────────────────────────────────────────────────────────────────

AIBMM_API_BASE = "https://api.aibmm.com"   # placeholder — replace with real endpoint

# Scene type keywords (used to auto-classify if scene_type not in manifest)
MASCOT_KEYWORDS = [
    "mascot", "character", "cartoon", "figure", "confused", "shocked",
    "pointing", "explaining", "shrug", "reaction",
]
MAP_KEYWORDS    = ["map", "region", "state", "district", "geography", "india map"]
CHART_KEYWORDS  = ["chart", "bar chart", "pie chart", "graph", "data", "percentage", "statistic"]
PHOTO_KEYWORDS  = ["parliament", "supreme court", "protest", "crowd", "building", "city", "real photo"]


def classify_scene(prompt: str) -> str:
    """Auto-classify a scene prompt into: mascot | map | chart | photo | general."""
    p = prompt.lower()
    if any(k in p for k in MAP_KEYWORDS):
        return "map"
    if any(k in p for k in CHART_KEYWORDS):
        return "chart"
    if any(k in p for k in PHOTO_KEYWORDS):
        return "photo"
    if any(k in p for k in MASCOT_KEYWORDS):
        return "mascot"
    return "mascot"   # default: use mascot style for general illustration scenes


def _parse_prompts(md_path: Path) -> list[dict]:
    """Parse image_prompts_one_line_per_prompt.md into a list of shot dicts."""
    shots = []
    text  = md_path.read_text(encoding="utf-8")

    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue

        # Extract FILE field
        m_file = re.search(r"`([^`]+\.png)`", block)
        if not m_file:
            continue
        filename = m_file.group(1)

        # Extract PROMPT field
        m_prompt = re.search(r"PROMPT:\s*(.+?)(?:\s+OVERLAY:|\s+CUE:|$)", block, re.DOTALL)
        prompt   = m_prompt.group(1).strip() if m_prompt else ""

        # Extract SHOT number
        m_shot = re.search(r"\*\*SHOT (\d+)\*\*", block)
        shot_num = int(m_shot.group(1)) if m_shot else 0

        shots.append({
            "shot_num":  shot_num,
            "file":      filename,
            "prompt":    prompt,
            "scene_type": classify_scene(prompt),
        })

    return shots


# ── Image generation via AIBMM ────────────────────────────────────────────────

def _generate_via_aibmm(prompt: str, use_mascot_ref: bool, api_key: str) -> bytes | None:
    """
    Call AIBMM generate_image endpoint.
    Returns raw PNG bytes on success, None on failure.

    NOTE: This uses the AIBMM REST API directly.
    The exact endpoint / payload shape should be confirmed from AIBMM docs.
    Adjust AIBMM_API_BASE and payload keys if needed.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    payload: dict = {
        "prompt": prompt,
        "model":  "gpt-image-2",
        "size":   "1792x1024",   # closest 16:9 option in GPT Image 2
    }
    if use_mascot_ref:
        payload["image_urls"] = [MASCOT_REFERENCE_URL]

    try:
        resp = requests.post(
            f"{AIBMM_API_BASE}/v1/generate",
            headers=headers,
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        image_url = data.get("image_url") or data.get("url")
        if not image_url:
            print(f"  ⚠ No image_url in response: {data}")
            return None
        img_resp = requests.get(image_url, timeout=60)
        img_resp.raise_for_status()
        return img_resp.content
    except Exception as e:
        print(f"  ⚠ AIBMM call failed: {e}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def generate_images(project_dir: Path, scene_type_filter: str | None, overwrite: bool, api_key: str):
    prompts_path = project_dir / "image_prompts_one_line_per_prompt.md"
    images_dir   = project_dir / "images"

    if not prompts_path.exists():
        print(f"❌ Prompts file not found: {prompts_path}")
        print("   Run generate_image_prompts.py first.")
        sys.exit(1)

    images_dir.mkdir(parents=True, exist_ok=True)
    shots = _parse_prompts(prompts_path)

    if scene_type_filter:
        shots = [s for s in shots if s["scene_type"] == scene_type_filter]
        print(f"Filtered to {len(shots)} '{scene_type_filter}' shots.")

    print(f"\n{'─'*55}")
    print(f"Project : {project_dir.name}")
    print(f"Shots   : {len(shots)}")
    print(f"Mascot ref: {MASCOT_REFERENCE_URL[:60]}…")
    print(f"{'─'*55}\n")

    done = skipped = failed = 0

    for shot in shots:
        out_path = images_dir / shot["file"]

        if out_path.exists() and not overwrite:
            print(f"  ⏭  SHOT {shot['shot_num']:02d}  {shot['file']}  (exists)")
            skipped += 1
            continue

        stype = shot["scene_type"]
        if stype in ("map", "chart", "photo"):
            print(f"  ⚙  SHOT {shot['shot_num']:02d}  {shot['file']}  [{stype}] → use dedicated generator")
            skipped += 1
            continue

        use_ref = (stype == "mascot")
        print(f"  ⏳ SHOT {shot['shot_num']:02d}  {shot['file']}  [{stype}]  ref={'yes' if use_ref else 'no'}")

        png_bytes = _generate_via_aibmm(shot["prompt"], use_ref, api_key)
        if png_bytes:
            out_path.write_bytes(png_bytes)
            print(f"  ✓  SHOT {shot['shot_num']:02d}  {shot['file']}  ({len(png_bytes)//1024}KB)")
            done += 1
        else:
            print(f"  ✗  SHOT {shot['shot_num']:02d}  {shot['file']}  FAILED")
            failed += 1

        time.sleep(2)   # rate limit buffer

    print(f"\n{'─'*55}")
    print(f"Done: {done}  Skipped: {skipped}  Failed: {failed}")
    print(f"Images in: {images_dir}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project",    required=True, help="Episode folder (e.g. ep01)")
    parser.add_argument("--scene-type", default=None,  dest="scene_type",
                        choices=["mascot", "general"],
                        help="Only process scenes of this type (default: all AIBMM scenes)")
    parser.add_argument("--overwrite",  action="store_true", help="Overwrite existing images")
    args = parser.parse_args()

    api_key = os.environ.get("AIBMM_API_KEY", "")
    if not api_key:
        print("⚠  AIBMM_API_KEY not set — image generation will likely fail.")
        print("   Add it to your .env or set it as an environment variable.")

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = PIPELINE_DIR / args.project

    if not project_dir.exists():
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    generate_images(project_dir, args.scene_type, args.overwrite, api_key)


if __name__ == "__main__":
    main()
