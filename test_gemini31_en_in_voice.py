"""
test_gemini31_en_in_voice.py — A/B test: current pipeline voice vs. Gemini 3.1 + en-in + style prompt.

The pipeline (generate_source_audio.py) calls the Gemini Developer API
(generativelanguage.googleapis.com via the google-genai SDK) with a bare voice name,
no locale, no style prompt. Your Cloud TTS Studio screenshot uses a DIFFERENT API —
Cloud Text-to-Speech (texttospeech.googleapis.com/v1beta1/text:synthesize) — which
additionally supports voice.languageCode ("en-in") and an input.prompt field for
free-text style instructions, on the newer gemini-3.1-flash-tts-preview model.

This script hits that Cloud TTS endpoint directly with the SAME GEMINI_API_KEY already
in .env, to check whether it's actually authorized for that API before you invest more
time in this direction. It reuses the exact same sample text as the existing
voice_previews/gemini_Charon.wav baseline (generated via the current pipeline's
approach) so you can A/B them directly — no need to regenerate the baseline.

Standalone — does not touch the pipeline or any of its config files.

Usage:
    python test_gemini31_en_in_voice.py
    python test_gemini31_en_in_voice.py --style "Warm, conversational Indian English narration, slightly witty"
    python test_gemini31_en_in_voice.py --voice Kore --locale en-IN
    python test_gemini31_en_in_voice.py --model gemini-2.5-flash-preview-tts   # sanity-check the older model on Cloud TTS too

Requires:
    GEMINI_API_KEY in .env (same key already used by the rest of the pipeline)
"""

import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
import wave

import requests

# ── Auto-load .env ───────────────────────────────────────────────────────────
_env = pathlib.Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Same text as voice_previews/gemini_Charon.wav (the existing pipeline-generated
# baseline) so the two files are directly comparable, not just similar.
SAMPLE_TEXT = (
    "And this is Union Territories. While most coverage of Indian governance "
    "shows you the 28 states, the Chief Ministers, the coalition dramas — "
    "there's this whole other layer nobody talks about. Nine territories where "
    "the central government runs things directly, and your local elected "
    "government has roughly the power of a very enthusiastic suggestion box."
)

DEFAULT_STYLE = "Read aloud in a warm, witty, conversational Indian English tone for a faceless YouTube channel narration."

CLOUD_TTS_URL = "https://texttospeech.googleapis.com/v1beta1/text:synthesize"


def _get_gcloud_access_token() -> tuple[str | None, str | None]:
    """Fetch a short-lived OAuth2 access token from the already-authenticated gcloud
    CLI, plus the active project ID (needed for the X-Goog-User-Project quota header
    when calling a Cloud API with user credentials instead of a service account).
    The token is never printed — it only ever flows into the Authorization header.
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


def synthesize_cloud_tts(text: str, style_prompt: str, voice_name: str,
                          language_code: str, model_name: str,
                          api_key: str | None = None,
                          access_token: str | None = None,
                          project: str | None = None) -> bytes:
    """POST to the Cloud Text-to-Speech REST API. Returns raw LINEAR16 PCM bytes.
    Raises RuntimeError with the full response body on any non-200, since Cloud API
    error messages (API not enabled, billing not enabled, invalid model, etc.) are
    specific and worth surfacing verbatim rather than swallowing.

    Cloud TTS rejects plain API keys ("API keys are not supported by this API") — it
    needs OAuth2 user credentials or a service account. Pass access_token (from
    gcloud auth print-access-token) for the former.
    """
    body = {
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "pitch": 0,
            "speakingRate": 1,
        },
        "input": {
            "prompt": style_prompt,
            "text": text,
        },
        "voice": {
            "languageCode": language_code,
            "modelName": model_name,
            "name": voice_name,
        },
    }

    params = {}
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
        if project:
            headers["X-Goog-User-Project"] = project
    elif api_key:
        params["key"] = api_key
    else:
        raise RuntimeError("Neither access_token nor api_key provided")

    resp = requests.post(CLOUD_TTS_URL, params=params, headers=headers, json=body, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}\n{resp.text}")
    data = resp.json()
    if "audioContent" not in data:
        raise RuntimeError(f"No audioContent in response: {json.dumps(data)[:500]}")
    return base64.b64decode(data["audioContent"])


def _save_wav(pcm_bytes: bytes, out_path: pathlib.Path, rate: int = 24000):
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm_bytes)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice",    default="Charon", help="Voice name (default: Charon)")
    parser.add_argument("--locale",   default="en-IN", dest="locale", help="languageCode (default: en-IN)")
    parser.add_argument("--model",    default="gemini-3.1-flash-tts-preview", help="Cloud TTS model name")
    parser.add_argument("--style",    default=DEFAULT_STYLE, help="Style-instruction prompt")
    parser.add_argument("--text",     default=SAMPLE_TEXT, help="Text to narrate")
    parser.add_argument("--out",      default="voice_previews", help="Output folder")
    parser.add_argument("--no-style", action="store_true", help="Omit the style prompt entirely (locale-only test)")
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(exist_ok=True)

    style = "" if args.no_style else args.style

    # Cloud TTS rejects plain API keys. Prefer a gcloud-issued OAuth2 access token
    # (already authenticated on this machine); fall back to GEMINI_API_KEY only so
    # the error message is informative if gcloud isn't available.
    access_token, project = _get_gcloud_access_token()
    api_key = os.environ.get("GEMINI_API_KEY")
    if not access_token and not api_key:
        print("❌ Neither gcloud auth nor GEMINI_API_KEY available.")
        sys.exit(1)
    auth_mode = f"gcloud OAuth2 (project: {project})" if access_token else "GEMINI_API_KEY (will likely fail — see below)"

    print(f"\n  Model    : {args.model}")
    print(f"  Voice    : {args.voice}")
    print(f"  Locale   : {args.locale}")
    print(f"  Style    : {style or '(none)'}")
    print(f"  Auth     : {auth_mode}")
    print(f"  Text     : \"{args.text[:70]}...\"")
    print(f"\n  Calling Cloud Text-to-Speech API...")

    try:
        pcm = synthesize_cloud_tts(
            args.text, style, args.voice, args.locale, args.model,
            api_key=api_key, access_token=access_token, project=project,
        )
    except RuntimeError as e:
        print(f"\n❌ Cloud TTS call failed:\n{e}")
        print(
            "\n  If this is a 403/PERMISSION_DENIED: the active gcloud account/project likely\n"
            "  doesn't have the Cloud Text-to-Speech API enabled, or billing isn't set up.\n"
            "  Enable it here: https://console.cloud.google.com/apis/library/texttospeech.googleapis.com\n"
            "  If this is a 400/model-not-found: gemini-3.1-flash-tts-preview may not be\n"
            "  generally available via this API yet, even if it's visible in TTS Studio."
        )
        sys.exit(1)

    tag = args.locale.replace("-", "")
    out_path = out_dir / f"gemini31_{args.voice}_{tag}{'_styled' if style else ''}.wav"
    _save_wav(pcm, out_path)
    size_kb = out_path.stat().st_size // 1024

    print(f"\n✓ Saved → {out_path}  ({size_kb} KB)")
    baseline = pathlib.Path("voice_previews") / f"gemini_{args.voice}.wav"
    print(f"\n  A/B against the existing baseline (same text, current pipeline config):")
    print(f"    {baseline}  {'(exists)' if baseline.exists() else '(not found — run test_gemini_tts.py first)'}")
    print(f"    {out_path}")


if __name__ == "__main__":
    main()
