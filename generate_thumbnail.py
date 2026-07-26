"""
generate_thumbnail.py — YouTube Thumbnail Generator for The Interested Indian

Produces a 1280×720px PNG ready for YouTube upload.
Layout:
  • Base art composited from common/thumbnails/base_light.png / base_dark.png
    (mascot + India map/silhouette + baked-in "THE INTERESTED INDIAN" footer),
    cover-fit-cropped to 1280×720. Falls back to a solid-colour canvas if missing.
  • Title text in bold in the natural gap between the mascot and map/silhouette art,
    with a translucent scrim behind it for legibility — auto-wraps, auto-sizes
  • Small episode-number badge (e.g. "EP01") in the top-right corner
  • Fallback path only (no base art found): thin accent rule + own footer strip +
    optional --accent-image PATH overlaying a right-side image at 40% canvas width

Themes (--theme):
  dark  — Navy (#1A2B4C) bg, white text, amber (#F0A500) accent  [default]
  light — Cream (#FAF7F2) bg, dark brown (#2C1A0E) text, crimson (#C0392B) accent
  auto  — Alternates dark/light by episode number (odd=dark, even=light).
           Episode number parsed from project folder name: ep01→1, ep03→3, etc.

Font priority (first found wins):
  1. fonts/ subfolder in this directory  (drop any .ttf there)
  2. Windows system fonts  (Calibri Bold, Arial Bold)
  3. PIL default (always works — no TTF needed)

Usage:
    python generate_thumbnail.py --project ep01
    python generate_thumbnail.py --project ep01 --theme light
    python generate_thumbnail.py --project ep01 --theme auto
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
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:
    print("❌ Pillow not found.\n   Run: pip install Pillow --break-system-packages")
    sys.exit(1)

# ── Themes ─────────────────────────────────────────────────────────────────────

THEMES = {
    "dark": {
        "bg":        (12,  24,  40),    # #0C1828  deep navy
        "text":      (255, 255, 255),   # white
        "accent":    (240, 165,  0),    # #F0A500  amber
        "footer_bg": (6,   14,  28),    # darker navy strip
        "footer_fg": (240, 165,  0),    # amber
        "shadow":    (0,   0,   0),
    },
    "light": {
        "bg":        (250, 247, 242),   # #FAF7F2  warm cream
        "text":      (44,  26,  14),    # #2C1A0E  dark brown
        "accent":    (192,  57,  43),   # #C0392B  crimson
        "footer_bg": (44,  26,  14),    # dark brown strip
        "footer_fg": (240, 165,  0),    # amber (stays consistent across themes)
        "shadow":    (200, 185, 165),
    },
}


def _parse_episode_num(project_dir: Path) -> int:
    """Parse episode number from the project folder name (ep01→1, ep03→3, …). Defaults to 1."""
    m = re.search(r"\d+", project_dir.name)
    return int(m.group()) if m else 1


def _resolve_theme(project_dir: Path, theme_arg: str) -> tuple[str, dict]:
    """Return (theme_name, colour_dict) for the resolved theme.
    For 'auto', parses the episode number from the project folder name
    (ep01→1, ep03→3, …) and picks dark for odd, light for even episodes.
    """
    if theme_arg in ("dark", "light"):
        return theme_arg, THEMES[theme_arg]
    # auto
    ep_num = _parse_episode_num(project_dir)
    chosen = "dark" if ep_num % 2 == 1 else "light"
    print(f"  Theme  : auto → {chosen} (episode {ep_num})")
    return chosen, THEMES[chosen]

CANVAS_W, CANVAS_H = 1280, 720
SAFE_MARGIN = 64          # pixels inset from all edges

PIPELINE_DIR = Path(__file__).parent

# Legacy single-value aliases (used internally; overridden by theme at render time)
BG_COLOR     = THEMES["dark"]["bg"]
TEXT_COLOR   = THEMES["dark"]["text"]
ACCENT_COLOR = THEMES["dark"]["accent"]
FOOTER_BG    = THEMES["dark"]["footer_bg"]

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


def _load_base_image(theme_name: str) -> Image.Image | None:
    """Load the pre-generated base thumbnail art (mascot + India map/silhouette +
    baked-in "THE INTERESTED INDIAN" footer branding) for this theme, and cover-fit
    -crop it to exactly CANVAS_W×CANVAS_H. Source art is 1672×941 — nearly the same
    aspect ratio as the 1280×720 canvas, so the crop is minimal.
    Returns None if the base file is missing (caller falls back to the solid-colour canvas).
    """
    base_path = PIPELINE_DIR / "common" / "thumbnails" / f"base_{theme_name}.png"
    if not base_path.exists():
        print(f"  ⚠ Base thumbnail art not found ({base_path}) — falling back to solid-colour canvas")
        return None
    try:
        img = Image.open(base_path).convert("RGB")
    except Exception as e:
        print(f"  ⚠ Could not load base thumbnail image: {e}")
        return None
    return ImageOps.fit(img, (CANVAS_W, CANVAS_H), method=Image.LANCZOS, centering=(0.5, 0.5))


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

def _render_on_base(
    base: Image.Image,
    title: str,
    out_path: Path,
    t: dict,
    episode_num: int | None,
    accent_image_path: str | None,
    theme_name: str = "dark",
):
    """Composite title + episode badge onto the pre-made base thumbnail art.
    Both base_light.png and base_dark.png flank a mascot on one side and a map/
    silhouette on the other, leaving a natural gap roughly in the horizontal centre —
    that's where the title goes, widened slightly with a scrim behind it for
    legibility since it partially overlaps the art on either side.
    The base art already has "THE INTERESTED INDIAN" baked into its own footer bar,
    so this does NOT draw a new footer — that would duplicate it.
    """
    canvas = base.copy()
    draw = ImageDraw.Draw(canvas)
    if accent_image_path:
        print("  ⚠ Ignoring --accent-image — base thumbnail art already includes mascot/map illustration")

    text_zone_w = int(CANVAS_W * 0.44)
    text_left   = (CANVAS_W - text_zone_w) // 2
    text_max_w  = text_zone_w - 40

    footer_safe_h   = 76   # keep clear of the base art's own baked-in footer bar
    title_area_top  = SAFE_MARGIN
    title_area_bot  = CANVAS_H - footer_safe_h
    title_max_h     = title_area_bot - title_area_top - 32

    lines, font = _fit_title(draw, title, text_max_w, title_max_h)
    try:
        lh = font.getbbox("Ag")[3] + 14
    except AttributeError:
        lh = 60
    total_text_h = lh * len(lines)
    y_start = title_area_top + (title_max_h - total_text_h) // 2

    # Legibility scrim behind the text zone
    scrim_pad = 24
    scrim_box = (
        text_left - scrim_pad, y_start - scrim_pad,
        text_left + text_zone_w + scrim_pad, y_start + total_text_h + scrim_pad,
    )
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rectangle(scrim_box, fill=(*t["bg"], 165))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = text_left + (text_zone_w - w) // 2
        draw.text((x + 3, y_start + 3), line, font=font, fill=t["shadow"])
        draw.text((x, y_start), line, font=font, fill=t["text"])
        y_start += lh

    # Episode-number badge — top corner OPPOSITE the mascot (light: mascot right → badge
    # top-left; dark: mascot left → badge top-right), per channel_config.json's own
    # base-art descriptions, so it never sits over the mascot's head.
    if episode_num:
        badge_text = f"EP{episode_num:02d}"
        badge_font = _find_font(30, bold=True)
        bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = 18, 10
        badge_top = 28
        mascot_on_right = (theme_name == "light")
        if mascot_on_right:
            badge_left = 28
            badge_box = (badge_left, badge_top, badge_left + bw + 2 * pad_x, badge_top + bh + 2 * pad_y)
        else:
            badge_right = CANVAS_W - 28
            badge_box = (badge_right - bw - 2 * pad_x, badge_top, badge_right, badge_top + bh + 2 * pad_y)
        draw.rectangle(badge_box, fill=t["accent"])
        draw.text((badge_box[0] + pad_x, badge_box[1] + pad_y - bbox[1]),
                   badge_text, font=badge_font, fill=(255, 255, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out_path), "PNG", optimize=True)
    print(f"✓ Thumbnail saved → {out_path}  ({CANVAS_W}×{CANVAS_H}px)")


def render_thumbnail(
    title: str,
    out_path: Path,
    accent_image_path: str | None = None,
    theme: dict | None = None,
    theme_name: str = "dark",
    episode_num: int | None = None,
):
    t = theme or THEMES["dark"]   # colours for this render

    base = _load_base_image(theme_name)
    if base is not None:
        _render_on_base(base, title, out_path, t, episode_num, accent_image_path, theme_name)
        return

    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), t["bg"])
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
        draw.text((x + 3, y_start + 3), line, font=font, fill=t["shadow"])
        draw.text((x, y_start), line, font=font, fill=t["text"])
        y_start += lh

    # Accent rule
    rule_y = CANVAS_H - footer_h - 6
    draw.rectangle([(text_left, rule_y), (text_right, rule_y + 4)], fill=t["accent"])

    # Footer strip
    draw.rectangle([(0, CANVAS_H - footer_h), (CANVAS_W, CANVAS_H)], fill=t["footer_bg"])

    footer_font = _find_font(28, bold=True)
    footer_text = f"THE INTERESTED INDIAN · EP{episode_num:02d}" if episode_num else "THE INTERESTED INDIAN"
    bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
    fw = bbox[2] - bbox[0]
    fh = bbox[3] - bbox[1]
    fx = (CANVAS_W - fw) // 2
    fy = CANVAS_H - footer_h + (footer_h - fh) // 2
    draw.text((fx, fy), footer_text, font=footer_font, fill=t["footer_fg"])

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
    parser.add_argument("--theme", default="auto", choices=["dark", "light", "auto"],
                        help="Colour theme: dark (navy), light (cream), or auto (alternates by episode number). Default: auto")
    args = parser.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = PIPELINE_DIR / args.project
    if not project_dir.exists():
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    title       = args.title or _read_title(project_dir)
    out_path    = Path(args.out) if args.out else project_dir / "thumbnail.png"
    theme_name, theme = _resolve_theme(project_dir, args.theme)
    episode_num = _parse_episode_num(project_dir)

    print(f"  Title  : {title}")
    print(f"  Theme  : {args.theme}")
    print(f"  Episode: {episode_num}")
    print(f"  Output : {out_path}")
    if args.accent_image:
        print(f"  Accent : {args.accent_image}")

    render_thumbnail(
        title, out_path,
        accent_image_path=args.accent_image,
        theme=theme,
        theme_name=theme_name,
        episode_num=episode_num,
    )


if __name__ == "__main__":
    main()
