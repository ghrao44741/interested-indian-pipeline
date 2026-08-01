"""
generate_srt.py — Aeonium Glow / That's Why pipeline
Reads manifest.json, measures each scene's MP3 duration via mutagen,
and writes properly timed .srt and .ass caption files.

Usage:
    python generate_srt.py --project short-03

Output:
    {project}/output/{episode}_captions.srt  (upload to YouTube as a subtitle track)
    {project}/output/{episode}_captions.ass  (used to burn captions — see stitch_video_longform.py)
"""

import json
import os
import argparse
import textwrap

from mutagen.mp3 import MP3


# Max characters per caption line, paired with ASS_FONT_SIZE below — the two
# must move together, since this is really "how many characters fit on one line
# at that size". Measured against the actual ffmpeg/libass render in a 1920x1080
# frame; staying under the width libass would wrap at keeps our line breaks
# authoritative (no surprise re-wrap pushing a 2-line caption off frame).
# At 52px Bold, ~32 chars spans roughly 1000px of the 1920 frame.
MAX_LINE_CHARS = 32
# Minimum seconds a caption chunk must occupy; shorter chunks are merged upward
MIN_CHUNK_SECS = 1.2


def seconds_to_srt_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    ms = int(round((s % 1) * 1000))
    if ms == 1000:
        sec += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def seconds_to_ass_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    cs = int(round((s % 1) * 100))
    if cs == 100:
        sec += 1
        cs = 0
    return f"{h:d}:{m:02d}:{sec:02d}.{cs:02d}"


def _rebalance(text: str, max_chars: int) -> str:
    """Re-wrap merged text so its lines are of similar length.

    Merging previously just glued two greedily-wrapped lines back together with
    a newline, which kept the greedy split: "A re-exam was scheduled for 21st" /
    "June." — an orphaned word under a full-width line, rendering as a ragged
    two-line caption box. Re-wrapping at the narrowest width that still yields
    the same number of lines evens them out.
    """
    words = text.split()
    if not words:
        return text
    lines_needed = len(textwrap.wrap(text, width=max_chars))
    if lines_needed <= 1:
        return text
    # Narrow the width until it would need an extra line; the last width that
    # still fits is the most balanced one.
    best = textwrap.wrap(text, width=max_chars)
    for width in range(max_chars, max(len(max(words, key=len)), 1) - 1, -1):
        candidate = textwrap.wrap(text, width=width)
        if len(candidate) > lines_needed:
            break
        best = candidate
    return "\n".join(best)


def split_caption_entries(text: str, start: float, end: float,
                          max_chars: int = MAX_LINE_CHARS) -> list:
    """
    Wrap text and return one or more timed caption entries.
    Long scenes are split into 1-line chunks with word-count-proportional
    durations. Chunks that would be shorter than MIN_CHUNK_SECS are merged
    into their neighbour so no caption flashes too briefly.

    One line per chunk (not two) keeps each caption's rendered height as
    short as possible — characters are often drawn large/centered (more so
    after Ken Burns zoom), so a shorter box is less likely to sit across a
    face regardless of where a given scene's artwork happens to place it.
    """
    lines = textwrap.wrap(text, width=max_chars)

    # One line per chunk — see docstring for why not 2.
    chunks = list(lines)

    if len(chunks) == 1:
        return [{"start": start, "end": end, "text": chunks[0]}]

    duration = end - start
    total_words = max(len(text.split()), 1)

    # Calculate raw durations by word count
    raw_dur = [duration * len(c.split()) / total_words for c in chunks]

    # Merge short trailing chunks into the previous one (may create 3-line entry)
    while len(chunks) > 1 and raw_dur[-1] < MIN_CHUNK_SECS:
        chunks[-2] = _rebalance(chunks[-2] + " " + chunks[-1], max_chars)
        raw_dur[-2] += raw_dur[-1]
        chunks.pop(); raw_dur.pop()

    # Merge short leading chunks into the next one
    while len(chunks) > 1 and raw_dur[0] < MIN_CHUNK_SECS:
        chunks[1] = _rebalance(chunks[0] + " " + chunks[1], max_chars)
        raw_dur[1] += raw_dur[0]
        chunks.pop(0); raw_dur.pop(0)

    if len(chunks) == 1:
        return [{"start": start, "end": end, "text": chunks[0]}]

    # Build final entries
    entries = []
    cursor = start
    for i, (chunk, dur) in enumerate(zip(chunks, raw_dur)):
        chunk_end = end if i == len(chunks) - 1 else cursor + dur
        entries.append({"start": cursor, "end": chunk_end, "text": chunk})
        cursor = chunk_end

    return entries


def get_mp3_duration(path: str, fallback: float = 3.0) -> float:
    try:
        return MP3(path).info.length
    except Exception as e:
        print(f"  ⚠️  Cannot read {os.path.basename(path)}: {e} — using {fallback}s fallback")
        return fallback


