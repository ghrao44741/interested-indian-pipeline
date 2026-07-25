"""
generate_source_audio.py
The Interested Indian — Full-Script Voiceover Generator

Supports two providers:
  - elevenlabs  (default, reads from channel_config.json + ELEVENLABS_API_KEY in .env)
  - edge        (free Edge TTS, no API key needed)

The script produces one continuous voiceover MP3 in {project}/source_audio/,
which auto_split_scenes_v1_stage3_export.py then ingests for WhisperX alignment.

USAGE — list voices:
    python generate_source_audio.py --list-voices
    python generate_source_audio.py --list-voices --provider edge --locale en-IN

USAGE — preview (first N sentences):
    python generate_source_audio.py --project ep01 \\
        --script ep01/script_*.txt --preview 3

USAGE — full generation:
    python generate_source_audio.py --project ep01 \\
        --script ep01/script_*.txt

    Override voice or provider:
    python generate_source_audio.py --project ep01 \\
        --script ep01/script_*.txt \\
        --provider elevenlabs --voice gYQ0co3BoppQZ8BDM3lj
    python generate_source_audio.py --project ep01 \\
        --script ep01/script_*.txt \\
        --provider edge --voice en-US-GuyNeural
"""

import argparse
import asyncio
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

# ── Auto-load .env ─────────────────────────────────────────────────────────────
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── Load channel config ────────────────────────────────────────────────────────
_cfg_path = Path(__file__).parent / "channel_config.json"
_voice_cfg: dict = {}
if _cfg_path.exists():
    try:
        _voice_cfg = json.loads(_cfg_path.read_text(encoding="utf-8")).get("voice", {})
    except Exception:
        pass

DEFAULT_PROVIDER   = _voice_cfg.get("provider", "elevenlabs")
DEFAULT_VOICE_EL   = _voice_cfg.get("default", "gYQ0co3BoppQZ8BDM3lj")
DEFAULT_MODEL_EL   = _voice_cfg.get("model", "eleven_multilingual_v2")
DEFAULT_STABILITY  = _voice_cfg.get("stability", 0.5)
DEFAULT_SIMILARITY = _voice_cfg.get("similarity_boost", 0.75)
DEFAULT_VOICE_EDGE = "en-US-GuyNeural"

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def first_n_sentences(text: str, n: int) -> str:
    sentences = SENTENCE_SPLIT_RE.split(text.strip())
    return " ".join(sentences[:n]).strip()


# ── ElevenLabs provider ────────────────────────────────────────────────────────

EL_CHUNK_LIMIT = 9500  # ElevenLabs max is 10000; keep buffer for safety


def _split_into_chunks(text: str, max_chars: int = EL_CHUNK_LIMIT) -> list[str]:
    """Split text on sentence boundaries so each chunk is ≤ max_chars."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, current = [], ""
    for sentence in sentences:
        # If a single sentence is too long, hard-split it
        if len(sentence) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            for i in range(0, len(sentence), max_chars):
                chunks.append(sentence[i:i + max_chars])
            continue
        if len(current) + len(sentence) + 1 > max_chars:
            chunks.append(current.strip())
            current = sentence
        else:
            current = (current + " " + sentence).strip() if current else sentence
    if current:
        chunks.append(current.strip())
    return chunks


def _elevenlabs_call(text: str, voice_id: str, api_key: str,
                      model: str, stability: float, similarity: float) -> bytes:
    """Single ElevenLabs API call → returns raw MP3 bytes."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = json.dumps({
        "text": text,
        "model_id": model,
        "voice_settings": {"stability": stability, "similarity_boost": similarity},
    }).encode()
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()[:300]
        print(f"❌ ElevenLabs API error {e.code}: {body_err}")
        sys.exit(1)


def _concat_mp3_chunks(chunk_paths: list[Path], output_path: str):
    """Concatenate MP3 chunk files → single output file via pydub or ffmpeg."""
    try:
        from pydub import AudioSegment
        combined = AudioSegment.empty()
        for p in chunk_paths:
            combined += AudioSegment.from_mp3(str(p))
        combined.export(output_path, format="mp3")
        return
    except ImportError:
        pass

    # Fallback: ffmpeg concat (requires ffmpeg on PATH)
    import tempfile, subprocess
    list_file = Path(output_path).parent / "_concat_list.txt"
    list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in chunk_paths))
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-c", "copy", output_path],
        check=True, capture_output=True
    )
    list_file.unlink(missing_ok=True)


def elevenlabs_generate(text: str, voice_id: str, output_path: str,
                         model: str = DEFAULT_MODEL_EL,
                         stability: float = DEFAULT_STABILITY,
                         similarity: float = DEFAULT_SIMILARITY):
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        print("❌ ELEVENLABS_API_KEY not set in .env")
        sys.exit(1)

    chunks = _split_into_chunks(text)
    print(f"  ElevenLabs: {len(chunks)} chunk(s), voice={voice_id}, model={model}")

    if len(chunks) == 1:
        audio_bytes = _elevenlabs_call(text, voice_id, api_key, model, stability, similarity)
        Path(output_path).write_bytes(audio_bytes)
        print(f"  ✓ Chunk 1/1 done")
    else:
        tmp_dir = Path(output_path).parent / "_el_chunks"
        tmp_dir.mkdir(exist_ok=True)
        chunk_paths = []
        for i, chunk in enumerate(chunks, 1):
            print(f"  ⏳ Chunk {i}/{len(chunks)} ({len(chunk)} chars)...")
            audio_bytes = _elevenlabs_call(chunk, voice_id, api_key, model, stability, similarity)
            p = tmp_dir / f"chunk_{i:03d}.mp3"
            p.write_bytes(audio_bytes)
            chunk_paths.append(p)
            print(f"  ✓ Chunk {i}/{len(chunks)} done")

        print(f"  Concatenating {len(chunks)} chunks...")
        _concat_mp3_chunks(chunk_paths, output_path)

        # Cleanup temp chunks
        for p in chunk_paths:
            p.unlink(missing_ok=True)
        tmp_dir.rmdir()
        print(f"  ✓ Concatenation done")


