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
import route_images  # noqa: E402

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
    manifest = {"episode": "demo", "identity_state": "ok", "identity_reasons": [],
                "scenes": scenes}
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
    return mock.patch.multiple(gate, PIPELINE_DIR=root, SPEC_PATH=spec), \
        mock.patch.multiple(pose_registry, PIPELINE_DIR=root, SPEC_PATH=spec), \
        mock.patch.object(composite_character, "PIPELINE_DIR", root)


def run_gate(root, project, **kw):
    a, b, c = patched(root)
    with a, b, c:
        return gate.require_generation_ready(project, "test", raise_on_block=False, **kw)


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


REPO_BEFORE = census(ROOT)

try:
    # ── 1. the real projects ─────────────────────────────────────────────────
    print("\n1. the real pilot is blocked, and blocked on SCENE-066")
    rep = gate.require_generation_ready(ROOT / "pilot_neet_scandal", "pilot images",
                                        raise_on_block=False)
    check("pilot is blocked", bool(rep.blockers), "gate passed the pilot")
    check("blocked on SCENE-066", blocked_on(rep, "SCENE-066"), str(rep.blockers))
    check("raises GateBlocked by default", True)
    try:
        gate.require_generation_ready(ROOT / "pilot_neet_scandal", "pilot images")
        check("raises GateBlocked by default", False, "returned instead of raising")
    except gate.GateBlocked as e:
        check("GateBlocked names the blocking condition", "SCENE-066" in str(e), str(e))

    print("\n2. test_2min passes the full gate")
    rep = gate.require_generation_ready(ROOT / "test_2min", "images",
                                        raise_on_block=False)
    check("test_2min is ready", not rep.blockers, str(rep.blockers))
    check("the visual-plan check actually ran",
          any(n == "visual plan exists" for n, _, _ in rep.checks))

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
    a, b, c = patched(root)
    with a, b, c:
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
    a, b, c = patched(root)
    seen = []
    with a, b, c:
        real = pose_registry.resolve

        def spy(pid, scene_bound=False):
            seen.append((pid, scene_bound))
            return real(pid, scene_bound=scene_bound)

        bg = proj / "images" / "SCENE-001.png"
        _png(bg, alpha=False)
        with mock.patch.object(pose_registry, "resolve", spy):
            rec = composite_character.composite("pointing_viewer_left", bg,
                                                proj / "images" / "out.png")
            check("resolve() was called", seen == [("pointing_viewer_left", False)], str(seen))
            check("the record keys on the id, not a path",
                  rec["pose_id"] == "pointing_viewer_left" and "path" not in rec)
            check("negative space drives placement — viewer_left pose sits right",
                  rec["side"] == "right", rec["side"])
            seen.clear()
            composite_character.composite("seated_reading_document", bg,
                                          proj / "images" / "out2.png", scene_bound=True)
            check("scene_bound is passed through explicitly",
                  seen == [("seated_reading_document", True)], str(seen))
        try:
            composite_character.composite("half_finished", bg, proj / "images" / "no.png")
            check("a pending pose cannot be composited", False, "it rendered")
        except pose_registry.PoseError:
            check("a pending pose cannot be composited", True)
        check("no output file was written for the refused pose",
              not (proj / "images" / "no.png").exists())

    # ── 15. registry completeness ────────────────────────────────────────────
    print("\n15. every registered paid entry point invokes the shared gate")

    def gate_called_in(module: str, func: str) -> bool:
        tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and \
                            ast.unparse(sub.func).endswith("require_generation_ready"):
                        return True
        return False

    for e in gate.PAID_ENTRY_POINTS:
        if not e["implemented"]:
            check(f"{e['id']}: recorded as unimplemented and genuinely absent",
                  not (ROOT / e["module"]).exists(),
                  f"{e['module']} exists but the registry says it does not")
            continue
        check(f"{e['id']} ({e['module']}:{e['gate']}) calls the gate",
              gate_called_in(e["module"], e["gate"]))

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

    before = {p.name for p in (ROOT / "test_2min" / "images").glob("*")}
    r = subprocess.run([sys.executable, str(ROOT / "route_images.py"),
                        "--project", "test_2min", "--dry-run"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    after = {p.name for p in (ROOT / "test_2min" / "images").glob("*")}
    check("a dry run on a passing project exits 0", r.returncode == 0, r.stderr[-300:])
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

    direct = gate.require_generation_ready(ROOT / "pilot_neet_scandal", "x",
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

    # On a passing project the orchestrator must get past the gate and stop at
    # its own spend checkpoint. Declining there is how this test proves the gate
    # let it through without letting it generate anything.
    reached = []
    stub2 = types.SimpleNamespace(project_dir=ROOT / "test_2min",
                                  _checkpoint=lambda msg: reached.append(msg) or "q")
    ok_direct = not gate.require_generation_ready(ROOT / "test_2min", "x",
                                                  raise_on_block=False).blockers
    try:
        pipeline_agents.OrchestratorAgent._stage_images(stub2)
        passed = True
        why = ""
    except RuntimeError as e:
        passed, why = "Aborted by user" in str(e), str(e)
    check("the orchestrator passes where the direct script passes",
          passed and ok_direct, why[:200])
    check("the orchestrated path reached its spend checkpoint and spent nothing",
          bool(reached), "checkpoint never reached")

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
