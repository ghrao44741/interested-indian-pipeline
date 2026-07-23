"""
search_pexels.py — Pexels photo fetcher for The Interested Indian

Downloads the best-matching free stock photo for "photo" scene types.
Photos are resized to 1280×720 (16:9) and saved as PNG.

Pexels API: free, no watermark, commercial use OK.
Get your key at: https://www.pexels.com/api/

SETUP:
    1. pip install requests pillow --break-system-packages
    2. Add PEXELS_API_KEY=your_key_here to your .env file

USAGE — single query:
    python search_pexels.py --query "Supreme Court India" --out ep01/images/SCENE-005.png

USAGE — pipeline mode (reads photo scenes from image_prompts file):
    python search_pexels.py --project ep01

USAGE — preview search results without downloading:
    python search_pexels.py --query "Indian Parliament" --preview

How it picks the photo:
  1. Searches Pexels for the query
  2. Prefers landscape (width > height) photos
  3. Picks the highest-resolution landscape photo in the top 5 results
  4. Crops/letterboxes to exact 16:9 if needed
  5. Saves as PNG

Keywords are auto-extracted from the NARRATION field of photo-type scenes.
Use --query to override with a specific search term.
"""

import argparse
import io
import os
import re
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent
PEXELS_API   = "https://api.pexels.com/v1"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    # 1. Environment variable
    key = os.environ.get("PEXELS_API_KEY", "")
    if key:
        return key
    # 2. .env file in pipeline directory
    env_path = PIPELINE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("PEXELS_API_KEY"):
                key = line.split("=", 1)[-1].strip().strip('"').strip("'")
                if key:
                    return key
    return ""


def _search(query: str, api_key: str, per_page: int = 10) -> list[dict]:
    """Return list of Pexels photo objects for the query."""
    try:
        import requests
    except ImportError:
        print("❌ pip install requests --break-system-packages")
        sys.exit(1)

    resp = requests.get(
        f"{PEXELS_API}/search",
        headers={"Authorization": api_key},
        params={"query": query, "per_page": per_page, "orientation": "landscape"},
        timeout=15,
    )
    if resp.status_code == 401:
        print("❌ Pexels API key invalid or missing. Check PEXELS_API_KEY in .env")
        sys.exit(1)
    resp.raise_for_status()
    return resp.json().get("photos", [])


def _pick_best(photos: list[dict]) -> dict | None:
    """Pick the best landscape photo from results."""
    landscape = [p for p in photos if p.get("width", 0) > p.get("height", 0)]
    if not landscape:
        landscape = photos
    # Sort by resolution (area)
    landscape.sort(key=lambda p: p.get("width", 0) * p.get("height", 0), reverse=True)
    return landscape[0] if landscape else None


def _download_and_resize(photo: dict, out_path: Path) -> bool:
    """Download photo, crop to 16:9, save as PNG."""
    try:
        import requests
        from PIL import Image
    except ImportError:
        print("❌ pip install requests pillow --break-system-packages")
        sys.exit(1)

    # Prefer large, fall back to original
    url = (photo.get("src", {}).get("large2x")
           or photo.get("src", {}).get("large")
           or photo.get("src", {}).get("original"))
    if not url:
        return False

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    img = Image.open(io.BytesIO(resp.content)).convert("RGB")

    # Crop to 16:9
    target_w, target_h = 1280, 720
    src_w, src_h = img.size
    src_ratio    = src_w / src_h
    target_ratio = target_w / target_h

    if src_ratio > target_ratio:
        # image is wider — crop sides
        new_w = int(src_h * target_ratio)
        left  = (src_w - new_w) // 2
        img   = img.crop((left, 0, left + new_w, src_h))
    elif src_ratio < target_ratio:
        # image is taller — crop top/bottom
        new_h = int(src_w / target_ratio)
        top   = (src_h - new_h) // 2
        img   = img.crop((0, top, src_w, top + new_h))

    img = img.resize((target_w, target_h), Image.LANCZOS)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG", optimize=True)
    return True


def _keywords_from_narration(narration: str) -> str:
    """
    Auto-extract a Pexels search query from narration text.
    Strips policy jargon, keeps proper nouns and location names.
    """
    # Remove common filler words
    stop = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "this", "that", "is", "was", "are", "were",
        "it", "its", "their", "there", "here", "by", "as", "has", "had",
        "have", "been", "be", "not", "no", "so", "if", "then", "from",
    }
    words = re.findall(r"\b[A-Z][a-zA-Z]+\b", narration)  # capitalised words (proper nouns)
    keywords = [w for w in words if w.lower() not in stop]
    # Fallback if nothing capitalised
    if not keywords:
        keywords = [w for w in narration.split() if len(w) > 4 and w.lower() not in stop]
    return " ".join(keywords[:5]) + " India"


