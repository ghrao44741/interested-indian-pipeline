"""Deterministic tests for pose resolution and replay safety.

No API calls and no image generation. The property under test: an unapproved,
altered, missing or raw asset must never be resolvable, and a replay must never
destroy provenance.

Run:  python tests/test_pose_registry.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pose_registry as pr  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def expect_error(name, fn, needle=""):
    try:
        fn()
        check(name, False, "no error raised")
    except pr.PoseError as e:
        check(name, needle.lower() in str(e).lower() if needle else True, str(e)[:70])


REAL = json.loads((ROOT / "character" / "character_spec.json").read_text(encoding="utf-8"))


def with_spec(mutate):
    """Run against a temporary copy of the spec, leaving the real one untouched."""
    spec = json.loads(json.dumps(REAL))
    mutate(spec)
    td = Path(tempfile.mkdtemp())
    p = td / "character_spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return mock.patch.object(pr, "SPEC_PATH", p)


print("\nresolution guards")
expect_error("unknown pose rejected", lambda: pr.resolve("no_such_pose"), "unknown pose")

def _pend(s):
    s["pose_library"]["registry"]["neutral_presenter"]["status"] = "pending-approval"
with with_spec(_pend):
    expect_error("pending pose rejected", lambda: pr.resolve("neutral_presenter"), "not approved")

def _badhash(s):
    s["pose_library"]["registry"]["neutral_presenter"]["sha256"] = "0" * 64
with with_spec(_badhash):
    expect_error("hash mismatch rejected", lambda: pr.resolve("neutral_presenter"), "hash mismatch")

def _missing(s):
    s["pose_library"]["registry"]["neutral_presenter"]["path"] = "character/poses/not_here.png"
with with_spec(_missing):
    expect_error("missing file rejected", lambda: pr.resolve("neutral_presenter"), "missing")

print("\nscene-bound protection")
expect_error("scene-bound rejected without permission",
             lambda: pr.resolve("seated_reading_document"), "scene-bound")
check("scene-bound accepted with explicit permission",
      pr.resolve("seated_reading_document", scene_bound=True).exists())

print("\nlisting")
generic = pr.list_poses(generic_only=True)
check("generic listing excludes the scene-bound tableau",
      "seated_reading_document" not in generic, str(generic))
def _pend2(s):
    s["pose_library"]["registry"]["thinking_hand_at_chin"]["status"] = "pending-approval"
with with_spec(_pend2):
    check("generic listing excludes pending assets",
          "thinking_hand_at_chin" not in pr.list_poses(generic_only=True))

print("\nraw sources are unreachable")
raws = list((ROOT / "character" / "pose_sources").rglob("_raw_*.png"))
check("raw source files exist to be excluded", bool(raws), "no raws found")
registered = {e["path"] for e in pr._registry().values()}
check("no raw source is registered",
      not any("pose_sources" in p for p in registered))
check("no registered path sits outside character/poses/",
      all(p.startswith("character/poses/") for p in registered), str(registered))
expect_error("a raw id is not resolvable",
             lambda: pr.resolve("_raw_neutral_presenter"), "unknown pose")

print("\nintegrity: every approved asset")
a = pr.audit()
check("all 11 approved assets pass audit", len(a["ok"]) == 11 and not a["problems"],
      f"ok={len(a['ok'])} problems={a['problems']}")

print("\nno hash-verification bypass exists")
import inspect  # noqa: E402
check("resolve() has no verify_hash parameter",
      "verify_hash" not in inspect.signature(pr.resolve).parameters,
      str(inspect.signature(pr.resolve)))

print("\nreplay preserves provenance without generating")
import generate_poses as gp  # noqa: E402

spec_before = (ROOT / "character" / "character_spec.json").read_bytes()
calls = {"n": 0}


class _Boom:
    class images:
        @staticmethod
        def edit(**kw):
            calls["n"] += 1
            raise AssertionError("replay must not call the API")


results = gp.generate_batch(json.loads(spec_before), _Boom(), batch=2, force=False)
check("replay returns all 7 batch-2 records", len(results) == 7, f"got {len(results)}")
check("replay performed zero generation calls", calls["n"] == 0, str(calls["n"]))
check("every replayed record is marked ok", all(r.get("ok") for r in results))
check("every replayed record kept its hash",
      all(len(r.get("sha256", "")) == 64 for r in results))
check("replay left the spec byte-for-byte unchanged",
      (ROOT / "character" / "character_spec.json").read_bytes() == spec_before)

print("\nregistry() public accessor (Task 2B-B2a)")
check("registry() returns the same data _registry() does",
      pr.registry() == pr._registry())
with with_spec(_pend):
    check("registry() reflects a mutated spec the same way _registry() does",
          pr.registry() == pr._registry())

print("\n" + "=" * 58)
print(f"FAILED ({len(failures)}): {failures}" if failures else "ALL PASS")
sys.exit(1 if failures else 0)
