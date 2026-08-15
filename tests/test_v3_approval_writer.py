"""Task 2B-B2b-3 — the schema-v3 approval writer (approve_checkpoint.py's
write_approval_v3), exercised end to end against the REAL, now-activated
require_canonical_visual_execution_ready().

Every other B2b-3 test file (test_generation_gate.py, test_approval_gate.py,
test_canonical_workflow.py) proves the v3 *validator* is correct against
hand-built approval records. This file proves the v3 *writer* — the thing a
human actually runs — produces a record the real validator accepts, and
refuses every canonical blocker before writing anything.

No provider/API/GPU/download call anywhere in this file.

    python tests/test_v3_approval_writer.py
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if "anthropic" not in sys.modules:
    _stub = types.ModuleType("anthropic")
    _stub.Anthropic = object
    sys.modules["anthropic"] = _stub

import approve_checkpoint as ac          # noqa: E402
import channel_context as cc             # noqa: E402
import channel_fixture                   # noqa: E402
import generation_gate as gate           # noqa: E402
import plan_visuals                      # noqa: E402
import pose_registry                     # noqa: E402
import render_channel_dna as rd          # noqa: E402
import renderers                         # noqa: E402
import route_failures                    # noqa: E402
import source_ids                        # noqa: E402
import visual_routes as vr               # noqa: E402

failures = []
_fixtures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def census(root: Path) -> dict:
    out = {}
    for sub in ("character",):
        d = root / sub
        if d.exists():
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    out[f.relative_to(root).as_posix()] = sha(f)
    return out


# ── fixture world — a genuinely gate-passing v3 baseline ───────────────────
#
# Same shape as test_generation_gate.py's build_canonical_baseline(), kept
# separate per this codebase's convention (each test file duplicates its own
# minimal fixture helpers rather than importing another test file's
# top-level code) — but here the approval itself is produced by the REAL
# write_approval_v3(), never hand-built.

SCRIPT = ("The minister resigned in July. Nobody expected it.\n\n"
          "The exam was cancelled the same week. Then it was rescheduled.\n")


def _png(path: Path, alpha=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA" if alpha else "RGB", (256, 256),
                   (250, 247, 242, 0) if alpha else (250, 247, 242))
    im.paste(Image.new("RGBA", (80, 160), (60, 60, 60, 255)), (88, 60))
    im.save(path)


def build_fixture() -> tuple[Path, Path]:
    td = Path(tempfile.mkdtemp())
    _fixtures.append(td)

    for m in ("body-master.png", "face-master.png"):
        _png(td / "character" / "canonical" / m, alpha=False)
    spec = {
        "version": 4, "identity": {"prompt_block": "x"}, "immutable": ["a"],
        "variable": ["b"], "negative_prompt": "none",
        "negative_prompt_by_framing": {"full_body": ""}, "style": {"rules": ["flat"]},
        "references": {"body_master": "character/canonical/body-master.png",
                       "face_master": "character/canonical/face-master.png"},
        "master_authority": {"body_master": ["clothing"], "face_master": ["face"]},
        "masters": {k: {"path": f"character/canonical/{v}",
                        "sha256": sha(td / "character" / "canonical" / v),
                        "status": "approved"}
                    for k, v in (("body_master", "body-master.png"),
                                 ("face_master", "face-master.png"))},
        "pose_library": {"version": 2, "status": "approved", "approved_batches": [1],
                         "registry": {}},
    }
    (td / "character" / "character_spec.json").write_text(
        json.dumps(spec, indent=2), encoding="utf-8")

    proj = td / "demo_project"
    proj.mkdir()
    (proj / "script_demo.txt").write_text(SCRIPT, encoding="utf-8")
    units = source_ids.build_source_units(SCRIPT)
    source_ids.save_units(proj, units)
    scenes = [{"id": f"SCENE-{i:03d}", "image": f"SCENE-{i:03d}.png",
              "script": u["text"], "source_ids": [u["id"]],
              "shot_instance_id": f"{u['id']}-S01",
              "source_match": "ok", "visual_match": "ok", "visual_state": "planned",
              "visual_asset_id": f"VIS-{i:03d}-A"}
             for i, u in enumerate(units, 1)]
    manifest = channel_fixture.stamp(
        {"episode": "demo", "identity_state": "ok", "identity_reasons": [],
         "scenes": scenes}, project_dir=proj)
    channel_fixture.install(td)
    (proj / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return td, proj


def install_canonical_channel(root: Path) -> None:
    doc = channel_fixture.pack_document()
    doc["renderers"]["capabilities"]["ILLUSTRATION"] = "flux_illustration"
    d = root / "channels" / channel_fixture.CHANNEL_ID
    d.mkdir(parents=True, exist_ok=True)
    (d / "channel.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")


def _canonical_route(scene, visual_type, renderer_id, cost_category, paid, route_args,
                     narration, prompt=None) -> dict:
    return {
        "visual_asset_id": scene["visual_asset_id"], "source_ids": scene["source_ids"],
        "shot_instance_id": scene["shot_instance_id"], "scene_id": scene["id"],
        "output_file": scene["image"], "status": "READY", "review_reasons": [],
        "candidate_visual_types": [], "routing_confidence": 0.9, "manual_override": None,
        "visual_type": visual_type, "route_args": route_args, "narration": narration,
        "prompt": prompt, "visual_cue": None, "overlay_text": None,
        "renderer_id": renderer_id, "host_renderer_id": None,
        "cost_category": cost_category, "paid": paid,
        "host_present": False, "host_method": None, "host_pose_id": None,
        "host_scene_bound": None, "host_reference_asset_ids": None, "host_placement": None,
    }


def install_canonical_routes(proj: Path, ctx) -> dict:
    manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
    scenes = manifest["scenes"]
    routes = [
        _canonical_route(scenes[0], "PHOTO", "pexels", "free_api", False,
                         {"map": None, "chart": None, "timeline": None, "document": None,
                          "photo": {"query": "a government building", "constraints": None},
                          "illustration": None, "reenactment": None}, "n1"),
    ] + [
        _canonical_route(sc, "ILLUSTRATION", "flux_illustration", "paid_api", True,
                         {"map": None, "chart": None, "timeline": None, "document": None,
                          "photo": None,
                          "illustration": {"prompt": "a mascot scene", "style_tags": []},
                          "reenactment": None}, f"n{i}", prompt="a mascot scene")
        for i, sc in enumerate(scenes[1:], start=2)
    ]
    doc = {
        "schema_version": vr.SCHEMA_VERSION, "project_id": proj.name,
        "routes_id": vr.new_routes_id(), "generated_at": "2026-01-01T00:00:00+00:00",
        "inputs": {"manifest_sha256": vr.file_sha256(proj / "manifest.json")},
        "channel": ctx.plan_binding(), "routes": routes,
        "renderer_registry_sha256": "0" * 64, "routes_content_sha256": "0" * 64,
    }
    rids = vr.referenced_renderer_ids(routes)
    doc["renderer_registry_sha256"] = vr.compute_renderer_registry_sha256(rids, renderers.RENDERERS)
    doc["routes_content_sha256"] = vr.compute_routes_content_sha256(doc)
    errs = vr.schema_errors(doc)
    if errs:
        raise AssertionError(f"canonical fixture is not schema-valid: {errs}")
    vr.write_atomic(doc, proj)
    return doc


def patched(root: Path):
    spec = root / "character" / "character_spec.json"
    return (mock.patch.multiple(cc, PIPELINE_DIR=root, CHANNELS_DIR=root / "channels"),
            mock.patch.multiple(gate, PIPELINE_DIR=root, SPEC_PATH=spec),
            mock.patch.multiple(pose_registry, PIPELINE_DIR=root, SPEC_PATH=spec),
            mock.patch.object(ac, "PIPELINE_DIR", root))


class World:
    def __init__(self, root):
        self.ctxs = patched(root)

    def __enter__(self):
        for c in self.ctxs:
            c.__enter__()
        return self

    def __exit__(self, *a):
        for c in reversed(self.ctxs):
            c.__exit__(*a)
        return False


def build_baseline():
    """(root, proj, ctx, doc) — a genuinely v3-writer-approvable world:
    valid identity, a valid extended channel, and an executable canonical
    routes document with no approval yet."""
    root, proj = build_fixture()
    install_canonical_channel(root)
    with World(root):
        ctx = cc.load_channel_for_project(proj)
        doc = install_canonical_routes(proj, ctx)
    return root, proj, ctx, doc


def expected_confirmation(proj, doc):
    return gate.canonical_confirmation_phrase(proj.name, doc["routes_id"])


REPO_BEFORE = census(ROOT)

try:
    # ── 1. the happy path ───────────────────────────────────────────────────
    print("\n1. write_approval_v3 produces a record the real gate accepts")
    root, proj, ctx, doc = build_baseline()
    with World(root):
        out = ac.write_approval_v3(proj, "Giri", expected_confirmation(proj, doc))
    check("the approval file was written", out.exists())
    rec = json.loads(out.read_text(encoding="utf-8"))
    check("schema_version is exactly int 3",
          type(rec.get("schema_version")) is int and rec["schema_version"] == 3)
    check("routes_id is bound", rec.get("routes_id") == doc["routes_id"])
    check("routes_file_sha256 is bound to the exact bytes on disk",
          rec.get("routes_file_sha256") == vr.file_sha256(proj / vr.ROUTES_NAME))
    check("routes_md_sha256 is bound to the exact bytes on disk",
          rec.get("routes_md_sha256") == vr.file_sha256(proj / vr.ROUTES_MD_NAME))
    check("manifest_sha256 is bound to the exact bytes on disk",
          rec.get("manifest_sha256") == vr.file_sha256(proj / "manifest.json"))
    check("routes_content_sha256 is freshly recomputed, matching the document",
          rec.get("routes_content_sha256") == vr.compute_routes_content_sha256(doc))
    check("renderer_registry_sha256 is freshly recomputed",
          rec.get("renderer_registry_sha256") == doc["renderer_registry_sha256"])
    check("channel binding is recorded", rec.get("channel") == ctx.plan_binding())
    check("failure_revision is recorded", rec.get("failure_revision") == 0)
    check("approver identity is recorded", rec.get("approved_by") == "Giri")
    check("approval timestamp is timezone-aware",
          rec.get("approved_at", "").endswith("+00:00"))
    check("paid_generation is a strict, freshly-derived summary",
          rec.get("paid_generation") == gate.canonical_paid_generation_summary(doc))

    with World(root):
        snapshot = gate.require_canonical_visual_execution_ready(proj)
    check("the real, now-activated gate accepts this exact approval",
          isinstance(snapshot, vr.DispatchSnapshot))
    check("the accepted snapshot names the same routes_id",
          snapshot.routes_id == doc["routes_id"])

    # ── 2. refuses with no approver ─────────────────────────────────────────
    print("\n2. write_approval_v3 refuses with no approver")
    root, proj, ctx, doc = build_baseline()
    try:
        with World(root):
            ac.write_approval_v3(proj, "", expected_confirmation(proj, doc))
        check("refused", False, "it approved")
    except ac.ApprovalRefused as e:
        check("refused", "approver" in str(e).lower())
    check("nothing was written", not (proj / gate.APPROVAL_NAME).exists())

    # ── 3. refuses on the wrong confirmation phrase ─────────────────────────
    print("\n3. write_approval_v3 refuses on the wrong confirmation phrase")
    root, proj, ctx, doc = build_baseline()
    try:
        with World(root):
            ac.write_approval_v3(proj, "Giri", "I approve whatever")
        check("refused", False, "it approved")
    except ac.ApprovalRefused as e:
        check("refused", "confirmation phrase" in str(e).lower())
    check("nothing was written", not (proj / gate.APPROVAL_NAME).exists())

    # ── 4. refuses when canonical routes are not executable ────────────────
    print("\n4. write_approval_v3 refuses when there is no visual_routes.json at all")
    root, proj = build_fixture()
    install_canonical_channel(root)
    try:
        with World(root):
            ac.write_approval_v3(proj, "Giri", "anything")
        check("refused", False, "it approved")
    except ac.ApprovalRefused as e:
        check("refused", "not executable" in str(e).lower())
    check("nothing was written", not (proj / gate.APPROVAL_NAME).exists())

    # ── 5. refuses on an unresolved route failure ───────────────────────────
    print("\n5. write_approval_v3 refuses on an unresolved route failure")
    root, proj, ctx, doc = build_baseline()
    with World(root):
        route_failures.record_failure(proj, doc["routes"][0], reason="synthetic failure")
    try:
        with World(root):
            ac.write_approval_v3(proj, "Giri", expected_confirmation(proj, doc))
        check("refused", False, "it approved")
    except ac.ApprovalRefused as e:
        check("refused", "route failure" in str(e).lower())
    check("nothing was written", not (proj / gate.APPROVAL_NAME).exists())

    # ── 6. refuses on narration tampering ───────────────────────────────────
    # Tampered BEFORE the routes document is built, so the routes document's
    # own inputs.manifest_sha256 binds the already-tampered manifest bytes —
    # isolating this case to purely a narration-binding failure, not an
    # unrelated "manifest changed since routes were built" routes-integrity
    # failure.
    print("\n6. write_approval_v3 refuses narration tampering")
    root, proj = build_fixture()
    install_canonical_channel(root)
    m = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
    m["narration_effective_profile"] = {**m.get("narration_effective_profile", {}),
                                        "provider": "tampered"}
    (proj / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    with World(root):
        ctx = cc.load_channel_for_project(proj)
        doc = install_canonical_routes(proj, ctx)
        problems = gate.narration_binding_problems(proj, m, ctx)
    check("the tampered manifest genuinely fails narration_binding_problems() "
         "(fixture sanity check)", bool(problems), "no problems reported")
    try:
        with World(root):
            ac.write_approval_v3(proj, "Giri", expected_confirmation(proj, doc))
        check("refused", False, "it approved")
    except ac.ApprovalRefused as e:
        check("refused", "narration" in str(e).lower(), str(e))
    check("nothing was written", not (proj / gate.APPROVAL_NAME).exists())

    # ── 7. atomic write: a mid-write failure preserves the prior approval ──
    print("\n7. write is atomic — a mid-write failure preserves the previous approval byte-for-byte")
    root, proj, ctx, doc = build_baseline()
    with World(root):
        ac.write_approval_v3(proj, "Giri", expected_confirmation(proj, doc))
    prior_bytes = (proj / gate.APPROVAL_NAME).read_bytes()

    root2, proj2, ctx2, doc2 = build_baseline()
    with World(root2):
        ac.write_approval_v3(proj2, "Giri", expected_confirmation(proj2, doc2))
    prior_bytes2 = (proj2 / gate.APPROVAL_NAME).read_bytes()
    with mock.patch("json.dump", side_effect=RuntimeError("simulated serialization failure")):
        try:
            with World(root2):
                ac.write_approval_v3(proj2, "Giri", expected_confirmation(proj2, doc2))
            check("a mid-write failure raises", False, "it did not raise")
        except RuntimeError:
            check("a mid-write failure raises", True)
    check("the previous approval survives byte-for-byte",
          (proj2 / gate.APPROVAL_NAME).read_bytes() == prior_bytes2)
    check("no stray temp file was left behind",
          not [p for p in proj2.glob("*.tmp")])

    # ── 8. cleanup-failure surfaced truthfully ──────────────────────────────
    print("\n8. a temp-file cleanup failure after a write failure is surfaced, not swallowed")
    root3, proj3, ctx3, doc3 = build_baseline()
    with mock.patch("json.dump", side_effect=RuntimeError("simulated serialization failure")), \
         mock.patch("os.unlink", side_effect=OSError("simulated cleanup failure")):
        try:
            with World(root3):
                ac.write_approval_v3(proj3, "Giri", expected_confirmation(proj3, doc3))
            check("the cleanup failure is surfaced", False, "did not raise")
        except OSError as e:
            check("the cleanup failure is surfaced", "cleanup" in str(e).lower(), str(e))
        except RuntimeError:
            check("the cleanup failure is surfaced", False,
                 "the original error masked the cleanup failure")
    check("nothing was approved", not (proj3 / gate.APPROVAL_NAME).exists())

    # ── 9. plan_visuals.py is a refusal shim ────────────────────────────────
    print("\n9. plan_visuals.py's CLI refuses; the library functions remain read-only")
    code = plan_visuals.main()
    check("plan_visuals.main() refuses (nonzero exit)", code != 0, f"exit={code}")
    root4, proj4 = build_fixture()
    before = {p.as_posix(): sha(p) for p in proj4.rglob("*") if p.is_file()}
    with World(root4):
        p = plan_visuals.build_plan(proj4)
        plan_visuals.render_md(p)
    after = {p.as_posix(): sha(p) for p in proj4.rglob("*") if p.is_file()}
    check("build_plan()/render_md() remain read-only — nothing was written",
          before == after, str(set(after) ^ set(before)))

finally:
    for td in _fixtures:
        shutil.rmtree(td, ignore_errors=True)
    REPO_AFTER = census(ROOT)
    moved = [k for k in REPO_BEFORE if REPO_AFTER.get(k) != REPO_BEFORE[k]]
    added = [k for k in REPO_AFTER if k not in REPO_BEFORE]
    check("no repository character asset was modified", not moved, str(moved))
    check("no repository character asset was added", not added, str(added))

print("\n" + "=" * 58)
print(f"FAILED ({len(failures)}): {failures}" if failures else "ALL PASS")
sys.exit(1 if failures else 0)
