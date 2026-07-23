"""
auto_split_scenes_v1_stage3_export.py
Snapshot of auto_split_scenes.py (baseline) + one isolated addition:
after the manifest is built, also write a plain [MM:SS] timestamped
script file, for pasting into the Stage 3 (image prompt + editing cue)
step of chat-based script-to-video workflows like The Interested Indian.

Nothing about transcription, splitting, or grouping logic changed from
baseline — this only adds a formatting/export step at the very end of
main(). If this addition ever needs reverting, go back to the baseline
auto_split_scenes.py; nothing here is load-bearing for the rest of the
pipeline (stitch_video.py etc. still only reads manifest.json).

That's Why / Aeonium Glow — Whisper-based Auto Scene Splitter

Replaces manual scene splitting. Workflow:
  1. Write your full script as ONE continuous text (no manual scene breaks)
  2. Generate ONE voiceover audio file from it (Edge TTS / Google TTS / etc)
  3. Place that audio file inside {project}/source_audio/  (e.g. ep02/source_audio/shorts1.wav)
  4. Run this script — it uses local Whisper to get word-level timestamps,
     splits at sentence boundaries, and further splits any sentence that
     would exceed MAX_SCENE_SECONDS
  5. Outputs a manifest.json (inside the project folder) with per-scene
     audio clips already cut to size, AND a timestamped_script.txt with
     one [MM:SS] line per scene (grouped scenes merged into one line
     each, in LongVideo mode) ready to paste into a Stage 3 chat prompt

FOLDER CONVENTION:
  Only this script (the "compiler") and the other pipeline scripts live
  at the root level. Every project's own files — source audio, generated
  scene audio, manifest, images, videos, output — live inside that
  project's own folder:

    shorts_pipeline2/
    ├── auto_split_scenes.py      <- compiler, stays at root
    ├── generate_audio.py
    ├── stitch_video.py
    ├── generate_cta_card.py
    └── ep02/                      <- everything for this project lives here
        ├── source_audio/
        │   └── shorts1.wav        <- input voiceover goes here
        ├── audio/                 <- output: cut scene clips land here
        ├── images/
        ├── videos/
        ├── output/
        └── manifest.json          <- written here automatically

SETUP:
    pip install whisperx
    (uses your existing CUDA-enabled PyTorch from openai-whisper if present)
    No HuggingFace token needed — that's only required for speaker
    diarization, which this script doesn't use (single-speaker TTS audio).

USAGE:
    python auto_split_scenes.py --audio shorts1.wav --project ep02 --max-seconds 10
    (shorts1.wav must already be inside ep02/source_audio/)
    Add --device cpu if you don't have a CUDA GPU (slower).
"""

import argparse
import json
import os
import re
import subprocess
import whisperx
import gc
import torch

# Common Whisper mis-transcriptions for brand/product names.
# Add more pairs here as you discover new misheard terms.
BRAND_CORRECTIONS = {
    r"\ba\s+yonium\s+glow\b": "Aeonium Glow",
    r"\bay\s*onium\s+glow\b": "Aeonium Glow",
    r"\beonium\s+glow\b": "Aeonium Glow",
    r"\bthat'?s\s+y\b": "That's Why",  # guard against "that's y" mis-hearing "That's Why"
}

def apply_brand_corrections(text: str) -> str:
    """Fix known brand-name mis-transcriptions, case-insensitive."""
    corrected = text
    for pattern, replacement in BRAND_CORRECTIONS.items():
        corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)
    return corrected

def transcribe_with_timestamps(audio_path: str, model_size: str = "base", device: str = "cuda") -> dict:
    """
    Run WhisperX locally: transcribe, then run forced phoneme alignment
    (wav2vec2) for word-level timestamps significantly more accurate than
    plain Whisper's word_timestamps. No HuggingFace token needed — that's
    only required for speaker diarization, which we don't use here since
    this is single-speaker TTS audio.
    """
    compute_type = "float16" if device == "cuda" else "int8"

    print(f"Loading WhisperX model ({model_size}, {device}, {compute_type})...")
    model = whisperx.load_model(model_size, device=device, compute_type=compute_type)

    print(f"Transcribing {audio_path}...")
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=16)

    # Free the transcription model before loading the alignment model
    del model
    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()

    print(f"Running forced alignment for precise word timestamps...")
    align_model, metadata = whisperx.load_align_model(
        language_code=result["language"], device=device
    )
    result = whisperx.align(
        result["segments"], align_model, metadata, audio, device,
        return_char_alignments=False
    )

    del align_model
    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()

    return result

