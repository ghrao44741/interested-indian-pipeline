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
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Stdlib-only module — safe to import under the WhisperX venv, which has none of
# the orchestrator's dependencies.
import source_ids

# This script is shared with sibling channels whose checkouts have no Channel
# Pack support at all. Importing defensively is what lets the same file keep
# working there unchanged — see resolve_creation_channel() below for the four
# cases, none of which silently discards an explicit --channel.
try:
    import channel_context
except ImportError:                                     # sibling-channel checkout
    channel_context = None

import whisperx
import gc
import torch

# Common Whisper mis-transcriptions for brand/product names.
# Add more pairs here as you discover new misheard terms.
# Known mis-transcriptions, applied to every scene's text before it reaches the
# manifest — and therefore before it reaches the burned-in captions. Whisper is
# transcribing TTS audio of a script we already have, so any divergence here is
# an ASR error, never the speaker misspeaking.
#
# Ordering matters: longer/more specific patterns must come before the shorter
# ones they contain, since these are applied in sequence.
BRAND_CORRECTIONS = {
    # ── sibling channels (kept: the split script is shared) ──
    r"\ba\s+yonium\s+glow\b": "Aeonium Glow",
    r"\bay\s*onium\s+glow\b": "Aeonium Glow",
    r"\beonium\s+glow\b": "Aeonium Glow",
    r"\bthat'?s\s+y\b": "That's Why",  # guard against "that's y" mis-hearing "That's Why"

    # ── The Interested Indian: exam/institution names ──
    r"\bnith\s*yugi\b": "Neet U.G.",
    r"\bneat\s+u\.?\s*g\.?\b": "Neet U.G.",
    r"\bneet[\s-]*ug\b": "Neet U.G.",
    r"\bneed\s+u\.?\s*g\.?\b": "Neet U.G.",
    r"\bneat\s+mess\b": "Neet mess",
    r"\bm\.?\s*b\.?\s*s\b(?!\.?\s*b)": "M.B.B.S.",   # "MBS seats" -> M.B.B.S.
    r"\bisro\b": "ISRO",
    r"\bisrao\b": "ISRO",
    r"\bc\.?\s*b\.?\s*i\b": "CBI",

    # ── Indian place and person names ──
    r"\bbangalore\s*u\b": "Bengaluru",
    r"\bjantar\s+r?mantar\b": "Jantar Mantar",
    r"\bvangachuk\b": "Wangchuk",
    r"\bwangachuk\b": "Wangchuk",
    r"\bjushi\b": "Joshi",

    # ── common word-level mishearings ──
    r"\bguest\s+papers?\b": "guess papers",   # hand-fixed twice before; make it stick
    r"\bmem\s*page\b": "meme page",
    r"\bmem\s+account\b": "meme account",
    r"\blag\s+(followers|members)\b": r"lakh \1",
    r"\bblackards?\b": "placards",
    r"\bbatten(s)?\b": r"baton\1",
    r"\bsound\s+bite\b": "soundbite",
}

def apply_brand_corrections(text: str) -> str:
    """Fix known brand-name mis-transcriptions, case-insensitive."""
    corrected = text
    for pattern, replacement in BRAND_CORRECTIONS.items():
        corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)
    return corrected

def transcribe_with_timestamps(audio_path: str, model_size: str = "base", device: str = "cuda",
                                compute_type: str = None, batch_size: int = 16) -> dict:
    """
    Run WhisperX locally: transcribe, then run forced phoneme alignment
    (wav2vec2) for word-level timestamps significantly more accurate than
    plain Whisper's word_timestamps. No HuggingFace token needed — that's
    only required for speaker diarization, which we don't use here since
    this is single-speaker TTS audio.
    """
    if compute_type is None:
        compute_type = "float16" if device == "cuda" else "int8"

    print(f"Loading WhisperX model ({model_size}, {device}, {compute_type})...")
    model = whisperx.load_model(model_size, device=device, compute_type=compute_type)

    print(f"Transcribing {audio_path}...")
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=batch_size)

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

MIN_EDGE_PADDING = 0.15   # never cut tighter than the old fixed pad
SAFETY_MARGIN    = 0.05   # keep off the neighbour's first word
ATTACK_BACKOFF   = 0.05   # start a hair inside the silence so the first consonant survives
MAX_TAIL_EXTEND  = 2.50   # cap for the final scene, which has no next-scene boundary


