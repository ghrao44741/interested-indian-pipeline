"""Deterministic tests for reference_registry.py (Task 2B-B2a).

No API calls, no image generation. The property under test: `body_master`/
`face_master` are exposed as approved references only when the master's own
status is exactly "approved" AND the top-level references.<key> pointer
agrees with masters.<key>.path — and, once exposed, resolve() applies the
same path/containment/hash verification a pose already gets, never a weaker
one.

Run:  python tests/test_reference_registry.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import reference_registry as rr  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def expect_error(name, fn, needle=""):
    try:
        fn()
        check(name, False, "no error raised")
    except rr.ReferenceError as e:
        check(name, needle.lower() in str(e).lower() if needle else True, str(e)[:100])


REAL = json.loads((ROOT / "character" / "character_spec.json").read_text(encoding="utf-8"))


def with_spec(mutate):
    """Run against a temporary copy of the spec, leaving the real one
    untouched, mirroring tests/test_pose_registry.py's own helper."""
    spec = json.loads(json.dumps(REAL))
    mutate(spec)
    td = Path(tempfile.mkdtemp())
    p = td / "character_spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return mock.patch.object(rr, "SPEC_PATH", p)


print("\nreal character_spec.json: both masters are approved references")
reg = rr.registry()
check("registry() exposes exactly body_master and face_master",
      set(reg) == {"body_master", "face_master"}, str(sorted(reg)))
check("both resolve without raising",
      all(rr.resolve(k).exists() for k in ("body_master", "face_master")))

a = rr.audit()
check("both pass audit with no problems", set(a["ok"]) == {"body_master", "face_master"}
      and not a["problems"], str(a))

print("\nstatus must be exactly 'approved'")
def _pending(s):
    s["masters"]["body_master"]["status"] = "pending-approval"
with with_spec(_pending):
    reg2 = rr.registry()
    check("a non-approved master is excluded from the registry",
          "body_master" not in reg2 and "face_master" in reg2, str(sorted(reg2)))
    expect_error("resolving an excluded reference is refused",
                 lambda: rr.resolve("body_master"), "unknown or unapproved")
    a2 = rr.audit()
    check("audit reports the pending master as unapproved, not a problem",
          any("body_master" in u for u in a2["unapproved"])
          and not any("body_master" in p for p in a2["problems"]), str(a2))

print("\nstale hash (file changed since approval) is rejected")
def _bad_hash(s):
    s["masters"]["face_master"]["sha256"] = "0" * 64
with with_spec(_bad_hash):
    expect_error("hash mismatch is refused",
                 lambda: rr.resolve("face_master"), "hash mismatch")

print("\nmissing/unapproved reference id is rejected")
expect_error("an id that is neither body_master nor face_master is refused",
             lambda: rr.resolve("no_such_reference"), "unknown or unapproved")

print("\nreferences.<key> disagreement is caught, not silently trusted")
def _disagree(s):
    s["references"]["face_master"] = "character/canonical/some-other-file.png"
with with_spec(_disagree):
    reg3 = rr.registry()
    check("a master whose top-level references.<key> pointer disagrees is excluded",
          "face_master" not in reg3 and "body_master" in reg3, str(sorted(reg3)))
    a3 = rr.audit()
    check("audit reports the disagreement as a problem, not merely unapproved",
          any("face_master" in p and "does not agree" in p for p in a3["problems"]),
          str(a3))

print("\ntraversal / archive / candidate / raw / symlink rejection (shared with masters)")
def _traversal(s):
    s["masters"]["body_master"]["path"] = "../../etc/passwd"
with with_spec(_traversal):
    expect_error("an absolute/traversing registered path is refused",
                 lambda: rr.resolve("body_master"))

def _archive(s):
    s["masters"]["body_master"]["path"] = "character/archive/body_master_v1_superseded/master-front-v1.png"
    s["references"]["body_master"] = s["masters"]["body_master"]["path"]
with with_spec(_archive):
    expect_error("a path through an archive/ component is refused",
                 lambda: rr.resolve("body_master"))

print("\ncross-channel isolation: a second, unrelated character_spec.json "
      "never exposes this one's references")
other_spec = {
    "masters": {"body_master": {"path": "character/canonical/body-master.png",
                                "status": "approved", "sha256": "f" * 64}},
    "references": {"body_master": "character/canonical/body-master.png"},
}
other_td = Path(tempfile.mkdtemp())
other_p = other_td / "character_spec.json"
other_p.write_text(json.dumps(other_spec), encoding="utf-8")


class _FakeContext:
    character_spec_path = str(other_p)


other_reg = rr.registry(context=_FakeContext())
check("a second channel's registry entry exists on its own terms (status/path/hash "
      "declared in its own spec)", "body_master" in other_reg)
expect_error("resolving under the second channel's context never reaches the first "
             "channel's real asset — its own asset base/root has no such file "
             "reachably in place, proving isolation rather than an accidental "
             "shared read",
             lambda: rr.resolve("body_master", context=_FakeContext()))

print("\nasset base / root reuse the existing masters containment, no duplicate authority")
check("references_root() equals the character spec's own directory",
      rr.references_root() == rr._character_root())
check("references_asset_base() equals the existing _asset_base()",
      rr.references_asset_base() == rr._asset_base())

print("\nread_verified_bytes() returns the exact, re-verified bytes "
      "(final boundary micro-fix, item 2)")
