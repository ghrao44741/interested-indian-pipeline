"""
stamp_manifest.py — Aeonium Glow / That's Why pipeline
Reads each scene's MP3, measures its duration, and writes
start / end / duration back into manifest.json in-place.

Run this once after audio is generated, before stitch or SRT.

Usage:
    python stamp_manifest.py --project short-03

After stamping, manifest.json each scene will have:
    "start":    0.0,
    "end":      3.072,
    "duration": 3.072
"""

import json
import os
import argparse
import shutil
from datetime import datetime

from mutagen.mp3 import MP3

# Must match the trailing pad stitch_video_longform.py's build_clip_from_image()/
# build_clip_from_video() bake into every rendered clip ("-t audio_duration + 0.5").
# Omitting this here previously caused stamp_manifest to under-count each scene's
# real length by 0.5s, compounding to ~1 minute of drift over a 135-scene episode —
# manifest timestamps (and anything built from them, like generate_srt.py) silently
# fell further and further behind the actual rendered video as the episode went on.
CLIP_PAD_SECONDS = 0.5


def get_mp3_duration(path: str, fallback: float = 3.0) -> float:
    try:
        return MP3(path).info.length
    except Exception as e:
        print(f"  ⚠️  Cannot read {os.path.basename(path)}: {e} — using {fallback}s fallback")
        return fallback


def stamp_manifest(project_dir: str):
    manifest_path = os.path.join(project_dir, "manifest.json")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    episode = manifest["episode"]
    scenes = manifest["scenes"]

    # ── backup before overwriting ────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = manifest_path.replace(".json", f"_backup_{ts}.json")
    shutil.copy2(manifest_path, backup_path)
    print(f"📦 Backup saved : {backup_path}")

    # ── measure and stamp each scene ─────────────────────────────────────────
    print(f"🎵 Stamping timestamps for: {episode} ({len(scenes)} scenes)\n")

    cursor = 0.0
    for scene in scenes:
        audio_path = os.path.join(project_dir, scene["audio"])
        duration = get_mp3_duration(audio_path) + CLIP_PAD_SECONDS

        scene["start"] = round(cursor, 6)
        scene["end"] = round(cursor + duration, 6)
        scene["duration"] = round(duration, 6)

        print(f"  {scene['id']}  {scene['start']:.3f}s → {scene['end']:.3f}s  ({duration:.3f}s)")
        cursor += duration

    manifest["total_duration"] = round(cursor, 6)

    # ── write back ───────────────────────────────────────────────────────────
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Manifest stamped : {manifest_path}")
    print(f"   Total duration   : {cursor:.3f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stamp scene timestamps into manifest.json")
    parser.add_argument("--project", required=True, help="Project folder name (e.g. short-03)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = (
        args.project
        if os.path.isabs(args.project)
        else os.path.join(script_dir, args.project)
    )

    if not os.path.isdir(project_dir):
        print(f"❌ Project folder not found: {project_dir}")
        exit(1)

    stamp_manifest(project_dir)
