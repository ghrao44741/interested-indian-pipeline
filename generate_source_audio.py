"""
generate_source_audio.py
The Interested Indian — Full-Script Voiceover Generator

Supports five providers:
  - gemini_cloudtts  (Gemini 3.1 via Cloud Text-to-Speech, en-IN locale + style
                       prompt for an Indian-accented narration. Requires the
                       gcloud CLI installed and authenticated: gcloud auth login.
                       Cloud TTS rejects plain API keys, unlike the other providers.
                       Documented cross-chunk voice-drift issue on long scripts.)
  - gemini           (Gemini Developer API — reads GEMINI_API_KEY from .env, no
                       locale/style control, sounds more generically American)
  - elevenlabs       (default — reads from channel_config.json + ELEVENLABS_API_KEY
                       in .env)
  - grok             (xAI — reads from channel_config.json + XAI_API_KEY in .env,
                       same key already used for image generation. Supports custom
                       voice cloning via console.x.ai's Voice Library — live
                       passphrase verification there, not automatable from here.
                       15,000 char/request limit, cheaper than ElevenLabs.)
  - edge             (free Edge TTS, no API key needed)

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
        --provider gemini --voice Charon
    python generate_source_audio.py --project ep01 \\
        --script ep01/script_*.txt \\
        --provider elevenlabs --voice gYQ0co3BoppQZ8BDM3lj
    python generate_source_audio.py --project ep01 \\
        --script ep01/script_*.txt \\
        --provider edge --voice en-US-GuyNeural
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import channel_context

# The pipeline root — mockable in tests (mock.patch.object(gsa, "SCRIPT_DIR", ...))
# so a relative --out can be proven to resolve against it rather than against a
# project directory, without needing the real repository layout.
SCRIPT_DIR = Path(__file__).parent

# ── Auto-load .env ─────────────────────────────────────────────────────────────
_env_path = SCRIPT_DIR / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── Load channel config ────────────────────────────────────────────────────────
_cfg_path = SCRIPT_DIR / "channel_config.json"
_voice_cfg: dict = {}
if _cfg_path.exists():
    try:
        _voice_cfg = json.loads(_cfg_path.read_text(encoding="utf-8")).get("voice", {})
    except Exception:
        pass

DEFAULT_PROVIDER   = _voice_cfg.get("provider", "gemini")
DEFAULT_VOICE_EL   = _voice_cfg.get("elevenlabs_default", "gYQ0co3BoppQZ8BDM3lj")
DEFAULT_MODEL_EL   = _voice_cfg.get("model", "eleven_multilingual_v2")
DEFAULT_STABILITY  = _voice_cfg.get("stability", 0.5)
DEFAULT_SIMILARITY = _voice_cfg.get("similarity_boost", 0.75)
DEFAULT_SPEED_EL   = _voice_cfg.get("speed", 1.0)  # ElevenLabs generation-time pacing control
                                                     # (0.7-1.2 typical range, NOT a post-hoc
                                                     # time-stretch — baked into synthesis, so
                                                     # pitch/timbre stay natural at any setting).
DEFAULT_VOICE_EDGE   = "en-US-GuyNeural"
DEFAULT_VOICE_GEMINI   = _voice_cfg.get("gemini_voice", "Charon")
DEFAULT_MODEL_GEMINI   = "gemini-2.5-flash-preview-tts"
DEFAULT_SPEAKING_RATE  = _voice_cfg.get("gemini_speaking_rate", None)  # None = Gemini default (1.0)
GEMINI_CHUNK_LIMIT   = 4500   # conservative limit per API call for long scripts

DEFAULT_MODEL_CLOUDTTS  = _voice_cfg.get("gemini_cloudtts_model", "gemini-3.1-flash-tts-preview")
DEFAULT_LOCALE_CLOUDTTS = _voice_cfg.get("gemini_cloudtts_locale", "en-IN")
DEFAULT_STYLE_CLOUDTTS  = _voice_cfg.get("gemini_cloudtts_style", "")

DEFAULT_VOICE_GROK    = _voice_cfg.get("grok_voice_id", "eve")   # built-in (eve/ara/leo/rex/sal)
                                                                   # or an 8-char custom voice_id
                                                                   # from console.x.ai voice cloning
DEFAULT_LANGUAGE_GROK = _voice_cfg.get("grok_language", "en")     # BCP-47 code, or "auto"
DEFAULT_SPEED_GROK    = _voice_cfg.get("grok_speed", 1.0)         # generation-time pacing, same
                                                                   # idea as ElevenLabs' speed
GROK_TTS_URL = "https://api.x.ai/v1/tts"
GROK_CHUNK_LIMIT = 14500  # xAI's documented hard limit is 15,000 chars/request; buffer for safety

PRODUCTION_TARGET_LUFS = -14.0  # fixed pipeline invariant for every production write —
                                  # deliberately NOT sourced from channel_config.json or
                                  # any per-channel legacy adapter, so a channel cannot
                                  # accidentally (or silently) loosen this by editing its
                                  # generated compatibility file.

TARGET_LUFS = _voice_cfg.get("target_lufs", -14.0)  # broadcast/streaming-standard loudness
                                                       # target — normalized once here, on the
                                                       # single master narration file, so every
                                                       # provider/voice switch (and every future
                                                       # episode) sounds consistently loud instead
                                                       # of whatever level the TTS API happened to
                                                       # output. Applied BEFORE the split stage
                                                       # cuts per-scene clips from this file, so
                                                       # every clip inherits it for free rather
                                                       # than each tiny clip being normalized
                                                       # independently (unreliable on short audio).
CLOUD_TTS_URL   = "https://texttospeech.googleapis.com/v1beta1/text:synthesize"
CLOUDTTS_CHUNK_LIMIT = 3500   # Google's official Gemini-TTS docs state the text field
                               # (and the prompt field, separately) can be at most 4,000
                               # BYTES — using chars here as a proxy, so 3500 leaves a
                               # safety margin for any multi-byte characters plus the
                               # ~130-char style_prompt, which counts against its own
                               # separate 4,000-byte limit, not this one.

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def first_n_sentences(text: str, n: int) -> str:
    sentences = SENTENCE_SPLIT_RE.split(text.strip())
    return " ".join(sentences[:n]).strip()


# ── ElevenLabs provider ────────────────────────────────────────────────────────

EL_CHUNK_LIMIT = 9500  # eleven_multilingual_v2 (the configured production model) caps at
                        # 10000 chars/request — 9500 leaves a safety buffer. Note:
                        # eleven_turbo_v2_5 / eleven_flash_v2_5 support up to 40000 chars
                        # (single-request, zero chunk-seam risk) but were A/B tested and
                        # rejected — they render this voice audibly differently from
                        # multilingual_v2. If the production model ever changes back to
                        # turbo/flash v2.5, raise this limit accordingly.


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
                      model: str, stability: float, similarity: float,
                      speed: float = 1.0) -> bytes:
    """Single ElevenLabs API call → returns raw MP3 bytes."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = json.dumps({
        "text": text,
        "model_id": model,
        "voice_settings": {"stability": stability, "similarity_boost": similarity, "speed": speed},
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


