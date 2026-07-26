"""
generate_source_audio.py
The Interested Indian — Full-Script Voiceover Generator

Supports four providers:
  - gemini_cloudtts  (default — Gemini 3.1 via Cloud Text-to-Speech, en-IN locale +
                       style prompt for an Indian-accented narration. Requires the
                       gcloud CLI installed and authenticated: gcloud auth login.
                       Cloud TTS rejects plain API keys, unlike the other providers.)
  - gemini           (Gemini Developer API — reads GEMINI_API_KEY from .env, no
                       locale/style control, sounds more generically American)
  - elevenlabs       (reads from channel_config.json + ELEVENLABS_API_KEY in .env)
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
import json
import os
import re
import subprocess
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

DEFAULT_PROVIDER   = _voice_cfg.get("provider", "gemini")
DEFAULT_VOICE_EL   = _voice_cfg.get("default", "gYQ0co3BoppQZ8BDM3lj")
DEFAULT_MODEL_EL   = _voice_cfg.get("model", "eleven_multilingual_v2")
DEFAULT_STABILITY  = _voice_cfg.get("stability", 0.5)
DEFAULT_SIMILARITY = _voice_cfg.get("similarity_boost", 0.75)
DEFAULT_VOICE_EDGE   = "en-US-GuyNeural"
DEFAULT_VOICE_GEMINI   = _voice_cfg.get("gemini_voice", "Charon")
DEFAULT_MODEL_GEMINI   = "gemini-2.5-flash-preview-tts"
DEFAULT_SPEAKING_RATE  = _voice_cfg.get("gemini_speaking_rate", None)  # None = Gemini default (1.0)
GEMINI_CHUNK_LIMIT   = 4500   # conservative limit per API call for long scripts

DEFAULT_MODEL_CLOUDTTS  = _voice_cfg.get("gemini_cloudtts_model", "gemini-3.1-flash-tts-preview")
DEFAULT_LOCALE_CLOUDTTS = _voice_cfg.get("gemini_cloudtts_locale", "en-IN")
DEFAULT_STYLE_CLOUDTTS  = _voice_cfg.get("gemini_cloudtts_style", "")
CLOUD_TTS_URL   = "https://texttospeech.googleapis.com/v1beta1/text:synthesize"
CLOUDTTS_CHUNK_LIMIT = 4500   # conservative — not independently confirmed for this
                               # Gemini-backed Cloud TTS endpoint; classic Cloud TTS
                               # has a well-known 5000-char input limit, this mirrors
                               # the same safety margin as GEMINI_CHUNK_LIMIT above.

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
                       speaking_rate: float | None = None):
    """Generate full narration via Cloud TTS (Gemini 3.1 + locale + style prompt),
    chunking long scripts and concatenating MP3 chunks via the same helper the
    ElevenLabs path uses.
    """
    access_token, project = _get_gcloud_access_token()
    if not access_token:
        print("⚠  Could not get a gcloud access token (gcloud not installed, or not")
        print("   authenticated — run: gcloud auth login). gemini_cloudtts needs gcloud;")
        print("   falling back to the 'gemini' provider (Gemini Developer API — no")
        print("   locale/style control, but works with just GEMINI_API_KEY).")
        gemini_generate(text, voice, output_path, speaking_rate=speaking_rate)
        return

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
        return

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

    print(f"  Concatenating {len(chunks)} chunks...")
    _concat_mp3_chunks(chunk_paths, output_path)

    for p in chunk_paths:
        p.unlink(missing_ok=True)
    tmp_dir.rmdir()
    print(f"  ✓ Concatenation done")


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
                        choices=["gemini_cloudtts", "gemini", "elevenlabs", "edge"],
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
    parser.add_argument("--speaking-rate", type=float, default=None, metavar="RATE",
                        help="Gemini TTS speed: 0.25 (slowest) – 4.0 (fastest). Default=1.0. "
                             "Try 0.85 for a slightly slower Charon pace.")
    args = parser.parse_args()

    # ── List voices ──
    if args.list_voices:
        if args.provider == "edge":
            await edge_list_voices(args.locale)
        elif args.provider in ("gemini", "gemini_cloudtts"):
            gemini_list_voices()   # same voice names for both Gemini-backed providers
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

    # Resolve voice
    if args.provider in ("gemini", "gemini_cloudtts"):
        voice = args.voice or DEFAULT_VOICE_GEMINI
    elif args.provider == "elevenlabs":
        voice = args.voice or DEFAULT_VOICE_EL
    else:
        voice = args.voice or DEFAULT_VOICE_EDGE

    if args.preview:
        text = first_n_sentences(text, args.preview)
        rate_tag = f"_rate{int(args.speaking_rate * 100)}" if args.speaking_rate is not None else ""
        output_filename = f"preview_{voice[:20].replace('/', '_')}{rate_tag}.mp3"
        print(f"\nGenerating voice preview ({args.preview} sentence(s))")
    else:
        output_filename = args.out
        print(f"\nGenerating full-script voiceover")

    # If --out is absolute or no --project given, use it directly; else put in project/source_audio/
    out_path = Path(output_filename)
    if out_path.is_absolute() or not args.project:
        output_path = str(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        source_audio_dir = Path(__file__).parent / args.project / "source_audio"
        source_audio_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(source_audio_dir / output_filename)

    word_count = len(text.split())
    char_count = len(text)
    print(f"Script  : {script_path.name} ({word_count} words, {char_count} chars)")
    print(f"Provider: {args.provider}")
    print(f"Voice   : {voice}")
    print(f"Output  : {output_path}")
    print(f"{'─' * 55}")

    if args.provider == "gemini_cloudtts":
        # CLI flag overrides config; config overrides Gemini default (1.0)
        rate = args.speaking_rate if args.speaking_rate is not None else DEFAULT_SPEAKING_RATE
        cloudtts_generate(text, voice, output_path, speaking_rate=rate)
    elif args.provider == "gemini":
        rate = args.speaking_rate if args.speaking_rate is not None else DEFAULT_SPEAKING_RATE
        gemini_generate(text, voice, output_path, speaking_rate=rate)
    elif args.provider == "elevenlabs":
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