def extract_words(whisper_result: dict) -> list:
    """Flatten WhisperX's segment structure into a flat list of {word, start, end}."""
    words = []
    for segment in whisper_result["segments"]:
        for word_info in segment.get("words", []):
            # WhisperX occasionally omits start/end for a word it couldn't
            # align confidently — skip those rather than crash.
            if "start" not in word_info or "end" not in word_info:
                continue
            words.append({
                "word": word_info["word"].strip(),
                "start": word_info["start"],
                "end": word_info["end"]
            })
    return words

def split_into_sentences(words: list) -> list:
    """
    Group words into sentences based on sentence-ending punctuation.
    Returns list of {text, start, end, words} per sentence.
    """
    sentences = []
    current_words = []

    for w in words:
        current_words.append(w)
        # sentence boundary: word ends with . ! or ?
        if re.search(r'[.!?]$', w["word"]):
            text = " ".join(x["word"] for x in current_words)
            sentences.append({
                "text": text,
                "start": current_words[0]["start"],
                "end": current_words[-1]["end"],
                "words": current_words
            })
            current_words = []

    # leftover words with no terminal punctuation
    if current_words:
        text = " ".join(x["word"] for x in current_words)
        sentences.append({
            "text": text,
            "start": current_words[0]["start"],
            "end": current_words[-1]["end"],
            "words": current_words
        })

    return sentences

def split_long_sentence(sentence: dict, max_seconds: float) -> list:
    """
    If a sentence exceeds max_seconds, split it at the nearest comma
    (or, failing that, at the midpoint word) into two or more chunks.
    """
    duration = sentence["end"] - sentence["start"]
    if duration <= max_seconds:
        return [sentence]

    words = sentence["words"]

    # try splitting at a comma near the middle
    comma_indices = [i for i, w in enumerate(words) if w["word"].endswith(",")]
    split_idx = None
    if comma_indices:
        # pick the comma closest to the midpoint
        mid = len(words) // 2
        split_idx = min(comma_indices, key=lambda i: abs(i - mid))
    else:
        split_idx = len(words) // 2 - 1
        if split_idx < 0:
            split_idx = 0

    first_words = words[:split_idx + 1]
    second_words = words[split_idx + 1:]

    if not first_words or not second_words:
        return [sentence]  # can't split further, accept as-is

    first = {
        "text": " ".join(w["word"] for w in first_words),
        "start": first_words[0]["start"],
        "end": first_words[-1]["end"],
        "words": first_words
    }
    second = {
        "text": " ".join(w["word"] for w in second_words),
        "start": second_words[0]["start"],
        "end": second_words[-1]["end"],
        "words": second_words
    }

    # recursively split further if still too long
    return split_long_sentence(first, max_seconds) + split_long_sentence(second, max_seconds)

def build_scenes(sentences: list, max_seconds: float) -> list:
    """Apply max_seconds splitting to every sentence, return flat scene list."""
    scenes = []
    for sentence in sentences:
        split_results = split_long_sentence(sentence, max_seconds)
        scenes.extend(split_results)
    return scenes

# ── Scene Grouping (timing-based) ──────────────────────────────────
# Groups scenes into visual units using pause timing, not word patterns.
# Word-based detection (matching "sign one", "step two", etc.) is brittle —
# it breaks on digit-vs-word variation ("Sign 1" vs "Sign one"), unexpected
# phrasing, or any script that doesn't use a numbered-list format at all.
#
# Timing is more reliable: a genuinely new idea tends to follow a longer
# pause; a label, fragment, or continuation of the same idea tends to
# follow a very short pause. We chain consecutive short-gap, short-duration
# scenes together into one visual group, regardless of what words they use.
#
# This works for "Sign one... translucent leaves... if they look glassy...",
# numbered or not, in any phrasing, any language structure.

DEFAULT_FRAGMENT_MAX_SECONDS = 2.5   # a scene this short is probably a fragment, not a complete idea