def _parse_photo_scenes(md_path: Path) -> list[dict]:
    """Parse photo-type scenes from image_prompts_one_line_per_prompt.md."""
    scenes = []
    text = md_path.read_text(encoding="utf-8")
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        prompt_lower = block.lower()
        if not any(k in prompt_lower for k in
                   ["parliament", "supreme court", "protest", "crowd",
                    "building", "city", "real photo", "skyline", "historical"]):
            continue
        m_file   = re.search(r"`([^`]+\.png)`", block)
        m_narr   = re.search(r'NARRATION:\s*"([^"]+)"', block)
        m_prompt = re.search(r"PROMPT:\s*(.+?)(?:\s+OVERLAY:|\s+CUE:|$)", block, re.DOTALL)
        if not m_file:
            continue
        scenes.append({
            "file":      m_file.group(1),
            "narration": m_narr.group(1) if m_narr else "",
            "prompt":    m_prompt.group(1).strip() if m_prompt else "",
        })
    return scenes


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_photo(
    query:    str,
    out_path: Path,
    api_key:  str,
    preview:  bool = False,
) -> bool:
    """
    Search Pexels for `query`, download best result to `out_path`.
    Returns True on success.
    """
    print(f"  🔍 Pexels: '{query}'")
    photos = _search(query, api_key)
    if not photos:
        print(f"  ⚠  No results for '{query}'")
        return False

    best = _pick_best(photos)
    if not best:
        return False

    if preview:
        print(f"  Preview results:")
        for p in photos[:5]:
            print(f"    [{p['width']}×{p['height']}] {p['url']}")
            print(f"    Photographer: {p.get('photographer')}")
        return True

    ok = _download_and_resize(best, out_path)
    if ok:
        print(f"  ✓  {best.get('photographer')} — {best['url']}")
        print(f"     → {out_path}")
    return ok


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--query",   default=None,
                        help="Pexels search query (e.g. 'Supreme Court India')")
    parser.add_argument("--out",     default=None,
                        help="Output PNG path")
    parser.add_argument("--project", default=None,
                        help="Episode folder — auto-process all photo scenes")
    parser.add_argument("--overwrite", action="store_true",
                        help="Re-download even if output file already exists")
    parser.add_argument("--preview", action="store_true",
                        help="Print search results without downloading")
    args = parser.parse_args()

    api_key = _get_api_key()
    if not api_key:
        print("❌ PEXELS_API_KEY not set.")
        print("   Add it to .env: PEXELS_API_KEY=your_key_here")
        print("   Get a free key at: https://www.pexels.com/api/")
        sys.exit(1)

    # ── Single query mode ──
    if args.query:
        out = Path(args.out) if args.out else Path(
            args.query.lower().replace(" ", "_")[:40] + ".png"
        )
        fetch_photo(args.query, out, api_key, preview=args.preview)
        return

    # ── Project batch mode ──
    if not args.project:
        parser.error("Provide --query or --project")

    project_dir   = PIPELINE_DIR / args.project
    prompts_path  = project_dir / "image_prompts_one_line_per_prompt.md"
    images_dir    = project_dir / "images"

    if not prompts_path.exists():
        print(f"❌ Prompts file not found: {prompts_path}")
        print("   Run generate_image_prompts.py first.")
        sys.exit(1)

    scenes = _parse_photo_scenes(prompts_path)
    if not scenes:
        print("  ℹ  No photo-type scenes found in prompts file.")
        return

    print(f"\n  Photo scenes found: {len(scenes)}")
    done = skipped = failed = 0

    for s in scenes:
        out_path = images_dir / s["file"]
        if out_path.exists() and not args.overwrite:
            print(f"  ⏭  {s['file']} (exists)")
            skipped += 1
            continue
        query = _keywords_from_narration(s["narration"] or s["prompt"])
        ok = fetch_photo(query, out_path, api_key)
        if ok:
            done += 1
        else:
            failed += 1

    print(f"\n  Done: {done}  Skipped: {skipped}  Failed: {failed}")


if __name__ == "__main__":
    main()
