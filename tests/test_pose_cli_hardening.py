"""CLI-level hardening tests for pose replay and integrity.

These drive generate_poses.main() through sys.argv rather than calling
generate_batch() directly. That distinction matters: an earlier replay fix passed
at the function level while the CLI still rewrote the spec, so the entry point is
what has to be proven.

Every case asserts three things together — exit code, zero API calls, and the
spec left byte-for-byte unchanged — because any one of them passing alone is not
safety.

Run:  python tests/test_pose_cli_hardening.py
"""

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import generate_poses as gp  # noqa: E402
import pose_registry as pr  # noqa: E402

SPEC = ROOT / "character" / "character_spec.json"
POSES = ROOT / "character" / "poses"
VICTIM = POSES / "host_thinking_hand_at_chin.png"

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


class NoCall:
    """Any API use fails the test outright."""
    class images:
        @staticmethod
        def edit(**kw):
            raise AssertionError("generation attempted during a replay")


def run_cli(argv):
    before = SPEC.read_bytes()
    with mock.patch.object(sys, "argv", ["generate_poses.py"] + argv), \
         mock.patch.object(gp, "get_client", lambda: NoCall()):
        try:
            code = gp.main()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        except AssertionError as e:
            return "API_CALLED:" + str(e), SPEC.read_bytes() == before
    return code, SPEC.read_bytes() == before


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


ORIGINAL = sha(VICTIM)
SAFE = Path(tempfile.mkdtemp()) / "orig.png"
shutil.copy2(VICTIM, SAFE)

print("\n1. healthy replay")
code, unchanged = run_cli(["--batch", "2"])
check("exits 0", code == 0, f"exit={code}")
check("spec byte-identical", unchanged)
check("no API call", not str(code).startswith("API_CALLED"))

print("\n2. tampered approved file")
img = Image.open(VICTIM).convert("RGBA")
img.putpixel((5, 5), (255, 0, 0, 255))
img.save(VICTIM)
try:
    check("tamper actually changed the bytes", sha(VICTIM) != ORIGINAL)
    code, unchanged = run_cli(["--batch", "2"])
    check("exits nonzero", code != 0 and not str(code).startswith("API_CALLED"), f"exit={code}")
    check("spec unchanged", unchanged)
finally:
    shutil.copy2(SAFE, VICTIM)
check("file restored", sha(VICTIM) == ORIGINAL)

print("\n3. approved file missing")
hold = Path(tempfile.mkdtemp()) / "held.png"
shutil.move(str(VICTIM), str(hold))
try:
    code, unchanged = run_cli(["--batch", "2"])
    check("exits nonzero", code != 0 and not str(code).startswith("API_CALLED"), f"exit={code}")
    check("spec unchanged", unchanged)
    code, unchanged = run_cli(["--batch", "2", "--force"])
    check("--force on a missing approved asset exits nonzero",
          code != 0 and not str(code).startswith("API_CALLED"), f"exit={code}")
    check("spec unchanged under --force", unchanged)
finally:
    shutil.move(str(hold), str(VICTIM))
check("file restored", sha(VICTIM) == ORIGINAL)

print("\n4. invalid alpha (fully opaque asset)")
Image.new("RGBA", (1024, 1024), (250, 247, 242, 255)).save(VICTIM)
try:
    code, unchanged = run_cli(["--batch", "2"])
    check("exits nonzero", code != 0 and not str(code).startswith("API_CALLED"), f"exit={code}")
    check("spec unchanged", unchanged)
finally:
    shutil.copy2(SAFE, VICTIM)
check("file restored", sha(VICTIM) == ORIGINAL)

print("\n5. existing file with no provenance record")
spec_copy = json.loads(SPEC.read_text(encoding="utf-8"))
spec_copy["pose_library"]["batch_2_results"] = [
    r for r in spec_copy["pose_library"]["batch_2_results"]
    if r["id"] != "thinking_hand_at_chin"]
spec_copy["pose_library"]["registry"].pop("thinking_hand_at_chin", None)
tmp_spec = Path(tempfile.mkdtemp()) / "character_spec.json"
tmp_spec.write_text(json.dumps(spec_copy), encoding="utf-8")
before = tmp_spec.read_bytes()
with mock.patch.object(gp, "SPEC_PATH", tmp_spec), \
     mock.patch.object(gp, "load_spec",
                       lambda: json.loads(tmp_spec.read_text(encoding="utf-8"))), \
     mock.patch.object(gp, "get_client", lambda: NoCall()), \
     mock.patch.object(sys, "argv", ["generate_poses.py", "--batch", "2"]):
    try:
        code = gp.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except AssertionError:
        code = "API_CALLED"
check("exits nonzero", code != 0 and code != "API_CALLED", f"exit={code}")
check("spec unchanged", tmp_spec.read_bytes() == before)

print("\n6. verify_asset enforces each rule")
rec = pr.metadata("thinking_hand_at_chin")
ok, probs, obs = gp.verify_asset(VICTIM, rec)
check("healthy asset verifies", ok, str(probs))
check("observed hash reported", obs.get("sha256") == ORIGINAL)
ok2, probs2, _ = gp.verify_asset(VICTIM, {**rec, "sha256": "0" * 64})
check("hash mismatch reports expected and observed",
      not ok2 and "mismatch" in probs2[0] and "0000" in probs2[0], str(probs2))
ok3, probs3, _ = gp.verify_asset(VICTIM, {**rec, "dimensions": "1x1"})
check("dimension mismatch caught", not ok3, str(probs3))
flat = Path(tempfile.mkdtemp()) / "flat.png"
Image.new("RGBA", (1024, 1024), (250, 247, 242, 255)).save(flat)
ok4, probs4, _ = gp.verify_asset(flat, {"sha256": sha(flat), "dimensions": "1024x1024"})
check("opaque image fails corners and transparency",
      not ok4 and any("corner" in p for p in probs4), str(probs4))
ok5, probs5, _ = gp.verify_asset(Path("nope.png"), rec)
check("missing file reported", not ok5 and "missing" in probs5[0])

print("\n7. batch-wide --force over approved poses is rejected")
# The old candidate-on-force behaviour is gone: it spent one paid call per pose
# and overwrote each pose's own raw provenance. Replacement is now a single-pose
# command, covered non-destructively in test_replacement_candidate.py.
code, unchanged = run_cli(["--batch", "2", "--force"])
check("exits nonzero", code != 0 and not str(code).startswith("API_CALLED"), f"exit={code}")
check("zero API calls", not str(code).startswith("API_CALLED"))
check("spec unchanged", unchanged)
check("canonical bytes untouched", sha(VICTIM) == ORIGINAL)
check("no pose_candidates directory created in the repo",
      not (ROOT / "character" / "pose_candidates").exists())

print("\n8. final audit")
a = pr.audit()
check("all 11 approved assets pass audit", len(a["ok"]) == 11 and not a["problems"],
      f"{len(a['ok'])} ok, problems={a['problems']}")
check("victim file is byte-identical to where we started", sha(VICTIM) == ORIGINAL)

print("\n" + "=" * 58)
print(f"FAILED ({len(failures)}): {failures}" if failures else "ALL PASS")
sys.exit(1 if failures else 0)
