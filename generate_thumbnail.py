"""
generate_thumbnail.py — YouTube Thumbnail Generator for The Interested Indian

Produces a 1280×720px PNG ready for YouTube upload.
Layout:
  • Navy (#1A2B4C) full-bleed background
  • Title text in bold white — auto-wraps, auto-sizes to fill the canvas
  • Thin amber (#F0A500) rule below the title
  • "THE INTERESTED INDIAN" footer label in amber caps
  • Optional: --accent-image PATH overlays a right-side image (map, portrait, etc.)
    at 40% canvas width — use for maps from generate_india_map.py

Font priority (first found wins):
  1. fonts/ subfolder in this directory  (drop any .ttf there)
  2. Windows system fonts  (Calibri Bold, Arial Bold)
  3. PIL default (always works — no TTF needed)

Usage:
    python generate_thumbnail.py --project ep01
    python generate_thumbnail.py --project ep01 --title "Why Bihar Gets More Money" --out ep01/thumbnail.png
    python generate_thumbnail.py --project ep01 --accent-image ep01/images/map_bihar.png

Reads title from:
  1. --title argument (highest priority)
  2. episode_state.json → data.title
  3. metadata_*.txt → VIRAL VIDEO TITLE: line
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("❌ Pillow not found.\n   Run: pip install Pillow --break-system-packages")
    sys.exit(1)

# ── Brand colours ──────────────────────────────────────────────────────────────

BG_COLOR      = (26, 43, 76)      # #1A2B4C  navy
TEXT_COLOR    = (255, 255, 255)   # white
ACCENT_COLOR  = (240, 165, 0)     # #F0A500  amber
FOOTER_BG     = (18, 30, 54)      # slightly darker navy strip

CANVAS_W, CANVAS_H = 1280, 720
SAFE_MARGIN = 64          # pixels inset from all edges

PIPELINE_DIR = Path(__file__).parent

# ── Font loading ───────────────────────────────────────────────────────────────

def _find_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    """Return the best available font at `size` pt."""
    candidates = []

    # 1. local fonts/ folder
    fonts_dir = PIPELINE_DIR / "fonts"
    if fonts_dir.exists():
        for ext in ("*.ttf", "*.otf"):
            candidates += list(fonts_dir.glob(ext))

    # 2. Windows system fonts
    win_fonts = Path("C:/Windows/Fonts")
    if win_fonts.exists():
        preferred = [
            "calibrib.ttf",   # Calibri Bold
            "arialbd.ttf",    # Arial Bold
            "arial.ttf",
            "verdanab.ttf",
            "trebucbd.ttf",
            "georgiab.ttf",
        ]
        for name in (preferred if bold else preferred[2:]):
            p = win_fonts / name
            if p.exists():
                candidates.insert(0, p)  # prefer system fonts

    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            continue

    return ImageFont.load_default()


# ── Title auto-fit ─────────────────────────────────────────────────────────────

def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Word-wrap text so each line fits within max_width pixels."""
    words = text.split()
    lines, current = [], []
    for word in words:
        test = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines or [text]


def _fit_title(draw: ImageDraw.ImageDraw, title: str, max_w: int, max_h: int) -> tuple[list[str], ImageFont.ImageFont]:
    """Binary-search the largest font size where the title still fits in the text area."""
    lo, hi, best = 40, 140, (None, None)
    while lo <= hi:
        mid = (lo + hi) // 2
        font  = _find_font(mid, bold=True)
        lines = _wrap_text(title, font, max_w, draw)
        try:
            lh = font.getbbox("Ag")[3] + 16   # line height with leading
        except AttributeError:
            lh = mid + 16
        total_h = lh * len(lines)
        if total_h <= max_h and len(lines) <= 4:
            best = (lines, font)
            lo = mid + 1
        else:
            hi = mid - 1
    if best[0] is None:
        font  = _find_font(44, bold=True)
        lines = _wrap_text(title, font, max_w, draw)
        best  = (lines, font)
    return best


# ── Image helpers ──────────────────────────────────────────────────────────────

def _load_accent(path: str) -> Image.Image | None:
    try:
        img = Image.open(path).convert("RGBA")
        return img
    except Exception as e:
        print(f"  ⚠ Could not load accent image: {e}")
        return None


