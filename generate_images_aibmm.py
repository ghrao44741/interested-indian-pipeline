"""
generate_images_aibmm.py — Scene image generator using OpenAI GPT Image 2

Generates mascot and general illustration scenes for The Interested Indian.
Uses the locked mascot_reference.png as a style anchor so the character stays
consistent across all episodes.

Scene type routing:
  mascot  → images.edit() with mascot_reference.png → character stays consistent
  general → images.generate() with style description prepended to prompt
  map     → generate_india_map.py    (NOT this script)
  chart   → generate_chart.py        (NOT this script)
  photo   → search_pexels.py         (NOT this script)

SETUP:
    pip install openai pillow requests --break-system-packages
    Add OPENAI_API_KEY=your_key to .env

USAGE:
    python generate_images_aibmm.py --project ep01
    python generate_images_aibmm.py --project ep01 --overwrite
    python generate_images_aibmm.py --project ep01 --scene-type mascot
    python generate_images_aibmm.py --test  (generates one test image)

Reads:  {project}/image_prompts_one_line_per_prompt.md
Writes: {project}/images/SCENE-XXX.png  (1280×720)
"""

import argparse
import io
import os
import re
import sys
import time
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent

# ── Mascot reference ───────────────────────────────────────────────────────────

MASCOT_REFERENCE_LOCAL = PIPELINE_DIR / "mascot_reference.png"
MASCOT_REFERENCE_URL   = (
    "https://rqkumunldqvmynqxibca.supabase.co/storage/v1/object/public/"
    "generated-images/adhoc-1784824975374.png"
)

# Style prefix prepended to ALL prompts to anchor the visual DNA
STYLE_PREFIX = (
    "Flat digital cartoon illustration, warm cream background (#FAF7F2), "
    "bold black outlines, vibrant colors, 16:9 aspect ratio. "
    "Channel style: The Interested Indian — friendly, approachable, educational. "
)

# Keywords used to auto-classify scenes
_MAP_KW    = ["map", "region", "state", "district", "geography", "india map", "territory"]
_CHART_KW  = ["chart", "bar chart", "graph", "pie chart", "data", "percentage", "statistic", "infographic"]
_PHOTO_KW  = ["parliament", "supreme court", "protest", "crowd", "historical photo",
               "real photo", "city skyline", "building", "newspaper"]