def _compute_chunk_boundaries(chunk_paths: list[Path]) -> list[float]:
    """Cumulative seconds where each chunk boundary falls in the final concatenated
    audio — computed from each chunk's own real duration before the chunk files are
    deleted, so a reviewer can jump straight to a seam instead of estimating it from
    character offsets (speech rate isn't constant, so char-ratio timing drifts)."""
    from pydub import AudioSegment
    boundaries = []
    cursor = 0.0
    for p in chunk_paths[:-1]:
        cursor += len(AudioSegment.from_mp3(str(p))) / 1000.0
        boundaries.append(round(cursor, 2))
    return boundaries


def elevenlabs_generate(text: str, voice_id: str, output_path: str,
                         model: str = DEFAULT_MODEL_EL,
                         stability: float = DEFAULT_STABILITY,
                         similarity: float = DEFAULT_SIMILARITY,
                         speed: float = DEFAULT_SPEED_EL) -> list[float]:
    """Returns the list of chunk-boundary timestamps (seconds) in the final audio,
    or [] if the script fit in a single request (no seam)."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        print("❌ ELEVENLABS_API_KEY not set in .env")
        sys.exit(1)

    chunks = _split_into_chunks(text)
    print(f"  ElevenLabs: {len(chunks)} chunk(s), voice={voice_id}, model={model}, speed={speed}")

    if len(chunks) == 1:
        audio_bytes = _elevenlabs_call(text, voice_id, api_key, model, stability, similarity, speed)
        Path(output_path).write_bytes(audio_bytes)
        print(f"  ✓ Chunk 1/1 done")
        return []
    else:
        tmp_dir = Path(output_path).parent / "_el_chunks"
        tmp_dir.mkdir(exist_ok=True)
        chunk_paths = []
        for i, chunk in enumerate(chunks, 1):
            print(f"  ⏳ Chunk {i}/{len(chunks)} ({len(chunk)} chars)...")
            audio_bytes = _elevenlabs_call(chunk, voice_id, api_key, model, stability, similarity, speed)
            p = tmp_dir / f"chunk_{i:03d}.mp3"
            p.write_bytes(audio_bytes)
            chunk_paths.append(p)
            print(f"  ✓ Chunk {i}/{len(chunks)} done")

        boundaries = _compute_chunk_boundaries(chunk_paths)

        print(f"  Concatenating {len(chunks)} chunks...")
        _concat_mp3_chunks(chunk_paths, output_path)

        # Cleanup temp chunks
        for p in chunk_paths:
            p.unlink(missing_ok=True)
        tmp_dir.rmdir()
        print(f"  ✓ Concatenation done")
        return boundaries


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


# ── Grok (xAI) TTS provider ──────────────────────────────────────────────────────
#
# Reuses XAI_API_KEY (already in .env for image generation — no new secret needed).
# Response shape: raw binary MP3 bytes directly (Content-Type: audio/mpeg), NOT a
# JSON+base64 wrapper — xAI's own docs page describes the JSON shape, but a real
# live test call proved that wrong (see _grok_call's docstring for how this was
# caught). Don't trust that docs page if xAI changes this again — verify live.
#
# voice_id is a built-in voice (see grok_list_voices() for the real, current list —
# 26 as of 2026-07-29, not just the original 5) or an 8-char custom voice_id from
# console.x.ai's Voice Library (live passphrase verification required there — not
# something this script can do on a user's behalf). Custom Voice cloning itself is
# geo-restricted by xAI to the US (excluding Illinois); using the
# TTS endpoint with a voice_id you already have is not similarly restricted.

def _grok_call(text: str, voice_id: str, api_key: str, language: str, speed: float) -> bytes:
    """Single Grok TTS API call → returns raw MP3 bytes.

    NOTE: xAI's own docs page (docs.x.ai/developers/rest-api-reference/inference/
    text-to-speech) describes a JSON response with a base64 'audio' field — a real,
    live test call proved that wrong: the API actually returns raw binary audio
    directly (Content-Type: audio/mpeg, body starts with an MPEG frame-sync byte
    0xFF). Trusting the docs summary without a live test would have silently
    produced a corrupt file (attempting to JSON-decode raw MP3 bytes crashes
    immediately with a UnicodeDecodeError, which is how this was caught).
    """
    body = json.dumps({
        "text": text,
        "voice_id": voice_id,
        "language": language,
        "speed": speed,
        "output_format": {"codec": "mp3"},
    }).encode()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(GROK_TTS_URL, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()[:300]
        print(f"❌ Grok TTS API error {e.code}: {body_err}")
        sys.exit(1)


def grok_generate(text: str, voice_id: str, output_path: str,
                   language: str = DEFAULT_LANGUAGE_GROK,
                   speed: float = DEFAULT_SPEED_GROK) -> list[float]:
    """Returns the list of chunk-boundary timestamps (seconds) in the final audio,
    or [] if the script fit in a single request (no seam)."""
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        print("❌ XAI_API_KEY not set in .env")
        sys.exit(1)

    chunks = _split_into_chunks(text, max_chars=GROK_CHUNK_LIMIT)
    print(f"  Grok TTS: {len(chunks)} chunk(s), voice={voice_id}, language={language}, speed={speed}")

    if len(chunks) == 1:
        audio_bytes = _grok_call(text, voice_id, api_key, language, speed)
        Path(output_path).write_bytes(audio_bytes)
        print(f"  ✓ Chunk 1/1 done")
        return []
    else:
        tmp_dir = Path(output_path).parent / "_grok_chunks"
        tmp_dir.mkdir(exist_ok=True)
        chunk_paths = []
        for i, chunk in enumerate(chunks, 1):
            print(f"  ⏳ Chunk {i}/{len(chunks)} ({len(chunk)} chars)...")
            audio_bytes = _grok_call(chunk, voice_id, api_key, language, speed)
            p = tmp_dir / f"chunk_{i:03d}.mp3"
            p.write_bytes(audio_bytes)
            chunk_paths.append(p)
            print(f"  ✓ Chunk {i}/{len(chunks)} done")

        boundaries = _compute_chunk_boundaries(chunk_paths)

        print(f"  Concatenating {len(chunks)} chunks...")
        _concat_mp3_chunks(chunk_paths, output_path)

        for p in chunk_paths:
            p.unlink(missing_ok=True)
        tmp_dir.rmdir()
        print(f"  ✓ Concatenation done")
        return boundaries


def grok_list_voices():
    """Calls GET /v1/tts/voices for the real, current list — NOT a hardcoded
    static list. xAI added 21 new voices beyond the original 5 in July 2026;
    a static list would already be stale (confirmed: 'naksh', used by this
    channel, isn't one of the original 5 and wouldn't appear in one)."""
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        print("❌ XAI_API_KEY not set in .env")
        sys.exit(1)
    req = urllib.request.Request(
        "https://api.x.ai/v1/tts/voices",
        headers={"Authorization": f"Bearer {api_key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"❌ Grok TTS API error {e.code}: {e.read().decode()[:300]}")
        sys.exit(1)
    voices = data.get("voices", [])
    print(f"\nGrok TTS voices ({len(voices)} total):")
    print(f"{'─' * 70}")
    for v in sorted(voices, key=lambda x: x.get("name", "")):
        print(f"  {v.get('name',''):<15} {v.get('voice_id',''):<15} "
              f"{v.get('gender',''):<8} {v.get('language','')}")
    print("\nCustom voices: create one at console.x.ai (Voice Library) — live")
    print("passphrase verification required, ~90-120s recording, returns an")
    print("8-char voice_id. Then set channel_config.json voice.grok_voice_id")
    print("to that ID, or pass --voice <id> directly.")


# ── Gemini TTS provider ────────────────────────────────────────────────────────

GEMINI_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus",
    "Aoede", "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel",
    "Algieba", "Despina", "Erinome", "Algenib", "Rasalghul", "Laomedeia",
    "Achernar", "Alnilam", "Sulafat", "Schedar", "Gacrux", "Pulcherrima",
    "Achird", "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Electra",
]


def _gemini_call(text: str, voice: str, api_key: str, model: str,
                 speaking_rate: float | None = None) -> bytes:
    """Single Gemini TTS call → raw audio bytes (WAV or MP3)."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("❌ google-genai not installed. Run: pip install google-genai --break-system-packages")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    # Wrap in SSML prosody tag to control speaking rate (SDK has no native speaking_rate field)
    if speaking_rate is not None:
        rate_pct = f"{int(speaking_rate * 100)}%"
        contents = f'<speak><prosody rate="{rate_pct}">{text}</prosody></speak>'
    else:
        contents = text

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )
    part = response.candidates[0].content.parts[0]
    return part.inline_data.data, part.inline_data.mime_type