def group_scenes_by_timing(scenes: list, fragment_max_seconds: float = DEFAULT_FRAGMENT_MAX_SECONDS) -> list:
    """
    Chain consecutive scenes into visual groups based on scene duration
    alone, not word content or pause length. Tested against real TTS
    audio: pause length between scenes is fairly uniform (0.4-1.1s)
    regardless of whether a scene is a label fragment or a full sentence,
    so gap length isn't a useful signal here — duration is.

    Logic: any run of short (fragment) scenes, followed by the first
    scene long enough to be a complete standalone thought, becomes one
    group. E.g. "Sign 1." + "Translucent leaves." (both short fragments)
    + "If they look glassy..." (the first long scene) = one group.
    The group closes at that long scene; the next scene starts fresh.

    fragment_max_seconds: scenes at or under this duration count as
    fragments to be merged into the next group. Tune per project —
    short-form scripts with label+explanation pairs (Aeonium Glow) work
    well around 2.5s; long-form essays with full, complete sentences
    throughout (The Interested Indian) will rarely produce anything
    this short, so a higher value (8-15s) is usually more meaningful
    there if you want grouping to trigger at all.

    Mutates and returns the same scene dicts with 'visual_group_id' added.
    Every scene also gets 'scene_type': 'standalone' (own visual) or
    'grouped' (shares a visual_group_id with neighbors).
    """
    for scene in scenes:
        scene["scene_type"] = "standalone"
        scene["visual_group_id"] = None

    group_counter = 0
    pending_fragment_indices = []  # short scenes waiting to be attached to a group

    for i, scene in enumerate(scenes):
        duration = scene["end"] - scene["start"]
        is_fragment = duration <= fragment_max_seconds

        if is_fragment:
            pending_fragment_indices.append(i)
        else:
            # This is a "full" scene — closes out any pending fragments
            # into one group together with itself.
            if pending_fragment_indices:
                group_counter += 1
                gid = f"group-{group_counter:02d}"
                for j in pending_fragment_indices:
                    scenes[j]["visual_group_id"] = gid
                    scenes[j]["scene_type"] = "grouped"
                scenes[i]["visual_group_id"] = gid
                scenes[i]["scene_type"] = "grouped"
                pending_fragment_indices = []
            # else: a standalone full scene, nothing to do

    # If the script ends on a run of unattached fragments (no closing
    # full scene after them), group them together with each other.
    if len(pending_fragment_indices) > 1:
        group_counter += 1
        gid = f"group-{group_counter:02d}"
        for j in pending_fragment_indices:
            scenes[j]["visual_group_id"] = gid
            scenes[j]["scene_type"] = "grouped"

    return scenes

def format_timestamp(seconds: float) -> str:
    """Convert seconds -> MM:SS for the Stage 3 timestamped-script format."""
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02d}:{secs:02d}"

def build_timestamped_lines(manifest_scenes: list) -> list:
    """
    Build one [MM:SS] line per manifest scene, for pasting into a Stage 3
    chat prompt (image prompt + editing cue generation).

    Scenes sharing a visual_group_id are merged into a single line —
    their scripts joined in order, timestamped at the group's first
    scene — so a run of short fragments that will share one background
    image also appears as one shot in the timestamped script, matching
    the "hold the same background across consecutive lines" instruction
    these workflows expect. Standalone scenes (or all scenes, in
    ShortVideo mode, where nothing is grouped) get their own line.
    """
    lines = []
    seen_groups = set()

    for scene in manifest_scenes:
        gid = scene.get("visual_group_id")

        if gid:
            if gid in seen_groups:
                continue  # already emitted this group's merged line
            seen_groups.add(gid)
            members = [s for s in manifest_scenes if s.get("visual_group_id") == gid]
            combined_text = " ".join(m["script"] for m in members)
            start = members[0]["whisperx_start"]
        else:
            combined_text = scene["script"]
            start = scene["whisperx_start"]

        lines.append(f"[{format_timestamp(start)}] {combined_text}")

    return lines

def write_timestamped_script(manifest_scenes: list, project: str) -> str:
    """Write the timestamped script file and return its path."""
    lines = build_timestamped_lines(manifest_scenes)
    out_path = f"{project}/timestamped_script.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path

