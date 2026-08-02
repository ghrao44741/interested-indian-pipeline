"""Regression: runtime code must not discover pose assets by globbing.

Generation, validation and the registry itself legitimately touch
character/poses/. Everything else must go through pose_registry.resolve(), so a
raw, pending or altered file cannot reach a render.

This currently passes trivially — the router and compositor do not exist yet. It
is committed now so it fails the moment someone wires them the wrong way.

Run:  python tests/test_no_runtime_globbing.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"generate_poses.py", "validate_poses.py", "pose_registry.py",
           "generate_character.py", "export_character_package.py",
           "check_character_consistency.py"}

bad = []
for py in sorted(ROOT.glob("*.py")):
    if py.name in ALLOWED or py.name.startswith("test_"):
        continue
    src = py.read_text(encoding="utf-8", errors="ignore")
    for pat, why in ((r"character/poses", "direct character/poses/ path"),
                     (r"glob\([\"'].*host_.*[\"']\)", "glob for host_* assets")):
        for m in re.finditer(pat, src):
            line = src[:m.start()].count("\n") + 1
            bad.append(f"{py.name}:{line} — {why}")

print(f"scanned {len(list(ROOT.glob('*.py')))} modules; "
      f"{len(ALLOWED)} allowed to touch pose files directly")
print("violations:", bad or "none")
sys.exit(1 if bad else 0)