_MASCOT_KW = ["mascot", "character", "confused", "shocked", "pointing", "explaining",
               "shrug", "reaction", "reading", "cartoon figure"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        return key
    env_path = PIPELINE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY"):
                key = line.split("=", 1)[-1].strip().strip('"').strip("'")
                if key:
                    return key
    return ""


def _ensure_mascot_reference() -> Path | None:
    """Return path to mascot reference PNG, downloading if needed."""
    if MASCOT_REFERENCE_LOCAL.exists():
        return MASCOT_REFERENCE_LOCAL
    print(f"  ⚠  mascot_reference.png not found at {MASCOT_REFERENCE_LOCAL}")
    print(f"     Run in PowerShell:")
    print(f"     Invoke-WebRequest \"{MASCOT_REFERENCE_URL}\" -OutFile \"{MASCOT_REFERENCE_LOCAL}\"")
    return None


def classify_scene(prompt: str) -> str:
    p = prompt.lower()
    if any(k in p for k in _MAP_KW):    return "map"
    if any(k in p for k in _CHART_KW):  return "chart"
    if any(k in p for k in _PHOTO_KW):  return "photo"
    if any(k in p for k in _MASCOT_KW): return "mascot"
    return "mascot"   # default: mascot-style illustration


def _resize_to_16_9(image_bytes: bytes, out_path: Path):
    """Crop/resize image bytes to 1280×720 and save as PNG."""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    target_w, target_h = 1280, 720
    target_r = target_w / target_h
    src_r = w / h
    if src_r > target_r:
        new_w = int(h * target_r)
        img = img.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
    elif src_r < target_r:
        new_h = int(w / target_r)
        img = img.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))
    img = img.resize((target_w, target_h), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG", optimize=True)


# ── OpenAI image generation ────────────────────────────────────────────────────

def generate_mascot_scene(prompt: str, client, out_path: Path) -> bool:
    """
    Use images.edit() with mascot reference PNG so the character stays consistent.
    Passes the mascot sheet as the style anchor image.
    """
    mascot_path = _ensure_mascot_reference()
    if mascot_path is None:
        print("  ⚠  Falling back to images.generate() without mascot reference")
        return generate_general_scene(prompt, client, out_path)

    full_prompt = (
        STYLE_PREFIX
        + "Use the mascot character from the reference image — same round body, "
        + "amber glasses, spiky hair, cream kurta, pajama trousers. "
        + prompt
    )

    try:
        with open(str(mascot_path), "rb") as f:
            response = client.images.edit(
                model="gpt-image-2",
                image=f,
                prompt=full_prompt,
                size="1536x1024",
                n=1,
            )
        image_data = _extract_image(response)
        if image_data:
            _resize_to_16_9(image_data, out_path)
            return True
    except Exception as e:
        print(f"  ⚠  images.edit() failed ({e}), falling back to generate()")

    return generate_general_scene(prompt, client, out_path)


def generate_general_scene(prompt: str, client, out_path: Path) -> bool:
    """Use images.generate() for scenes without a mascot reference requirement."""
    full_prompt = STYLE_PREFIX + prompt

    try:
        response = client.images.generate(
            model="gpt-image-2",
            prompt=full_prompt,
            size="1536x1024",
            quality="high",
            n=1,
        )
        image_data = _extract_image(response)
        if image_data:
            _resize_to_16_9(image_data, out_path)
            return True
    except Exception as e:
        print(f"  ✗  images.generate() failed: {e}")

    return False


def _extract_image(response) -> bytes | None:
    """Extract PNG bytes from an OpenAI images response (url or b64_json)."""
    import base64, requests as req
    item = response.data[0]
    if hasattr(item, "b64_json") and item.b64_json:
        return base64.b64decode(item.b64_json)
    if hasattr(item, "url") and item.url:
        r = req.get(item.url, timeout=60)
        r.raise_for_status()
        return r.content
    return None


# ── Prompt file parser ─────────────────────────────────────────────────────────

def _parse_prompts(md_path: Path) -> list[dict]:
    shots = []
    for block in md_path.read_text(encoding="utf-8").split("\n\n"):
        block = block.strip()
        if not block:
            continue
        m_file   = re.search(r"`([^`]+\.png)`", block)
        m_prompt = re.search(r"PROMPT:\s*(.+?)(?:\s+OVERLAY:|\s+CUE:|$)", block, re.DOTALL)
        m_shot   = re.search(r"\*\*SHOT (\d+)\*\*", block)
        if not m_file:
            continue
        prompt = m_prompt.group(1).strip() if m_prompt else ""
        shots.append({
            "shot_num":   int(m_shot.group(1)) if m_shot else 0,
            "file":       m_file.group(1),
            "prompt":     prompt,
            "scene_type": classify_scene(prompt),
        })
    return shots


# ── Main batch generator ───────────────────────────────────────────────────────

def generate_images(project_dir: Path, scene_type_filter: str | None,
                    overwrite: bool, client):
    prompts_path = project_dir / "image_prompts_one_line_per_prompt.md"
    images_dir   = project_dir / "images"

    if not prompts_path.exists():
        print(f"❌ {prompts_path} not found — run generate_image_prompts.py first")
        sys.exit(1)

    images_dir.mkdir(parents=True, exist_ok=True)
    shots = _parse_prompts(prompts_path)

    if scene_type_filter:
        shots = [s for s in shots if s["scene_type"] == scene_type_filter]

    print(f"\n{'─'*55}")
    print(f"Project : {project_dir.name}")
    print(f"Shots   : {len(shots)}{f'  (filtered: {scene_type_filter})' if scene_type_filter else ''}")
    print(f"Mascot  : {'✓ found' if MASCOT_REFERENCE_LOCAL.exists() else '⚠ missing'}")
    print(f"{'─'*55}\n")

    done = skipped = failed = 0

    for shot in shots:
        out_path = images_dir / shot["file"]
        stype    = shot["scene_type"]

        if out_path.exists() and not overwrite:
            print(f"  ⏭  SHOT {shot['shot_num']:02d}  [{stype}]  {shot['file']}  (exists)")
            skipped += 1
            continue

        if stype in ("map", "chart", "photo"):
            print(f"  ⚙  SHOT {shot['shot_num']:02d}  [{stype}]  {shot['file']}  → use dedicated generator")
            skipped += 1
            continue

        print(f"  ⏳ SHOT {shot['shot_num']:02d}  [{stype}]  {shot['file']}")

        ok = (generate_mascot_scene(shot["prompt"], client, out_path)
              if stype == "mascot"
              else generate_general_scene(shot["prompt"], client, out_path))

        if ok:
            size_kb = out_path.stat().st_size // 1024
            print(f"  ✓  SHOT {shot['shot_num']:02d}  {shot['file']}  ({size_kb}KB)")
            done += 1
        else:
            print(f"  ✗  SHOT {shot['shot_num']:02d}  {shot['file']}  FAILED")
            failed += 1

        time.sleep(1.5)   # rate limit buffer

    print(f"\n{'─'*55}")
    print(f"Done: {done}  Skipped: {skipped}  Failed: {failed}")
    print(f"Images → {images_dir}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project",    default=None,
                        help="Episode folder (e.g. ep01)")
    parser.add_argument("--scene-type", default=None, dest="scene_type",
                        choices=["mascot", "general"],
                        help="Only generate scenes of this type")
    parser.add_argument("--overwrite",  action="store_true",
                        help="Regenerate even if output already exists")
    parser.add_argument("--test",       action="store_true",
                        help="Generate one test image to verify the setup")
    args = parser.parse_args()

    api_key = _get_api_key()
    if not api_key:
        print("❌ OPENAI_API_KEY not set. Add it to .env: OPENAI_API_KEY=sk-...")
        sys.exit(1)

    try:
        from openai import OpenAI
    except ImportError:
        print("❌ openai not found. Run: pip install openai --break-system-packages")
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    # ── Test mode ──
    if args.test:
        out = PIPELINE_DIR / "test_aibmm_scene.png"
        print("\n  Test: generating one mascot scene…")
        ok = generate_mascot_scene(
            "Chubby Indian cartoon mascot with amber glasses sitting at a desk, "
            "reading a large scroll labeled 'ARTICLE 356', one eyebrow raised in "
            "surprise. Warm cream background. Flat cartoon style.",
            client, out
        )
        if ok:
            print(f"\n  ✓ Test image saved → {out}")
            print("    Open it to verify mascot consistency and style.")
        else:
            print("\n  ✗ Test failed — check OPENAI_API_KEY and try again.")
        return

    if not args.project:
        parser.error("Provide --project ep01 (or --test to verify setup)")

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = PIPELINE_DIR / args.project
    if not project_dir.exists():
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    generate_images(project_dir, args.scene_type, args.overwrite, client)


if __name__ == "__main__":
    main()