def _pcm_to_wav(pcm_bytes: bytes, mime_type: str) -> bytes:
    """Wrap raw PCM bytes in a WAV container."""
    import io
    import wave as wavemod
    rate = 24000
    if "rate=" in mime_type:
        try:
            rate = int(mime_type.split("rate=")[1].split(";")[0])
        except Exception:
            pass
    buf = io.BytesIO()
    with wavemod.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _wav_to_mp3(wav_bytes: bytes, output_path: str):
    """Convert WAV bytes → MP3 file via pydub (ffmpeg backend)."""
    try:
        import io
        from pydub import AudioSegment
        seg = AudioSegment.from_wav(io.BytesIO(wav_bytes))
        seg.export(output_path, format="mp3")
        return
    except ImportError:
        pass
    # Fallback: write wav to disk, ffmpeg convert
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        tmp_path = tmp.name
    subprocess.run(
        ["ffmpeg", "-y", "-i", tmp_path, output_path],
        check=True, capture_output=True
    )
    Path(tmp_path).unlink(missing_ok=True)


def gemini_generate(text: str, voice: str, output_path: str,
                    model: str = DEFAULT_MODEL_GEMINI,
                    speaking_rate: float | None = None):
    """Generate full narration via Gemini TTS, chunking long scripts.

    speaking_rate: 0.25 (very slow) – 4.0 (very fast). Default (None) = 1.0.
    Recommended range for Charon narration: 0.80–0.95.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("❌ GEMINI_API_KEY not set in .env")
        print("   Get one free at: https://aistudio.google.com")
        sys.exit(1)

    rate_label = f"  speaking_rate={speaking_rate}" if speaking_rate is not None else ""
    chunks = _split_into_chunks(text, max_chars=GEMINI_CHUNK_LIMIT)
    print(f"  Gemini TTS: {len(chunks)} chunk(s), voice={voice}, model={model}{rate_label}")

    all_wav_chunks = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  ⏳ Chunk {i}/{len(chunks)} ({len(chunk)} chars)...", end=" ", flush=True)
        audio_data, mime_type = _gemini_call(chunk, voice, api_key, model,
                                              speaking_rate=speaking_rate)

        # Convert to WAV if raw PCM
        if "mp3" in mime_type or "mpeg" in mime_type:
            # Already MP3 — write directly if single chunk, else we need WAV for concat
            if len(chunks) == 1:
                Path(output_path).write_bytes(audio_data)
                print(f"✓")
                return
            # Multi-chunk MP3: convert each to WAV for concatenation
            try:
                import io
                from pydub import AudioSegment
                wav_bytes = AudioSegment.from_mp3(io.BytesIO(audio_data)).raw_data
                # Store pydub segment instead
                all_wav_chunks.append(("mp3", audio_data))
            except Exception:
                all_wav_chunks.append(("mp3", audio_data))
        else:
            wav_bytes = _pcm_to_wav(audio_data, mime_type)
            all_wav_chunks.append(("wav", wav_bytes))
        print("✓")

    if len(all_wav_chunks) == 1:
        fmt, data = all_wav_chunks[0]
        if fmt == "wav":
            _wav_to_mp3(data, output_path)
        else:
            Path(output_path).write_bytes(data)
    else:
        # Concatenate all chunks via pydub
        try:
            import io
            from pydub import AudioSegment
            combined = AudioSegment.empty()
            for fmt, data in all_wav_chunks:
                if fmt == "wav":
                    combined += AudioSegment.from_wav(io.BytesIO(data))
                else:
                    combined += AudioSegment.from_mp3(io.BytesIO(data))
            print(f"  Concatenating {len(all_wav_chunks)} chunks...")
            combined.export(output_path, format="mp3")
        except ImportError:
            print("❌ pydub required for multi-chunk Gemini audio. Run: pip install pydub --break-system-packages")
            sys.exit(1)


def gemini_list_voices():
    print(f"\nGemini TTS voices ({len(GEMINI_VOICES)} available):")
    print(f"{'─' * 40}")
    for v in GEMINI_VOICES:
        print(f"  {v}")
    print(f"\nCurrent default: {DEFAULT_VOICE_GEMINI}")
    print("Test voices: python test_gemini_tts.py --voice <name>")


# ── Gemini Cloud TTS provider (Gemini 3.1 + en-IN locale + style prompt) ────────
# Uses Cloud Text-to-Speech (texttospeech.googleapis.com), NOT the Gemini Developer
# API the 'gemini' provider above uses. Cloud TTS rejects plain API keys — it needs
# OAuth2 user credentials, so this shells out to the gcloud CLI for a short-lived
# access token. Requires: gcloud CLI installed + `gcloud auth login` run once.

def _get_gcloud_access_token() -> tuple[str | None, str | None]:
    """Fetch a short-lived OAuth2 access token from the already-authenticated gcloud
    CLI, plus the active project ID (sent as X-Goog-User-Project for billing/quota
    attribution when using user credentials instead of a service account).
    The token is never printed or logged — it only ever flows into the request header.
    Returns (token, project) — either may be None if gcloud isn't available/authed.
    """
    # shell=True needed on Windows: gcloud is a .cmd batch wrapper, and CreateProcess
    # can't resolve PATHEXT-extension scripts without going through cmd.exe.
    use_shell = sys.platform == "win32"
    try:
        tok = subprocess.run(["gcloud", "auth", "print-access-token"],
                              capture_output=True, text=True, timeout=15, shell=use_shell)
        proj = subprocess.run(["gcloud", "config", "get-value", "project"],
                               capture_output=True, text=True, timeout=15, shell=use_shell)
        token = tok.stdout.strip() if tok.returncode == 0 else None
        project = proj.stdout.strip() if proj.returncode == 0 else None
        return token, project
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None


def _cloudtts_call(text: str, voice: str, locale: str, model: str, style_prompt: str,
                    access_token: str, project: str | None,
                    speaking_rate: float | None = None) -> bytes:
    """Single Cloud Text-to-Speech API call → returns raw MP3 bytes.
    Requests MP3 encoding directly (unlike the raw-PCM 'gemini' provider path),
    so no WAV-wrapping step is needed for a single chunk.
    """
    body = {
        "audioConfig": {
            "audioEncoding": "MP3",
            "pitch": 0,
            "speakingRate": speaking_rate if speaking_rate is not None else 1,
        },
        "input": {
            "prompt": style_prompt,
            "text": text,
        },
        "voice": {
            "languageCode": locale,
            "modelName": model,
            "name": voice,
        },
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    if project:
        headers["X-Goog-User-Project"] = project
    req = urllib.request.Request(CLOUD_TTS_URL, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()[:500]
        print(f"❌ Cloud TTS API error {e.code}: {body_err}")
        if e.code == 401:
            print("   Access token expired or gcloud not authenticated — run: gcloud auth login")
        elif e.code == 403:
            print("   Check Cloud Text-to-Speech API + billing are enabled for the active gcloud project:")
            print("   https://console.cloud.google.com/apis/library/texttospeech.googleapis.com")
        sys.exit(1)
    if "audioContent" not in data:
        print(f"❌ No audioContent in Cloud TTS response: {json.dumps(data)[:300]}")
        sys.exit(1)
    return base64.b64decode(data["audioContent"])


def cloudtts_generate(text: str, voice: str, output_path: str,
                       model: str = DEFAULT_MODEL_CLOUDTTS,
                       locale: str = DEFAULT_LOCALE_CLOUDTTS,
                       style_prompt: str = DEFAULT_STYLE_CLOUDTTS,
                       speaking_rate: float | None = None,
                       strict: bool = False) -> tuple[list[float], str]:
    """Generate full narration via Cloud TTS (Gemini 3.1 + locale + style prompt),
    chunking long scripts and concatenating MP3 chunks via the same helper the
    ElevenLabs path uses. Returns (chunk-boundary timestamps in seconds — [] if
    the script fit in a single request, provider actually used).

    strict=True (production) refuses outright when no gcloud token is available,
    rather than falling back to plain 'gemini' — an approved gemini_cloudtts
    profile must never silently synthesize through a different provider that
    drops its model/locale/style, with the sidecar still claiming Cloud TTS ran.
    strict=False (evaluation) keeps the fallback; the caller must record the
    returned provider in the sidecar rather than assuming it matches what was
    requested.
    """
    access_token, project = _get_gcloud_access_token()
    if not access_token:
        if strict:
            print("❌ production narration requires gemini_cloudtts, but no gcloud "
                  "access token is available (gcloud not installed, or not "
                  "authenticated — run: gcloud auth login). Refusing rather than "
                  "silently falling back to plain 'gemini', which would use the "
                  "wrong model/locale/style under an approved Cloud TTS profile. "
                  "Nothing was synthesised.")
            sys.exit(1)
        print("⚠  Could not get a gcloud access token (gcloud not installed, or not")
        print("   authenticated — run: gcloud auth login). gemini_cloudtts needs gcloud;")
        print("   falling back to the 'gemini' provider (Gemini Developer API — no")
        print("   locale/style control, but works with just GEMINI_API_KEY).")
        gemini_generate(text, voice, output_path, speaking_rate=speaking_rate)
        return [], "gemini"

    rate_label = f"  speaking_rate={speaking_rate}" if speaking_rate is not None else ""
    chunks = _split_into_chunks(text, max_chars=CLOUDTTS_CHUNK_LIMIT)
    print(f"  Cloud TTS: {len(chunks)} chunk(s), voice={voice}, model={model}, "
          f"locale={locale}{rate_label}")
    if style_prompt:
        print(f"  Style: {style_prompt}")

    if len(chunks) == 1:
        audio_bytes = _cloudtts_call(text, voice, locale, model, style_prompt,
                                      access_token, project, speaking_rate)
        Path(output_path).write_bytes(audio_bytes)
        print(f"  ✓ Chunk 1/1 done")
        return [], "gemini_cloudtts"

    tmp_dir = Path(output_path).parent / "_cloudtts_chunks"
    tmp_dir.mkdir(exist_ok=True)
    chunk_paths = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  ⏳ Chunk {i}/{len(chunks)} ({len(chunk)} chars)...")
        audio_bytes = _cloudtts_call(chunk, voice, locale, model, style_prompt,
                                      access_token, project, speaking_rate)
        p = tmp_dir / f"chunk_{i:03d}.mp3"
        p.write_bytes(audio_bytes)
        chunk_paths.append(p)
        print(f"  ✓ Chunk {i}/{len(chunks)} done")

    boundaries = _compute_chunk_boundaries(chunk_paths)

    print(f"  Concatenating {len(chunks)} chunks...")
    _concat_mp3_chunks(chunk_paths, output_path)

    for p in chunk_paths:
        p.unlink(missing_ok=True)
    tmp_dir.rmdir()
    print(f"  ✓ Concatenation done")
    return boundaries, "gemini_cloudtts"


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


# ── Indian-term pronunciation (applied to TTS input only) ─────────────────────
#
# The channel is moving to a neutral international-English voice, chosen for
# natural intonation and question prosody over accent authenticity — Indian
# identity is carried by the subject, character and vocabulary instead. What a
# neutral voice does NOT get right on its own is Indian names and terms, so
# respell them phonetically here.
#
# IMPORTANT: this rewrites only the string handed to the synthesiser. The script
# file keeps its real spelling, and captions are unaffected — they are built from
# WhisperX's transcript of the audio, and auto_split_scenes' BRAND_CORRECTIONS
# maps any phonetic artefacts back to the correct spelling. So "Neet U.G." can be
# spoken correctly while the caption still reads "NEET-UG".
#
# Case-insensitive. Ordered: longer, more specific entries first.
PRONUNCIATION_DICT = {
    r"\bNEET[-\s]?UG\b":      "Neet U.G.",
    r"\bNEET\b":              "Neet",
    r"\bNTA\b":               "N-T-A",
    r"\bCBI\b":               "C-B-I",
    r"\bRTI\b":               "R-T-I",
    r"\bISRO\b":              "ISRO",
    r"\bMBBS\b":              "M.B.B.S.",
    r"\bLok Sabha\b":         "Loke Sabha",
    r"\bRajya Sabha\b":       "Rahj-ya Sabha",
    r"\bJantar Mantar\b":     "Juntar Muntar",
    r"\bSonam Wangchuk\b":    "Sonam Wong-chook",
    r"\bWangchuk\b":          "Wong-chook",
    r"\bBengaluru\b":         "Bengaluroo",
    r"\bLucknow\b":           "Luck-now",
    r"\bRajasthan\b":         "Rah-jasthan",
    r"\bBihar\b":             "Bih-har",
    r"\bKota\b":              "Koh-ta",
    r"\bLadakh\b":            "La-dakh",
    r"\bcrore\b":             "crore",
    r"\blakh\b":              "lakh",
}


def apply_pronunciation(text: str) -> str:
    """Respell Indian names/terms for the synthesiser. Never touches captions."""
    changed = 0
    for pattern, replacement in PRONUNCIATION_DICT.items():
        text, n = re.subn(pattern, replacement, text, flags=re.IGNORECASE)
        changed += n
    if changed:
        print(f"  Pronunciation: {changed} Indian term(s) respelled for TTS "
              f"(captions keep original spelling)")
    return text


async def edge_generate(text: str, voice: str, output_path: str,
                         rate: str = "+0%", pitch: str = "+0Hz"):
    try:
        import edge_tts
    except ImportError:
        print("❌ edge-tts not installed. Run: pip install edge-tts")
        sys.exit(1)
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(output_path)


# ── Duration helper ────────────────────────────────────────────────────────────

def get_duration(path: str) -> float:
    try:
        from mutagen.mp3 import MP3
        return MP3(path).info.length
    except Exception:
        return 0.0


def normalize_loudness(audio_path: str, target_lufs: float = TARGET_LUFS) -> dict | None:
    """Two-pass ffmpeg loudnorm to a target integrated loudness, in place.
    Two-pass (measure, then apply using the measured values) is what ffmpeg's
    own loudnorm docs recommend for accuracy — single-pass can be off by
    several LU on speech material. Returns the measured stats dict on success,
    None if normalization couldn't be applied (ffmpeg missing/failed) — in
    which case the original file is left untouched rather than corrupted.
    """
    measure_cmd = [
        "ffmpeg", "-i", audio_path,
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(measure_cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        print("  ⚠ ffmpeg not found — skipping loudness normalization")
        return None
    except subprocess.TimeoutExpired:
        print("  ⚠ Loudness measurement timed out — skipping normalization")
        return None

    stderr = result.stderr
    json_start = stderr.rfind("{")
    json_end = stderr.rfind("}") + 1
    if json_start == -1 or json_end == 0:
        print("  ⚠ Could not measure loudness (loudnorm pass 1 produced no stats) — skipping")
        return None
    try:
        measured = json.loads(stderr[json_start:json_end])
    except json.JSONDecodeError:
        print("  ⚠ Could not parse loudness measurement — skipping normalization")
        return None

    tmp_out = f"{audio_path}.normalized.mp3"
    apply_filter = (
        f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true:print_format=json"
    )
    apply_cmd = ["ffmpeg", "-y", "-i", audio_path, "-af", apply_filter, tmp_out]
    try:
        result2 = subprocess.run(apply_cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print("  ⚠ Loudness normalization apply pass timed out — keeping original audio")
        Path(tmp_out).unlink(missing_ok=True)
        return None

    if result2.returncode != 0 or not Path(tmp_out).exists() or Path(tmp_out).stat().st_size == 0:
        print("  ⚠ Loudness normalization failed — keeping original audio")
        Path(tmp_out).unlink(missing_ok=True)
        return None

    # Pass 1's "output_i" is only a PREDICTION assuming linear normalization —
    # when the input is far from the target with little true-peak headroom
    # (common for narration that was never normalized before), ffmpeg silently
    # falls back to dynamic (adaptive-gain) normalization instead, which can
    # land noticeably off that prediction. Parse pass 2's own stats so what
    # gets logged (and stored in the sidecar) is what actually happened, not
    # what pass 1 assumed would happen — confirmed this matters on real
    # narration audio (predicted -14.0, actual result -16.02 LUFS).
    actual = dict(measured)
    stderr2 = result2.stderr
    j2_start, j2_end = stderr2.rfind("{"), stderr2.rfind("}") + 1
    if j2_start != -1 and j2_end != 0:
        try:
            actual.update(json.loads(stderr2[j2_start:j2_end]))
        except json.JSONDecodeError:
            pass  # keep pass-1 prediction as a fallback rather than failing the whole normalize

    Path(tmp_out).replace(audio_path)
    print(f"  ✓ Loudness normalized: {actual['input_i']} -> {actual.get('output_i', '?')} LUFS "
          f"(target {target_lufs}, type={actual.get('normalization_type', '?')})")
    return actual


# Which settings key holds "the voice" and which CLI flag/settings-key pairs
# matter, per provider. Kept here (and in render_channel_dna.py, which needs
# the same mapping for the generated adapter) rather than inferred, since
# guessing wrong here is exactly the kind of silent mismatch this fix exists
# to close.
_PRODUCTION_CHECKS_BY_PROVIDER = {
    "edge": (("--voice", "voice", "voice"), ("--edge-rate", "edge_rate", "rate"),
            ("--edge-pitch", "edge_pitch", "pitch")),
    "gemini": (("--voice", "voice", "voice"),
              ("--speaking-rate", "speaking_rate", "speaking_rate")),
    "gemini_cloudtts": (("--voice", "voice", "voice"),
                       ("--speaking-rate", "speaking_rate", "speaking_rate")),
    "elevenlabs": (("--voice", "voice", "voice_id"), ("--speed", "speed", "speed")),
    "grok": (("--voice", "voice", "voice_id"), ("--speed", "speed", "speed")),
}


def _resolve_production_voice(args, channel) -> dict:
    """Build the effective TTS config for a production (approved) write.

    Every generation-affecting value comes from
    `channel.config["voice"]["approved_profile"]` — nothing here falls back to
    `legacy_config` or a DEFAULT_* constant, because "derive production config
    from the approved profile" is meaningless if any field can still fall
    through to something else. Any CLI flag the operator explicitly passed
    (detectable because every one of these now defaults to None) that
    disagrees with the approved profile is refused — before any credential is
    read, any client built, or any request made. `--preview` is the place to
    experiment with something else; this path is not.
    """
    profile = channel_context._thaw(channel.config["voice"]["approved_profile"])
    provider = profile["provider"]
    settings = profile["settings"]

    conflicts = []
    if args.provider is not None and args.provider != provider:
        conflicts.append(f"--provider={args.provider!r} conflicts with the "
                         f"approved profile's provider={provider!r}")
    for flag, arg_attr, settings_key in _PRODUCTION_CHECKS_BY_PROVIDER.get(provider, ()):
        cli_value = getattr(args, arg_attr)
        if cli_value is not None and str(cli_value) != str(settings[settings_key]):
            conflicts.append(f"{flag}={cli_value!r} conflicts with the approved "
                             f"profile's {settings_key}={settings[settings_key]!r}")

    if conflicts:
        print(f"\nproduction narration must use the approved voice profile "
              f"({provider}) exactly — {len(conflicts)} conflict(s):",
              file=sys.stderr)
        for c in conflicts:
            print(f"  - {c}", file=sys.stderr)
        print(f"\nUse --preview targeting {channel.voice_preview_dir} to experiment "
              f"with a different provider or voice. Nothing was synthesised.",
              file=sys.stderr)
        sys.exit(1)

    return {"provider": provider, **settings}


def _write_voice_sidecar(output_path: str, provider: str, voice: str, speaking_rate,
                          chunk_boundaries_sec: list[float] | None = None,
                          loudness: dict | None = None,
                          speed: float | None = None) -> None:
    """Record which voice config produced this audio file, alongside it as
    {output_path}.voice.json. Without this, there's no way to later answer
    "what voice made this file" other than re-listening — the exact gap that
    let common/cta/cta.mp3 go stale (wrong voice) unnoticed for a while.

    chunk_boundaries_sec lets review_narration_audio.py jump straight to a known
    chunk-concatenation seam instead of guessing where it is. loudness records
    the measured-vs-target LUFS from normalize_loudness(), if it ran.
    """
    sidecar = {
        "provider": provider,
        "voice": voice,
        "speaking_rate": speaking_rate,
        "chunk_boundaries_sec": chunk_boundaries_sec or [],
        "loudness": loudness,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if provider == "gemini_cloudtts":
        sidecar["model"]  = DEFAULT_MODEL_CLOUDTTS
        sidecar["locale"] = DEFAULT_LOCALE_CLOUDTTS
        sidecar["style"]  = DEFAULT_STYLE_CLOUDTTS
    elif provider == "gemini":
        sidecar["model"] = DEFAULT_MODEL_GEMINI
    elif provider == "elevenlabs":
        sidecar["model"] = DEFAULT_MODEL_EL
        sidecar["speed"] = speed
    elif provider == "grok":
        sidecar["language"] = DEFAULT_LANGUAGE_GROK
        sidecar["speed"] = speed

    try:
        Path(f"{output_path}.voice.json").write_text(
            json.dumps(sidecar, indent=2), encoding="utf-8"
        )
    except OSError as e:
        print(f"  ⚠ Could not write voice-config sidecar: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project",   help="Episode folder (e.g. ep01)")
    parser.add_argument("--script",    help="Path to narration .txt file")
    parser.add_argument("--provider",  default=None,
                        choices=["gemini_cloudtts", "gemini", "elevenlabs", "grok", "edge"],
                        help=f"TTS provider (default from channel_config: {DEFAULT_PROVIDER}). "
                             f"For a production write, must match the approved profile, "
                             f"or be omitted.")
    parser.add_argument("--voice",     default=None,
                        help="Voice ID (ElevenLabs) or voice name (Edge TTS). Defaults from "
                             "channel_config.json for evaluation; must match the approved "
                             "profile, or be omitted, for a production write.")
    parser.add_argument("--out",       default=None,
                        help="Explicit output path. Honored exactly as given — preview "
                             "or not — never silently rewritten or redirected. Omit to "
                             "get the default for the mode you're in: a full-script run "
                             "defaults to {project}/source_audio/narration.mp3; a "
                             "--preview run defaults to the channel's preview directory.")
    parser.add_argument("--preview",   type=int, metavar="N", default=None,
                        help="Synthesize only the first N sentences (for quick A/B voice testing)")
    parser.add_argument("--list-voices", action="store_true",
                        help="List available voices for the selected provider and exit")
    parser.add_argument("--locale",    default="en-US",
                        help="Locale filter for --list-voices with edge provider (e.g. en-IN)")
    parser.add_argument("--speaking-rate", type=float, default=None, metavar="RATE",
                        help="Gemini TTS speed: 0.25 (slowest) – 4.0 (fastest). Default=1.0. "
                             "Try 0.85 for a slightly slower Charon pace.")
    parser.add_argument("--speed", type=float, default=None, metavar="SPEED",
                        help=f"Generation-pacing speed for ElevenLabs (0.7-1.2, default "
                             f"{DEFAULT_SPEED_EL}) or Grok (default {DEFAULT_SPEED_GROK}). "
                             f"Not the same parameter as --speaking-rate (that's Gemini-only).")
    parser.add_argument("--no-normalize", action="store_true",
                        help=f"Skip loudness normalization (default: normalize full-script "
                             f"generations to {TARGET_LUFS} LUFS via ffmpeg loudnorm)")
    parser.add_argument("--edge-rate", default=None, metavar="RATE",
                        help="Edge TTS speaking-rate offset, e.g. '+12%%' or '-10%%'. Edge-only. "
                             "Default '+0%%' for evaluation.")
    parser.add_argument("--edge-pitch", default=None, metavar="PITCH",
                        help="Edge TTS pitch offset, e.g. '+15Hz' or '-5Hz'. Edge-only. "
                             "Default '+0Hz' for evaluation.")
    parser.add_argument("--channel", default=None,
                        help="Channel pack governing this narration. Needed only when "
                             "the episode has no manifest yet and more than one pack "
                             "is installed — narration legitimately runs before the "
                             "split stage, so there is not always an episode to ask.")
    args = parser.parse_args()

    # ── List voices ──
    if args.list_voices:
        if args.provider == "edge":
            await edge_list_voices(args.locale)
        elif args.provider in ("gemini", "gemini_cloudtts"):
            gemini_list_voices()   # same voice names for both Gemini-backed providers
        elif args.provider == "grok":
            grok_list_voices()
        else:
            elevenlabs_list_voices()
        return

    if not args.script:
        parser.error("--script is required unless using --list-voices")

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

    # Free-form resolution — the default for evaluation, and for the preview
    # filename below regardless of how this run eventually classifies. A
    # production write below replaces every one of these with the approved
    # profile's own values; nothing production-facing reads past this point.
    cli_provider = args.provider or DEFAULT_PROVIDER
    if cli_provider in ("gemini", "gemini_cloudtts"):
        voice = args.voice or DEFAULT_VOICE_GEMINI
    elif cli_provider == "elevenlabs":
        voice = args.voice or DEFAULT_VOICE_EL
    elif cli_provider == "grok":
        voice = args.voice or DEFAULT_VOICE_GROK
    else:
        voice = args.voice or DEFAULT_VOICE_EDGE

    if args.preview:
        text = first_n_sentences(text, args.preview)
        print(f"\nGenerating voice preview ({args.preview} sentence(s))")
    else:
        print(f"\nGenerating full-script voiceover")

    # Resolve the channel BEFORE choosing a default output — the preview
    # default destination is the channel's own preview directory, so the
    # channel has to exist first.
    project_root = (SCRIPT_DIR / args.project) if args.project else None
    try:
        channel = channel_context.channel_for_voice(project_dir=project_root,
                                                     requested=args.channel)
    except channel_context.ChannelError as e:
        print(f"\ncannot determine the channel governing this narration: {e}",
              file=sys.stderr)
        sys.exit(1)

    # ── Output path resolution ───────────────────────────────────────────────
    # An explicit --out is honored EXACTLY as given, preview or not — it is
    # never silently rewritten or redirected, and never has project/
    # source_audio/ prepended behind the operator's back. Only an OMITTED
    # --out gets a default, and the default depends on mode: a full-script
    # run defaults to {project}/source_audio/narration.mp3 (the existing
    # bare-filename shorthand); a --preview run with no --out defaults into
    # the channel's own preview directory, so the natural way to run a
    # preview lands somewhere evaluation is actually allowed.
    if args.out is not None:
        output_filename = args.out
        out_path = Path(output_filename)
        if out_path.is_absolute():
            intended = out_path
        elif args.preview:
            # A relative --out during --preview resolves from the pipeline
            # root, NEVER from the project's source_audio/ — the whole point
            # of an explicit destination is that it is exact, not a
            # production shorthand. Prepending project/source_audio/ here
            # (as production's bare-filename shorthand does) would silently
            # redirect a candidate preview into a path that looks production-
            # adjacent, contradicting "honored exactly".
            intended = SCRIPT_DIR / out_path
        elif not args.project:
            intended = out_path
        else:
            intended = SCRIPT_DIR / args.project / "source_audio" / output_filename
    elif args.preview:
        rate_tag = f"_rate{int(args.speaking_rate * 100)}" if args.speaking_rate is not None else ""
        output_filename = f"preview_{voice[:20].replace('/', '_')}{rate_tag}.mp3"
        intended = channel.voice_preview_dir / output_filename
    else:
        output_filename = "narration.mp3"
        if args.project:
            intended = SCRIPT_DIR / args.project / "source_audio" / output_filename
        else:
            intended = Path(output_filename)

    # ── Voice gate ──────────────────────────────────────────────────────────
    # Runs before the directory is created, before any credential is read,
    # before a client is built and before a single byte is synthesised.
    #
    # Classified by DESTINATION, not by --preview — --preview only truncates
    # the input text; it says nothing about where the result is going, and
    # must never confer evaluation privileges. A full-script run targeting
    # voice_previews/ is still evaluation; a --preview run targeting the
    # project's source_audio/ is still production and is held to the approved
    # profile exactly like a full run.
    try:
        mode, output_path_p = channel_context.classify_voice_output(
            channel, intended, project_dir=project_root)
    except channel_context.VoiceNotApproved as e:
        print(f"\n{e}", file=sys.stderr)
        print("\nNothing was synthesised.", file=sys.stderr)
        sys.exit(1)
    output_path = str(output_path_p)

    # A truncated preview must never become canonical narration — refuse
    # before anything else happens, regardless of how --out got there.
    if mode == "production" and args.preview:
        print(f"\n✗ --preview cannot target a production destination "
              f"({output_path}) — a one-sentence clip must never become "
              f"canonical narration. Preview into {channel.voice_preview_dir} "
              f"instead. Nothing was synthesised.", file=sys.stderr)
        sys.exit(1)

    # Model/locale/style/stability/similarity/language: only the active
    # provider's fields are meaningful, but every name the dispatch below
    # reads must exist regardless of which provider that is.
    model = locale = style_prompt = stability = similarity = language = None

    if mode == "production":
        # Every generation-affecting value comes from the approved profile.
        # Any CLI flag the operator explicitly passed that disagrees is
        # refused here, before anything is built or written — --preview is
        # the place to experiment with a different provider or voice, not this
        # one, regardless of what --out happens to point at.
        effective = _resolve_production_voice(args, channel)
        cli_provider = effective["provider"]
        args_edge_rate = args_edge_pitch = None
        args_speaking_rate = args_speed = None
        if cli_provider == "edge":
            voice, args_edge_rate, args_edge_pitch = (
                effective["voice"], effective["rate"], effective["pitch"])
        elif cli_provider == "gemini":
            voice, args_speaking_rate = effective["voice"], effective["speaking_rate"]
            model = effective["model"]
        elif cli_provider == "gemini_cloudtts":
            voice, args_speaking_rate = effective["voice"], effective["speaking_rate"]
            model, locale, style_prompt = (
                effective["model"], effective["locale"], effective["style"])
        elif cli_provider == "elevenlabs":
            voice, args_speed = effective["voice_id"], effective["speed"]
            model, stability, similarity = (
                effective["model"], effective["stability"], effective["similarity_boost"])
        else:  # grok
            voice, args_speed = effective["voice_id"], effective["speed"]
            language = effective["language"]
        args_edge_rate = args_edge_rate if args_edge_rate is not None else "+0%"
        args_edge_pitch = args_edge_pitch if args_edge_pitch is not None else "+0Hz"

        # Production is held to two more invariants, checked before anything
        # is built or written: normalization is never optional, and the tool
        # that performs it must actually be present.
        if args.no_normalize:
            print(f"\n✗ production narration is always normalized to "
                  f"{PRODUCTION_TARGET_LUFS} LUFS — --no-normalize only "
                  f"applies to evaluation writes. Nothing was synthesised.",
                  file=sys.stderr)
            sys.exit(1)
        import shutil as _shutil
        if _shutil.which("ffmpeg") is None:
            print(f"\n✗ ffmpeg not found on PATH — production narration "
                  f"requires loudness normalization and cannot proceed "
                  f"without it. Nothing was synthesised.", file=sys.stderr)
            sys.exit(1)
    else:
        args_edge_rate = args.edge_rate if args.edge_rate is not None else "+0%"
        args_edge_pitch = args.edge_pitch if args.edge_pitch is not None else "+0Hz"
        args_speaking_rate = args.speaking_rate
        args_speed = args.speed

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    word_count = len(text.split())
    char_count = len(text)
    print(f"Script  : {script_path.name} ({word_count} words, {char_count} chars)")
    print(f"Mode    : {mode}")
    print(f"Provider: {cli_provider}")
    print(f"Voice   : {voice}")
    print(f"Output  : {output_path}")
    print(f"{'─' * 55}")

    rate = args_speaking_rate if args_speaking_rate is not None else DEFAULT_SPEAKING_RATE
    if args_speed is not None:
        speed = args_speed
    elif cli_provider == "grok":
        speed = DEFAULT_SPEED_GROK
    else:
        speed = DEFAULT_SPEED_EL

    # Respell Indian names/terms phonetically for the synthesiser only. Applied
    # here, once, so every provider benefits — see apply_pronunciation().
    text = apply_pronunciation(text)

    chunk_boundaries: list[float] = []
    actual_provider = cli_provider
    if cli_provider == "gemini_cloudtts":
        chunk_boundaries, actual_provider = cloudtts_generate(
            text, voice, output_path,
            model=model if model is not None else DEFAULT_MODEL_CLOUDTTS,
            locale=locale if locale is not None else DEFAULT_LOCALE_CLOUDTTS,
            style_prompt=style_prompt if style_prompt is not None else DEFAULT_STYLE_CLOUDTTS,
            speaking_rate=rate, strict=(mode == "production"))
    elif cli_provider == "gemini":
        gemini_generate(text, voice, output_path,
                        model=model if model is not None else DEFAULT_MODEL_GEMINI,
                        speaking_rate=rate)
    elif cli_provider == "elevenlabs":
        chunk_boundaries = elevenlabs_generate(
            text, voice, output_path,
            model=model if model is not None else DEFAULT_MODEL_EL,
            stability=stability if stability is not None else DEFAULT_STABILITY,
            similarity=similarity if similarity is not None else DEFAULT_SIMILARITY,
            speed=speed)
    elif cli_provider == "grok":
        chunk_boundaries = grok_generate(
            text, voice, output_path,
            language=language if language is not None else DEFAULT_LANGUAGE_GROK,
            speed=speed)
    else:
        await edge_generate(text, voice, output_path, rate=args_edge_rate, pitch=args_edge_pitch)

    # Normalize loudness. Production is never optional here — the two inputs
    # that used to be able to skip it (--preview and --no-normalize) are both
    # refused outright for a production destination above, before synthesis
    # even started; a production write that somehow still fails to normalize
    # (ffmpeg present at the preflight check but failing here) is fatal, not a
    # warning, and does not get a sidecar written over unnormalized audio.
    # Evaluation keeps today's soft behavior — skipped for --preview (short
    # A/B test clips; loudnorm's measurement pass is unreliable on very short
    # audio) and skippable via --no-normalize.
    loudness = None
    if mode == "production":
        loudness = normalize_loudness(output_path, target_lufs=PRODUCTION_TARGET_LUFS)
        if loudness is None:
            print(f"\n✗ loudness normalization failed — production narration "
                  f"must be normalized to {PRODUCTION_TARGET_LUFS} LUFS; "
                  f"refusing to write a production sidecar over unnormalized "
                  f"audio.", file=sys.stderr)
            sys.exit(1)
    elif not args.preview and not args.no_normalize:
        loudness = normalize_loudness(output_path)

    # Write a voice-config sidecar alongside the audio — otherwise there's no
    # way to later answer "what voice made this file" other than re-listening
    # (exactly the gap that let common/cta/cta.mp3 go stale unnoticed).
    if mode == "production":
        # channel_context is an unconditional import in this script — there is
        # no "production write with no resolvable channel" case here to fall
        # back from: classify_voice_output() already raised before this point
        # if none existed. So production is always full-binding, and a failure
        # to write it is fatal, not a warning — the whole reason it exists is
        # to make a later swap or a re-approval detectable.
        audio_sha256 = hashlib.sha256(Path(output_path).read_bytes()).hexdigest()
        approved_profile = channel_context._thaw(
            channel.config["voice"]["approved_profile"])
        sidecar = {
            "schema_version": 1,
            "channel_id": channel.channel_id,
            "voice_profile_sha256": channel.voice_profile_sha256,
            "effective_profile": approved_profile,
            "audio_sha256": audio_sha256,
            "normalization": {
                "policy": "always",
                "target_lufs": PRODUCTION_TARGET_LUFS,
                "measured": loudness,
            },
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        channel_context._write_atomic_text(
            Path(f"{output_path}.voice.json"),
            json.dumps(sidecar, indent=2, ensure_ascii=False))
    else:
        # actual_provider may differ from the requested cli_provider — the
        # gemini_cloudtts→gemini evaluation fallback is exactly that case —
        # so the sidecar records what actually ran, not what was requested.
        _write_voice_sidecar(output_path, actual_provider, voice, rate,
                             chunk_boundaries_sec=chunk_boundaries, loudness=loudness,
                             speed=speed)

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
