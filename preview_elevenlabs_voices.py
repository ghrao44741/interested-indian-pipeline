"""
preview_elevenlabs_voices.py — Search & preview ElevenLabs Indian English male voices

Reads ELEVENLABS_API_KEY from .env, searches the shared voice library for
Indian English male voices, and generates short audio previews from your
script's opening lines so you can compare them side by side.

USAGE:
    python preview_elevenlabs_voices.py              # search + list voices
    python preview_elevenlabs_voices.py --preview    # generate audio samples
    python preview_elevenlabs_voices.py --voice-id ABC123 --preview  # test one specific voice
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
from pathlib import Path

# ── Load .env ──────────────────────────────────────────────────────────────────
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
if not API_KEY:
    print("❌ ELEVENLABS_API_KEY not found in .env")
    sys.exit(1)

HEADERS = {"xi-api-key": API_KEY, "Content-Type": "application/json"}

# Opening lines for preview (short — keeps cost low)
PREVIEW_TEXT = (
    "So here's something I learned last week that I still can't quite believe. "
    "If the prime minister wants to dismiss a state government in India, "
    "they can't just do it."
)


def api_get(path: str, params: dict = None) -> dict:
    url = f"https://api.elevenlabs.io{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def search_indian_voices() -> list[dict]:
    """Search shared voice library for Indian English male voices — two passes:
    1. accent=indian filter (catches tagged voices)
    2. No accent filter, keyword search for 'indian' / 'south asian' in description
    Deduplicates by voice_id.
    """
    seen = set()
    results = []

    # Pass 1: accent=indian tagged voices
    print("Pass 1: accent=indian tagged male voices...")
    for page in range(3):
        try:
            data = api_get("/v1/shared-voices", {
                "gender": "male",
                "accent": "indian",
                "language": "en",
                "page_size": 20,
                "page": page,
            })
            voices = data.get("voices", [])
            if not voices:
                break
            for v in voices:
                vid = v.get("voice_id", "")
                if vid not in seen:
                    seen.add(vid)
                    v["_source"] = "accent-tagged"
                    results.append(v)
        except Exception as e:
            print(f"  ⚠ Page {page} error: {e}")
            break
    print(f"  Found {len(results)} accent-tagged voices\n")

    # Pass 2: keyword search — "indian" in description/name, no accent filter
    print("Pass 2: keyword search (indian / south asian) in all male English voices...")
    kw_hits = 0
    for page in range(5):
        try:
            data = api_get("/v1/shared-voices", {
                "gender": "male",
                "language": "en",
                "page_size": 100,
                "page": page,
            })
            voices = data.get("voices", [])
            if not voices:
                break
            for v in voices:
                vid = v.get("voice_id", "")
                if vid in seen:
                    continue
                blob = " ".join([
                    v.get("name", ""),
                    v.get("description") or "",
                    str(v.get("labels", {})),
                ]).lower()
                if any(kw in blob for kw in ("indian", "south asian", "india", "hindi")):
                    seen.add(vid)
                    v["_source"] = "keyword"
                    results.append(v)
                    kw_hits += 1
        except Exception as e:
            print(f"  ⚠ Page {page} error: {e}")
            break
    print(f"  Found {kw_hits} additional keyword-matched voices\n")

    return results


def list_my_voices() -> list[dict]:
    """List voices in your own ElevenLabs library."""
    try:
        data = api_get("/v1/voices")
        return data.get("voices", [])
    except Exception as e:
        print(f"  ⚠ Could not fetch your voices: {e}")
        return []


def generate_preview(voice_id: str, voice_name: str, out_dir: Path) -> Path | None:
    """Generate a short audio preview for a voice and save as MP3."""
    url = "https://api.elevenlabs.io/v1/text-to-speech/" + voice_id
    body = json.dumps({
        "text": PREVIEW_TEXT,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }).encode()
    req = urllib.request.Request(url, data=body, headers={**HEADERS, "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out_path = out_dir / f"preview_{voice_name.replace(' ', '_')[:30]}_{voice_id[:8]}.mp3"
            out_path.write_bytes(r.read())
            return out_path
    except Exception as e:
        print(f"  ✗ {voice_name}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preview",  action="store_true", help="Generate audio preview MP3s")
    parser.add_argument("--voice-id", default=None, help="Test a specific voice ID")
    parser.add_argument("--limit",    type=int, default=15, help="Max voices to preview (default 15)")
    args = parser.parse_args()

    out_dir = Path(__file__).parent / "voice_previews"
    out_dir.mkdir(exist_ok=True)

    # ── Single voice mode ──
    if args.voice_id:
        print(f"Testing voice: {args.voice_id}")
        if args.preview:
            path = generate_preview(args.voice_id, args.voice_id, out_dir)
            if path:
                print(f"  ✓ Saved: {path}")
        return

    # ── Search mode ──
    voices = search_indian_voices()

    # Also include any Indian voices in your own library
    my_voices = [v for v in list_my_voices()
                 if "indian" in str(v.get("labels", {})).lower()
                 or "india" in str(v.get("description", "")).lower()]

    all_voices = my_voices + voices

    if not all_voices:
        print("No Indian English male voices found.")
        print("Try searching manually at elevenlabs.io/voice-library")
        print("Filter: Language=English, Accent=Indian, Gender=Male")
        return

    print(f"Found {len(all_voices)} voice(s):\n")
    print(f"{'#':<4} {'Src':<8} {'Name':<35} {'Voice ID':<25} {'Description'}")
    print("─" * 110)
    for i, v in enumerate(all_voices[:args.limit], 1):
        name   = v.get("name", "Unknown")[:34]
        vid    = v.get("voice_id", "")[:24]
        desc   = (v.get("description") or "")[:45]
        source = v.get("_source", "?")[:7]
        print(f"{i:<4} {source:<8} {name:<35} {vid:<25} {desc}")

    if args.preview:
        print(f"\nGenerating {min(args.limit, len(all_voices))} audio previews...\n")
        for v in all_voices[:args.limit]:
            name = v.get("name", "Unknown")
            vid  = v.get("voice_id", "")
            print(f"  ⏳ {name} ({vid[:8]}...)")
            path = generate_preview(vid, name, out_dir)
            if path:
                print(f"  ✓ {path.name}")

        print(f"\nAll previews saved to: {out_dir}")
        print("Listen and pick your favorite, then:")
        print("  1. Note the Voice ID")
        print("  2. Add to .env:  ELEVENLABS_VOICE_ID=<id>")
        print("  3. Tell Claude — I'll wire it into generate_source_audio.py")
    else:
        print(f"\nRun with --preview to generate audio samples.")
        print(f"Or test a specific voice: python preview_elevenlabs_voices.py --voice-id <id> --preview")


if __name__ == "__main__":
    main()
