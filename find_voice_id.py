"""find_voice_id.py — Find full voice ID for a voice added to your ElevenLabs library"""
import json, os, urllib.request
from pathlib import Path

env = Path(__file__).parent / ".env"
for line in env.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

KEY = os.environ.get("ELEVENLABS_API_KEY", "")
HEADERS = {"xi-api-key": KEY}

# Check your own voice library first
print("=== Your voice library ===")
req = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers=HEADERS)
data = json.loads(urllib.request.urlopen(req, timeout=10).read())
for v in data.get("voices", []):
    print(f"  {v['name']:<40} {v['voice_id']}")

print()

# Search shared library for 'anx' by name
print("=== Shared library — searching for 'anx' ===")
for page in range(3):
    url = f"https://api.elevenlabs.io/v1/shared-voices?gender=male&language=en&page_size=100&page={page}"
    req = urllib.request.Request(url, headers=HEADERS)
    voices = json.loads(urllib.request.urlopen(req, timeout=15).read()).get("voices", [])
    if not voices:
        break
    for v in voices:
        if "anx" in v.get("name", "").lower() or v.get("voice_id", "").startswith("gYQ0co3B"):
            print(f"  Name    : {v['name']}")
            print(f"  Voice ID: {v['voice_id']}")
            print(f"  Desc    : {v.get('description', '')}")
            print()
