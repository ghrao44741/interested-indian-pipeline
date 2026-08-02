"""Regression: runtime code must not discover pose assets directly.

Only the generator, validator and registry may touch character/poses/. Everything
else resolves through pose_registry.resolve(), so a raw, pending, tampered or
unapproved file cannot reach a render.

Scans recursively — the previous version globbed only top-level *.py and would
have missed a router placed in a subpackage, which is exactly where one would
normally live.

The negative check alone would pass trivially if nothing resolved poses at all,
which was exactly the state before Task 6. So it is paired with positive
assertions that the router and the compositor do consume the resolver.

Run:  python tests/test_no_runtime_globbing.py
"""

import ast
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Repository-relative, not filename-only: a "pose_registry.py" dropped into a
# subpackage must not inherit the real module's exemption.
ALLOWED = {"generate_poses.py", "validate_poses.py", "pose_registry.py"}

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".claude", "node_modules",
             "tests", "archive", "pose_sources", "pose_candidates"}

PATTERNS = [
    (r"[\"']character[/\\]poses", "literal character/poses path"),
    # optional ")" so both Path("character") / "poses" and ROOT / "character" /
    # "poses" are caught — the second is the more natural construction and the
    # earlier pattern missed it entirely
    (r"[\"']character[\"']\s*\)?\s*/\s*[\"']poses[\"']",
     'path built as ... "character" / "poses"'),
    (r"\.r?glob\(\s*[\"'][^\"']*host_[^\"']*[\"']", "glob over host_* assets"),
    (r"os\.listdir\([^)]*poses", "os.listdir over the pose directory"),
    (r"os\.scandir\([^)]*poses", "os.scandir over the pose directory"),
    (r"iterdir\(\)[^\n]*poses", "directory iteration over poses"),
    (r"open\(\s*[^)]*host_[a-z_]*\.png", "direct open of a host_*.png asset"),
]


def scan(root: Path, allowed: set):
    bad, scanned = [], 0
    for py in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in py.parts):
            continue
        rel = py.relative_to(root).as_posix()
        if rel in allowed:
            continue
        scanned += 1
        src = py.read_text(encoding="utf-8", errors="ignore")
        for pat, why in PATTERNS:
            for m in re.finditer(pat, src):
                line = src[:m.start()].count("\n") + 1
                bad.append(f"{py.relative_to(root).as_posix()}:{line} — {why}")
    return bad, scanned


failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


print("\nreal repository")
bad, scanned = scan(ROOT, ALLOWED)
print(f"  scanned {scanned} runtime modules recursively; allowlist = {sorted(ALLOWED)}")
check("no runtime module accesses pose files directly", not bad, str(bad))

print("\nthe check actually detects violations (nested fixtures)")
td = Path(tempfile.mkdtemp())
fixtures = {
    "pkg/deep/router.py": 'P = "character/poses/host_neutral_presenter.png"\n',
    "pkg/deep/compositor.py": 'from pathlib import Path\np = Path("character") / "poses"\n',
    "pkg/globber.py": 'from pathlib import Path\nfiles = Path(".").glob("host_*.png")\n',
    "pkg/lister.py": 'import os\nos.listdir("character/poses")\n',
    "pkg/opener.py": 'f = open("host_thinking_hand_at_chin.png", "rb")\n',
}
for rel, src in fixtures.items():
    p = td / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src, encoding="utf-8")

found, _ = scan(td, set())
for rel in fixtures:
    check(f"detects {rel}", any(f.startswith(rel + ":") for f in found), str(found))

check("nested files are reached at all (rglob, not glob)",
      any("deep/" in f for f in found), str(found))

print("\nthe resolver is actually consumed (the negative check is not vacuous)")


def calls_in(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}


comp = ROOT / "composite_character.py"
router = ROOT / "route_images.py"
check("compositor exists", comp.exists())
check("router exists", router.exists())
comp_calls = calls_in(comp)
check("compositor calls pose_registry.resolve()", "pose_registry.resolve" in comp_calls)
check("compositor calls pose_registry.metadata()", "pose_registry.metadata" in comp_calls)
check("router delegates rendering to the compositor",
      "composite_character.render_production" in calls_in(router))
check("router reads pose ids from the registry, not from disk",
      "pose_registry.list_poses" in calls_in(router))

print("\n" + "=" * 58)
print(f"FAILED ({len(failures)}): {failures}" if failures else "ALL PASS")
sys.exit(1 if failures else 0)
