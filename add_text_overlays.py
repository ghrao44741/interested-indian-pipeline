"""
add_text_overlays.py — PIL text overlay stage for The Interested Indian

Reads the OVERLAY field from image_prompts_one_line_per_prompt.md and burns
clean, accurate text onto each scene image. Replaces the AI-rendered text
approach (which produced typos) with pixel-perfect PIL rendering.

Visual design:
  - Bottom banner: ~85px, navy (#1A2B4C) at 85% opacity
  - Primary text: white, bold, centred, 28–32px
  - Channel badge: "The Interested Indian" in small amber text, bottom-right
  - Shot counter: faint white top-right corner (optional, --no-counter to hide)

SETUP:
    pip install pillow --break-system-packages
    (fonts downloaded automatically from Google Fonts on first run, or falls back to system fonts)

USAGE:
    # Process all images for a project
    python add_text_overlays.py --project ep01

    # Process one specific scene
    python add_text_overlays.py --project ep01 --scene SCENE-001

    # Preview only (prints what overlay each image would get, no writes)
    python add_text_overlays.py --project ep01 --dry-run

    # Overwrite already-overlaid images
    python add_text_overlays.py --project ep01 --overwrite

    # Skip the shot counter in top-right corner
    python add_text_overlays.py --project ep01 --no-counter

Reads:  {project}/image_prompts_one_line_per_prompt.md
Reads:  {project}/images/SCENE-XXX.png  (source images)
Writes: {project}/images/SCENE-XXX.png  (in-place, adds _orig backup first time)
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent

# ── Brand colours ──────────────────────────────────────────────────────────────
BANNER_COLOR   = (26, 43, 76)      # #1A2B4C navy
BANNER_OPACITY = 220               # 0–255 (220 ≈ 86%)
TEXT_COLOR     = (255, 255, 255)   # white
BADGE_COLOR    = (240, 165, 0)     # #F0A500 amber
COUNTER_COLOR  = (200, 200, 200)   # light grey

BANNER_HEIGHT  = 88                # pixels at 1280×720
PADDING_X      = 40                # left/right text padding
BADGE_TEXT     = "The Interested Indian"

# ── Font resolution ────────────────────────────────────────────────────────────

_FONT_CACHE: dict[tuple, object] = {}

def _get_font(size: int, bold: bool = False):
    """Return a PIL ImageFont at the given size. Searches common system paths."""
    from PIL import ImageFont

    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    candidates = []
    if bold:
        candidates = [
            # Windows
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/tahoma.ttf",
            # Linux / WSL
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
            # Matplotlib bundled (always present if matplotlib installed)
            str(Path(__file__).parent.parent / "Aeonium_Glow" / "fonts" / "DMSans-Bold.ttf"),
        ]
        # Also search matplotlib fonts
        try:
            import matplotlib
            mpl_fonts = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
            candidates += [
                str(mpl_fonts / "DejaVuSans-Bold.ttf"),
                str(mpl_fonts / "DejaVuSansDisplay.ttf"),
            ]
        except Exception:
            pass
    else:
        candidates = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
        try:
            import matplotlib
            mpl_fonts = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
            candidates.append(str(mpl_fonts / "DejaVuSans.ttf"))
        except Exception:
            pass

    for path in candidates:
        if Path(path).exists():
            font = ImageFont.truetype(path, size)
            _FONT_CACHE[key] = font
            return font

    # Fallback: PIL default (very small, no size control — last resort)
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ── Overlay renderer ───────────────────────────────────────────────────────────

def add_overlay(
    img_path: Path,
    overlay_text: str,
    shot_label: str = "",
    dry_run: bool = False,
    overwrite: bool = False,
    no_counter: bool = False,
) -> bool:
    """
    Burn overlay_text onto img_path in-place.
    Backs up original to SCENE-XXX_orig.png on first run.
    Returns True on success.
    """
    from PIL import Image, ImageDraw

    if not img_path.exists():
        print(f"  ⚠  {img_path.name} — not found, skipping")
        return False

    if dry_run:
        print(f"  [dry-run] {img_path.name}  →  \"{overlay_text}\"")
        return True

    # Backup original before first overlay
    orig_path = img_path.parent / (img_path.stem + "_orig.png")
    if not orig_path.exists():
        shutil.copy2(img_path, orig_path)
    elif not overwrite:
        print(f"  ⏭  {img_path.name}  (already overlaid — use --overwrite)")
        return True

    img = Image.open(orig_path if orig_path.exists() else img_path).convert("RGBA")
    W, H = img.size

    # Scale banner height proportionally if image isn't 1280×720
    banner_h = max(70, int(BANNER_HEIGHT * H / 720))

    # ── Banner layer ──────────────────────────────────────────────────────
    banner = Image.new("RGBA", (W, banner_h), (*BANNER_COLOR, BANNER_OPACITY))

    # Slight top-edge gradient: fade from transparent to opaque over 8px
    for y in range(min(8, banner_h)):
        alpha = int(BANNER_OPACITY * y / 8)
        for x in range(W):
            r, g, b, _ = banner.getpixel((x, y))
            banner.putpixel((x, y), (r, g, b, alpha))

    # Paste banner at bottom
    img.paste(banner, (0, H - banner_h), banner)

    draw = ImageDraw.Draw(img)

    # ── Primary overlay text ───────────────────────────────────────────────
    font_size = max(24, int(32 * H / 720))
    font = _get_font(font_size, bold=True)

    # Measure text to centre it
    try:
        bbox = draw.textbbox((0, 0), overlay_text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except AttributeError:
        # Older PIL
        tw, th = draw.textsize(overlay_text, font=font)

    text_x = (W - tw) // 2
    text_y = H - banner_h + (banner_h - th) // 2 - 4

    # Subtle shadow
    shadow_offset = max(1, int(2 * H / 720))
    draw.text(
        (text_x + shadow_offset, text_y + shadow_offset),
        overlay_text, font=font, fill=(0, 0, 0, 160)
    )
    draw.text((text_x, text_y), overlay_text, font=font, fill=(*TEXT_COLOR, 255))

    # ── Channel badge (bottom-right) ──────────────────────────────────────
    badge_size = max(11, int(13 * H / 720))
    badge_font = _get_font(badge_size, bold=False)
    try:
        bbbox = draw.textbbox((0, 0), BADGE_TEXT, font=badge_font)
        bw = bbbox[2] - bbbox[0]
    except AttributeError:
        bw, _ = draw.textsize(BADGE_TEXT, font=badge_font)

    draw.text(
        (W - bw - PADDING_X // 2, H - int(banner_h * 0.28)),
        BADGE_TEXT, font=badge_font, fill=(*BADGE_COLOR, 200)
    )

    # ── Shot counter (top-right corner, subtle) ───────────────────────────
    if shot_label and not no_counter:
        counter_size = max(11, int(13 * H / 720))
        counter_font = _get_font(counter_size, bold=False)
        padding = int(16 * H / 720)
        try:
            cbbox = draw.textbbox((0, 0), shot_label, font=counter_font)
            cw = cbbox[2] - cbbox[0]
        except AttributeError:
            cw, _ = draw.textsize(shot_label, font=counter_font)
        draw.text(
            (W - cw - padding, padding),
            shot_label, font=counter_font, fill=(*COUNTER_COLOR, 160)
        )

    # Save back (as RGB PNG — no alpha in final)
    img.convert("RGB").save(str(img_path), "PNG", optimize=True)
    return True


# ── Prompt file parser ─────────────────────────────────────────────────────────

def parse_overlay_map(md_path: Path) -> dict[str, dict]:
    """
    Parse image_prompts_one_line_per_prompt.md.
    Returns {filename: {"overlay": str, "shot": str}} mapping.
    Covers both grouped (SCENE-006.png → group overlay) and standalone shots.
    """
    result = {}
    text = md_path.read_text(encoding="utf-8")

    for block in re.split(r"\n{2,}", text):
        block = block.strip()
        if not block:
            continue

        m_file    = re.search(r"`([^`]+\.png)`", block)
        m_overlay = re.search(r"OVERLAY:\s*(.+?)(?:\s+CUE:|$)", block, re.DOTALL)
        m_shot    = re.search(r"\*\*SHOT\s+(\d+)\*\*", block)

        if not m_file:
            continue

        filename = m_file.group(1)
        overlay  = m_overlay.group(1).strip() if m_overlay else ""
        shot     = f"SHOT {m_shot.group(1)}" if m_shot else ""

        result[filename] = {"overlay": overlay, "shot": shot}

    return result


# ── Group → scene mapping ──────────────────────────────────────────────────────

def build_scene_overlay_map(
    overlay_map: dict[str, dict],
    images_dir: Path,
) -> dict[str, dict]:
    """
    Expand group-level overlays to per-scene overlays.
    If the prompts file has "SCENE-006.png" directly, that maps directly.
    Groups in the manifest share the same overlay text.
    Returns {scene_filename: {"overlay": str, "shot": str}}
    """
    # The prompts file now uses SCENE-XXX.png filenames for standalone shots
    # and group representative filenames for grouped shots.
    # Walk all PNGs in images_dir and resolve overlay:
    scene_map = {}
    for png in sorted(images_dir.glob("SCENE-*.png")):
        if "_orig" in png.name:
            continue
        fn = png.name
        if fn in overlay_map:
            scene_map[fn] = overlay_map[fn]
        else:
            # Try to find from manifest groups — for now, leave overlay blank
            # (will be filled by manifest lookup below)
            scene_map[fn] = {"overlay": "", "shot": ""}
    return scene_map


def load_manifest_group_map(project_dir: Path) -> dict[str, str]:
    """
    Build {scene_id → group_id} from manifest, used to inherit group overlays.
    Returns {SCENE-006 → group-01, SCENE-007 → group-01, ...}
    """
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    return {
        s["id"]: s.get("visual_group_id") or s["id"]
        for s in manifest["scenes"]
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def process_project(
    project_dir: Path,
    scene_filter: str | None,
    dry_run: bool,
    overwrite: bool,
    no_counter: bool,
):
    prompts_path = project_dir / "image_prompts_one_line_per_prompt.md"
    images_dir   = project_dir / "images"

    if not prompts_path.exists():
        print(f"❌ {prompts_path} not found — run generate_image_prompts.py first")
        sys.exit(1)

    if not images_dir.exists():
        print(f"❌ {images_dir} not found — run image generation first")
        sys.exit(1)

    overlay_map = parse_overlay_map(prompts_path)

    # Build group → overlay lookup from the prompts
    # The prompts file maps shot → file, but multiple scenes share one file when grouped.
    # We need scene_file → overlay. Since grouped scenes all share the same group image,
    # we also need to cover SCENE-006 → group-01 overlay, etc.
    # Strategy: load manifest, find which SCENE belongs to which group_id,
    # then find which prompt file has that group's image.

    manifest_path = project_dir / "manifest.json"
    scene_to_group: dict[str, str | None] = {}
    group_to_image: dict[str, str] = {}

    if manifest_path.exists():
        import json
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for s in manifest["scenes"]:
            sid  = s["id"]
            gid  = s.get("visual_group_id")
            img  = Path(s["image"]).name
            scene_to_group[sid] = gid
            if gid:
                # All grouped scenes share the first scene's image file
                if gid not in group_to_image:
                    group_to_image[gid] = img

    # Build full scene → overlay map
    # Walk images that exist
    pngs = sorted(p for p in images_dir.glob("SCENE-*.png") if "_orig" not in p.name)
    if scene_filter:
        pngs = [p for p in pngs if p.name == scene_filter or p.stem == scene_filter]

    print(f"\n{'─'*55}")
    print(f"Project : {project_dir.name}")
    print(f"Images  : {len(pngs)}")
    print(f"Overlays: {len(overlay_map)} entries in prompts file")
    print(f"{'─'*55}\n")

    done = skipped = failed = 0

    for png in pngs:
        fn = png.name
        scene_id = png.stem  # e.g. SCENE-006

        # Resolve overlay text:
        # 1. Direct match in prompts file
        # 2. Group lookup: find the group image in overlay_map
        overlay_data = overlay_map.get(fn)
        if not overlay_data or not overlay_data.get("overlay"):
            gid = scene_to_group.get(scene_id)
            if gid:
                group_img = group_to_image.get(gid)
                overlay_data = overlay_map.get(group_img, {}) if group_img else {}

        overlay_text = (overlay_data or {}).get("overlay", "")
        shot_label   = (overlay_data or {}).get("shot", "")

        if not overlay_text:
            print(f"  ⚠  {fn}  — no overlay text found, skipping")
            skipped += 1
            continue

        print(f"  {'[DRY]' if dry_run else '⏳'} {fn}  →  \"{overlay_text}\"")
        ok = add_overlay(png, overlay_text, shot_label, dry_run, overwrite, no_counter)
        if ok:
            done += 1
        else:
            failed += 1

    print(f"\n{'─'*55}")
    print(f"{'[DRY RUN] ' if dry_run else ''}Done: {done}  Skipped: {skipped}  Failed: {failed}")
    if not dry_run:
        print(f"Originals backed up as SCENE-XXX_orig.png")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project",    required=True,
                        help="Episode folder (e.g. ep01)")
    parser.add_argument("--scene",      default=None,
                        help="Process only this scene (e.g. SCENE-001 or SCENE-001.png)")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Print what would happen without writing files")
    parser.add_argument("--overwrite",  action="store_true",
                        help="Re-apply overlays even if already done")
    parser.add_argument("--no-counter", action="store_true",
                        help="Suppress shot counter in top-right corner")
    args = parser.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = PIPELINE_DIR / args.project
    if not project_dir.exists():
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("❌ PIL not found. Run: pip install pillow --break-system-packages")
        sys.exit(1)

    process_project(
        project_dir=project_dir,
        scene_filter=args.scene,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        no_counter=args.no_counter,
    )


if __name__ == "__main__":
    main()