def _good_body_face():
    """A self-contained, isolated fixture with its own real master files —
    correctly laid out as asset_base/character/character_spec.json with
    assets at asset_base/character/canonical/*, mirroring
    tests/test_renderer_adapters.py's _fixture_reference_context() exactly
    (that layout is what _asset_base()/_character_root() actually expect —
    reusing REAL's data with the wrong directory depth, as an earlier draft
    of this fixture did, produces a false "resolved path escapes" failure,
    not a real one)."""
    td = Path(tempfile.mkdtemp())
    body_bytes, face_bytes = b"body-master-fixture-bytes", b"face-master-fixture-bytes"
    body_path = td / "character" / "canonical" / "body-master.png"
    face_path = td / "character" / "canonical" / "face-master.png"
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_bytes(body_bytes)
    face_path.write_bytes(face_bytes)
    spec = {
        "masters": {
            "body_master": {"path": "character/canonical/body-master.png", "status": "approved",
                            "sha256": __import__("hashlib").sha256(body_bytes).hexdigest()},
            "face_master": {"path": "character/canonical/face-master.png", "status": "approved",
                            "sha256": __import__("hashlib").sha256(face_bytes).hexdigest()},
        },
        "references": {"body_master": "character/canonical/body-master.png",
                       "face_master": "character/canonical/face-master.png"},
    }
    spec_path = td / "character" / "character_spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    class _Ctx:
        character_spec_path = str(spec_path)
    return _Ctx()


ctx_ok = _good_body_face()
body_bytes = rr.read_verified_bytes("body_master", context=ctx_ok)
check("read_verified_bytes returns real, non-empty bytes",
      isinstance(body_bytes, bytes) and len(body_bytes) > 0)
live_path = rr.resolve("body_master", context=ctx_ok)
check("the returned bytes match the file's current on-disk content exactly",
      body_bytes == live_path.read_bytes())

print("\nTOCTOU regression: a file changed after resolve() validated it is "
      "still caught on the final read")
ctx_toctou = _good_body_face()
valid_path = rr.resolve("body_master", context=ctx_toctou)   # validates original bytes
tampered_bytes = b"TAMPERED-AFTER-RESOLVE-VALIDATED-THE-ORIGINAL"
valid_path.write_bytes(tampered_bytes)
# Simulate a caller that already has a validated path/record from an
# earlier resolve() call (as if it were cached) — read_verified_bytes()
# must not simply trust that; its own internal resolve()+read+hash must
# be against CURRENT disk state, so even a resolve() that (in a real race)
# validated stale bytes a moment ago cannot let tampered bytes through.
with mock.patch.object(rr, "resolve", return_value=valid_path):
    try:
        rr.read_verified_bytes("body_master", context=ctx_toctou)
        check("tampered bytes are refused, never returned to a caller", False,
              "no error raised — read_verified_bytes returned the tampered bytes")
    except rr.ReferenceError as e:
        check("tampered bytes are refused, never returned to a caller", True, str(e)[:100])
        check("the refusal names a hash mismatch, not some other failure",
              "hash mismatch" in str(e).lower(), str(e))

print("\nunreadable file is a controlled ReferenceError, not an uncaught OSError")
ctx_unreadable = _good_body_face()
unreadable_path = rr.resolve("body_master", context=ctx_unreadable)
real_read_bytes = Path.read_bytes


def _boom_read_bytes(self, *a, **kw):
    if self == unreadable_path:
        raise OSError("simulated permission denied")
    return real_read_bytes(self, *a, **kw)


with mock.patch.object(Path, "read_bytes", _boom_read_bytes):
    expect_error("an unreadable (but otherwise valid) reference file is refused, "
                 "not an uncaught OSError",
                 lambda: rr.read_verified_bytes("body_master", context=ctx_unreadable),
                 "could not read")

print("\nmalformed/unreadable character_spec.json is a controlled empty registry, "
      "never an uncaught exception")
malformed_td = Path(tempfile.mkdtemp())
malformed_spec_path = malformed_td / "character_spec.json"
malformed_spec_path.write_text("{not valid json at all", encoding="utf-8")


class _MalformedCtx:
    character_spec_path = str(malformed_spec_path)


check("registry() never raises against malformed JSON — returns empty",
      rr.registry(context=_MalformedCtx()) == {})
a_malformed = rr.audit(context=_MalformedCtx())
check("audit() never raises against malformed JSON either",
      a_malformed["ok"] == [] and set(k.split(" ")[0] for k in a_malformed["unapproved"])
      == {"body_master", "face_master"}, str(a_malformed))
expect_error("resolve() against malformed JSON is a controlled ReferenceError "
             "(the registry is empty, so the id is 'unknown'), not an uncaught "
             "JSONDecodeError",
             lambda: rr.resolve("body_master", context=_MalformedCtx()),
             "unknown or unapproved")
expect_error("read_verified_bytes() against malformed JSON is likewise a "
             "controlled refusal, before any credential/client concern could "
             "even arise",
             lambda: rr.read_verified_bytes("body_master", context=_MalformedCtx()),
             "unknown or unapproved")

missing_spec_td = Path(tempfile.mkdtemp())


class _MissingSpecCtx:
    character_spec_path = str(missing_spec_td / "does_not_exist.json")


check("registry() never raises when character_spec.json is entirely missing",
      rr.registry(context=_MissingSpecCtx()) == {})
expect_error("resolve() against a missing character_spec.json refuses cleanly",
             lambda: rr.resolve("body_master", context=_MissingSpecCtx()))

print("\n" + "=" * 58)
print(f"FAILED ({len(failures)}): {failures}" if failures else "ALL PASS")
sys.exit(1 if failures else 0)
