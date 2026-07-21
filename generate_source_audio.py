"""
generate_source_audio.py
shorts_pipeline2 — Full-Script Voiceover Generator (Edge TTS)

Companion to auto_split_scenes_v1_stage3_export.py. That script expects
ONE continuous voiceover file already sitting in {project}/source_audio/
before it runs WhisperX and splits it into scenes. This script produces
that file, directly from a raw narration .txt (no manifest.json needed —
there isn't one yet at this point in the pipeline).

Uses edge_tts.Communicate() (the Python function), not the edge-tts CLI.
Calling the function directly avoids passing a long script through a
shell argument (quoting headaches, OS argument-length limits) — the
library itself chunks long text internally before sending it over the
websocket, so a multi-thousand-word script is not a special case here.

FOLDER CONVENTION (matches the rest of shorts_pipeline2):
    {project}/source_audio/{output filename}   <- written here

SETUP (run once):
    pip install edge-tts mutagen

USAGE — browse voices:
    python generate_source_audio.py --list-voices
    python generate_source_audio.py --list-voices --locale en-GB

USAGE — preview a voice on just the opening lines (fast, cheap):
    python generate_source_audio.py --project interested-indian-04 \\
        --script script_south_india_tax_devolution.txt \\
        --voice en-US-GuyNeural --preview 2

    Writes {project}/source_audio/preview_en-US-GuyNeural.mp3 using only
    the first 2 sentences — run this once per candidate voice, listen,
    then generate the full file with the one you like.

USAGE — full generation:
    python generate_source_audio.py --project interested-indian-04 \\
        --script script_south_india_tax_devolution.txt \\
        --voice en-US-GuyNeural \\
        --out narration.mp3

    --script can be an absolute path or a path relative to where you run
    this from (e.g. the script_*.txt file downloaded from Stage 2).
    --voice defaults to en-US-JennyNeural to match the existing manifest
    convention, but for a calm analytical essay voice, try a few
    candidates with --preview first — en-US-GuyNeural, en-US-AndrewNeural,
    and en-US-ChristopherNeural are common picks for this register.
"""

import argparse
import asyncio
import os
import re

import edge_tts
from mutagen.mp3 import MP3


SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def first_n_sentences(text: str, n: int) -> str:
    """Return the first n sentences of text, roughly split on . ! ?"""
    sentences = SENTENCE_SPLIT_RE.split(text.strip())
    return " ".join(sentences[:n]).strip()


async def list_voices(locale: str = "en-US"):
    voices = await edge_tts.list_voices()
    matches = [v for v in voices if v["Locale"] == locale]
    matches.sort(key=lambda v: v["ShortName"])

    print(f"\nVoices for locale '{locale}' ({len(matches)} found):")
    print(f"{'─' * 60}")
    for v in matches:
        personalities = ", ".join(v.get("VoiceTag", {}).get("VoicePersonalities", []))
        print(f"  {v['ShortName']:30s} {v['Gender']:8s} {personalities}")


async def generate_full_audio(text: str, voice: str, output_path: str):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Project folder name (e.g. interested-indian-04). Required unless --list-voices.")
    parser.add_argument("--script", help="Path to the raw narration .txt file. Required unless --list-voices.")
    parser.add_argument("--voice", default="en-US-JennyNeural", help="Edge TTS voice name")
    parser.add_argument("--out", default="narration.mp3", help="Output filename, written inside {project}/source_audio/")
    parser.add_argument("--preview", type=int, metavar="N", default=None,
                         help="Only synthesize the first N sentences of the script, for quickly "
                              "A/B-ing voices before committing to a full generation. Output is "
                              "written as preview_{voice}.mp3 (ignores --out) so multiple voice "
                              "previews don't overwrite each other.")
    parser.add_argument("--list-voices", action="store_true",
                         help="List available Edge TTS voices for a locale and exit — no audio generated.")
    parser.add_argument("--locale", default="en-US", help="Locale filter for --list-voices, e.g. en-US, en-GB, en-IN")
    args = parser.parse_args()

    if args.list_voices:
        await list_voices(args.locale)
        return

    if not args.project or not args.script:
        parser.error("--project and --script are required unless using --list-voices")

    if not os.path.exists(args.script):
        print(f"\n✗ Script file not found: {args.script}")
        return

    with open(args.script, "r", encoding="utf-8") as f:
        text = f.read().strip()

    if not text:
        print(f"\n✗ Script file is empty: {args.script}")
        return

    source_audio_dir = f"{args.project}/source_audio"
    os.makedirs(source_audio_dir, exist_ok=True)

    if args.preview:
        text = first_n_sentences(text, args.preview)
        output_filename = f"preview_{args.voice}.mp3"
        output_path = f"{source_audio_dir}/{output_filename}"
        print(f"\nGenerating voice preview ({args.preview} sentence{'s' if args.preview != 1 else ''})")
    else:
        output_filename = args.out
        output_path = f"{source_audio_dir}/{output_filename}"
        print(f"\nGenerating full-script voiceover")

    word_count = len(text.split())
    print(f"Script: {args.script} ({word_count} words used)")
    print(f"Voice: {args.voice}")
    print(f"Output: {output_path}")
    print(f"{'─' * 55}")

    await generate_full_audio(text, args.voice, output_path)

    duration = MP3(output_path).info.length
    print(f"✓ Done — {duration:.1f}s ({duration/60:.1f} min)")

    if args.preview:
        print(f"\nListen to {output_path}, then re-run with a different --voice to compare,")
        print(f"or drop --preview and add --out narration.mp3 for the full generation.")
    else:
        print(f"\nNext step:")
        print(f"  python auto_split_scenes_v1_stage3_export.py --audio {output_filename} "
              f"--project {args.project} --video-type LongVideo")


if __name__ == "__main__":
    asyncio.run(main())