def detect_silences(source_audio: str, noise_db: int = -40, min_dur: float = 0.12):
    """Return [(silence_start, silence_end), ...] via ffmpeg's silencedetect.

    Pure ffmpeg on purpose — the split stage runs under the WhisperX venv, which
    has no pydub.
    """
    cmd = ["ffmpeg", "-i", source_audio, "-af",
           f"silencedetect=n={noise_db}dB:d={min_dur}", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True).stderr
    silences, cur = [], None
    for line in out.splitlines():
        if "silence_start:" in line:
            try:
                cur = float(line.split("silence_start:")[1].strip().split()[0])
            except (ValueError, IndexError):
                cur = None
        elif "silence_end:" in line and cur is not None:
            try:
                silences.append((cur, float(line.split("silence_end:")[1].strip().split()[0])))
            except (ValueError, IndexError):
                pass
            cur = None
    return silences


def refine_bounds(start: float, end: float, silences, prev_end=None, next_start=None):
    """Widen a scene's [start, end] out to where the audio actually goes quiet.

    Whisper's word-level end times land *before* the speech has finished
    decaying — sometimes by over a second — so cutting at end + a fixed 0.15s
    sliced the last word off. The rendered video then dropped it entirely
    ("...scheduled for June 21st" -> "...for 21st", which ASR hears as "May").
    Measured across three episodes and three different TTS providers this hit
    22-30% of scenes, so it is voice-independent and predates the voice switch.

    The tail extends to the start of the next detected silence, and the lead
    back to the end of the previous one, each clamped so a clip can never reach
    into a neighbouring scene's speech.
    """
    # Tail: the silence that immediately precedes the NEXT scene's speech is the
    # true end of this scene. Deliberately not the *first* silence after `end` —
    # that is often a pause inside the sentence (".. in about 48 hours, <pause>
    # he wasn't."), and stopping there re-creates the very truncation this fixes.
    # The final scene has no next-scene boundary; without a cap it would run to
    # the last silence anywhere in the file and swallow the trailing dead air.
    limit = (next_start - SAFETY_MARGIN) if next_start is not None else end + MAX_TAIL_EXTEND
    clip_end = end + MIN_EDGE_PADDING
    for s_start, _ in silences:
        if end - 0.05 <= s_start <= limit:
            clip_end = max(clip_end, s_start)   # keep the latest qualifying one
    clip_end = min(clip_end, limit)

    # Lead: the end of the silence *immediately* before this scene's speech —
    # the latest qualifying one, not the earliest. Taking the earliest reached
    # back across the previous scene's extended tail, so both clips contained
    # the same words and the render said them twice.
    # The previous scene's tail stops at that same silence's *start*, so keying
    # off its *end* (minus a hair, to not clip the word's attack) guarantees the
    # two clips can never overlap.
    floor_ = (prev_end + SAFETY_MARGIN) if prev_end is not None else 0.0
    lead_ends = [s_end for _, s_end in silences if s_end <= start + 0.05]
    if lead_ends:
        clip_start = max(lead_ends) - ATTACK_BACKOFF
    else:
        clip_start = start - MIN_EDGE_PADDING
    clip_start = max(clip_start, floor_, 0.0)
    clip_start = min(clip_start, start)

    clip_end = max(clip_end, end)          # never cut shorter than Whisper's span
    return clip_start, clip_end