def cut_audio_clip(source_audio: str, start: float, end: float, output_path: str, padding: float = 0.15):
    """Use ffmpeg to cut a clip from the source audio with small padding."""
    start_padded = max(0, start - padding)
    duration = (end - start) + (padding * 2)

    cmd = [
        "ffmpeg", "-y",
        "-i", source_audio,
        "-ss", str(start_padded),
        "-t", str(duration),
        "-c:a", "libmp3lame",
        "-q:a", "2",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ ffmpeg error cutting {output_path}:")
        print(result.stderr[-300:])
        raise RuntimeError(f"Failed to cut clip: {output_path}")

def main():
    parser = argparse.ArgumentParser(description="Auto-split a full voiceover into scenes using Whisper")
    parser.add_argument("--audio", required=True,
                         help="Filename of the full voiceover (e.g. shorts1.wav). "
                              "Must already be placed inside {project}/source_audio/")
    parser.add_argument("--project", required=True, help="Project folder name (e.g. ep02)")
    parser.add_argument("--max-seconds", type=float, default=10.0, help="Max duration per scene")
    parser.add_argument("--fragment-max-seconds", type=float, default=DEFAULT_FRAGMENT_MAX_SECONDS,
                         help="Only used with --video-type LongVideo. Scenes at or under this "
                              "duration are treated as fragments and merged into the next full "
                              "scene's visual group. Default 2.5s suits short-form label+explanation "
                              "scripts (Aeonium Glow). Long-form essay scripts with full sentences "
                              "throughout (The Interested Indian) rarely produce scenes this short — "
                              "try 8-15s there if you want grouping to actually trigger.")
    parser.add_argument("--model", default="large-v2", help="Whisper model size: tiny, base, small, medium, large-v2, large-v3")
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                         help="Run on GPU (cuda) or CPU. Use cpu if you don't have a CUDA GPU.")
    parser.add_argument("--title", default="Untitled Episode", help="Episode title for manifest")
    parser.add_argument("--voice", default="en-US-JennyNeural", help="Voice used for original generation")
    parser.add_argument("--video-type", choices=["ShortVideo", "LongVideo"], default="ShortVideo",
                         help="'ShortVideo' (default): simple sentence-splitting only, no scene "
                              "grouping — you eyeball image pairing yourself. "
                              "'LongVideo': adds duration-based scene grouping and a creative split "
                              "shot list. Note: this is about script STRUCTURE (numbered/stepped "
                              "lists with label+explanation pairs), not actual video duration — a "
                              "75-second Short with a numbered-steps format benefits from "
                              "--video-type LongVideo just as much as a 10-minute episode does.")
    args = parser.parse_args()

    audio_dir = f"{args.project}/audio"
    source_audio_dir = f"{args.project}/source_audio"
    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(source_audio_dir, exist_ok=True)

    audio_path = f"{source_audio_dir}/{args.audio}"
    if not os.path.exists(audio_path):
        print(f"\n✗ Audio file not found: {audio_path}")
        print(f"  Place '{args.audio}' inside the '{source_audio_dir}/' folder and try again.")
        return

    # Step 1: Transcribe with word timestamps
    whisper_result = transcribe_with_timestamps(audio_path, model_size=args.model, device=args.device)
    words = extract_words(whisper_result)
    print(f"\n✓ Transcribed {len(words)} words")

    # Save word-level timestamps for downstream use (karaoke captions, etc.)
    audio_stem = os.path.splitext(args.audio)[0]
    words_filename = f"source_audio/{audio_stem}_words.json"
    words_path = f"{args.project}/{words_filename}"
    with open(words_path, "w", encoding="utf-8") as f:
        json.dump({
            "audio": args.audio,
            "language": whisper_result.get("language", "en"),
            "word_segments": words
        }, f, indent=2)
    print(f"✓ Word segments saved: {words_path}")

    # Step 2: Group into sentences
    sentences = split_into_sentences(words)
    print(f"✓ Found {len(sentences)} sentences")

    # Step 3: Split any sentence over max_seconds
    scenes = build_scenes(sentences, args.max_seconds)
    print(f"✓ Split into {len(scenes)} scenes (max {args.max_seconds}s each)")

    # Step 3.5: Group scenes by timing — only for --format long.
    # Shorts stay simple: just sentence-split scenes, no grouping detection.
    # You decide visual pairing yourself by eye, which is faster than any
    # automated grouping for a handful of scenes. Long-form videos with
    # 40-60+ scenes benefit from automatic grouping instead.
    if args.video_type == "LongVideo":
        scenes = group_scenes_by_timing(scenes, fragment_max_seconds=args.fragment_max_seconds)
        grouped_count = sum(1 for s in scenes if s["scene_type"] == "grouped")
        print(f"✓ Grouped scenes by timing (fragment threshold {args.fragment_max_seconds}s): "
              f"{grouped_count} grouped, {len(scenes) - grouped_count} standalone")
    else:
        # Simple mode: every scene is standalone, no grouping
        for scene in scenes:
            scene["scene_type"] = "standalone"
            scene["visual_group_id"] = None

    # Step 4: Cut audio clips and build manifest
    print(f"\n{'─' * 55}")
    print("Cutting audio clips...")
    manifest_scenes = []

    for i, scene in enumerate(scenes, 1):
        scene_id = f"SCENE-{i:03d}"
        duration = scene["end"] - scene["start"]
        audio_filename = f"audio/{scene_id}.mp3"
        output_path = f"{args.project}/{audio_filename}"

        cut_audio_clip(audio_path, scene["start"], scene["end"], output_path)

        flag = " ⚠️  OVER LIMIT" if duration > args.max_seconds else ""
        corrected_text = apply_brand_corrections(scene["text"])

        if args.video_type == "LongVideo":
            type_tag = "🔗 GROUPED" if scene["scene_type"] == "grouped" else "▫️  STANDALONE"
            group_note = f" [{scene['visual_group_id']}]" if scene["visual_group_id"] else ""
            print(f"  {scene_id}  {duration:.1f}s  {type_tag}{group_note}  — {corrected_text[:45]}{flag}")
        else:
            print(f"  {scene_id}  {duration:.1f}s  — {corrected_text[:50]}{flag}")

        manifest_scenes.append({
            "id": scene_id,
            "image": f"images/{scene_id}.png",
            "audio": audio_filename,
            "script": corrected_text,
            "scene_type": scene["scene_type"],
            "visual_group_id": scene["visual_group_id"],
            "whisperx_start": round(scene["start"], 6),
            "whisperx_end": round(scene["end"], 6)
        })

    # Step 5: Write manifest.json
    manifest = {
        "episode": args.project,
        "title": args.title,
        "voice": args.voice,
        "word_segments_file": words_filename,
        "scenes": manifest_scenes
    }

    manifest_path = f"{args.project}/manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n{'─' * 55}")
    print(f"✓ Done! manifest.json written with {len(manifest_scenes)} scenes")
    print(f"  Location: {manifest_path}")

    # Step 6: Export the plain [MM:SS] timestamped script for Stage 3
    timestamped_path = write_timestamped_script(manifest_scenes, args.project)
    line_count = len(build_timestamped_lines(manifest_scenes))
    print(f"✓ timestamped_script.txt written with {line_count} lines")
    print(f"  Location: {timestamped_path}")
    print(f"  Paste this file's contents into your Stage 3 chat prompt.")

    # Summary of visual groups — shows which scenes can share one image
    groups = {}
    for s in manifest_scenes:
        if s["visual_group_id"]:
            groups.setdefault(s["visual_group_id"], []).append(s["id"])

    if groups and args.video_type == "LongVideo":
        print(f"\n  Visual groups detected (these can share ONE image each):")
        for gid, scene_ids in groups.items():
            print(f"    {gid}: {' + '.join(scene_ids)}")
        unique_images_needed = len(groups) + sum(
            1 for s in manifest_scenes if not s["visual_group_id"]
        )
        print(f"\n  Images needed: {len(manifest_scenes)} scenes → "
              f"{unique_images_needed} unique images (if groups share visuals)")

        # ── Creative Split Suggestion ──────────────────────────────
        # This is separate from the technical grouping above. The grouping
        # is mechanical — based on pause timing alone. This section is
        # for creative judgment: combine each group's full narration into
        # a single shot-list entry, ready to paste into a chat to get
        # actual image prompts written for it. The script doesn't generate
        # prompts itself — that's a creative call, not a timing calculation.
        print(f"\n{'─' * 55}")
        print(f"  PROPOSED CREATIVE SPLIT")
        print(f"  (paste this block into chat to get image prompts written)")
        print(f"{'─' * 55}")

        scene_by_id = {s["id"]: s for s in manifest_scenes}
        printed_groups = set()
        shot_number = 0

        for s in manifest_scenes:
            gid = s["visual_group_id"]
            if gid:
                if gid in printed_groups:
                    continue
                printed_groups.add(gid)
                member_ids = groups[gid]
                combined_text = " ".join(scene_by_id[mid]["script"] for mid in member_ids)
                shot_number += 1
                print(f"\n  SHOT {shot_number} [{gid} — covers {', '.join(member_ids)}]")
                print(f"  \"{combined_text}\"")
            else:
                shot_number += 1
                print(f"\n  SHOT {shot_number} [{s['id']} — standalone]")
                print(f"  \"{s['script']}\"")

    print(f"\nNext steps:")
    if args.video_type == "LongVideo":
        print(f"  1. Review scene types above (🔗 grouped / ▫️ standalone)")
        print(f"  2. Review the PROPOSED CREATIVE SPLIT shot list above")
        print(f"  3. Paste that shot list into chat to get image prompts per shot")
        print(f"  4. Generate one image per shot, named after its group_id")
        print(f"     (or its scene id, for standalone shots)")
        print(f"  5. Run stitch_video.py")
    else:
        print(f"  1. Review the scene splits above")
        print(f"  2. Decide by eye which scenes should share one image, if any")
        print(f"  3. Generate images named after SCENE-XXX (or reuse one filename")
        print(f"     across scenes you want to share — stitch_video.py finds")
        print(f"     whichever image file exists for that scene id)")
        print(f"  4. Run stitch_video.py")

if __name__ == "__main__":
    main()