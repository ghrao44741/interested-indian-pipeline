"""debug_eleven_auth.py — Diagnose ElevenLabs 401 errors"""
import json, os, urllib.request, urllib.error
from pathlib import Path

# Load .env
env = Path(__file__).parent / ".env"
for line in env.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

KEY = os.environ.get("ELEVENLABS_API_KEY", "")
print(f"Key: {KEY[:8]}...{KEY[-4:]} ({len(KEY)} chars)\n")

def test(label, url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            print(f"  ✓ {label} — 200 OK")
            if "subscription" in data:
                tier = data.get("subscription", {}).get("tier", "unknown")
                chars = data.get("subscription", {}).get("character_limit", 0)
                used  = data.get("subscription", {}).get("character_count", 0)
                print(f"    Tier: {tier}  |  Chars: {used}/{chars}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        print(f"  ✗ {label} — HTTP {e.code}: {body}")
        return False
    except Exception as e:
        print(f"  ✗ {label} — {e}")
        return False

ENDPOINTS = [
    ("GET /v1/user (xi-api-key header)",
     "https://api.elevenlabs.io/v1/user",
     {"xi-api-key": KEY}),
    ("GET /v1/user (Authorization Bearer)",
     "https://api.elevenlabs.io/v1/user",
     {"Authorization": f"Bearer {KEY}"}),
    ("GET /v1/voices (your library)",
     "https://api.elevenlabs.io/v1/voices",
     {"xi-api-key": KEY}),
    ("GET /v1/shared-voices (community library)",
     "https://api.elevenlabs.io/v1/shared-voices?gender=male&accent=indian&language=en&page_size=5",
     {"xi-api-key": KEY}),
]

for label, url, headers in ENDPOINTS:
    print(f"Testing: {label}")
    test(label, url, headers)
    print()
