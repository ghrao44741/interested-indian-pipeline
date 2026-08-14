"""Generation-readiness gate — deterministic, non-destructive, zero paid calls.

Every check that can fail is exercised against a synthetic fixture character and
project built in a temp directory, with PIPELINE_DIR and SPEC_PATH patched in
both generation_gate and pose_registry. The two real projects are used read-only
for the two facts that must hold about production: the pilot blocks on SCENE-066
and test_2min passes.

A whole-tree hash census of the repository's character assets runs in try/finally
so an interrupted run still reports anything that moved.

Run:  python tests/test_generation_gate.py
"""

import ast
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import composite_character  # noqa: E402
import generation_gate as gate  # noqa: E402
import pose_registry  # noqa: E402
import render_channel_dna as rd  # noqa: E402
import renderers  # noqa: E402
import route_failures  # noqa: E402
import route_images  # noqa: E402
import visual_routes as vr  # noqa: E402
import channel_context as cc  # noqa: E402
import channel_fixture  # noqa: E402

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
    # Every subtree, not a chosen few: a narrower census missed previews being
    # written into the real character/ directory by a test.
    for sub in ("character",):
        d = root / sub
        if d.exists():
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    out[f.relative_to(root).as_posix()] = sha(f)
    spec = root / "character" / "character_spec.json"
    if spec.exists():
        out["character/character_spec.json"] = sha(spec)
    return out


# ── synthetic fixture ────────────────────────────────────────────────────────

SCRIPT = ("The minister resigned in July. Nobody expected it.\n\n"
          "The exam was cancelled the same week. Then it was rescheduled.\n")


def _png(path: Path, alpha=True, blank=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA" if alpha else "RGB", (256, 256),
                   (250, 247, 242, 0) if alpha else (250, 247, 242))
    if not blank:
        im.paste(Image.new("RGBA", (80, 160), (60, 60, 60, 255)), (88, 60))
    im.save(path)