def cut_audio_clip(source_audio: str, start: float, end: float, output_path: str,
                   padding: float = 0.15, prev_end: float = None, next_start: float = None,
                   silences=None):
    """Cut a clip from the source audio, extended to the real speech boundaries."""
    if silences:
        start_padded, clip_end = refine_bounds(start, end, silences, prev_end, next_start)
        duration = clip_end - start_padded
    else:
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
    parser.add_argument("--channel", default=None,
                        help="Channel pack this episode belongs to. Required in a "
                             "checkout that has channels/, refused in one that does "
                             "not. An episode's channel is chosen once, by a person, "
                             "and never changes afterwards.")
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
    parser.add_argument("--compute-type", default=None,
                         help="WhisperX compute type (e.g. float16, int8_float16, int8). "
                              "Defaults to float16 on cuda / int8 on cpu if not set. "
                              "Use int8_float16 on GPUs with limited VRAM (<8GB) to reduce OOM risk.")
    parser.add_argument("--batch-size", type=int, default=16,
                         help="WhisperX transcription batch size. Lower it (e.g. 8) on GPUs with "
                              "limited VRAM (<8GB) to reduce OOM risk.")
    parser.add_argument("--title", default="Untitled Episode", help="Episode title for manifest")
    parser.add_argument("--voice", default="en-US-JennyNeural", help="Voice used for original generation")
    parser.add_argument("--script", default=None,
                        help="Canonical script for persistent source ids. Defaults to the "
                             "project's production script (drafts/backups excluded). Required "
                             "explicitly when a project has several candidate scripts.")
    parser.add_argument("--allow-missing-script", action="store_true",
                        help="Development override: split without a canonical script. The "
                             "manifest is marked identity-blocked and downstream routing and "
                             "paid generation will refuse it.")
    parser.add_argument("--video-type", choices=["ShortVideo", "LongVideo"], default="ShortVideo",
                         help="'ShortVideo' (default): simple sentence-splitting only, no scene "
                              "grouping — you eyeball image pairing yourself. "
                              "'LongVideo': adds duration-based scene grouping and a creative split "
                              "shot list. Note: this is about script STRUCTURE (numbered/stepped "
                              "lists with label+explanation pairs), not actual video duration — a "
                              "75-second Short with a numbered-steps format benefits from "
                              "--video-type LongVideo just as much as a 10-minute episode does.")
    args = parser.parse_args()

    # ── Channel assignment ──────────────────────────────────────────────────
    # Settled before any transcription work, so an unusable answer costs seconds
    # rather than a full GPU pass. Four cases, and an explicit --channel is never
    # silently dropped: a manifest that quietly lacks the channel someone asked
    # for would be wrong in a way nothing downstream ever reports.
    channel_id = channel_dna_version = None
    if channel_context is None:
        if args.channel:
            print(f"\n✗ --channel {args.channel!r} was given, but this checkout has no "
                  f"channel_context.py and cannot honour a channel assignment.")
            return 2
    else:
        existing_path = Path(args.project) / "manifest.json"
        existing_id = None
        if existing_path.is_file():
            try:
                existing_id = json.loads(
                    existing_path.read_text(encoding="utf-8")).get("channel_id")
            except (OSError, json.JSONDecodeError):
                existing_id = None
        if existing_id and args.channel and args.channel != existing_id:
            print(f"\n✗ {args.project} already belongs to channel {existing_id!r}; "
                  f"refusing to reassign it to {args.channel!r}. A channel is chosen "
                  f"once and never changes.")
            return 2
        try:
            channel_id, channel_dna_version = channel_context.resolve_creation_channel(
                args.channel or existing_id)
        except channel_context.ChannelError as e:
            print(f"\n✗ {e}")
            return 2

    audio_dir = f"{args.project}/audio"
    source_audio_dir = f"{args.project}/source_audio"
    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(source_audio_dir, exist_ok=True)

    audio_path = f"{source_audio_dir}/{args.audio}"
    if not os.path.exists(audio_path):
        print(f"\n✗ Audio file not found: {audio_path}")
        print(f"  Place '{args.audio}' inside the '{source_audio_dir}/' folder and try again.")
        return 2

    # ── Narration binding verification ──────────────────────────────────────
    # Required only when a real channel was actually assigned above — not
    # merely because channel_context happens to be importable. A sibling
    # checkout could have the module on its path with no channels/ directory
    # at all, in which case channel_id is None and none of this applies; that
    # is the genuinely-unchanged legacy case.
    #
    # Runs before transcribe_with_timestamps — the expensive GPU pass — so a
    # refusal costs seconds, not minutes.
    verified_voice = args.voice
    verified_narration_fields = {}
    if channel_id:
        sidecar_path = Path(f"{audio_path}.voice.json")
        if not sidecar_path.is_file():
            print(f"\n✗ no verified narration sidecar at {sidecar_path}")
            print(f"  This channel requires narration produced through "
                  f"generate_source_audio.py's production path, which writes one. "
                  f"Re-narrate rather than supplying audio directly.")
            return 2
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"\n✗ {sidecar_path} is not valid JSON: {e}")
            return 2

        problems = []
        if sidecar.get("schema_version") != 1:
            problems.append(f"unsupported sidecar schema_version "
                            f"{sidecar.get('schema_version')!r}")
        if sidecar.get("channel_id") != channel_id:
            problems.append(f"sidecar names channel {sidecar.get('channel_id')!r}, "
                            f"this episode is assigned to {channel_id!r}")

        ctx_for_check = None
        try:
            ctx_for_check = channel_context.load_channel(channel_id)
        except channel_context.ChannelError as e:
            problems.append(str(e))

        recorded_hash = sidecar.get("voice_profile_sha256")
        effective = sidecar.get("effective_profile")
        if ctx_for_check is not None:
            # A profile re-approved between narration and split must be caught
            # here, not only later at the generation gate.
            if recorded_hash != ctx_for_check.voice_profile_sha256:
                problems.append(
                    f"sidecar's voice_profile_sha256 no longer matches {channel_id}'s "
                    f"current approved profile — the profile changed since this "
                    f"narration was produced; re-narrate")
            # The missing link a hash-only check would leave open: the hash
            # must actually describe the settings it claims to, and both must
            # still match what the channel currently approves — not merely
            # agree with each other while describing something else entirely.
            self_hash = (channel_context.canonical_sha256(effective)
                        if effective is not None else None)
            if self_hash != recorded_hash:
                problems.append(
                    "sidecar's effective_profile does not hash to its own recorded "
                    "voice_profile_sha256 — the settings and the hash claiming to "
                    "describe them disagree")
            current_profile = channel_context._thaw(
                (ctx_for_check.config.get("voice", {}) or {}).get("approved_profile") or {})
            if effective != current_profile:
                problems.append(
                    "sidecar's effective_profile no longer matches the channel's "
                    "current approved_profile exactly")

        recorded_audio_sha = sidecar.get("audio_sha256")
        if not recorded_audio_sha:
            problems.append("sidecar has no audio_sha256 recorded")
        else:
            found_audio_sha = hashlib.sha256(Path(audio_path).read_bytes()).hexdigest()
            if found_audio_sha != recorded_audio_sha:
                problems.append(
                    f"the audio file no longer matches the sidecar's recorded hash — "
                    f"someone replaced it after narration (recorded "
                    f"{recorded_audio_sha[:12]}…, found {found_audio_sha[:12]}…)")

        if problems:
            print(f"\n✗ narration sidecar verification failed for {sidecar_path}:")
            for p in problems:
                print(f"  - {p}")
            return 2

        print(f"  ✓ narration sidecar verified against {channel_id}'s approved "
              f"voice profile")
        voice_key = "voice" if "voice" in effective else "voice_id"
        verified_voice = effective.get(voice_key, args.voice)
        verified_narration_fields = {
            "narration_audio_file": f"source_audio/{args.audio}",
            "narration_voice_profile_sha256": recorded_hash,
            "narration_audio_sha256": recorded_audio_sha,
            "narration_effective_profile": effective,
        }

    # Step 1: Transcribe with word timestamps
    whisper_result = transcribe_with_timestamps(audio_path, model_size=args.model, device=args.device,
                                                 compute_type=args.compute_type, batch_size=args.batch_size)
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
    # Detected once for the whole narration, then reused for every clip so each
    # scene can be extended to where the speech actually stops (see refine_bounds).
    silences = detect_silences(audio_path)
    if silences:
        print(f"  {len(silences)} silence region(s) detected for boundary snapping")
    else:
        print("  ⚠ No silence regions detected — falling back to fixed padding, which is "
              "known to truncate the last word of ~25% of scenes. Check ffmpeg/silencedetect.")
    manifest_scenes = []

    for i, scene in enumerate(scenes, 1):
        scene_id = f"SCENE-{i:03d}"
        duration = scene["end"] - scene["start"]
        audio_filename = f"audio/{scene_id}.mp3"
        output_path = f"{args.project}/{audio_filename}"

        # Neighbour boundaries so the clip can pad into the surrounding silence
        # without ever reaching into the adjacent scene's speech.
        prev_end   = scenes[i - 2]["end"]   if i >= 2            else None
        next_start = scenes[i]["start"]     if i < len(scenes)   else None
        cut_audio_clip(audio_path, scene["start"], scene["end"], output_path,
                       prev_end=prev_end, next_start=next_start, silences=silences)

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
            "duration": round(scene["end"] - scene["start"], 3),
            "whisperx_start": round(scene["start"], 6),
            "whisperx_end": round(scene["end"], 6)
        })

    # Step 4.5: Attach persistent source identity.
    #
    # Scenes above are numbered by position, and position moves on every
    # re-narration — that renumbering silently reassigned artwork three times in
    # one session. Identity comes from the script instead: stable SRC ids
    # assigned per sentence, aligned back to these ASR-derived scenes. SCENE-NNN
    # stays as display order only.
    identity = source_ids.IDENTITY_OK
    identity_reasons = []
    script_path = args.script
    if not script_path and not args.allow_missing_script:
        # strict: ambiguous or missing script raises rather than guessing, because
        # everything downstream keys off the identity derived here.
        script_path = source_ids.pick_production_script(args.project, strict=True)
    elif not script_path:
        script_path = source_ids.pick_production_script(args.project, strict=False)

    migration_blocked = None
    if script_path and Path(script_path).exists():
        try:
            units, change = source_ids.sync_units(
                Path(args.project), Path(script_path).read_text(encoding="utf-8"))
        except source_ids.MigrationBlocked as blocked:
            # Sidecar untouched. Still emit a manifest so the operator can see the
            # split, but mark it blocked so nothing routes or generates from it.
            migration_blocked = blocked
            units = source_ids.load_sidecar(Path(args.project))["units"]
            change = {"added": [], "changed": [], "removed": [], "unchanged": 0}
            print(f"\n  ⛔ MIGRATION BLOCKED — sidecar left unchanged, resolution required:")
            for a in blocked.ambiguities[:6]:
                print(f"     - [{a['key']}] {a['message']}")
                print(f"       resolve with: {{\"action\": \"reuse\", \"candidate_index\": "
                      f"{a['candidates'][0]['index']}, \"source_id\": "
                      f"\"{a['old_source_ids'][0]}\"}}  or  {{\"action\": \"new\"}}")

        for scene, info in zip(manifest_scenes, source_ids.align_scenes(manifest_scenes, units)):
            scene.update(info)

        # Persistent visual identity, anchored to the text each visual was
        # approved against — so a re-split re-attaches artwork by overlap rather
        # than by recomputed S01/S02 position.
        if migration_blocked is None:
            migrated = source_ids.migrate_visual_slots(units)
            assignments, tie_issues = source_ids.assign_visual_assets(units, manifest_scenes)
            for scene, info in zip(manifest_scenes, assignments):
                scene.update(info)
            # save_units() preserves the monotonic id high-water mark; recomputing
            # it from the current units would recycle a deleted id.
            source_ids.save_units(Path(args.project), units)
            if migrated:
                print(f"  migrated {migrated} pre-lifecycle visual slot(s) as 'planned'")
        else:
            # Nothing is allocated or persisted against an unresolved migration.
            tie_issues = []

        identity, identity_reasons = source_ids.identity_state(manifest_scenes)
        if migration_blocked is not None:
            identity = source_ids.IDENTITY_BLOCKED
            identity_reasons = ([f"unresolved source migration [{a['key']}]: {a['message']}"
                                 for a in migration_blocked.ambiguities] + identity_reasons)

        print(f"\n✓ Source identity: {len(units)} unit(s) from {Path(script_path).name}")
        if change["changed"] or change["added"] or change["removed"]:
            print(f"  script changed since last run — edited: {len(change['changed'])}, "
                  f"new: {len(change['added'])}, removed: {len(change['removed'])}")
        for issue in tie_issues[:4]:
            print(f"  ⚠ visual tie: {issue}")
        if identity == source_ids.IDENTITY_BLOCKED:
            print(f"\n  ⛔ IDENTITY BLOCKED — routing and paid generation will refuse this manifest:")
            for r in identity_reasons:
                print(f"     - {r}")
            print("     Resolve by correcting the script or re-running the affected scenes.")
    else:
        identity = source_ids.IDENTITY_BLOCKED
        identity_reasons = ["no canonical script — scenes carry no persistent identity"]
        print("\n  ⚠ No canonical script — artwork cannot survive a re-split "
              "(--allow-missing-script was used).")

    # Step 5: Write manifest.json
    total_duration = round(sum(s["duration"] for s in manifest_scenes), 3)
    manifest = {
        "episode": os.path.basename(args.project.rstrip("/\\")),  # always "ep01", never full path
        # Which channel's DNA, character, poses and rules apply to this episode.
        # Written once at creation; every later stage reads it rather than being
        # told, so an episode's identity cannot be changed from a command line.
        "channel_id": channel_id,
        "channel_dna_version": channel_dna_version,
        "title": args.title,
        # Keeps its existing compatibility meaning: a plain voice-name string.
        # When a verified sidecar exists, this comes from IT rather than from
        # the free-form --voice claim; it does not become an object — the full
        # profile and its hashes live in the separate narration_* fields below.
        "voice": verified_voice,
        "word_segments_file": words_filename,
        "total_duration": total_duration,
        # Downstream gate: routing and paid generation must refuse a blocked
        # manifest rather than generate against uncertain identity.
        "identity_state": identity,
        "identity_reasons": identity_reasons,
        **verified_narration_fields,
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
    sys.exit(main())