"""
test_gemini_tts.py — Quick voice sampler for Gemini TTS.
Tests 4 voices and saves each as a .wav file so you can A/B them.

Usage:
    python test_gemini_tts.py
    python test_gemini_tts.py --voice Charon   # test one voice only
    python test_gemini_tts.py --list           # print all available voices

Requires:
    pip install google-genai --break-system-packages
    GEMINI_API_KEY in .env or environment
"""

import argparse
import os
import pathlib
import struct
import sys
import wave

# Auto-load .env
_env = pathlib.Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("❌ google-genai not installed.")
    print("   Run: pip install google-genai --break-system-packages")
    sys.exit(1)

SAMPLE_TEXT = (
    "And this is Union Territories. While most coverage of Indian governance "
    "shows you the 28 states, the Chief Ministers, the coalition dramas — "
    "there's this whole other layer nobody talks about. Nine territories where "
    "the central government runs things directly, and your local elected "
    "government has roughly the power of a very enthusiastic suggestion box."
)

# Good male candidates for a commentary narrator
VOICES_TO_TEST = ["Charon", "Fenrir", "Orus", "Puck"]

ALL_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus",
    "Aoede", "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel",
    "Algieba", "Despina", "Erinome", "Algenib", "Rasalghul", "Laomedeia",
    "Achernar", "Alnilam", "Sulafat", "Schedar", "Gacrux", "Pulcherrima",
    "Achird", "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager",
    "Electra",
]


def test_voice(client, voice_name: str, out_dir: pathlib.Path):
    print(f"  Generating {voice_name}...", end=" ", flush=True)
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=SAMPLE_TEXT,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice_name
                        )
                    )
                )
            ),
        )
        part      = response.candidates[0].content.parts[0]
        audio_data = part.inline_data.data
        mime_type  = part.inline_data.mime_type  # e.g. "audio/L16;rate=24000" or "audio/mp3"

        if "mp3" in mime_type or "mpeg" in mime_type:
            out_path = out_dir / f"gemini_{voice_name}.mp3"
            out_path.write_bytes(audio_data)
        else:
            # Raw PCM — wrap in WAV container so Windows can play it.
            # Gemini TTS returns 24 kHz, 16-bit, mono by default.
            rate = 24000
            if "rate=" in mime_type:
                try:
                    rate = int(mime_type.split("rate=")[1].split(";")[0])
                except Exception:
                    pass
            out_path = out_dir / f"gemini_{voice_name}.wav"
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)   # 16-bit = 2 bytes
                wf.setframerate(rate)
                wf.writeframes(audio_data)

        size_kb = out_path.stat().st_size // 1024
        print(f"✓  {out_path.name}  ({size_kb} KB)")
        return out_path
    except Exception as e:
        print(f"✗  {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Test Gemini TTS voices")
    parser.add_argument("--voice",  default=None, help="Test a single voice by name")
    parser.add_argument("--list",   action="store_true", help="Print all available voices")
    parser.add_argument("--out",    default="voice_previews", help="Output folder (default: voice_previews)")
    args = parser.parse_args()

    if args.list:
        print("Available Gemini voices:")
        for v in ALL_VOICES:
            print(f"  {v}")
        return

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY not set.")
        print("   Get one at: https://aistudio.google.com")
        print("   Then add GEMINI_API_KEY=AIza... to your .env file")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(exist_ok=True)

    voices = [args.voice] if args.voice else VOICES_TO_TEST

    print(f"\nTesting {len(voices)} voice(s) — output → {out_dir}/")
    print(f"Sample text: \"{SAMPLE_TEXT[:80]}...\"\n")

    saved = []
    for voice in voices:
        result = test_voice(client, voice, out_dir)
        if result:
            saved.append(result)

    print(f"\n✓ Done. {len(saved)} file(s) saved to {out_dir}/")
    print("  Listen to each and note which feels right for the channel.")
    print("  Then add GEMINI_DEFAULT_VOICE=<name> to your .env")


if __name__ == "__main__":
    main()