def elevenlabs_list_voices():
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        print("❌ ELEVENLABS_API_KEY not set in .env")
        sys.exit(1)
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": api_key}
    )
    data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    voices = data.get("voices", [])
    print(f"\nYour ElevenLabs voices ({len(voices)} total):")
    print(f"{'─' * 70}")
    for v in sorted(voices, key=lambda x: x.get("name", "")):
        print(f"  {v.get('name',''):<45} {v.get('voice_id','')}")


# ── Edge TTS provider ──────────────────────────────────────────────────────────

async def edge_list_voices(locale: str = "en-US"):
    try:
        import edge_tts
    except ImportError:
        print("❌ edge-tts not installed. Run: pip install edge-tts")
        sys.exit(1)
    voices = await edge_tts.list_voices()
    matches = [v for v in voices if v["Locale"] == locale]
    matches.sort(key=lambda v: v["ShortName"])
    print(f"\nEdge TTS voices for '{locale}' ({len(matches)} found):")
    print(f"{'─' * 60}")
    for v in matches:
        personalities = ", ".join(v.get("VoiceTag", {}).get("VoicePersonalities", []))
        print(f"  {v['ShortName']:30s} {v['Gender']:8s} {personalities}")


async def edge_generate(text: str, voice: str, output_path: str):
    try:
        import edge_tts
    except ImportError:
        print("❌ edge-tts not installed. Run: pip install edge-tts")
        sys.exit(1)
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


# ── Duration helper ────────────────────────────────────────────────────────────

def get_duration(path: str) -> float:
    try:
        from mutagen.mp3 import MP3
        return MP3(path).info.length
    except Exception:
        return 0.0


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project",   help="Episode folder (e.g. ep01)")
    parser.add_argument("--script",    help="Path to narration .txt file")
    parser.add_argument("--provider",  default=DEFAULT_PROVIDER,
                        choices=["elevenlabs", "edge"],
                        help=f"TTS provider (default from channel_config: {DEFAULT_PROVIDER})")
    parser.add_argument("--voice",     default=None,
                        help="Voice ID (ElevenLabs) or voice name (Edge TTS). Defaults from channel_config.json.")
    parser.add_argument("--out",       default="narration.mp3",
                        help="Output filename inside {project}/source_audio/")
    parser.add_argument("--preview",   type=int, metavar="N", default=None,
                        help="Synthesize only the first N sentences (for quick A/B voice testing)")
    parser.add_argument("--list-voices", action="store_true",
                        help="List available voices for the selected provider and exit")
    parser.add_argument("--locale",    default="en-US",
                        help="Locale filter for --list-voices with edge provider (e.g. en-IN)")
    args = parser.parse_args()

    # ── List voices ──
    if args.list_voices:
        if args.provider == "edge":
            await edge_list_voices(args.locale)
        else:
            elevenlabs_list_voices()
        return

    if not args.project or not args.script:
        parser.error("--project and --script are required unless using --list-voices")

    # Resolve script path
    script_path = Path(args.script)
    if not script_path.is_absolute():
        script_path = Path(__file__).parent / script_path
    if not script_path.exists():
        # Try glob if partial name given
        matches = list(Path(__file__).parent.glob(args.script))
        if matches:
            script_path = matches[0]
        else:
            print(f"❌ Script file not found: {args.script}")
            sys.exit(1)

    text = script_path.read_text(encoding="utf-8").strip()
    if not text:
        print(f"❌ Script file is empty: {script_path}")
        sys.exit(1)

    # Resolve voice
    if args.provider == "elevenlabs":
        voice = args.voice or DEFAULT_VOICE_EL
    else:
        voice = args.voice or DEFAULT_VOICE_EDGE

    source_audio_dir = Path(__file__).parent / args.project / "source_audio"
    source_audio_dir.mkdir(parents=True, exist_ok=True)

    if args.preview:
        text = first_n_sentences(text, args.preview)
        output_filename = f"preview_{voice[:20].replace('/', '_')}.mp3"
        print(f"\nGenerating voice preview ({args.preview} sentence(s))")
    else:
        output_filename = args.out
        print(f"\nGenerating full-script voiceover")

    output_path = str(source_audio_dir / output_filename)

    word_count = len(text.split())
    char_count = len(text)
    print(f"Script  : {script_path.name} ({word_count} words, {char_count} chars)")
    print(f"Provider: {args.provider}")
    print(f"Voice   : {voice}")
    print(f"Output  : {output_path}")
    print(f"{'─' * 55}")

    if args.provider == "elevenlabs":
        elevenlabs_generate(text, voice, output_path)
    else:
        await edge_generate(text, voice, output_path)

    duration = get_duration(output_path)
    if duration:
        print(f"✓ Done — {duration:.1f}s ({duration/60:.1f} min)")
    else:
        print(f"✓ Done — saved to {output_path}")

    if args.preview:
        print(f"\nListen, then re-run with different --voice to compare.")
        print(f"Drop --preview for full generation.")
    else:
        print(f"\nNext step:")
        print(f"  python auto_split_scenes_v1_stage3_export.py "
              f"--audio {output_filename} --project {args.project} --video-type LongVideo")


if __name__ == "__main__":
    asyncio.run(main())
