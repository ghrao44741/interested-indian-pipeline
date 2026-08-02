"""Replacement-candidate workflow — non-destructive by construction.

Two layers of protection, because a test that mutates repository assets is a
liability even when it cleans up:

  1. A complete temporary fixture project with PIPELINE_DIR and SPEC_PATH
     patched, so generation writes nowhere near the repository.
  2. A whole-tree hash census taken before and compared after, inside try/finally,
     covering all approved poses, every raw source, both masters and the spec.
     An interrupted or failing run still reports what moved.

Run:  python tests/test_replacement_candidate.py
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

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def census(root: Path) -> dict:
    """Hash every asset a test must not disturb."""
    out = {}
    for sub in ("character/poses", "character/pose_sources", "character/canonical"):
        d = root / sub
        if d.exists():
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    out[f.relative_to(root).as_posix()] = sha(f)
    spec = root / "character" / "character_spec.json"
    if spec.exists():
        out["character/character_spec.json"] = sha(spec)
    return out


# ── fixture project ──────────────────────────────────────────────────────────

def build_fixture() -> Path:
    """A minimal but complete project: two masters, one approved pose, a spec."""
    td = Path(tempfile.mkdtemp())
    (td / "character" / "poses").mkdir(parents=True)
    (td / "character" / "pose_sources" / "batch1").mkdir(parents=True)

    def img(path, alpha=True):
        im = Image.new("RGBA" if alpha else "RGB", (1024, 1024),
                       (250, 247, 242, 0) if alpha else (250, 247, 242))
        im.paste(Image.new("RGBA", (300, 600), (60, 60, 60, 255)), (360, 200))
        im.save(path)

    for m in ("body-master.png", "face-master.png"):
        (td / "character" / "canonical").mkdir(parents=True, exist_ok=True)
        img(td / "character" / "canonical" / m, alpha=False)
    pose = td / "character" / "poses" / "host_demo_pose.png"
    img(pose)
    (td / "character" / "pose_sources" / "batch1" / "_raw_demo_pose.png").write_bytes(b"RAWDATA")

    spec = {
        "version": 4,
        "identity": {"prompt_block": "x", "summary": "x", "age_reads_as": "25-30"},
        "immutable": ["a"], "variable": ["b"], "negative_prompt": "none",
        "negative_prompt_by_framing": {"full_body": ""},
        "style": {"rules": ["flat"], "palette": {"background_default": "#FAF7F2"}},
        "references": {"body_master": "character/canonical/body-master.png",
                       "face_master": "character/canonical/face-master.png"},
        "master_authority": {"body_master": ["clothing"], "face_master": ["face"]},
        "pose_library": {
            "version": 2, "status": "approved", "approved_batches": [1],
            "batch_1": [{"id": "demo_pose", "brief": "standing", "expression": "neutral"}],
            "batch_1_results": [{"id": "demo_pose", "ok": True,
                                 "path": "character/poses/host_demo_pose.png",
                                 "sha256": sha(pose), "dimensions": "1024x1024"}],
            "registry": {"demo_pose": {"id": "demo_pose", "status": "approved",
                                       "path": "character/poses/host_demo_pose.png",
                                       "sha256": sha(pose), "dimensions": "1024x1024"}},
        },
    }
    (td / "character" / "character_spec.json").write_text(json.dumps(spec, indent=2),
                                                          encoding="utf-8")
    return td


class Gen:
    """Counts calls and returns a usable image."""
    def __init__(self, blank=False):
        self.n = 0
        self.blank = blank
        outer = self

        class images:
            @staticmethod
            def edit(**kw):
                outer.n += 1
                import base64
                import io
                buf = io.BytesIO()
                im = Image.new("RGB", (1024, 1024), (250, 247, 242))
                if not outer.blank:
                    im.paste(Image.new("RGB", (300, 600), (60, 60, 60)), (360, 200))
                im.save(buf, format="PNG")

                class R:
                    data = [type("D", (), {"b64_json": base64.b64encode(buf.getvalue()).decode()})]
                return R()
        self.images = images


def run(fx: Path, argv, client):
    spec_path = fx / "character" / "character_spec.json"
    with mock.patch.object(gp, "PIPELINE_DIR", fx), \
         mock.patch.object(gp, "SPEC_PATH", spec_path), \
         mock.patch.object(gp, "load_spec",
                           lambda: json.loads(spec_path.read_text(encoding="utf-8"))), \
         mock.patch.object(gp, "get_client", lambda: client), \
         mock.patch.object(sys, "argv", ["generate_poses.py"] + argv):
        try:
            return gp.main()
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1


REPO_BEFORE = census(ROOT)

try:
    print("\nbatch-wide --force over approved poses is rejected")
    fx = build_fixture()
    g = Gen()
    before = census(fx)
    code = run(fx, ["--batch", "1", "--force"], g)
    check("exits nonzero", code != 0, f"exit={code}")
    check("zero API calls", g.n == 0, f"calls={g.n}")
    check("fixture untouched", census(fx) == before)

    print("\nreplacement requires a valid, approved pose id")
    fx = build_fixture(); g = Gen()
    check("unknown id exits nonzero and spends nothing",
          run(fx, ["--replacement-candidate", "no_such_pose"], g) != 0 and g.n == 0)
    fx2 = build_fixture(); g2 = Gen()
    s = json.loads((fx2 / "character" / "character_spec.json").read_text(encoding="utf-8"))
    s["pose_library"]["registry"]["demo_pose"]["status"] = "pending-approval"
    (fx2 / "character" / "character_spec.json").write_text(json.dumps(s), encoding="utf-8")
    check("unapproved id exits nonzero and spends nothing",
          run(fx2, ["--replacement-candidate", "demo_pose"], g2) != 0 and g2.n == 0)

    print("\none approved pose -> exactly one call, fully contained")
    fx = build_fixture()
    before = census(fx)
    g = Gen()
    code = run(fx, ["--replacement-candidate", "demo_pose"], g)
    check("exits 0", code == 0, f"exit={code}")
    check("exactly one API call", g.n == 1, f"calls={g.n}")

    vdir = fx / "character" / "pose_candidates" / "demo_pose" / "v01"
    for f in ("raw.png", "asset.png", "candidate.json"):
        check(f"wrote {f}", (vdir / f).exists())
    written = {p.relative_to(fx).as_posix() for p in fx.rglob("*")
               if p.is_file() and p.relative_to(fx).as_posix() not in before}
    check("every new file is under pose_candidates/demo_pose/v01/",
          all(w.startswith("character/pose_candidates/demo_pose/v01/")
              or w == "character/character_spec.json" for w in written), str(written))
    check("nothing written into character/poses/",
          not any(w.startswith("character/poses/") for w in written))
    check("nothing written into character/pose_sources/",
          not any(w.startswith("character/pose_sources/") for w in written))

    after = census(fx)
    unchanged = {k: v for k, v in before.items()
                 if k != "character/character_spec.json"}
    check("approved pose, raw sources and masters all byte-identical",
          all(after[k] == v for k, v in unchanged.items()),
          str([k for k, v in unchanged.items() if after.get(k) != v]))

    s2 = json.loads((fx / "character" / "character_spec.json").read_text(encoding="utf-8"))
    s1 = json.loads(json.dumps(json.loads(
        (build_fixture() / "character" / "character_spec.json").read_text(encoding="utf-8"))))
    check("approved registry entry unchanged",
          s2["pose_library"]["registry"]["demo_pose"]["sha256"]
          == before["character/poses/host_demo_pose.png"])
    check("approved batch results unchanged",
          s2["pose_library"]["batch_1_results"][0]["sha256"]
          == before["character/poses/host_demo_pose.png"])
    cands = s2["pose_library"].get("replacement_candidates", [])
    check("candidate recorded separately as pending-approval",
          len(cands) == 1 and cands[0]["status"] == "pending-approval", str(cands))

    print("\nsecond request creates v02, not v01")
    g = Gen()
    run(fx, ["--replacement-candidate", "demo_pose"], g)
    check("v02 created", (fx / "character" / "pose_candidates" / "demo_pose" / "v02").exists())
    check("v01 raw preserved", (vdir / "raw.png").exists())

    print("\ninvalid candidate is kept for diagnosis but never registered")
    fx = build_fixture()
    g = Gen(blank=True)          # empty frame -> no subject, fails verification
    code = run(fx, ["--replacement-candidate", "demo_pose"], g)
    check("exits nonzero", code != 0, f"exit={code}")
    vd = fx / "character" / "pose_candidates" / "demo_pose" / "v01"
    check("raw kept for diagnosis", (vd / "raw.png").exists())
    s3 = json.loads((fx / "character" / "character_spec.json").read_text(encoding="utf-8"))
    check("not added to the candidate registry",
          not s3["pose_library"].get("replacement_candidates"))

finally:
    REPO_AFTER = census(ROOT)
    moved = [k for k in REPO_BEFORE if REPO_AFTER.get(k) != REPO_BEFORE[k]]
    added = [k for k in REPO_AFTER if k not in REPO_BEFORE]
    check("no repository asset was modified", not moved, str(moved))
    check("no repository asset was added", not added, str(added))

print("\n" + "=" * 58)
print(f"FAILED ({len(failures)}): {failures}" if failures else "ALL PASS")
sys.exit(1 if failures else 0)