def build_fixture() -> tuple[Path, Path]:
    """(pipeline_root, project_dir) — a complete, passing world."""
    import source_ids

    td = Path(tempfile.mkdtemp())
    _fixtures.append(td)

    for m in ("body-master.png", "face-master.png"):
        _png(td / "character" / "canonical" / m, alpha=False)
    poses = {
        "neutral_presenter": ("host_neutral_presenter.png", "approved", "both_sides", []),
        "pointing_viewer_left": ("host_pointing_viewer_left.png", "approved", "viewer_left", []),
        "seated_reading_document": ("host_seated_reading_document.png",
                                    "approved_scene_bound", "none", ["desk", "chair"]),
        "half_finished": ("host_half_finished.png", "pending-approval", "both_sides", []),
    }
    registry = {}
    for pid, (fname, status, ns, geom) in poses.items():
        f = td / "character" / "poses" / fname
        _png(f)
        registry[pid] = {
            "id": pid, "path": f"character/poses/{fname}", "sha256": sha(f),
            "dimensions": "256x256", "status": status, "direction": "front",
            "negative_space": ns, "includes_geometry": geom,
            "generic_compositing_allowed": status == "approved" and not geom,
        }
    # An archived superseded identity, deliberately reachable on disk but not
    # registered — the shape of the asset that must never render.
    _png(td / "character" / "archive" / "old_identity" / "host_neutral_presenter.png")

    spec = {
        "version": 4,
        "identity": {"prompt_block": "x"}, "immutable": ["a"], "variable": ["b"],
        "negative_prompt": "none", "negative_prompt_by_framing": {"full_body": ""},
        "style": {"rules": ["flat"]},
        "references": {"body_master": "character/canonical/body-master.png",
                       "face_master": "character/canonical/face-master.png"},
        "master_authority": {"body_master": ["clothing"], "face_master": ["face"]},
        "masters": {
            "body_master": {"path": "character/canonical/body-master.png",
                            "sha256": sha(td / "character/canonical/body-master.png"),
                            "status": "approved"},
            "face_master": {"path": "character/canonical/face-master.png",
                            "sha256": sha(td / "character/canonical/face-master.png"),
                            "status": "approved"},
        },
        "pose_library": {"version": 2, "status": "approved", "approved_batches": [1],
                         "registry": registry},
    }
    (td / "character" / "character_spec.json").write_text(
        json.dumps(spec, indent=2), encoding="utf-8")

    proj = td / "demo_project"
    proj.mkdir()
    (proj / "script_demo.txt").write_text(SCRIPT, encoding="utf-8")
    units = source_ids.build_source_units(SCRIPT)
    source_ids.save_units(proj, units)

    scenes = []
    for i, u in enumerate(units, 1):
        scenes.append({
            "id": f"SCENE-{i:03d}", "image": f"SCENE-{i:03d}.png",
            "script": u["text"], "source_ids": [u["id"]],
            "shot_instance_id": f"{u['id']}-S01",
            "source_match": "ok", "visual_match": "ok", "visual_state": "planned",
            "visual_asset_id": f"VIS-{i:03d}-A",
        })
    manifest = channel_fixture.stamp(
        {"episode": "demo", "identity_state": "ok", "identity_reasons": [],
         "scenes": scenes}, project_dir=proj)
    channel_fixture.install(td)
    (proj / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    plan = {"project": "demo_project", "needs_review": [],
            "manifest_identity": {"scenes": len(scenes),
                                  "shot_instance_ids": sorted(s["shot_instance_id"]
                                                              for s in scenes)}}
    (proj / "visual_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return td, proj


def patched(root: Path):
    """Point both the gate and the registry at the fixture world."""
    spec = root / "character" / "character_spec.json"
    # PREVIEW_DIR is computed at import time, so patching PIPELINE_DIR alone
    # leaves previews landing in the real repository — which is what happened
    # here until the census was widened to cover the whole character/ tree.
    return mock.patch.multiple(cc, PIPELINE_DIR=root,
                               CHANNELS_DIR=root / "channels"), \
        mock.patch.multiple(gate, PIPELINE_DIR=root, SPEC_PATH=spec), \
        mock.patch.multiple(pose_registry, PIPELINE_DIR=root, SPEC_PATH=spec), \
        mock.patch.multiple(composite_character, PIPELINE_DIR=root,
                            PREVIEW_DIR=root / "character" / "previews")


def run_gate(root, project, **kw):
    """The identity gate. Approval behaviour is tests/test_approval_gate.py."""
    a, b, c, d = patched(root)
    with a, b, c, d:
        return gate.require_identity_ready(project, "test", raise_on_block=False, **kw)


def edit_spec(root: Path, fn):
    p = root / "character" / "character_spec.json"
    s = json.loads(p.read_text(encoding="utf-8"))
    fn(s)
    p.write_text(json.dumps(s, indent=2), encoding="utf-8")


def edit_manifest(proj: Path, fn):
    p = proj / "manifest.json"
    m = json.loads(p.read_text(encoding="utf-8"))
    fn(m)
    p.write_text(json.dumps(m, indent=2), encoding="utf-8")


def blocked_on(rep, fragment):
    return any(fragment in b for b in rep.blockers)


# ── canonical (v3) fixture — Task 2B-B2b-1 ──────────────────────────────────
#
# A second, additive fixture layer on top of build_fixture(): the same
# identity-clean project, a channel pack extended with an ILLUSTRATION
# capability, a schema-valid two-route visual_routes.json/.md pair (one free
# PHOTO route, one paid ILLUSTRATION route — so canonical_paid_generation_
# summary() has something real to report), and a matching v3 approval. None
# of this is wired into any live gate; it exists solely to exercise
# _check_approval_v3() and _canonical_execution_problems() directly.

def install_canonical_channel(root: Path, channel_id: str = channel_fixture.CHANNEL_ID) -> None:
    """Extends the fixture channel pack with an ILLUSTRATION capability, so a
    canonical routes fixture can carry one paid route alongside a free one.
    Must run after channel_fixture.install() (which build_fixture() already
    calls) so this overwrite is the final word on the pack's content."""
    doc = channel_fixture.pack_document(channel_id)
    doc["renderers"]["capabilities"]["ILLUSTRATION"] = "flux_illustration"
    d = root / "channels" / channel_id
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
    """Writes a schema-valid, fully executable visual_routes.json/.md pair
    covering every scene build_fixture()'s manifest carries: the first scene
    as a free PHOTO route, every remaining scene as a paid ILLUSTRATION route.
    Returns the written document. Uses visual_routes.write_atomic(), so a
    stale routes_content_sha256 or a schema violation raises here — a broken
    fixture fails loudly at build time, not as a mysterious later blocker."""
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


def build_v3_approval(proj: Path, doc: dict, ctx, *, approved_by="Giri",
                      approved_at="2026-01-01T00:00:00+00:00", tamper=None) -> dict:
    """A self-consistent v3 approval for `doc`'s routes document, written to
    disk. `tamper(rec)` mutates the in-memory record before it is written —
    used by the binding-completeness regressions below."""
    rec = {
        "schema_version": gate.APPROVAL_V3_SCHEMA_VERSION,
        "project": proj.name,
        "routes_id": doc["routes_id"],
        "routes_file_sha256": vr.file_sha256(proj / vr.ROUTES_NAME),
        "routes_md_sha256": vr.file_sha256(proj / vr.ROUTES_MD_NAME),
        "routes_content_sha256": vr.compute_routes_content_sha256(doc),
        "renderer_registry_sha256": doc["renderer_registry_sha256"],
        "manifest_sha256": vr.file_sha256(proj / "manifest.json"),
        "channel": ctx.plan_binding(),
        "failure_revision": route_failures.revision(proj),
        "approved_at": approved_at, "approved_by": approved_by,
        "confirmation": gate.canonical_confirmation_phrase(proj.name, doc["routes_id"]),
        "paid_generation": gate.canonical_paid_generation_summary(doc),
    }
    if tamper is not None:
        tamper(rec)
    (proj / gate.APPROVAL_NAME).write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def build_canonical_baseline(*, approval_tamper=None):
    """(root, proj, ctx, doc) — build_fixture() plus a complete, passing
    canonical world: an extended channel, an executable routes document and a
    matching v3 approval."""
    root, proj = build_fixture()
    install_canonical_channel(root)
    a, b, c, d = patched(root)
    with a, b, c, d:
        ctx = cc.load_channel_for_project(proj)
        doc = install_canonical_routes(proj, ctx)
        build_v3_approval(proj, doc, ctx, tamper=approval_tamper)
    return root, proj, ctx, doc


def run_canonical(root, proj, **kw):
    """The unwired canonical validator, called directly — never through
    require_canonical_visual_execution_ready(), which still always raises."""
    a, b, c, d = patched(root)
    with a, b, c, d:
        return gate._canonical_execution_problems(proj, "test", **kw)


def rewrite_approval(proj: Path, fn) -> None:
    p = proj / gate.APPROVAL_NAME
    rec = json.loads(p.read_text(encoding="utf-8"))
    fn(rec)
    p.write_text(json.dumps(rec, indent=2), encoding="utf-8")


REPO_BEFORE = census(ROOT)

try:
    # ── 1. the real projects ─────────────────────────────────────────────────
    print("\n1. the real pilot is blocked, and blocked on SCENE-066")
    rep = gate.require_identity_ready(ROOT / "pilot_neet_scandal", "pilot images",
                                      raise_on_block=False)
    check("pilot is blocked", bool(rep.blockers), "gate passed the pilot")
    check("blocked on SCENE-066", blocked_on(rep, "SCENE-066"), str(rep.blockers))
    check("raises GateBlocked by default", True)
    try:
        gate.require_identity_ready(ROOT / "pilot_neet_scandal", "pilot images")
        check("raises GateBlocked by default", False, "returned instead of raising")
    except gate.GateBlocked as e:
        check("GateBlocked names the blocking condition", "SCENE-066" in str(e), str(e))

    print("\n2. test_2min passes the identity gate")
    rep = gate.require_identity_ready(ROOT / "test_2min", "images",
                                      raise_on_block=False)
    check("test_2min identity is ready", not rep.blockers, str(rep.blockers))
    check("the identity gate does not ask about approval",
          not any("approval" in n.lower() for n, _, _ in rep.checks))

    # ── 3. fixture: the healthy baseline ─────────────────────────────────────
    print("\n3. the synthetic fixture is healthy to begin with")
    root, proj = build_fixture()
    rep = run_gate(root, proj)
    check("fixture passes", not rep.blockers, str(rep.blockers))

    print("\n4. missing manifest fails")
    root, proj = build_fixture()
    (proj / "manifest.json").unlink()
    rep = run_gate(root, proj)
    check("blocked", blocked_on(rep, "manifest exists"), str(rep.blockers))

    print("\n5. blocked and ambiguous identity fail")
    root, proj = build_fixture()
    edit_manifest(proj, lambda m: m.update(identity_state="blocked",
                                           identity_reasons=["SCENE-002 unmatched"]))
    check("identity_state=blocked is refused",
          blocked_on(run_gate(root, proj), "manifest identity is ok"))
    root, proj = build_fixture()
    edit_manifest(proj, lambda m: m["scenes"][0].update(visual_match="NEEDS_REVIEW"))
    check("an unresolved NEEDS_REVIEW scene is refused",
          blocked_on(run_gate(root, proj), "no scene awaits identity review"))

    print("\n6. duplicate source and visual ids fail")
    root, proj = build_fixture()
    edit_manifest(proj, lambda m: m["scenes"][1].update(
        shot_instance_id=m["scenes"][0]["shot_instance_id"]))
    check("duplicate shot instance ids refused",
          blocked_on(run_gate(root, proj), "shot instance ids are unique"))
    root, proj = build_fixture()
    edit_manifest(proj, lambda m: m["scenes"][1].update(
        visual_asset_id=m["scenes"][0]["visual_asset_id"]))
    check("duplicate visual asset ids refused",
          blocked_on(run_gate(root, proj), "visual asset ids are unique"))
    root, proj = build_fixture()
    sc = json.loads((proj / "source_units.json").read_text(encoding="utf-8"))
    sc["units"][1]["id"] = sc["units"][0]["id"]
    (proj / "source_units.json").write_text(json.dumps(sc), encoding="utf-8")
    check("duplicate source unit ids refused",
          blocked_on(run_gate(root, proj), "source unit ids are unique"))

    print("\n7. a changed script makes the recorded fingerprints stale")
    root, proj = build_fixture()
    (proj / "script_demo.txt").write_text(
        SCRIPT.replace("resigned in July", "resigned in August"), encoding="utf-8")
    check("stale fingerprints refused",
          blocked_on(run_gate(root, proj), "script fingerprints are current"))

    print("\n8. a tampered master fails")
    root, proj = build_fixture()
    _png(root / "character" / "canonical" / "face-master.png", alpha=False, blank=True)
    rep = run_gate(root, proj)
    check("hash mismatch refused",
          blocked_on(rep, "master face_master matches approved hash"), str(rep.blockers))
    root, proj = build_fixture()
    (root / "character" / "canonical" / "body-master.png").unlink()
    check("missing master refused",
          blocked_on(run_gate(root, proj), "master body_master present"))

    print("\n9. a tampered pose fails")
    root, proj = build_fixture()
    _png(root / "character" / "poses" / "host_neutral_presenter.png", blank=True)
    rep = run_gate(root, proj)
    check("registry audit catches it", blocked_on(rep, "pose registry audits clean"),
          str(rep.blockers))
    rep = run_gate(root, proj, pose_id="neutral_presenter")
    check("selecting it is refused", blocked_on(rep, "'neutral_presenter' resolves"),
          str(rep.blockers))

    print("\n10. a pending pose can never be selected")
    root, proj = build_fixture()
    rep = run_gate(root, proj, pose_id="half_finished")
    check("pending status refused", blocked_on(rep, "half_finished"), str(rep.blockers))
    a, b, c, d = patched(root)
    with a, b, c, d:
        check("pending pose is not even listed",
              "half_finished" not in pose_registry.list_poses())

    print("\n11. raw, candidate and archived assets are unreachable")
    root, proj = build_fixture()
    edit_spec(root, lambda s: s["pose_library"]["registry"]["neutral_presenter"].update(
        path="character/archive/old_identity/host_neutral_presenter.png"))
    rep = run_gate(root, proj, pose_id="neutral_presenter")
    check("an archived path escapes containment and is refused",
          blocked_on(rep, "'neutral_presenter' resolves"), str(rep.blockers))
    root, proj = build_fixture()
    cand = root / "character" / "pose_candidates" / "neutral_presenter" / "v01" / "asset.png"
    _png(cand)
    edit_spec(root, lambda s: s["pose_library"]["registry"]["neutral_presenter"].update(
        path="character/pose_candidates/neutral_presenter/v01/asset.png",
        sha256=sha(cand)))
    check("an unreviewed candidate is refused even with a matching hash",
          blocked_on(run_gate(root, proj, pose_id="neutral_presenter"),
                     "'neutral_presenter' resolves"))
    root, proj = build_fixture()
    edit_spec(root, lambda s: s["pose_library"]["registry"]["neutral_presenter"].update(
        path="../outside.png"))
    check("an upward traversal is refused",
          blocked_on(run_gate(root, proj, pose_id="neutral_presenter"),
                     "'neutral_presenter' resolves"))

    print("\n12. scene-bound poses need explicit permission")
    root, proj = build_fixture()
    rep = run_gate(root, proj, pose_id="seated_reading_document")
    check("refused without permission", blocked_on(rep, "seated_reading_document"),
          str(rep.blockers))
    rep = run_gate(root, proj, pose_id="seated_reading_document", scene_bound=True)
    check("allowed with permission", not rep.blockers, str(rep.blockers))
    rep = run_gate(root, proj, pose_id="neutral_presenter", scene_bound=True)
    check("permission does not weaken anything else", not rep.blockers, str(rep.blockers))

    # ── 13. router records ids, compositor resolves them ─────────────────────
    print("\n13. the router records pose ids and never a path")
    root, proj = build_fixture()
    prompts = proj / route_images.PROMPTS_FILE
    prompts.write_text(
        '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` TYPE: HOST '
        'HOST_POSE: pointing_viewer_left NARRATION: "one" PROMPT: bg\n'
        '**SHOT 02** · SCENE-002 · standalone → `SCENE-002.png` TYPE: HOST '
        'HOST_POSE: seated_reading_document SCENE_BOUND: true NARRATION: "two" PROMPT: bg\n'
        '**SHOT 03** · SCENE-003 · standalone → `SCENE-003.png` TYPE: CARTOON '
        'NARRATION: "three" PROMPT: bg\n', encoding="utf-8")
    shots = route_images.parse_shots(prompts)
    host = [s for s in shots if s["type"] == "HOST"]
    check("HOST shots are recognised", len(host) == 2, str([s["type"] for s in shots]))
    check("pose ids are recorded",
          [s["pose_id"] for s in host] == ["pointing_viewer_left",
                                           "seated_reading_document"])
    check("scene_bound is carried explicitly",
          [s["scene_bound"] for s in host] == [False, True])
    leaked = [f"{s['shot_num']}:{k}" for s in shots for k, v in s.items()
              if isinstance(v, str) and ("character/poses" in v or ".png" in v
                                         and "host_" in v)]
    check("no shot record contains a pose file path", not leaked, str(leaked))

    print("\n14. the compositor resolves exclusively through pose_registry")
    src = ast.parse((ROOT / "composite_character.py").read_text(encoding="utf-8"))
    calls = {ast.unparse(n.func) for n in ast.walk(src) if isinstance(n, ast.Call)}
    check("calls pose_registry.resolve()", "pose_registry.resolve" in calls)
    check("calls pose_registry.metadata()", "pose_registry.metadata" in calls)
    check("uses LANCZOS when scaling",
          "Image.LANCZOS" in (ROOT / "composite_character.py").read_text(encoding="utf-8"))

    root, proj = build_fixture()
    a, b, c, d = patched(root)
    seen = []
    with a, b, c, d:
        real = pose_registry.resolve

        def spy(pid, scene_bound=False, **kw):      # **kw: pose calls carry a context
            seen.append((pid, scene_bound))
            return real(pid, scene_bound=scene_bound)

        bg = proj / "images" / "SCENE-001.png"
        _png(bg, alpha=False)
        with mock.patch.object(pose_registry, "resolve", spy):
            rec = composite_character.render_preview("pointing_viewer_left", bg,
                                                     "out")
            # The gate resolves too — the audit checks every approved pose and
            # the pose-selection check resolves the chosen one. What matters is
            # that the render itself resolves the id it was given.
            check("resolve() was called for the requested pose",
                  seen[-1] == ("pointing_viewer_left", False), str(seen))
            check("the record keys on the id, not a path",
                  rec["pose_id"] == "pointing_viewer_left" and "path" not in rec)
            check("negative space drives placement — viewer_left pose sits right",
                  rec["side"] == "right", rec["side"])
            seen.clear()
            composite_character.render_preview("seated_reading_document", bg,
                                               "out2", scene_bound=True)
            check("scene_bound is passed through explicitly",
                  seen[-1] == ("seated_reading_document", True), str(seen))
        try:
            composite_character.render_preview("half_finished", bg, "no")
            check("a pending pose cannot be composited", False, "it rendered")
        except (pose_registry.PoseError, gate.GateBlocked):
            check("a pending pose cannot be composited", True)
        check("no output file was written for the refused pose",
              not (root / "character" / "previews" / "no.png").exists())

    # ── 15. registry completeness ────────────────────────────────────────────
    print("\n15. every registered paid entry point invokes the shared gate")

    def gate_called_in(module: str, func: str, kind: str) -> bool:
        tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
        want = f"require_{kind}_ready"
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
                for n in ast.walk(node):
                    if isinstance(n, ast.Call) and ast.unparse(n.func).endswith(want):
                        return True
        return False

    for e in gate.PAID_ENTRY_POINTS:
        if not e["implemented"]:
            check(f"{e['id']}: recorded as unimplemented and genuinely absent",
                  not (ROOT / e["module"]).exists(),
                  f"{e['module']} exists but the registry says it does not")
            continue
        for g in e["gates"]:
            check(f"{e['id']} ({e['module']}:{g['function']}) calls "
                  f"require_{g['kind']}_ready",
                  gate_called_in(e["module"], g["function"], g["kind"]))

    print("\n16. no unregistered paid image path exists")
    MARKERS = [("images.generate(", "image generation"),
               ("images.edit(", "image edit"),
               ("api.pexels.com", "pexels fetch")]
    registered = {e["module"] for e in gate.PAID_ENTRY_POINTS}
    known_other = {e["module"] for e in gate.OTHER_PAID_APIS}
    allowed = registered | known_other | {"generation_gate.py"}
    unregistered = []
    for py in sorted(ROOT.glob("*.py")):
        if py.name in allowed or py.name.startswith("test_") or py.name.startswith("debug_"):
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        for marker, why in MARKERS:
            if marker in text:
                unregistered.append(f"{py.name} — {why}")
    check("every module that can call a paid image API is registered",
          not unregistered, str(unregistered))

    # ── 17. blocked and dry-run paths spend nothing ──────────────────────────
    print("\n17. a blocked preflight makes zero paid calls and writes nothing")
    import generate_images_flux as flux

    calls = []
    with mock.patch.object(flux, "_OpenAI",
                           lambda **kw: calls.append("client") or types.SimpleNamespace()), \
         mock.patch.object(flux, "load_env_key",
                           lambda *a, **k: calls.append("key") or "sk-test"), \
         mock.patch.object(flux, "generate_image_grok",
                           lambda *a, **k: calls.append("generate")), \
         mock.patch.object(sys, "argv",
                           ["generate_images_flux.py", "--project",
                            str(ROOT / "pilot_neet_scandal")]):
        try:
            flux.main()
            code = 0
        except SystemExit as e:
            code = e.code
    check("generate_images_flux exits nonzero on the blocked pilot", code == 1, f"exit={code}")
    check("no API key was read, no client built, nothing generated", not calls, str(calls))

    before = {p.name for p in (ROOT / "pilot_neet_scandal" / "images").glob("*")}
    r = subprocess.run([sys.executable, str(ROOT / "route_images.py"),
                        "--project", "pilot_neet_scandal"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    after = {p.name for p in (ROOT / "pilot_neet_scandal" / "images").glob("*")}
    check("route_images exits nonzero on the blocked pilot", r.returncode == 1,
          f"exit={r.returncode}")
    check("route_images created no new image files", before == after,
          str(after - before))

    # Task 2B-B2b-2a: --dry-run now inspects/reports the canonical
    # visual_routes.json artifact, never the legacy prompts/plan.
    # test_2min has no visual_routes.json (canonical authoring is a separate,
    # later milestone — see the B2b-1 handoff), so an honest dry-run report
    # says the artifact is unavailable and exits nonzero — that is correct
    # reporting, not a false "clean" pass, and it still writes nothing.
    before = {p.name for p in (ROOT / "test_2min" / "images").glob("*")}
    r = subprocess.run([sys.executable, str(ROOT / "route_images.py"),
                        "--project", "test_2min", "--dry-run"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    after = {p.name for p in (ROOT / "test_2min" / "images").glob("*")}
    check("a dry run with no canonical routing artifact reports unexecutable "
         "and exits nonzero", r.returncode == 1, r.stdout[-400:] + r.stderr[-300:])
    check("the report names the missing artifact",
          "visual_routes.json" in r.stdout, r.stdout[-400:])
    check("a dry run creates no image files", before == after, str(after - before))

    print("\n18. direct and orchestrated execution give the same verdict")
    # pipeline_agents imports the anthropic SDK at module level for its review
    # agents. Those play no part in the gate, and stubbing the import keeps this
    # suite runnable on an interpreter without the SDK installed — which is the
    # interpreter most people will run it on.
    if "anthropic" not in sys.modules:
        stub = types.ModuleType("anthropic")
        stub.Anthropic = object          # used only as a type annotation
        sys.modules["anthropic"] = stub
    import pipeline_agents

    direct = gate.require_identity_ready(ROOT / "pilot_neet_scandal", "x",
                                         raise_on_block=False)
    stub = types.SimpleNamespace(project_dir=ROOT / "pilot_neet_scandal")
    try:
        pipeline_agents.OrchestratorAgent._stage_images(stub)
        orchestrated_blocked = False
        err = ""
    except RuntimeError as e:
        orchestrated_blocked, err = True, str(e)
    check("the orchestrator blocks where the direct script blocks",
          orchestrated_blocked and bool(direct.blockers))
    check("and blocks for the same reason", "SCENE-066" in err, err[:200])

    # On an identity-clean project the orchestrated prompts stage must get past
    # its gate and reach the work itself. _stage_images is not used here: it now
    # requires Checkpoint 3, which test_2min deliberately does not have.
    ran = []
    # _stage_prompts now owns the prompt review-and-edit checkpoint, moved there
    # from _stage_images so no edit can land after approval.
    stub2 = types.SimpleNamespace(project_dir=ROOT / "test_2min",
                                  _run_cmd=lambda cmd, label=None: ran.append(label),
                                  _checkpoint=lambda msg: "")
    ok_direct = not gate.require_identity_ready(ROOT / "test_2min", "x",
                                                raise_on_block=False).blockers
    try:
        pipeline_agents.OrchestratorAgent._stage_prompts(stub2)
        passed, why = True, ""
    except RuntimeError as e:
        passed, why = False, str(e)
    check("the orchestrator passes where the direct gate passes",
          passed and ok_direct, why[:200])
    check("and reached the stage body", bool(ran), "never got past the gate")

    # ...and the images stage, which does require approval, refuses on a project
    # that has none — the same verdict the direct script gives.
    stub3 = types.SimpleNamespace(project_dir=ROOT / "test_2min",
                                  _checkpoint=lambda msg: "q")
    try:
        pipeline_agents.OrchestratorAgent._stage_images(stub3)
        blocked_images = False
        detail = "it proceeded"
    except RuntimeError as e:
        blocked_images, detail = "Checkpoint 3 approval" in str(e), str(e)
    check("the orchestrated images stage refuses without approval",
          blocked_images, detail[:200])

    # ── 19. canonical (v3) foundation — Task 2B-B2b-1 ─────────────────────────
    print("\n19. the canonical baseline passes _canonical_execution_problems()")
    root, proj, ctx, doc = build_canonical_baseline()
    rep = run_canonical(root, proj)
    check("canonical baseline passes", not rep.blockers, str(rep.blockers))

    print("\n19a. v2 and v3 approval validators are isolated")
    V2_SHAPED = {
        "schema_version": 2, "project": "demo_project", "plan_id": "aaaaaaaa",
        "manifest_sha256": "a" * 64, "visual_plan_sha256": "a" * 64,
        "visual_plan_md_sha256": "a" * 64, "prompts_sha256": "a" * 64,
        "failure_revision": 0, "approved_at": "2026-01-01T00:00:00+00:00",
        "approved_by": "Giri", "confirmation": "x", "paid_generation": {"shots": 1},
        "channel": {"channel_id": "fixture_channel", "channel_dna_version": 1,
                   "channel_json_sha256": "a" * 64, "character_spec_sha256": "a" * 64,
                   "voice_profile_sha256": "a" * 64},
    }

    root, proj, ctx, doc = build_canonical_baseline()
    v3_rec = json.loads((proj / gate.APPROVAL_NAME).read_text(encoding="utf-8"))
    check("a valid v3 approval carries schema_version 3", v3_rec.get("schema_version") == 3)

    rep_v2_on_v3 = gate.GateReport(operation="x", project=proj.name, scope="test")
    gate._check_approval_v2(rep_v2_on_v3, proj, None, ctx)
    check("_check_approval_v2 rejects a v3-shaped approval",
          blocked_on(rep_v2_on_v3, "approval schema is supported"),
          str(rep_v2_on_v3.blockers))

    rep_v3_on_v3 = gate.GateReport(operation="x", project=proj.name, scope="test")
    a, b, c, d = patched(root)
    with a, b, c, d:
        routes_load = vr.inspect_project_routes(proj, operation="test")
        gate._check_approval_v3(rep_v3_on_v3, proj, routes_load, ctx)
    check("_check_approval_v3 accepts the valid v3 approval",
          not rep_v3_on_v3.blockers, str(rep_v3_on_v3.blockers))

    (proj / gate.APPROVAL_NAME).write_text(json.dumps(V2_SHAPED, indent=2), encoding="utf-8")
    rep_v2_on_v2 = gate.GateReport(operation="x", project=proj.name, scope="test")
    gate._check_approval_v2(rep_v2_on_v2, proj, None, ctx)
    check("_check_approval_v2 accepts the schema_version of a v2-shaped approval",
          "approval schema is supported" not in
          [n for n, ok, _ in rep_v2_on_v2.checks if not ok],
          str(rep_v2_on_v2.blockers))

    rep_v3_on_v2 = gate.GateReport(operation="x", project=proj.name, scope="test")
    a, b, c, d = patched(root)
    with a, b, c, d:
        routes_load2 = vr.inspect_project_routes(proj, operation="test")
        gate._check_approval_v3(rep_v3_on_v2, proj, routes_load2, ctx)
    check("_check_approval_v3 rejects a v2-shaped approval",
          blocked_on(rep_v3_on_v2, "v3 approval schema_version is exactly 3"),
          str(rep_v3_on_v2.blockers))
    check("...and refuses structurally, before reading a single v3-specific field",
          len(rep_v3_on_v2.blockers) == 1, str(rep_v3_on_v2.blockers))

    print("\n19b. static isolation: no cross-calls between the v2 and v3 paths")
    gg_src = ast.parse((ROOT / "generation_gate.py").read_text(encoding="utf-8"))

    def _calls_and_names(func_name):
        for node in ast.walk(gg_src):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == func_name):
                calls = {ast.unparse(n.func) for n in ast.walk(node)
                        if isinstance(n, ast.Call)}
                names = {ast.unparse(n) for n in ast.walk(node)
                        if isinstance(n, (ast.Name, ast.Attribute))}
                return calls, names
        raise AssertionError(f"{func_name} not found in generation_gate.py")

    rg_calls, _ = _calls_and_names("require_generation_ready")
    check("require_generation_ready does not call _check_approval_v3",
          not any(c.endswith("_check_approval_v3") for c in rg_calls), str(rg_calls))

    cep_calls, cep_names = _calls_and_names("_canonical_execution_problems")
    check("canonical validation does not call _check_approval_v2",
          not any(c.endswith("_check_approval_v2") for c in cep_calls), str(cep_calls))
    check("canonical validation does not call _check_visual_plan",
          not any(c.endswith("_check_visual_plan") for c in cep_calls), str(cep_calls))
    check("canonical validation never references VISUAL_PLAN_NAME",
          "VISUAL_PLAN_NAME" not in cep_names)

    # ── 20. v3 binding completeness ───────────────────────────────────────────
    print("\n20. each bound v3 field, tampered alone, blocks by name")

    def check_tamper(label, key, bad_value, expect_fragment):
        root, proj, ctx, doc = build_canonical_baseline()
        rewrite_approval(proj, lambda rec: rec.__setitem__(key, bad_value))
        rep = run_canonical(root, proj)
        check(label, blocked_on(rep, expect_fragment), str(rep.blockers))

    check_tamper("tampered project blocks", "project", "some_other_project",
                "v3 approval names this project")
    check_tamper("tampered routes_id blocks", "routes_id", "not-the-real-routes-id",
                "v3 approval names the current routes_id")
    check_tamper("tampered routes_file_sha256 blocks", "routes_file_sha256", "f" * 64,
                "visual_routes.json is unchanged since approval")
    check_tamper("tampered routes_md_sha256 blocks", "routes_md_sha256", "f" * 64,
                "visual_routes.md is unchanged since approval")
    check_tamper("tampered routes_content_sha256 blocks", "routes_content_sha256", "f" * 64,
                "routes_content_sha256 is freshly recomputed and matches")
    check_tamper("tampered renderer_registry_sha256 blocks", "renderer_registry_sha256",
                "f" * 64, "renderer_registry_sha256 is freshly recomputed and matches")
    check_tamper("tampered manifest_sha256 blocks", "manifest_sha256", "f" * 64,
                "manifest is unchanged since approval")
    check_tamper("tampered channel binding blocks", "channel", {"channel_id": "other"},
                "v3 approval channel binding matches the current channel")
    check_tamper("tampered failure_revision blocks", "failure_revision", 999,
                "v3 approval is current with the failure record")
    check_tamper("blank approved_by blocks", "approved_by", "   ",
                "v3 approval names a human approver")
    check_tamper("timezone-naive approved_at blocks", "approved_at", "2026-01-01T00:00:00",
                "v3 approval timestamp is timezone-aware")
    check_tamper("wrong confirmation phrase blocks", "confirmation",
                "I approve something else",
                "v3 approval confirmation names this project and routes_id")
    check_tamper("wrong paid-generation summary blocks", "paid_generation",
                {"shots": 999, "paid_route_ids": [], "paid_count": 0},
                "v3 approval paid-generation summary matches the approved routes")

    print("\n20a. routes JSON/Markdown bytes tampered on disk block, not just the record")
    root, proj, ctx, doc = build_canonical_baseline()
    (proj / vr.ROUTES_NAME).write_text(
        (proj / vr.ROUTES_NAME).read_text(encoding="utf-8") + " ", encoding="utf-8")
    rep = run_canonical(root, proj)
    check("a hand-edited visual_routes.json is refused",
          blocked_on(rep, "visual_routes.json is unchanged since approval"),
          str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    (proj / vr.ROUTES_MD_NAME).write_text(
        (proj / vr.ROUTES_MD_NAME).read_text(encoding="utf-8") + "\n<!-- tampered -->\n",
        encoding="utf-8")
    rep = run_canonical(root, proj)
    check("a hand-edited visual_routes.md is refused",
          blocked_on(rep, "visual_routes.md is unchanged since approval")
          or any("does not match a fresh render" in b for b in rep.blockers),
          str(rep.blockers))

    print("\n20b. a copied project tree with matching hashes still refuses on project binding")
    root, proj, ctx, doc = build_canonical_baseline()
    copy_root = Path(tempfile.mkdtemp())
    _fixtures.append(copy_root)
    shutil.copytree(root, copy_root, dirs_exist_ok=True)
    copied_proj = copy_root / "renamed_project"
    shutil.move(str(copy_root / proj.name), str(copied_proj))
    rep = run_canonical(copy_root, copied_proj)
    check("a project renamed after copying is refused on project binding",
          blocked_on(rep, "v3 approval names this project"), str(rep.blockers))

    # ── 21. narration enforcement ─────────────────────────────────────────────
    print("\n21. narration enforcement reaches the unwired canonical validator directly")

    def check_narration_break(label, root, proj):
        rep = run_canonical(root, proj)
        check(label, blocked_on(rep, "narration binding is verified"), str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    edit_manifest(proj, lambda m: m.update(narration_audio_sha256="0" * 64))
    check_narration_break("a changed narration audio hash is refused", root, proj)

    root, proj, ctx, doc = build_canonical_baseline()
    audio_path = proj / "source_audio" / "narration.mp3"
    audio_path.write_bytes(audio_path.read_bytes() + b"tampered")
    check_narration_break("tampered narration audio bytes are refused", root, proj)

    root, proj, ctx, doc = build_canonical_baseline()
    edit_manifest(proj, lambda m: m.update(narration_voice_profile_sha256="0" * 64))
    check_narration_break("a recorded voice-profile hash mismatch is refused", root, proj)

    root, proj, ctx, doc = build_canonical_baseline()
    edit_manifest(proj, lambda m: m.update(narration_effective_profile={
        **m["narration_effective_profile"], "provider": "elevenlabs"}))
    check_narration_break(
        "an effective profile that no longer matches the channel is refused", root, proj)

    root, proj, ctx, doc = build_canonical_baseline()
    edit_manifest(proj, lambda m: m.pop("narration_audio_file"))
    check_narration_break("a missing required narration field is refused", root, proj)

    root, proj, ctx, doc = build_canonical_baseline()
    ch_path = root / "channels" / channel_fixture.CHANNEL_ID / "channel.json"
    chdoc = json.loads(ch_path.read_text(encoding="utf-8"))
    chdoc["voice"]["approved_profile"]["settings"]["voice"] = "a-different-voice"
    ch_path.write_text(json.dumps(chdoc, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
    (root / "channels" / channel_fixture.CHANNEL_ID / cc.DNA_NAME).write_text(
        rd.render_dna(chdoc), encoding="utf-8")
    check_narration_break(
        "the channel's current approved voice differing from what was narrated is refused",
        root, proj)

    # ── 22. the universal refusal remains ─────────────────────────────────────
    print("\n22. the universal canonical refusal is untouched by any of the above")
    root, proj, ctx, doc = build_canonical_baseline()
    direct = run_canonical(root, proj)
    check("the direct canonical validator passes this fixture", not direct.blockers,
          str(direct.blockers))
    a, b, c, d = patched(root)
    with a, b, c, d:
        try:
            gate.require_canonical_visual_execution_ready(proj)
            refused, err = False, ""
        except gate.GateBlocked as e:
            refused, err = True, str(e)
    check("require_canonical_visual_execution_ready still refuses even when every "
          "direct canonical check passes", refused, err[:200])
    check("the refusal is the intentional temporary-disable message, not a real check "
          "failing", "remains disabled" in err)

    print("\n22a. all six canonical adapters remain universally blocked")
    canonical_entry = gate.entry_point("images.canonical_adapters")
    registered_fns = {g["function"] for g in canonical_entry["gates"]}
    check("exactly the six documented adapters are gated",
          registered_fns == {"adapt_map", "adapt_chart", "adapt_photo", "adapt_flux",
                             "adapt_host_composite", "adapt_flux_reference_anchor"},
          str(registered_fns))
    for g in canonical_entry["gates"]:
        check(f"{g['function']} still calls require_canonical_visual_execution_ready as "
             f"its declared gate", g["kind"] == "canonical_visual_execution")

    # ── 23. legacy (v2) regression ────────────────────────────────────────────
    print("\n23. require_generation_ready() ignores a v3 approval entirely")
    root, proj, ctx, doc = build_canonical_baseline()
    a, b, c, d = patched(root)
    with a, b, c, d:
        rep = gate.require_generation_ready(proj, "test", raise_on_block=False)
    check("a v3 approval does not satisfy require_generation_ready()",
          blocked_on(rep, "approval schema is supported"), str(rep.blockers))
    check("require_generation_ready still reports the generation scope",
          rep.scope == "generation")

    # ── 24. malformed/unreadable v3 approval inputs — Task 2B-B2b-1 micro-fix ─
    print("\n24. malformed/unreadable v3 approval inputs become named blockers")

    def v3_only(proj: Path, ctx):
        """Calls _check_approval_v3() with a placeholder routes_load (no real
        visual_routes.json ever read) — isolates the approval-file existence/
        read/parse checks from everything else the full canonical composition
        also checks, so a Path-method patch here cannot leak into unrelated
        checks."""
        placeholder = vr.ProjectRoutesLoad(
            project_dir=proj, operation="test", doc=None, context=ctx,
            manifest=None, manifest_sha256=None,
            routes_path=proj / vr.ROUTES_NAME, routes_md_path=proj / vr.ROUTES_MD_NAME)
        rep = gate.GateReport(operation="x", project=proj.name, scope="test")
        gate._check_approval_v3(rep, proj, placeholder, ctx)
        return rep

    root, proj, ctx, doc = build_canonical_baseline()
    approval_path = proj / gate.APPROVAL_NAME
    with mock.patch.object(type(approval_path), "exists",
                           side_effect=OSError("simulated stat failure")):
        rep = v3_only(proj, ctx)
    check("an existence-check failure on the approval path is a named blocker, "
         "not a crash", blocked_on(rep, "v3 approval exists"), str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    approval_path = proj / gate.APPROVAL_NAME
    with mock.patch.object(type(approval_path), "read_text",
                           side_effect=OSError("simulated permission error")):
        rep = v3_only(proj, ctx)
    check("a read failure on the approval file is a named blocker, not a crash",
          blocked_on(rep, "v3 approval record parses"), str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    (proj / gate.APPROVAL_NAME).write_bytes(b"\xff\xfe\x00b\x00a\x00d")
    rep = v3_only(proj, ctx)
    check("invalid UTF-8 in the approval file is a named blocker, not a crash",
          blocked_on(rep, "v3 approval record parses"), str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    (proj / gate.APPROVAL_NAME).write_text("not valid json {", encoding="utf-8")
    rep = v3_only(proj, ctx)
    check("malformed JSON in the approval file is a named blocker, not a crash",
          blocked_on(rep, "v3 approval record parses"), str(rep.blockers))

    for bad_shape, label in ((json.dumps([1, 2, 3]), "a JSON array"),
                             (json.dumps("hello"), "a JSON string"),
                             (json.dumps(None), "a JSON null")):
        root, proj, ctx, doc = build_canonical_baseline()
        (proj / gate.APPROVAL_NAME).write_text(bad_shape, encoding="utf-8")
        rep = v3_only(proj, ctx)
        check(f"{label} instead of a JSON object is a named blocker, not a crash",
              blocked_on(rep, "v3 approval record parses"), str(rep.blockers))

    # ── 25. immediate schema isolation ────────────────────────────────────────
    print("\n25. schema-mismatch refusal is immediate — v2 touches nothing v2-specific")
    root, proj, ctx, doc = build_canonical_baseline()
    rep_v2_on_v3 = gate.GateReport(operation="x", project=proj.name, scope="test")
    gate._check_approval_v2(rep_v2_on_v3, proj, None, ctx)
    check("_check_approval_v2 against a v3 record produces exactly one blocker",
          len(rep_v2_on_v3.blockers) == 1, str(rep_v2_on_v3.blockers))
    check("...and it is the unsupported-schema blocker",
          bool(rep_v2_on_v3.blockers) and "approval schema is supported"
          in rep_v2_on_v3.blockers[0], str(rep_v2_on_v3.blockers))
    check("_check_approval_v2 adds no check beyond existence/parse/schema",
          {n for n, ok, _ in rep_v2_on_v3.checks} == {
              "Checkpoint 3 approval exists", "approval record parses",
              "approval schema is supported"}, str(rep_v2_on_v3.checks))

    print("\n25a. valid v2 behavior is unchanged by the early-return fix")
    root, proj = build_fixture()
    rep_missing = gate.GateReport(operation="x", project=proj.name, scope="test")
    gate._check_approval_v2(rep_missing, proj, None, None)
    check("a project with no approval file at all still blocks on existence, as before",
          blocked_on(rep_missing, "Checkpoint 3 approval exists"), str(rep_missing.blockers))
    root, proj = build_fixture()
    (proj / gate.APPROVAL_NAME).write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    rep_nonobj = gate.GateReport(operation="x", project=proj.name, scope="test")
    gate._check_approval_v2(rep_nonobj, proj, None, None)
    check("a non-object JSON value is a controlled blocker for v2 too, not a crash",
          blocked_on(rep_nonobj, "approval record parses"), str(rep_nonobj.blockers))

    # ── 26. artifact/recomputation failures during v3 checking ───────────────
    print("\n26. recomputation/artifact failures are named blockers, not crashes")

    def v3_full_check(root: Path, proj: Path, ctx):
        a, b, c, d = patched(root)
        with a, b, c, d:
            routes_load = vr.inspect_project_routes(proj, operation="test")
        rep = gate.GateReport(operation="x", project=proj.name, scope="test")
        gate._check_approval_v3(rep, proj, routes_load, ctx)
        return rep

    root, proj, ctx, doc = build_canonical_baseline()
    with mock.patch.object(vr, "file_sha256",
                           side_effect=OSError("simulated read failure")):
        rep = v3_full_check(root, proj, ctx)
    check("a routes-JSON hashing failure is a named blocker",
          blocked_on(rep, "visual_routes.json still present"), str(rep.blockers))
    check("a routes-Markdown hashing failure is a named blocker",
          blocked_on(rep, "visual_routes.md still present"), str(rep.blockers))
    check("a manifest hashing failure is a named blocker",
          blocked_on(rep, "manifest still present"), str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    with mock.patch.object(vr, "compute_routes_content_sha256",
                           side_effect=RuntimeError("simulated")):
        rep = v3_full_check(root, proj, ctx)
    check("a routes_content_sha256 recomputation failure is a named blocker",
          blocked_on(rep, "routes_content_sha256 is freshly recomputed and matches"),
          str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    with mock.patch.object(vr, "referenced_renderer_ids",
                           side_effect=RuntimeError("simulated")):
        rep = v3_full_check(root, proj, ctx)
    check("a referenced-renderer-id derivation failure is a named blocker",
          blocked_on(rep, "renderer_registry_sha256 is freshly recomputed and matches"),
          str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    with mock.patch.object(vr, "compute_renderer_registry_sha256",
                           side_effect=RuntimeError("simulated")):
        rep = v3_full_check(root, proj, ctx)
    check("a renderer_registry_sha256 recomputation failure is a named blocker",
          blocked_on(rep, "renderer_registry_sha256 is freshly recomputed and matches"),
          str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    with mock.patch.object(cc.ChannelContext, "plan_binding",
                           side_effect=RuntimeError("simulated")):
        rep = v3_full_check(root, proj, ctx)
    check("a channel plan-binding derivation failure is a named blocker",
          blocked_on(rep, "v3 approval channel binding matches the current channel"),
          str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    with mock.patch.object(route_failures, "revision",
                           side_effect=RuntimeError("simulated")):
        rep = v3_full_check(root, proj, ctx)
    check("a failure-revision read failure is a named blocker",
          blocked_on(rep, "failure record is readable"), str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    with mock.patch.object(gate, "canonical_confirmation_phrase",
                           side_effect=RuntimeError("simulated")):
        rep = v3_full_check(root, proj, ctx)
    check("a confirmation-phrase derivation failure is a named blocker",
          blocked_on(rep, "v3 approval confirmation names this project and routes_id"),
          str(rep.blockers))

    root, proj, ctx, doc = build_canonical_baseline()
    with mock.patch.object(gate, "canonical_paid_generation_summary",
                           side_effect=RuntimeError("simulated")):
        rep = v3_full_check(root, proj, ctx)
    check("a paid-generation summary derivation failure is a named blocker",
          blocked_on(rep, "v3 approval paid-generation summary matches the approved routes"),
          str(rep.blockers))

    # ── 27. paid-generation summary truth ─────────────────────────────────────
    print("\n27. canonical_paid_generation_summary reports PAID shots, truthfully")
    root, proj, ctx, doc = build_canonical_baseline()
    summary = gate.canonical_paid_generation_summary(doc)
    total_routes = len(doc["routes"])
    paid_ids_expected = sorted(
        r["visual_asset_id"] for r in doc["routes"]
        if renderers.RENDERERS.get(r["renderer_id"], {}).get("cost_category") == "paid_api")
    check("shots equals the paid route count, not the total route count",
          summary["shots"] == len(paid_ids_expected) and len(paid_ids_expected) < total_routes,
          f"shots={summary['shots']}, paid={len(paid_ids_expected)}, total={total_routes}")
    check("paid_count matches shots", summary["paid_count"] == summary["shots"])
    check("paid_route_ids is sorted and exact",
          summary["paid_route_ids"] == paid_ids_expected, str(summary))

    print("\n27a. changing a route between paid and free changes the summary")
    doc2 = json.loads(json.dumps(doc))
    for r in doc2["routes"]:
        if r["renderer_id"] == "flux_illustration":
            r.update(renderer_id="pexels", cost_category="free_api", paid=False,
                     visual_type="PHOTO", prompt=None,
                     route_args={"map": None, "chart": None, "timeline": None,
                                "document": None,
                                "photo": {"query": "x", "constraints": None},
                                "illustration": None, "reenactment": None})
            break
    summary2 = gate.canonical_paid_generation_summary(doc2)
    check("flipping one route from paid to free reduces the reported paid count by one",
          summary2["paid_count"] == summary["paid_count"] - 1,
          f"before={summary['paid_count']}, after={summary2['paid_count']}")

    print("\n27b. malformed route collections raise rather than silently omitting")

    def expect_summary_error(label, bad_doc):
        try:
            gate.canonical_paid_generation_summary(bad_doc)
            check(label, False, "did not raise CanonicalSummaryError")
        except gate.CanonicalSummaryError:
            check(label, True)

    expect_summary_error("a non-list routes value raises", {"routes": "not-a-list"})
    expect_summary_error("a non-object route raises", {"routes": ["not-a-route"]})
    expect_summary_error("a duplicate visual_asset_id raises", {"routes": [
        {"visual_asset_id": "VIS-001-A", "renderer_id": "pexels"},
        {"visual_asset_id": "VIS-001-A", "renderer_id": "pexels"}]})
    expect_summary_error("a missing visual_asset_id raises", {"routes": [
        {"visual_asset_id": None, "renderer_id": "pexels"}]})
    expect_summary_error("an unregistered renderer_id raises", {"routes": [
        {"visual_asset_id": "VIS-001-A", "renderer_id": "not_a_real_renderer"}]})

    bad_registry = dict(renderers.RENDERERS)
    bad_registry["bad_cost_renderer"] = {
        "module": "x.py", "entry": "main", "cost_category": "not_a_real_category",
        "implemented": True, "supports_reference_input": False}
    with mock.patch.object(renderers, "RENDERERS", bad_registry):
        expect_summary_error("a malformed cost_category raises", {"routes": [
            {"visual_asset_id": "VIS-001-A", "renderer_id": "bad_cost_renderer"}]})

    print("\n27c. an approval claiming paid shots the routes don't support is refused")
    root, proj, ctx, doc = build_canonical_baseline()
    all_ids = sorted(r["visual_asset_id"] for r in doc["routes"])
    rewrite_approval(proj, lambda rec: rec.__setitem__(
        "paid_generation", {"shots": len(all_ids), "paid_count": len(all_ids),
                            "paid_route_ids": all_ids}))
    rep = run_canonical(root, proj)
    check("an approval claiming every route is paid (the old, incorrect semantics) "
         "is refused", blocked_on(
             rep, "v3 approval paid-generation summary matches the approved routes"),
          str(rep.blockers))

    # ── 28. universal refusal reconfirmed ─────────────────────────────────────
    print("\n28. the universal canonical refusal still holds after the micro-fix")
    root, proj, ctx, doc = build_canonical_baseline()
    a, b, c, d = patched(root)
    with a, b, c, d:
        try:
            gate.require_canonical_visual_execution_ready(proj)
            refused, err = False, ""
        except gate.GateBlocked as e:
            refused, err = True, str(e)
    check("require_canonical_visual_execution_ready still refuses unconditionally",
          refused and "remains disabled" in err, err[:200])
    canonical_entry = gate.entry_point("images.canonical_adapters")
    check("all six canonical adapters are still gated by it",
          {g["function"] for g in canonical_entry["gates"]}
          == {"adapt_map", "adapt_chart", "adapt_photo", "adapt_flux",
             "adapt_host_composite", "adapt_flux_reference_anchor"}
          and all(g["kind"] == "canonical_visual_execution"
                 for g in canonical_entry["gates"]))

    # ── 29. type-strict schema versions — Task 2B-B2b-1 final micro-fix ──────
    print("\n29. schema_version must be an exact int — float/bool/string/null all refuse")

    V2_BASE = {
        "schema_version": 2, "project": "demo_project", "plan_id": "aaaaaaaa",
        "manifest_sha256": "a" * 64, "visual_plan_sha256": "a" * 64,
        "visual_plan_md_sha256": "a" * 64, "prompts_sha256": "a" * 64,
        "failure_revision": 0, "approved_at": "2026-01-01T00:00:00+00:00",
        "approved_by": "Giri", "confirmation": "x", "paid_generation": {"shots": 1},
        "channel": {"channel_id": "fixture_channel", "channel_dna_version": 1,
                   "channel_json_sha256": "a" * 64, "character_spec_sha256": "a" * 64,
                   "voice_profile_sha256": "a" * 64},
    }

    # (label, value_kind, schema check should pass). value_kind is a marker
    # string for VALID/FLOAT/STR/WRONG/MISSING, or the literal True/False/None
    # value itself for the boolean/null cases.
    SCHEMA_VERSION_CASES = [
        ("valid integer version", "VALID", True),
        ("corresponding float (X.0)", "FLOAT", False),
        ("boolean True", True, False),
        ("boolean False", False, False),
        ("numeric string", "STR", False),
        ("null", None, False),
        ("missing field", "MISSING", False),
        ("wrong integer", "WRONG", False),
    ]

    def _apply_schema_case(rec: dict, value_kind, valid_int: int) -> None:
        if value_kind == "VALID":
            rec["schema_version"] = valid_int
        elif value_kind == "FLOAT":
            rec["schema_version"] = float(valid_int)
        elif value_kind == "STR":
            rec["schema_version"] = str(valid_int)
        elif value_kind == "WRONG":
            rec["schema_version"] = valid_int + 97
        elif value_kind == "MISSING":
            rec.pop("schema_version", None)
        else:
            rec["schema_version"] = value_kind          # True / False / None

    def run_v2_schema_case(value_kind):
        root, proj = build_fixture()
        rec = dict(V2_BASE)
        _apply_schema_case(rec, value_kind, 2)
        (proj / gate.APPROVAL_NAME).write_text(json.dumps(rec, indent=2), encoding="utf-8")
        rep = gate.GateReport(operation="x", project=proj.name, scope="test")
        gate._check_approval_v2(rep, proj, None, None)
        return rep

    for label, value_kind, should_pass in SCHEMA_VERSION_CASES:
        rep = run_v2_schema_case(value_kind)
        schema_ok = not blocked_on(rep, "approval schema is supported")
        check(f"v2 schema_version [{label}]: "
             f"{'accepted' if should_pass else 'refused immediately'}",
              schema_ok == should_pass, str(rep.blockers))
        if not should_pass:
            check(f"v2 schema_version [{label}]: no v2-specific field was inspected",
                  {n for n, ok, _ in rep.checks} == {
                      "Checkpoint 3 approval exists", "approval record parses",
                      "approval schema is supported"}, str(rep.checks))

    def run_v3_schema_case(value_kind):
        root, proj, ctx, doc = build_canonical_baseline()
        rewrite_approval(proj, lambda rec: _apply_schema_case(rec, value_kind, 3))
        placeholder = vr.ProjectRoutesLoad(
            project_dir=proj, operation="test", doc=doc, context=ctx,
            manifest=None, manifest_sha256=None,
            routes_path=proj / vr.ROUTES_NAME, routes_md_path=proj / vr.ROUTES_MD_NAME)
        rep = gate.GateReport(operation="x", project=proj.name, scope="test")
        gate._check_approval_v3(rep, proj, placeholder, ctx)
        return rep

    for label, value_kind, should_pass in SCHEMA_VERSION_CASES:
        rep = run_v3_schema_case(value_kind)
        schema_ok = not blocked_on(rep, "v3 approval schema_version is exactly 3")
        check(f"v3 schema_version [{label}]: "
             f"{'accepted' if should_pass else 'refused immediately'}",
              schema_ok == should_pass, str(rep.blockers))
        if not should_pass:
            check(f"v3 schema_version [{label}]: no v3-specific field was inspected",
                  {n for n, ok, _ in rep.checks} == {
                      "v3 approval exists", "v3 approval record parses",
                      "v3 approval schema_version is exactly 3"}, str(rep.checks))

    # ── 30. canonical_paid_generation_summary — complete input strictness ────
    print("\n30. canonical_paid_generation_summary rejects every malformed input class")

    def expect_summary_error(label, bad_doc):
        try:
            gate.canonical_paid_generation_summary(bad_doc)
            check(label, False, "did not raise CanonicalSummaryError")
        except gate.CanonicalSummaryError:
            check(label, True)
        except Exception as e:
            check(label, False, f"raised {type(e).__name__}, not CanonicalSummaryError: {e}")

    expect_summary_error("doc is None raises", None)
    expect_summary_error("doc is a non-dict (list) raises", [1, 2, 3])
    expect_summary_error("doc is a non-dict (string) raises", "not a doc")
    expect_summary_error("missing 'routes' key raises", {})
    expect_summary_error("null 'routes' raises", {"routes": None})
    expect_summary_error("non-list 'routes' (dict) raises", {"routes": {}})
    expect_summary_error("non-list 'routes' (string) raises", {"routes": "x"})
    expect_summary_error("non-dict route raises", {"routes": ["not-a-route"]})
    expect_summary_error("missing visual_asset_id raises",
                         {"routes": [{"renderer_id": "pexels"}]})
    expect_summary_error("non-string visual_asset_id raises",
                         {"routes": [{"visual_asset_id": 123, "renderer_id": "pexels"}]})
    expect_summary_error("empty visual_asset_id raises",
                         {"routes": [{"visual_asset_id": "", "renderer_id": "pexels"}]})
    expect_summary_error("whitespace-only visual_asset_id raises",
                         {"routes": [{"visual_asset_id": "   ", "renderer_id": "pexels"}]})
    expect_summary_error("missing renderer_id raises",
                         {"routes": [{"visual_asset_id": "VIS-001-A"}]})
    expect_summary_error("non-string renderer_id raises",
                         {"routes": [{"visual_asset_id": "VIS-001-A", "renderer_id": 123}]})
    expect_summary_error("empty renderer_id raises",
                         {"routes": [{"visual_asset_id": "VIS-001-A", "renderer_id": ""}]})
    expect_summary_error("whitespace-only renderer_id raises",
                         {"routes": [{"visual_asset_id": "VIS-001-A",
                                     "renderer_id": "   "}]})
    expect_summary_error("unregistered renderer_id raises",
                         {"routes": [{"visual_asset_id": "VIS-001-A",
                                     "renderer_id": "not_a_real_renderer"}]})
    expect_summary_error("duplicate visual_asset_id raises (after exact validation)",
                         {"routes": [
                             {"visual_asset_id": "VIS-001-A", "renderer_id": "pexels"},
                             {"visual_asset_id": "VIS-001-A", "renderer_id": "pexels"}]})

    bad_registry = dict(renderers.RENDERERS)
    bad_registry["malformed_entry_renderer"] = "not-a-mapping"
    with mock.patch.object(renderers, "RENDERERS", bad_registry):
        expect_summary_error(
            "a malformed (non-mapping) renderer registry entry raises",
            {"routes": [{"visual_asset_id": "VIS-001-A",
                        "renderer_id": "malformed_entry_renderer"}]})

    bad_registry2 = dict(renderers.RENDERERS)
    bad_registry2["no_cost_category_renderer"] = {
        "module": "x.py", "entry": "main", "implemented": True,
        "supports_reference_input": False}
    with mock.patch.object(renderers, "RENDERERS", bad_registry2):
        expect_summary_error(
            "a renderer entry with no cost_category raises",
            {"routes": [{"visual_asset_id": "VIS-001-A",
                        "renderer_id": "no_cost_category_renderer"}]})

    empty_summary = gate.canonical_paid_generation_summary({"routes": []})
    check("a genuinely empty, genuinely valid routes list returns the zero summary",
          empty_summary == {"shots": 0, "paid_count": 0, "paid_route_ids": []},
          str(empty_summary))

    print("\n30a. the v3 validator still converts a CanonicalSummaryError into a blocker")
    root, proj, ctx, doc = build_canonical_baseline()
    with mock.patch.object(gate, "canonical_paid_generation_summary",
                           side_effect=gate.CanonicalSummaryError("simulated")):
        rep = v3_full_check(root, proj, ctx)
    check("a CanonicalSummaryError from the summary function is a named blocker, "
         "not a crash",
          blocked_on(rep, "v3 approval paid-generation summary matches the approved routes"),
          str(rep.blockers))

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
