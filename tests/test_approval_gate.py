"""Identity gate vs. Checkpoint 3 approval — the two must not be the same thing.

The earlier single gate treated "a visual plan exists, has no review items and
matches the manifest" as approval. plan_visuals.py writes that file itself, so
the check only proved a program agreed with itself and anyone could clear the
checkpoint by running a free command. These tests hold the two apart:

  - planning and prompt authoring need identity and nothing more
  - anything that downloads, generates or writes episode artwork needs an
    explicit approval record bound to the exact bytes of both the manifest and
    the plan

Everything runs against synthetic fixtures in a temp directory. A whole-tree hash
census of the repository's character assets runs in try/finally.

Run:  python tests/test_approval_gate.py
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

# The anthropic SDK plays no part in any gate, and stubbing it keeps this suite
# runnable on an interpreter without it installed — which is the interpreter most
# people will run it on. Real calls are asserted never to happen anyway.
if "anthropic" not in sys.modules:
    _stub = types.ModuleType("anthropic")
    _stub.Anthropic = object
    sys.modules["anthropic"] = _stub

import approve_checkpoint as ac  # noqa: E402
import composite_character  # noqa: E402
import generation_gate as gate  # noqa: E402
import plan_visuals  # noqa: E402
import pose_registry  # noqa: E402
import route_images  # noqa: E402
import source_ids  # noqa: E402
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


# ── fixture world ────────────────────────────────────────────────────────────

SCRIPT = ("The minister resigned in July. Nobody expected it.\n\n"
          "The exam was cancelled that week. Then it was rescheduled.\n")

PROMPTS_OK = (
    '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` TYPE: HOST '
    'HOST_POSE: neutral_presenter NARRATION: "one" PROMPT: bg\n'
    '**SHOT 02** · SCENE-002 · standalone → `SCENE-002.png` TYPE: PHOTO '
    'NARRATION: "two" PROMPT: a building\n'
    '**SHOT 03** · SCENE-003 · standalone → `SCENE-003.png` TYPE: CARTOON '
    'NARRATION: "three" PROMPT: an idea\n'
    '**SHOT 04** · SCENE-004 · standalone → `SCENE-004.png` TYPE: MAP '
    'MAP_ARGS: --highlight Kerala NARRATION: "four" PROMPT: a map\n'
)


def _png(path: Path, alpha=True, blank=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA" if alpha else "RGB", (256, 256),
                   (250, 247, 242, 0) if alpha else (250, 247, 242))
    if not blank:
        im.paste(Image.new("RGBA", (80, 160), (60, 60, 60, 255)), (88, 60))
    im.save(path)


def build_fixture(prompts: str = PROMPTS_OK) -> tuple[Path, Path]:
    td = Path(tempfile.mkdtemp())
    _fixtures.append(td)

    for m in ("body-master.png", "face-master.png"):
        _png(td / "character" / "canonical" / m, alpha=False)
    poses = {"neutral_presenter": ("host_neutral_presenter.png", "approved", "both_sides", []),
             "seated_reading_document": ("host_seated_reading_document.png",
                                         "approved_scene_bound", "none", ["desk"])}
    registry = {}
    for pid, (fname, status, ns, geom) in poses.items():
        f = td / "character" / "poses" / fname
        _png(f)
        registry[pid] = {"id": pid, "path": f"character/poses/{fname}", "sha256": sha(f),
                         "dimensions": "256x256", "status": status, "direction": "front",
                         "negative_space": ns, "includes_geometry": geom,
                         "generic_compositing_allowed": not geom}
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
                         "registry": registry},
    }
    (td / "character" / "character_spec.json").write_text(json.dumps(spec, indent=2),
                                                          encoding="utf-8")

    proj = td / "demo_project"
    proj.mkdir()
    (proj / "script_demo.txt").write_text(SCRIPT, encoding="utf-8")
    units = source_ids.build_source_units(SCRIPT)
    source_ids.save_units(proj, units)
    scenes = [{"id": f"SCENE-{i:03d}", "image": f"SCENE-{i:03d}.png", "script": u["text"],
               "source_ids": [u["id"]], "shot_instance_id": f"{u['id']}-S01",
               "source_match": "ok", "visual_match": "ok", "visual_state": "planned",
               "visual_asset_id": f"VIS-{i:03d}-A"}
              for i, u in enumerate(units, 1)]
    (proj / "manifest.json").write_text(
        json.dumps(channel_fixture.stamp(
            {"episode": "demo", "identity_state": "ok",
             "identity_reasons": [], "scenes": scenes}, project_dir=proj),
            indent=2), encoding="utf-8")
    channel_fixture.install(td)
    (proj / route_images.PROMPTS_FILE).write_text(prompts, encoding="utf-8")
    return td, proj


def patched(root: Path):
    spec = root / "character" / "character_spec.json"
    return (mock.patch.multiple(cc, PIPELINE_DIR=root,
                                CHANNELS_DIR=root / "channels"),
            mock.patch.multiple(gate, PIPELINE_DIR=root, SPEC_PATH=spec),
            mock.patch.multiple(pose_registry, PIPELINE_DIR=root, SPEC_PATH=spec),
            mock.patch.object(composite_character, "PIPELINE_DIR", root),
            mock.patch.object(composite_character, "PREVIEW_DIR",
                              root / "character" / "previews"))


class World:
    """Run a block with the fixture standing in for the real pipeline root."""

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


def plan(root, proj):
    """Write both artifacts, as plan_visuals.py does: the JSON the gate executes
    and the markdown the human reads. Approval now binds both."""
    with World(root):
        p = plan_visuals.build_plan(proj)
    (proj / plan_visuals.PLAN_JSON).write_text(
        json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
    (proj / plan_visuals.PLAN_MD).write_text(plan_visuals.render_md(p), encoding="utf-8")
    return p


def approve(root, proj, approver="Reviewer"):
    # The phrase names the plan, so it has to be read off the plan on disk.
    plan_id = json.loads(
        (proj / plan_visuals.PLAN_JSON).read_text(encoding="utf-8"))["plan_id"]
    with World(root):
        return ac.write_approval(proj, approver,
                                 ac.confirmation_phrase(proj.name, plan_id))


def verdict(root, proj, kind="generation", **kw):
    fn = {"identity": gate.require_identity_ready,
          "generation": gate.require_generation_ready}[kind]
    with World(root):
        return fn(proj, "test", raise_on_block=False, **kw)


def blocked_on(rep, fragment):
    return any(fragment in b for b in rep.blockers)


REPO_BEFORE = census(ROOT)

try:
    # ── 1 & 2 ────────────────────────────────────────────────────────────────
    print("\n1. identity-ready planning succeeds with no approval anywhere")
    root, proj = build_fixture()
    rep = verdict(root, proj, "identity")
    check("identity gate passes", not rep.blockers, str(rep.blockers))
    check("no approval record exists", not (proj / gate.APPROVAL_NAME).exists())
    p = plan(root, proj)
    check("the planner runs and produces a plan", (proj / plan_visuals.PLAN_JSON).exists())
    check("the plan has no review items", not p["needs_review"], str(p["needs_review"]))
    check("planning still did not create approval",
          not (proj / gate.APPROVAL_NAME).exists())

    print("\n2. prompt authoring is identity-gated, not approval-gated")
    src = (ROOT / "generate_image_prompts.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "generate_prompts")
    calls = [ast.unparse(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)]
    check("calls require_identity_ready", "require_identity_ready" in calls)
    check("does not call require_generation_ready",
          "require_generation_ready" not in calls)
    # ...and the gate itself must come before the key, the client and the write.
    body = ast.unparse(fn)
    i_gate = body.index("require_identity_ready")
    for marker, label in (("ANTHROPIC_API_KEY", "reads the API key"),
                          ("anthropic.Anthropic", "constructs the client"),
                          ("messages.create", "makes an API call"),
                          ("output_path.write_text", "overwrites its output")):
        check(f"gate runs before it {label}", i_gate < body.index(marker))

    print("\n3. prompt authoring is blocked before key/client/API when identity is bad")
    root, proj = build_fixture()
    m = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
    m["identity_state"] = "blocked"
    m["identity_reasons"] = ["SCENE-002 not matched"]
    (proj / "manifest.json").write_text(json.dumps(m), encoding="utf-8")

    existing = proj / route_images.PROMPTS_FILE
    before_bytes = existing.read_bytes()
    import generate_image_prompts as gip
    touched = []
    with World(root), \
         mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-should-not-be-read"}), \
         mock.patch.object(gip, "anthropic",
                           types.SimpleNamespace(
                               Anthropic=lambda **kw: touched.append("client"))):
        try:
            gip.generate_prompts(proj, 8, True)
            code = 0
        except SystemExit as e:
            code = e.code
    check("exits nonzero", code == 1, f"exit={code}")
    check("no client was constructed", not touched, str(touched))
    check("the existing prompts file was not overwritten",
          existing.read_bytes() == before_bytes)

    # ── 4 to 8: approval lifecycle ───────────────────────────────────────────
    print("\n4. generation is refused with no approval")
    root, proj = build_fixture()
    plan(root, proj)
    rep = verdict(root, proj)
    check("blocked", blocked_on(rep, "Checkpoint 3 approval exists"), str(rep.blockers))
    check("identity itself is fine", not verdict(root, proj, "identity").blockers)

    print("\n5. a valid approval permits generation")
    approve(root, proj)
    rep = verdict(root, proj)
    check("generation gate passes", not rep.blockers, str(rep.blockers))
    rec = json.loads((proj / gate.APPROVAL_NAME).read_text(encoding="utf-8"))
    for field in gate.APPROVAL_REQUIRED_FIELDS:
        # `is not None`, not truthiness: failure_revision is legitimately 0 on a
        # project where no route has ever failed.
        check(f"approval records {field}", rec.get(field) is not None)
    check("approval binds the manifest hash",
          rec["manifest_sha256"] == sha(proj / "manifest.json"))
    check("approval binds the visual-plan hash",
          rec["visual_plan_sha256"] == sha(proj / plan_visuals.PLAN_JSON))
    check("approval records the approved spend",
          "shots" in rec["paid_generation"])
    check("approval timestamp is UTC", rec["approved_at"].endswith("+00:00"))

    print("\n6. editing the manifest invalidates approval")
    m = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
    m["scenes"][0]["script"] = "edited after approval"
    (proj / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    check("blocked", blocked_on(verdict(root, proj),
                                "manifest is unchanged since approval"))

    print("\n7. editing the visual plan invalidates approval")
    root, proj = build_fixture()
    plan(root, proj)
    approve(root, proj)
    check("valid to begin with", not verdict(root, proj).blockers)
    d = json.loads((proj / plan_visuals.PLAN_JSON).read_text(encoding="utf-8"))
    d["paid_generation"]["shots"] = 999
    (proj / plan_visuals.PLAN_JSON).write_text(json.dumps(d, indent=2), encoding="utf-8")
    check("blocked", blocked_on(verdict(root, proj),
                                "visual plan is unchanged since approval"))

    print("\n8. re-running the planner neither grants nor restores approval")
    # Every planner run carries a fresh plan_id, so a plan cannot round-trip back
    # to previously-approved bytes. Before that, hand-editing the plan and then
    # re-running the planner silently restored the old approval.
    p_a = plan(root, proj)
    p_b = plan(root, proj)
    check("two runs are distinguishable artifacts", p_a["plan_id"] != p_b["plan_id"])
    rep = verdict(root, proj)
    check("still blocked after re-planning",
          blocked_on(rep, "visual plan is unchanged since approval"), str(rep.blockers))
    root, proj = build_fixture()
    plan(root, proj)
    plan(root, proj)
    check("planning twice creates no approval", not (proj / gate.APPROVAL_NAME).exists())

    print("\n   stale, malformed and incomplete approvals all block")
    root, proj = build_fixture()
    plan(root, proj)
    (proj / gate.APPROVAL_NAME).write_text("{not json", encoding="utf-8")
    check("malformed approval blocks", blocked_on(verdict(root, proj),
                                                  "approval record parses"))
    approve(root, proj)
    rec = json.loads((proj / gate.APPROVAL_NAME).read_text(encoding="utf-8"))
    del rec["approved_by"]
    (proj / gate.APPROVAL_NAME).write_text(json.dumps(rec), encoding="utf-8")
    check("incomplete approval blocks", blocked_on(verdict(root, proj),
                                                   "approval record is complete"))
    approve(root, proj)
    rec = json.loads((proj / gate.APPROVAL_NAME).read_text(encoding="utf-8"))
    rec["schema_version"] = 99
    (proj / gate.APPROVAL_NAME).write_text(json.dumps(rec), encoding="utf-8")
    check("unknown schema blocks", blocked_on(verdict(root, proj),
                                              "approval schema is supported"))
    approve(root, proj)
    rec = json.loads((proj / gate.APPROVAL_NAME).read_text(encoding="utf-8"))
    rec["project"] = "some_other_episode"
    (proj / gate.APPROVAL_NAME).write_text(json.dumps(rec), encoding="utf-8")
    check("an approval for another project blocks",
          blocked_on(verdict(root, proj), "approval names this project"))

    print("\n   approval cannot be granted on blocked identity or an unresolved plan")
    root, proj = build_fixture()
    plan(root, proj)
    m = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
    m["identity_state"] = "blocked"
    m["identity_reasons"] = ["x"]
    (proj / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    try:
        approve(root, proj)
        check("blocked identity cannot be approved", False, "it approved")
    except ac.ApprovalRefused as e:
        check("blocked identity cannot be approved", "identity is not ready" in str(e))
    check("nothing was written", not (proj / gate.APPROVAL_NAME).exists())

    root, proj = build_fixture(PROMPTS_OK.replace("MAP_ARGS: --highlight Kerala ", ""))
    plan(root, proj)
    try:
        approve(root, proj)
        check("an unresolved plan cannot be approved", False, "it approved")
    except ac.ApprovalRefused as e:
        check("an unresolved plan cannot be approved", "unresolved review item" in str(e))
    check("nothing was written", not (proj / gate.APPROVAL_NAME).exists())

    root, proj = build_fixture()
    plan(root, proj)
    for bad, why in ((("Reviewer", "yes"), "wrong phrase"),
                     (("", ac.confirmation_phrase("demo_project")), "no approver")):
        try:
            with World(root):
                ac.write_approval(proj, bad[0], bad[1])
            check(f"{why} is refused", False, "it approved")
        except ac.ApprovalRefused:
            check(f"{why} is refused", True)
    check("still no approval file", not (proj / gate.APPROVAL_NAME).exists())

    # ── 9 ────────────────────────────────────────────────────────────────────
    print("\n9. the planner and orchestrator cannot call the approval writer")

    def imports_of(module: str) -> set:
        names = set()
        for node in ast.walk(ast.parse((ROOT / module).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    for mod in ("plan_visuals.py", "pipeline_agents.py", "route_images.py",
                "generate_image_prompts.py"):
        check(f"{mod} does not import approve_checkpoint",
              "approve_checkpoint" not in imports_of(mod), str(imports_of(mod)))
    check("write_approval is called nowhere outside its own module",
          not [f.name for f in ROOT.glob("*.py")
               if f.name != "approve_checkpoint.py"
               and "write_approval" in f.read_text(encoding="utf-8")])

    # ...and the runtime half, which catches an indirect call: the check reads
    # the calling frame's module name, so the call is made from a namespace that
    # really is named plan_visuals rather than from a function merely labelled so.
    root, proj = build_fixture()
    plan(root, proj)
    ns = {"__name__": "plan_visuals", "ac": ac, "proj": proj}
    exec(compile("def sneak():\n"
                 "    return ac.write_approval(proj, 'Reviewer',\n"
                 "                             ac.confirmation_phrase(proj.name))\n",
                 "plan_visuals.py", "exec"), ns)
    try:
        with World(root):
            ns["sneak"]()
        check("a call from plan_visuals is refused at runtime", False, "it approved")
    except ac.ApprovalRefused as e:
        check("a call from plan_visuals is refused at runtime",
              "human decision" in str(e), str(e))
    check("nothing was written", not (proj / gate.APPROVAL_NAME).exists())

    # ── 10 to 12: no silent reroutes ─────────────────────────────────────────
    print("\n10. invalid MAP and CHART routes become NEEDS_REVIEW, never CARTOON")
    bad_prompts = (
        '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` TYPE: MAP '
        'NARRATION: "one" PROMPT: a map\n'
        '**SHOT 02** · SCENE-002 · standalone → `SCENE-002.png` TYPE: CHART '
        'CHART_ARGS: --type bar NARRATION: "two" PROMPT: a chart\n'
        '**SHOT 03** · SCENE-003 · standalone → `SCENE-003.png` TYPE: CHART '
        'CHART_ARGS: --type bar --data notjson NARRATION: "three" PROMPT: a chart\n'
        '**SHOT 04** · SCENE-004 · standalone → `SCENE-004.png` TYPE: HOST '
        'HOST_POSE: no_such_pose NARRATION: "four" PROMPT: host\n'
    )
    root, proj = build_fixture(bad_prompts)
    with World(root):
        shots = route_images.parse_shots(proj / route_images.PROMPTS_FILE)
    for s, want in zip(shots, ["MAP", "CHART", "CHART", "HOST"]):
        check(f"shot {s['shot_num']} keeps its planned route {want}",
              s["type"] == want and s["planned_type"] == want,
              f"became {s['type']}")
        check(f"shot {s['shot_num']} is flagged for review", s["needs_review"])
        check(f"shot {s['shot_num']} records why", bool(s["review_reason"]))
    check("nothing was rerouted to CARTOON",
          not any(s["type"] == "CARTOON" for s in shots))
    p = plan(root, proj)
    check("the plan carries all four review items", len(p["needs_review"]) == 4,
          str(p["needs_review"]))

    print("\n11. a Pexels failure does not fall back to AI")
    src = (ROOT / "route_images.py").read_text(encoding="utf-8")
    check("no PHOTO-to-AI fallback remains in the router",
          "ai_shot_files.extend" not in src and "falling back to AI" not in src)
    root, proj = build_fixture()
    plan(root, proj)
    approve(root, proj)
    calls = {"pexels": 0, "ai": 0}

    def fail_pexels(shot, images_dir, script_dir):
        calls["pexels"] += 1
        return False

    def count_ai(*a, **k):
        calls["ai"] += 1
        return True

    executable = json.loads(
        (proj / plan_visuals.PLAN_JSON).read_text(encoding="utf-8"))
    with World(root), \
         mock.patch.object(route_images, "run_pexels", fail_pexels), \
         mock.patch.object(route_images, "run_ai_batch", count_ai), \
         mock.patch.object(route_images, "run_map", lambda *a, **k: True), \
         mock.patch.object(route_images, "run_host", lambda *a, **k: True):
        code = route_images.dispatch_routes(proj, ROOT, executable, overwrite=True)
    check("the failing PHOTO shot was attempted once", calls["pexels"] == 1)
    check("it did not become an AI generation", calls["ai"] == 0,
          f"AI batch ran {calls['ai']}x after a PHOTO failure")
    check("dispatch reports failure", code == 1, f"exit={code}")
    check("the missing PHOTO file was not created",
          not (proj / "images" / "SCENE-002.png").exists())

    print("\n12. a route change requires re-planning and re-approval")
    root, proj = build_fixture()
    plan(root, proj)
    approve(root, proj)
    check("approved and ready", not verdict(root, proj).blockers)
    (proj / route_images.PROMPTS_FILE).write_text(
        PROMPTS_OK.replace("TYPE: PHOTO", "TYPE: CARTOON"), encoding="utf-8")
    # The routing input is bound now, so the gate notices immediately — before
    # anyone re-plans. That is the hole this closed: a free PHOTO route could be
    # edited into a paid AI route with both approved hashes intact.
    check("the gate notices the edited prompts at once",
          blocked_on(verdict(root, proj), "routing input is unchanged since approval"),
          str(verdict(root, proj).blockers))
    p2 = plan(root, proj)
    check("the re-plan sees the new route", p2["mix"].get("CARTOON") == 2,
          str(p2["mix"]))
    check("the changed cost is visible", p2["paid_generation"]["shots"] == 2,
          str(p2["paid_generation"]))
    check("the old approval still does not apply",
          blocked_on(verdict(root, proj), "visual plan is unchanged since approval"))

    # ── 13 ───────────────────────────────────────────────────────────────────
    print("\n13. the compositor cannot write production artwork without approval")
    check("composite() is no longer public",
          not hasattr(composite_character, "composite"))
    root, proj = build_fixture()
    plan(root, proj)
    bg = proj / "images" / "SCENE-001.png"
    _png(bg, alpha=False)
    out = proj / "images" / "SCENE-001.png"
    try:
        with World(root):
            composite_character.render_production(proj, "neutral_presenter", bg, out)
        check("refused without approval", False, "it rendered")
    except gate.GateBlocked as e:
        check("refused without approval", "Checkpoint 3 approval" in str(e))
    approve(root, proj)
    with World(root):
        rec = composite_character.render_production(proj, "neutral_presenter", bg, out)
    check("permitted once approved", rec["pose_id"] == "neutral_presenter")

    print("\n    a production render cannot write outside its approved project")
    outside = root / "elsewhere.png"
    try:
        with World(root):
            composite_character.render_production(proj, "neutral_presenter", bg, outside)
        check("refused", False, "it wrote outside the project")
    except ValueError as e:
        check("refused", "outside the approved project" in str(e))
    check("nothing was written there", not outside.exists())

    print("\n    previews are character-scope and confined to character/previews/")
    root, proj = build_fixture()
    bg2 = proj / "images" / "bg.png"
    _png(bg2, alpha=False)
    with World(root):
        rec = composite_character.render_preview("neutral_presenter", bg2, "check-1")
    check("a preview needs no episode approval", rec.get("preview") is True)
    check("it landed in character/previews/",
          (root / "character" / "previews" / "check-1.png").exists())
    with World(root):
        rec2 = composite_character.render_preview("neutral_presenter", bg2,
                                                  "../../demo_project/images/SCENE-003")
    check("a traversal in the preview name cannot escape",
          Path(rec2["output"]).parent == Path("."),
          rec2["output"])
    check("and nothing landed in the project",
          not (proj / "images" / "SCENE-003.png").exists())

    # ── 14 ───────────────────────────────────────────────────────────────────
    print("\n14. blocked paths spend nothing and touch no episode asset")
    root, proj = build_fixture()
    plan(root, proj)          # deliberately NOT approved
    before = {p.as_posix(): sha(p) for p in proj.rglob("*") if p.is_file()}
    spent = []
    with World(root), \
         mock.patch.object(route_images, "run_map",
                           lambda *a, **k: spent.append("map") or True), \
         mock.patch.object(route_images, "run_pexels",
                           lambda *a, **k: spent.append("pexels") or True), \
         mock.patch.object(route_images, "run_ai_batch",
                           lambda *a, **k: spent.append("ai") or True), \
         mock.patch.object(route_images, "run_host",
                           lambda *a, **k: spent.append("host") or True):
        executable = json.loads(
            (proj / plan_visuals.PLAN_JSON).read_text(encoding="utf-8"))
        try:
            route_images.dispatch_routes(proj, ROOT, executable, overwrite=True)
            check("dispatch refused", False, "it dispatched")
        except gate.GateBlocked:
            check("dispatch refused", True)
    check("no generator ran", not spent, str(spent))
    check("no images directory was created", not (proj / "images").exists())
    after = {p.as_posix(): sha(p) for p in proj.rglob("*") if p.is_file()}
    check("no project file changed", before == after,
          str(set(after) ^ set(before)))

    print("\n    the real pilot stays blocked at both gates")
    rep_id = gate.require_identity_ready(ROOT / "pilot_neet_scandal", "x",
                                         raise_on_block=False)
    check("identity blocked on SCENE-066", blocked_on(rep_id, "SCENE-066"),
          str(rep_id.blockers))
    rep_gen = gate.require_generation_ready(ROOT / "pilot_neet_scandal", "x",
                                            raise_on_block=False)
    check("generation blocked too", bool(rep_gen.blockers))
    check("the pilot has no approval record",
          not (ROOT / "pilot_neet_scandal" / gate.APPROVAL_NAME).exists())
    before_imgs = {p.name for p in (ROOT / "pilot_neet_scandal" / "images").glob("*")}
    r = subprocess.run([sys.executable, str(ROOT / "route_images.py"),
                        "--project", "pilot_neet_scandal"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    after_imgs = {p.name for p in (ROOT / "pilot_neet_scandal" / "images").glob("*")}
    check("the router exits nonzero on the pilot", r.returncode == 1, r.stdout[-300:])
    check("and created no image", before_imgs == after_imgs)

    print("\n    test_2min is identity-ready but not approved")
    check("identity ready", not gate.require_identity_ready(
        ROOT / "test_2min", "x", raise_on_block=False).blockers)
    rep = gate.require_generation_ready(ROOT / "test_2min", "x", raise_on_block=False)
    check("generation blocked for want of approval",
          blocked_on(rep, "Checkpoint 3 approval exists"), str(rep.blockers))
    r = subprocess.run([sys.executable, str(ROOT / "route_images.py"),
                        "--project", "test_2min", "--dry-run"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("a dry run still works without approval", r.returncode == 0, r.stdout[-400:])

    # ── 15 ───────────────────────────────────────────────────────────────────
    print("\n15. character work is separate from episode approval")
    root, proj = build_fixture()
    with World(root):
        rep = gate.require_character_ready("pose work", raise_on_block=False)
    check("character gate passes with no episode approval anywhere",
          not rep.blockers, str(rep.blockers))
    check("the character gate never looks at a project",
          rep.project is None and rep.scope == "character")
    check("no approval-related check ran",
          not any("approval" in n.lower() for n, _, _ in rep.checks))
    for mod, fname in (("generate_poses.py", "generate_batch"),
                       ("generate_character.py", "main")):
        tree = ast.parse((ROOT / mod).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == fname)
        names = [ast.unparse(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)]
        check(f"{mod}:{fname} uses the character gate",
              any("require_character_ready" in n for n in names))
        check(f"{mod}:{fname} does not require episode approval",
              not any("require_generation_ready" in n for n in names))

    # ── registry coherence ───────────────────────────────────────────────────
    print("\n    every registered entry point calls its declared gate")

    def gate_called(module, func, kind):
        tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
        want = f"require_{kind}_ready"
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and ast.unparse(sub.func).endswith(want):
                        return True
        return False

    for e in gate.PAID_ENTRY_POINTS:
        if not e["implemented"]:
            check(f"{e['id']} is genuinely absent", not (ROOT / e["module"]).exists())
            continue
        for g in e["gates"]:
            check(f"{e['id']}: {e['module']}:{g['function']} calls "
                  f"require_{g['kind']}_ready()",
                  gate_called(e["module"], g["function"], g["kind"]))

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