def _paste_accent(canvas: Image.Image, accent: Image.Image, margin: int):
    """Paste accent image on the right half, vertically centred, preserving aspect."""
    target_w = CANVAS_W // 2 - margin
    scale    = target_w / accent.width
    new_h    = int(accent.height * scale)
    resized  = accent.resize((target_w, new_h), Image.LANCZOS)

    x = CANVAS_W // 2 + margin // 2
    y = (CANVAS_H - new_h) // 2
    # Use alpha mask if available
    mask = resized.split()[3] if resized.mode == "RGBA" else None
    canvas.paste(resized.convert("RGB"), (x, y), mask)


# ── Read episode title ─────────────────────────────────────────────────────────

def _read_title(project_dir: Path) -> str:
    # 1. episode_state.json
    state_path = project_dir / "episode_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            title = state.get("data", {}).get("title", "")
            if title:
                return title
        except Exception:
            pass

    # 2. metadata_*.txt
    for meta_file in sorted(project_dir.glob("metadata_*.txt")):
        text = meta_file.read_text(encoding="utf-8")
        m = re.search(r"VIRAL VIDEO TITLE:\s*\n(.+)", text)
        if m:
            return m.group(1).strip()

    return project_dir.name.upper()


# ── Main render ────────────────────────────────────────────────────────────────

def render_thumbnail(
    title: str,
    out_path: Path,
    accent_image_path: str | None = None,
):
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_COLOR)
    draw   = ImageDraw.Draw(canvas)

    has_accent = accent_image_path is not None
    accent     = _load_accent(accent_image_path) if has_accent else None
    if has_accent and accent is None:
        has_accent = False

    # Text area: left half if accent, else full width
    text_right = (CANVAS_W // 2 - SAFE_MARGIN // 2) if has_accent else (CANVAS_W - SAFE_MARGIN)
    text_left  = SAFE_MARGIN
    text_max_w = text_right - text_left

    # Footer strip height
    footer_h   = 56

    # Title fits in the space above the footer rule
    title_area_top = SAFE_MARGIN
    title_area_bot = CANVAS_H - footer_h - 24   # 24px gap above rule
    title_max_h    = title_area_bot - title_area_top - 32

    lines, font = _fit_title(draw, title, text_max_w, title_max_h)

    # Measure total title block height
    try:
        lh = font.getbbox("Ag")[3] + 14
    except AttributeError:
        lh = 60
    total_text_h = lh * len(lines)

    # Vertically centre the title block in the title area
    y_start = title_area_top + (title_max_h - total_text_h) // 2

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = text_left + (text_max_w - w) // 2
        # Subtle drop shadow
        draw.text((x + 3, y_start + 3), line, font=font, fill=(0, 0, 0, 120))
        draw.text((x, y_start), line, font=font, fill=TEXT_COLOR)
        y_start += lh

    # Amber rule
    rule_y = CANVAS_H - footer_h - 6
    draw.rectangle([(text_left, rule_y), (text_right, rule_y + 4)], fill=ACCENT_COLOR)

    # Footer strip
    draw.rectangle([(0, CANVAS_H - footer_h), (CANVAS_W, CANVAS_H)], fill=FOOTER_BG)

    footer_font = _find_font(28, bold=True)
    footer_text = "THE INTERESTED INDIAN"
    bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
    fw = bbox[2] - bbox[0]
    fh = bbox[3] - bbox[1]
    fx = (CANVAS_W - fw) // 2
    fy = CANVAS_H - footer_h + (footer_h - fh) // 2
    draw.text((fx, fy), footer_text, font=footer_font, fill=ACCENT_COLOR)

    # Paste accent
    if has_accent:
        _paste_accent(canvas, accent, SAFE_MARGIN)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out_path), "PNG", optimize=True)
    print(f"✓ Thumbnail saved → {out_path}  ({CANVAS_W}×{CANVAS_H}px)")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="Episode folder (e.g. ep01 or absolute path)")
    parser.add_argument("--title",   default=None,  help="Override title text (default: read from episode state)")
    parser.add_argument("--out",     default=None,  help="Output PNG path (default: <project>/thumbnail.png)")
    parser.add_argument("--accent-image", default=None, dest="accent_image",
                        help="Optional right-side image to overlay (map, portrait, etc.)")
    args = parser.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = PIPELINE_DIR / args.project
    if not project_dir.exists():
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    title    = args.title or _read_title(project_dir)
    out_path = Path(args.out) if args.out else project_dir / "thumbnail.png"

    print(f"  Title  : {title}")
    print(f"  Output : {out_path}")
    if args.accent_image:
        print(f"  Accent : {args.accent_image}")

    render_thumbnail(title, out_path, accent_image_path=args.accent_image)


if __name__ == "__main__":
    main()