def _build_entries(project_dir: str) -> tuple:
    """Shared by generate_srt() and generate_ass() so both formats come from
    the exact same timing/text, rather than one being derived from the other."""
    manifest_path = os.path.join(project_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    episode = manifest["episode"]
    scenes = manifest["scenes"]

    raw_entries = []
    cursor = 0.0

    # Detect whether manifest has been stamped with timestamps already
    stamped = all("start" in s and "end" in s for s in scenes)
    if stamped:
        print(f"📋 Building captions for: {episode} ({len(scenes)} scenes)  [using manifest timestamps]")
    else:
        print(f"📋 Building captions for: {episode} ({len(scenes)} scenes)  [measuring MP3 durations]")
        print(f"   Tip: run stamp_manifest.py first to bake timestamps into the manifest.\n")

    for scene in scenes:
        if stamped:
            start = scene["start"]
            end = scene["end"]
        else:
            audio_path = os.path.join(project_dir, scene["audio"])
            duration = get_mp3_duration(audio_path)
            start = cursor
            end = cursor + duration
        sub_entries = split_caption_entries(scene["script"].strip(), start, end)
        raw_entries.extend(sub_entries)
        cursor = end

    # Append a caption entry for the shared CTA scene, if present — it's
    # rendered by stitch_video_longform.py as a pseudo-scene appended after
    # all manifest scenes, so it has no entry in manifest["scenes"] itself.
    common_cta = os.path.normpath(os.path.join(project_dir, "..", "common", "cta"))
    cta_script_path = os.path.join(common_cta, "cta_script.txt")
    cta_mp3_path = os.path.join(common_cta, "cta.mp3")
    if os.path.exists(cta_script_path) and os.path.exists(cta_mp3_path):
        cta_text = open(cta_script_path, "r", encoding="utf-8").read().strip()
        cta_duration = get_mp3_duration(cta_mp3_path)
        cta_start = cursor
        cta_end = cursor + cta_duration + 0.5  # matches the +0.5 pad build_clip_from_image gives every scene
        sub_entries = split_caption_entries(cta_text, cta_start, cta_end)
        raw_entries.extend(sub_entries)
        cursor = cta_end

    # Re-number sequentially after any splits
    entries = [dict(e, index=i) for i, e in enumerate(raw_entries, start=1)]
    return entries, cursor, episode, len(scenes)


def generate_srt(project_dir: str) -> str:
    entries, cursor, episode, n_scenes = _build_entries(project_dir)

    output_dir = os.path.join(project_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    srt_path = os.path.join(output_dir, f"{episode}_captions.srt")

    with open(srt_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(f"{entry['index']}\n")
            f.write(f"{seconds_to_srt_time(entry['start'])} --> {seconds_to_srt_time(entry['end'])}\n")
            f.write(f"{entry['text']}\n\n")

    print(f"\n✅ SRT written  : {srt_path}")
    print(f"   SRT entries  : {len(entries)}  (from {n_scenes} scenes)")
    print(f"   Total length : {seconds_to_srt_time(cursor)}")
    return srt_path


# ASS style tuned for 1920x1080 landscape: bottom-center, solid black box
# behind the text (BorderStyle=3) so it stays legible over any artwork, with
# MarginV clearing the OVERLAY title banner (132px tall at 1080p, see
# add_text_overlays.py's BANNER_HEIGHT). Unlike a plain SRT burned via
# ffmpeg's "subtitles" filter — which has no PlayResX/PlayResY of its own and
# left libass guessing at a small default reference resolution, scaling our
# wrapping and margin numbers unpredictably — this file declares its real
# resolution explicitly, so what we set here is exactly what renders.
ASS_MARGIN_V = 145

# Caption font size at 1920x1080. Was 28, which renders about 500px wide and is
# unreadable on a phone — flagged in review and confirmed against a real frame
# grab. 52 is in the normal range for burned captions at 1080p and stays legible
# at quarter-screen. Changing this REQUIRES re-tuning MAX_LINE_CHARS above.
ASS_FONT_SIZE = 52


def generate_ass(project_dir: str) -> str:
    entries, cursor, episode, n_scenes = _build_entries(project_dir)

    output_dir = os.path.join(project_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    ass_path = os.path.join(output_dir, f"{episode}_captions.ass")

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{ASS_FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,3,1,0,2,10,10,{ASS_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        for entry in entries:
            text_ass = entry["text"].replace("\n", "\\N")
            f.write(
                f"Dialogue: 0,{seconds_to_ass_time(entry['start'])},"
                f"{seconds_to_ass_time(entry['end'])},Default,,0,0,0,,{text_ass}\n"
            )

    print(f"\n✅ ASS written  : {ass_path}")
    print(f"   ASS entries  : {len(entries)}  (from {n_scenes} scenes)")
    print(f"   Total length : {seconds_to_srt_time(cursor)}")
    return ass_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate SRT captions from manifest.json")
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

    generate_srt(project_dir)
    generate_ass(project_dir)
