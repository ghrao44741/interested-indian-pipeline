"""The approved plan is the contract — nothing may change what gets generated.

Four ways around Checkpoint 3 that this covers:

  - the plan is derived from the prompts file, and dispatch used to re-read that
    file, so a PHOTO route could be edited into a paid AI route after approval
    with every approved hash intact
  - an unknown TYPE fell through keyword classification to CARTOON, and dispatch
    had a default bucket pointing at the AI generator
  - a runtime failure was printed and forgotten: the run continued into the paid
    batch, the approval stayed valid, and the next plan looked clean
  - the router discarded main()'s return value, so failures exited 0

Plus the artifact split: a human reads visual_plan.md, the gate executes
visual_plan.json, and until now only the JSON was bound.

Fixtures only. No paid calls, no repository writes.

Run:  python tests/test_route_binding.py
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

if "anthropic" not in sys.modules:
    _stub = types.ModuleType("anthropic")
    _stub.Anthropic = object
    sys.modules["anthropic"] = _stub

import approve_checkpoint as ac  # noqa: E402
import composite_character  # noqa: E402
import generation_gate as gate  # noqa: E402
import plan_visuals  # noqa: E402
import pose_registry  # noqa: E402
import route_failures  # noqa: E402
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
    d = root / "character"
    if d.exists():
        for f in sorted(d.rglob("*")):
            if f.is_file():
                out[f.relative_to(root).as_posix()] = sha(f)
    return out


SCRIPT = ("The minister resigned in July. Nobody expected it.\n\n"
          "The exam was cancelled that week. Then it was rescheduled.\n")

# PHOTO before CARTOON on purpose: the failure test needs a paid route sitting
# behind a free one that fails.
PROMPTS_OK = (
    '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` TYPE: PHOTO '
    'NARRATION: "one" PROMPT: a building\n'
    '**SHOT 02** · SCENE-002 · standalone → `SCENE-002.png` TYPE: CARTOON '
    'NARRATION: "two" PROMPT: an idea\n'
    '**SHOT 03** · SCENE-003 · standalone → `SCENE-003.png` TYPE: MAP '
    'MAP_ARGS: --highlight Kerala NARRATION: "three" PROMPT: a map\n'
    '**SHOT 04** · SCENE-004 · standalone → `SCENE-004.png` TYPE: HOST '
    'HOST_POSE: neutral_presenter NARRATION: "four" PROMPT: host\n'
)


def _png(path: Path, alpha=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA" if alpha else "RGB", (256, 256),
                   (250, 247, 242, 0) if alpha else (250, 247, 242))
    im.paste(Image.new("RGBA", (80, 160), (60, 60, 60, 255)), (88, 60))
    im.save(path)


def build_fixture(prompts: str = PROMPTS_OK) -> tuple[Path, Path]:
    td = Path(tempfile.mkdtemp())
    _fixtures.append(td)
    for m in ("body-master.png", "face-master.png"):
        _png(td / "character" / "canonical" / m, alpha=False)
    registry = {}
    for pid, fname, status, geom in (
            ("neutral_presenter", "host_neutral_presenter.png", "approved", []),
            ("seated_reading_document", "host_seated_reading_document.png",
             "approved_scene_bound", ["desk"])):
        f = td / "character" / "poses" / fname
        _png(f)
        registry[pid] = {"id": pid, "path": f"character/poses/{fname}", "sha256": sha(f),
                         "dimensions": "256x256", "status": status, "direction": "front",
                         "negative_space": "both_sides", "includes_geometry": geom,
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


def use_real_channel(proj: Path) -> None:
    """Point a fixture project at the real, installed channel.

    Only for the subprocess CLI tests below: those run route_images.py as a real
    process against the actual repository, unpatched, so CHANNELS_DIR is the real
    channels/ directory and the fixture pack this module installs into a temp
    root is not on that path. The real pack's character reference already
    resolves against this repo's own character/, which is what a real subprocess
    sees anyway.
    """
    m = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
    m["channel_id"] = "interested_indian"
    m["channel_dna_version"] = 1
    (proj / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")


class World:
    def __init__(self, root):
        spec = root / "character" / "character_spec.json"
        self.ctxs = [
            mock.patch.multiple(cc, PIPELINE_DIR=root,
                                CHANNELS_DIR=root / "channels"),
            mock.patch.multiple(gate, PIPELINE_DIR=root, SPEC_PATH=spec),
            mock.patch.multiple(pose_registry, PIPELINE_DIR=root, SPEC_PATH=spec),
            mock.patch.multiple(composite_character, PIPELINE_DIR=root,
                                PREVIEW_DIR=root / "character" / "previews"),
        ]

    def __enter__(self):
        for c in self.ctxs:
            c.__enter__()
        return self

    def __exit__(self, *a):
        for c in reversed(self.ctxs):
            c.__exit__(*a)
        return False


def plan(root, proj) -> dict:
    with World(root):
        p = plan_visuals.build_plan(proj)
    (proj / plan_visuals.PLAN_JSON).write_text(
        json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
    (proj / plan_visuals.PLAN_MD).write_text(plan_visuals.render_md(p), encoding="utf-8")
    return p


def approve(root, proj, approver="Reviewer"):
    p = json.loads((proj / plan_visuals.PLAN_JSON).read_text(encoding="utf-8"))
    with World(root):
        return ac.write_approval(proj, approver,
                                 ac.confirmation_phrase(proj.name, p["plan_id"]))


def verdict(root, proj):
    with World(root):
        return gate.require_generation_ready(proj, "test", raise_on_block=False)


def blocked_on(rep, fragment):
    return any(fragment in b for b in rep.blockers)


def load_plan(proj):
    return json.loads((proj / plan_visuals.PLAN_JSON).read_text(encoding="utf-8"))


REPO_BEFORE = census(ROOT)

try:
    # ── 1. the routing input is bound ────────────────────────────────────────
    print("\n1. editing the prompts after approval blocks generation")
    root, proj = build_fixture()
    p = plan(root, proj)
    check("the plan records its routing input",
          p["inputs"]["prompts_sha256"] == sha(proj / route_images.PROMPTS_FILE))
    approve(root, proj)
    check("approved and ready", not verdict(root, proj).blockers,
          str(verdict(root, proj).blockers))
    rec = json.loads((proj / gate.APPROVAL_NAME).read_text(encoding="utf-8"))
    check("the approval records it too",
          rec["prompts_sha256"] == p["inputs"]["prompts_sha256"])

    # The exact defect: a free PHOTO route edited into a paid AI route.
    (proj / route_images.PROMPTS_FILE).write_text(
        PROMPTS_OK.replace("TYPE: PHOTO", "TYPE: CARTOON"), encoding="utf-8")
    rep = verdict(root, proj)
    check("blocked once the prompts change",
          blocked_on(rep, "routing input is unchanged since approval"), str(rep.blockers))

    touched = []
    with World(root), \
         mock.patch.object(route_images, "run_pexels",
                           lambda *a, **k: touched.append("pexels") or True), \
         mock.patch.object(route_images, "run_ai_batch",
                           lambda *a, **k: touched.append("ai") or True), \
         mock.patch.object(route_images, "run_map",
                           lambda *a, **k: touched.append("map") or True), \
         mock.patch.object(route_images, "run_host",
                           lambda *a, **k: touched.append("host") or True):
        try:
            route_images.dispatch_routes(proj, ROOT, load_plan(proj), overwrite=True)
            check("dispatch refused", False, "it dispatched")
        except gate.GateBlocked:
            check("dispatch refused", True)
    check("no generator ran", not touched, str(touched))
    check("no images directory was created", not (proj / "images").exists())

    print("\n   dispatch executes the plan, not the prompts file")
    src = ast.parse((ROOT / "route_images.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(src)
              if isinstance(n, ast.FunctionDef) and n.name == "dispatch_routes")
    body = ast.unparse(fn)
    check("dispatch_routes never parses the prompts file",
          "parse_shots" not in body and "PROMPTS_FILE" not in body)
    check("dispatch_routes takes a plan", "plan" in [a.arg for a in fn.args.args])

    # ── 2. no unknown route reaches AI ───────────────────────────────────────
    print("\n2. missing, misspelled and future TYPEs all need review")
    odd = (
        '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` '
        'NARRATION: "one" PROMPT: a thing\n'
        '**SHOT 02** · SCENE-002 · standalone → `SCENE-002.png` TYPE: CARTOOON '
        'NARRATION: "two" PROMPT: a thing\n'
        '**SHOT 03** · SCENE-003 · standalone → `SCENE-003.png` TYPE: TIMELINE '
        'NARRATION: "three" PROMPT: a chronology\n'
        '**SHOT 04** · SCENE-004 · standalone → `SCENE-004.png` TYPE: PHOTO '
        'NARRATION: "four" PROMPT: bar chart of something\n'
    )
    root, proj = build_fixture(odd)
    with World(root):
        shots = route_images.parse_shots(proj / route_images.PROMPTS_FILE)
    for s, label in zip(shots[:3], ["missing", "misspelled", "unsupported future"]):
        check(f"{label} TYPE is UNCLASSIFIED",
              s["type"] == route_images.UNCLASSIFIED, s["type"])
        check(f"{label} TYPE needs review", s["needs_review"])
        check(f"{label} TYPE never becomes CARTOON", s["type"] != "CARTOON")
    check("a keyword hit is recorded as a candidate, not a decision",
          shots[0]["route_candidates"] == [] and shots[3]["type"] == "PHOTO",
          str(shots[0]["route_candidates"]))
    p = plan(root, proj)
    check("the plan carries all three for review", len(p["needs_review"]) == 3,
          str([r["reason"] for r in p["needs_review"]]))

    print("\n   an unknown route handed straight to dispatch is refused")
    root, proj = build_fixture()
    p = plan(root, proj)
    approve(root, proj)
    tampered = load_plan(proj)
    tampered["shots"][1]["visual_type"] = "HOLOGRAM"
    touched = []
    with World(root), \
         mock.patch.object(route_images, "run_pexels",
                           lambda *a, **k: touched.append("pexels") or True), \
         mock.patch.object(route_images, "run_ai_batch",
                           lambda *a, **k: touched.append("ai") or True), \
         mock.patch.object(route_images, "run_map",
                           lambda *a, **k: touched.append("map") or True), \
         mock.patch.object(route_images, "run_host",
                           lambda *a, **k: touched.append("host") or True):
        try:
            route_images.dispatch_routes(proj, ROOT, tampered, overwrite=True)
            check("refused", False, "it dispatched")
        except route_images.RouteError as e:
            check("refused", "HOLOGRAM" in str(e), str(e))
    check("nothing ran, including the AI batch", not touched, str(touched))
    check("no images directory was created", not (proj / "images").exists())
    check("there is no default bucket in the source",
          "buckets.get(" not in (ROOT / "route_images.py").read_text(encoding="utf-8"))
    check("only named types may reach AI",
          route_images.AI_TYPES == frozenset({"CARTOON"}), str(route_images.AI_TYPES))

    print("\n   an invalid route LATE in the plan stops before the first generator")
    root, proj = build_fixture()
    plan(root, proj)
    approve(root, proj)
    late = load_plan(proj)
    late["shots"][-1]["visual_type"] = "SOMETHING_NEW"   # the HOST shot, last
    touched = []
    with World(root), \
         mock.patch.object(route_images, "run_pexels",
                           lambda *a, **k: touched.append("pexels") or True), \
         mock.patch.object(route_images, "run_map",
                           lambda *a, **k: touched.append("map") or True):
        try:
            route_images.dispatch_routes(proj, ROOT, late, overwrite=True)
            check("refused", False, "it dispatched")
        except route_images.RouteError:
            check("refused", True)
    check("the earlier valid shots were never generated", not touched, str(touched))

    # ── 3. runtime failures persist and invalidate ───────────────────────────
    print("\n3. a route failure stops the run, persists, and kills the approval")
    root, proj = build_fixture()
    plan(root, proj)
    approve(root, proj)
    calls = {"pexels": 0, "ai": 0, "map": 0, "host": 0}

    def counter(key, result):
        def fn(*a, **k):
            calls[key] += 1
            return result
        return fn

    with World(root), \
         mock.patch.object(route_images, "run_pexels", counter("pexels", False)), \
         mock.patch.object(route_images, "run_ai_batch", counter("ai", True)), \
         mock.patch.object(route_images, "run_map", counter("map", True)), \
         mock.patch.object(route_images, "run_host", counter("host", True)):
        code = route_images.dispatch_routes(proj, ROOT, load_plan(proj), overwrite=True)
    check("dispatch reports failure", code == 1, f"exit={code}")
    check("the PHOTO route was attempted", calls["pexels"] == 1)
    check("the AI batch never ran", calls["ai"] == 0, f"ai={calls['ai']}")
    check("nothing after the failure ran either",
          calls["map"] == 0 and calls["host"] == 0, str(calls))

    outstanding = route_failures.unresolved(proj)
    check("the failure is persisted", len(outstanding) == 1, str(outstanding))
    f = outstanding[0]
    check("keyed on the persistent visual asset id",
          f["visual_asset_id"] == "VIS-001-A", f["visual_asset_id"])
    for field in ("planned_route", "route_args_sha256", "reason", "failed_at",
                  "status", "shot", "file"):
        check(f"records {field}", f.get(field) is not None)
    check("it names the planned route, not a substitute",
          f["planned_route"] == "PHOTO", f["planned_route"])

    rep = verdict(root, proj)
    check("the old approval no longer permits dispatch",
          blocked_on(rep, "no unresolved route failures"), str(rep.blockers))
    with World(root), \
         mock.patch.object(route_images, "run_ai_batch", counter("ai", True)):
        try:
            route_images.dispatch_routes(proj, ROOT, load_plan(proj), overwrite=True)
            check("a second dispatch is refused", False, "it dispatched")
        except gate.GateBlocked:
            check("a second dispatch is refused", True)
    check("still no AI call", calls["ai"] == 0)

    p2 = plan(root, proj)
    check("the next plan carries the failure in needs_review",
          any(r.get("visual_asset_id") == "VIS-001-A" for r in p2["needs_review"]),
          str(p2["needs_review"]))
    try:
        approve(root, proj)
        check("it cannot be approved while outstanding", False, "it approved")
    except ac.ApprovalRefused as e:
        check("it cannot be approved while outstanding",
              "review item" in str(e) or "route failure" in str(e), str(e)[:120])

    print("\n   a transient resolution does not revive the old approval")
    root, proj = build_fixture()
    plan(root, proj)
    first = approve(root, proj)
    first_bytes = Path(first).read_bytes()
    with World(root), \
         mock.patch.object(route_images, "run_pexels", lambda *a, **k: False), \
         mock.patch.object(route_images, "run_ai_batch", lambda *a, **k: True):
        route_images.dispatch_routes(proj, ROOT, load_plan(proj), overwrite=True)
    rev_before = route_failures.revision(proj)
    route_failures.resolve(proj, "VIS-001-A", "Reviewer", "Pexels was down",
                           route_failures.TRANSIENT_RETRY)
    check("the revision moved", route_failures.revision(proj) > rev_before)
    check("no failures outstanding", not route_failures.unresolved(proj))
    check("the history is preserved",
          len(route_failures.load(proj)["failures"]) == 1
          and route_failures.load(proj)["failures"][0]["status"] == "resolved")
    hist = route_failures.load(proj)["failures"][0]
    for field in ("resolved_at", "resolved_by", "resolution_reason", "resolution_type"):
        check(f"the record keeps {field}", bool(hist.get(field)))
    check("the approval file is untouched", Path(first).read_bytes() == first_bytes)
    rep = verdict(root, proj)
    check("but the approval is no longer valid",
          blocked_on(rep, "current with the failure record"), str(rep.blockers))

    print("\n   changing the route resolves automatically and keeps the history")
    root, proj = build_fixture()
    plan(root, proj)
    approve(root, proj)
    with World(root), \
         mock.patch.object(route_images, "run_pexels", lambda *a, **k: False), \
         mock.patch.object(route_images, "run_ai_batch", lambda *a, **k: True):
        route_images.dispatch_routes(proj, ROOT, load_plan(proj), overwrite=True)
    old_plan_id = load_plan(proj)["plan_id"]
    (proj / route_images.PROMPTS_FILE).write_text(
        PROMPTS_OK.replace(
            '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` TYPE: PHOTO '
            'NARRATION: "one" PROMPT: a building',
            '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` TYPE: MAP '
            'MAP_ARGS: --highlight Goa NARRATION: "one" PROMPT: a map'),
        encoding="utf-8")
    p3 = plan(root, proj)
    check("the failure auto-resolved on the route change",
          not route_failures.unresolved(proj))
    check("the plan says so", p3["auto_resolved_failures"] == ["VIS-001-A"],
          str(p3["auto_resolved_failures"]))
    rec = route_failures.load(proj)["failures"][0]
    check("history kept with both hashes",
          rec["resolution_type"] == route_failures.ROUTE_CHANGED
          and rec["old_route_args_sha256"] != rec["new_route_args_sha256"])
    check("a new plan id was issued", p3["plan_id"] != old_plan_id)
    rep = verdict(root, proj)
    check("the old approval is still dead", bool(rep.blockers), str(rep.blockers))
    approve(root, proj)
    check("and a fresh approval works", not verdict(root, proj).blockers,
          str(verdict(root, proj).blockers))

    print("\n   resolution lives outside approve_checkpoint.py")
    ac_src = (ROOT / "approve_checkpoint.py").read_text(encoding="utf-8")
    check("approve_checkpoint has no --clear-failure", "clear-failure" not in ac_src)
    check("and does not resolve failures",
          "route_failures.resolve" not in ac_src)
    check("route_failures.py provides the command",
          "--resolve" in (ROOT / "route_failures.py").read_text(encoding="utf-8"))

    # ── 4. identity in the executable plan ───────────────────────────────────
    print("\n4. the plan must carry persistent artwork identity")
    root, proj = build_fixture()
    plan(root, proj)
    approve(root, proj)
    for mutate, fragment in (
            (lambda pl: pl["shots"][0].pop("visual_asset_id"), "visual_asset_id"),
            (lambda pl: pl["shots"][1].update(
                visual_asset_id=pl["shots"][0]["visual_asset_id"]), "duplicate"),
            (lambda pl: pl["shots"][0].update(visual_asset_id="VIS-999-Z"), "manifest")):
        bad = load_plan(proj)
        mutate(bad)
        try:
            with World(root):
                route_images.dispatch_routes(proj, ROOT, bad, overwrite=True)
            check(f"refused ({fragment})", False, "it dispatched")
        except (route_images.RouteError, gate.GateBlocked) as e:
            check(f"refused ({fragment})", fragment in str(e), str(e)[:140])

    print("\n   a shot that cannot be reconciled needs review")
    root, proj = build_fixture(
        PROMPTS_OK + '**SHOT 05** · SCENE-099 · standalone → `SCENE-099.png` '
                     'TYPE: CARTOON NARRATION: "five" PROMPT: orphan\n')
    p = plan(root, proj)
    orphan = [s for s in p["shots"] if s["shot"] == 5][0]
    check("the orphan has no visual_asset_id", orphan["visual_asset_id"] is None)
    check("and is queued for review",
          any(r.get("shot") == 5 for r in p["needs_review"]), str(p["needs_review"]))
    try:
        approve(root, proj)
        check("it cannot be approved", False, "it approved")
    except ac.ApprovalRefused:
        check("it cannot be approved", True)

    # ── 5. the reviewed document is bound ────────────────────────────────────
    print("\n5. the markdown a human reads is bound to the plan")
    root, proj = build_fixture()
    plan(root, proj)
    md = proj / plan_visuals.PLAN_MD
    text = md.read_text(encoding="utf-8")
    p = load_plan(proj)
    check("the document shows the plan id", p["plan_id"] in text)
    check("and when it was generated", p["generated_at"] in text)
    check("and the spend", "Paid generation" in text)
    check("and the mix", "## Mix" in text)

    md.write_text(text + "\n<!-- approved, honest -->\n", encoding="utf-8")
    try:
        approve(root, proj)
        check("a tampered document cannot be approved", False, "it approved")
    except ac.ApprovalRefused as e:
        check("a tampered document cannot be approved",
              "does not match a fresh render" in str(e), str(e)[:120])

    root, proj = build_fixture()
    plan(root, proj)
    approve(root, proj)
    check("valid to begin with", not verdict(root, proj).blockers)
    md = proj / plan_visuals.PLAN_MD
    md.write_text(md.read_text(encoding="utf-8") + "\n<!-- later -->\n", encoding="utf-8")
    check("editing it afterwards invalidates the approval",
          blocked_on(verdict(root, proj), "reviewed plan document is unchanged"),
          str(verdict(root, proj).blockers))

    print("\n   the confirmation phrase names the plan")
    root, proj = build_fixture()
    p_a = plan(root, proj)
    phrase_a = ac.confirmation_phrase(proj.name, p_a["plan_id"])
    check("the phrase carries the plan id", p_a["plan_id"][:8] in phrase_a)
    p_b = plan(root, proj)          # re-planned between review and approval
    try:
        with World(root):
            ac.write_approval(proj, "Reviewer", phrase_a)
        check("plan A's phrase cannot approve plan B", False, "it approved")
    except ac.ApprovalRefused as e:
        check("plan A's phrase cannot approve plan B",
              p_b["plan_id"][:8] in str(e), str(e)[:160])
    check("nothing was written", not (proj / gate.APPROVAL_NAME).exists())

    # ── 6. exit codes ────────────────────────────────────────────────────────
    print("\n6. the router CLI exits nonzero when it fails")
    check("main()'s return value is not discarded",
          "sys.exit(main())" in (ROOT / "route_images.py").read_text(encoding="utf-8"))

    def run_cli(root, proj, *args, env_extra=None):
        env = {**dict(__import__("os").environ), "PYTHONIOENCODING": "utf-8",
               "PYTHONPATH": str(ROOT), **(env_extra or {})}
        return subprocess.run(
            [sys.executable, str(ROOT / "route_images.py"), "--project", str(proj), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    # A real subprocess sees the real character spec, so the fixture project must
    # reference poses that exist there. The MAP/PHOTO/CARTOON shots do not. It
    # also sees the real channels/ directory, not the fixture pack World patches
    # in — so these projects point at the real installed channel instead.
    root, proj = build_fixture(
        '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` TYPE: MAP '
        'NARRATION: "one" PROMPT: a map\n')          # MAP with no args -> review
    use_real_channel(proj)
    r = run_cli(root, proj, "--dry-run")
    check("a dry run with review items exits 0 (it only reports)", r.returncode == 0,
          r.stdout[-300:])
    r = run_cli(root, proj)
    check("classification with NEEDS_REVIEW exits nonzero", r.returncode == 1,
          f"exit={r.returncode}: {r.stdout[-300:]}")

    root, proj = build_fixture(
        '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` TYPE: CARTOON '
        'NARRATION: "one" PROMPT: a thing\n')
    use_real_channel(proj)
    r = run_cli(root, proj, "--dry-run")
    check("a clean dry run exits 0", r.returncode == 0, r.stdout[-300:])
    r = run_cli(root, proj)
    check("no plan at all exits nonzero", r.returncode == 1,
          f"exit={r.returncode}: {r.stdout[-300:]}")
    check("and says a plan is needed first",
          "visual_plan.json" in r.stdout, r.stdout[-300:])

    # With a plan but no approval, the gate itself is what refuses. Planning goes
    # back through World(root), so the project reverts to the fixture channel
    # that lives on that patched path.
    m = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
    channel_fixture.stamp(m)
    (proj / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    plan(root, proj)
    use_real_channel(proj)
    r = run_cli(root, proj)
    check("a missing approval exits nonzero", r.returncode == 1,
          f"exit={r.returncode}: {r.stdout[-400:]}")
    check("and it names the checkpoint", "Checkpoint 3 approval" in r.stdout,
          r.stdout[-400:])

    print("\n   an approved dispatch exits 0, and a runtime failure exits nonzero")
    root, proj = build_fixture()
    plan(root, proj)
    approve(root, proj)
    with World(root), \
         mock.patch.object(route_images, "run_pexels", lambda *a, **k: True), \
         mock.patch.object(route_images, "run_map", lambda *a, **k: True), \
         mock.patch.object(route_images, "run_host", lambda *a, **k: True), \
         mock.patch.object(route_images, "run_ai_batch", lambda *a, **k: True):
        code = route_images.dispatch_routes(proj, ROOT, load_plan(proj), overwrite=True)
    check("a fully successful dispatch returns 0", code == 0, f"exit={code}")

    root, proj = build_fixture()
    plan(root, proj)
    approve(root, proj)
    with World(root), \
         mock.patch.object(route_images, "run_pexels", lambda *a, **k: False), \
         mock.patch.object(route_images, "run_ai_batch", lambda *a, **k: True):
        code = route_images.dispatch_routes(proj, ROOT, load_plan(proj), overwrite=True)
    check("a runtime failure returns nonzero", code == 1, f"exit={code}")

    # ── 7. the real projects ─────────────────────────────────────────────────
    print("\n7. the real projects are where they should be")
    rep_id = gate.require_identity_ready(ROOT / "pilot_neet_scandal", "x",
                                         raise_on_block=False)
    check("pilot identity blocked on SCENE-066", blocked_on(rep_id, "SCENE-066"),
          str(rep_id.blockers))
    check("pilot generation blocked too",
          bool(gate.require_generation_ready(ROOT / "pilot_neet_scandal", "x",
                                             raise_on_block=False).blockers))
    check("pilot has no approval",
          not (ROOT / "pilot_neet_scandal" / gate.APPROVAL_NAME).exists())
    check("pilot has no route failures",
          not route_failures.unresolved(ROOT / "pilot_neet_scandal"))
    check("test_2min identity is ready",
          not gate.require_identity_ready(ROOT / "test_2min", "x",
                                          raise_on_block=False).blockers)
    check("test_2min generation blocked for want of approval",
          blocked_on(gate.require_generation_ready(ROOT / "test_2min", "x",
                                                   raise_on_block=False),
                     "Checkpoint 3 approval exists"))

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
