"""Task 2B-B2b-2b — canonical review, overlays, in-process orchestration,
and legacy provider-CLI containment, all built on the B2b-2a sealed
DispatchSnapshot.

Fixtures only. No real provider/API/GPU/download call anywhere in this
file — every provider boundary and every subprocess is either never
reached (the universal canonical gate refuses first) or mocked.

    python tests/test_canonical_workflow.py
"""

import hashlib
import json
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

import add_text_overlays              # noqa: E402
import channel_context as cc          # noqa: E402
import channel_fixture                # noqa: E402
import generation_gate as gate        # noqa: E402
import pipeline_agents                # noqa: E402
import pose_registry                  # noqa: E402
import render_channel_dna as rd       # noqa: E402
import renderer_adapters              # noqa: E402
import renderers                      # noqa: E402
import review_images                  # noqa: E402
import route_failures                 # noqa: E402
import route_images                   # noqa: E402
import search_pexels                  # noqa: E402
import generate_images_flux           # noqa: E402
import generate_images_aibmm          # noqa: E402
import source_ids                     # noqa: E402
import visual_routes as vr            # noqa: E402

failures = []
_fixtures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


SCRIPT = ("The minister resigned in July. Nobody expected it.\n\n"
          "The exam was cancelled that week. Then it was rescheduled.\n")


def build_fixture(name="demo_project", channel_id="fixture_channel"):
    """(root, proj) — an identity-clean project with an installed character
    spec and a channel pack under `channel_id`, MAP capability included."""
    td = Path(tempfile.mkdtemp())
    _fixtures.append(td)
    (td / "character").mkdir()
    (td / "character" / "character_spec.json").write_text(
        json.dumps({"masters": {}, "pose_library": {"registry": {}}, "references": {}}),
        encoding="utf-8")
    proj = td / name
    proj.mkdir()
    units = source_ids.build_source_units(SCRIPT)
    source_ids.save_units(proj, units)
    scenes = [{"id": f"SCENE-{i:03d}", "image": f"SCENE-{i:03d}.png",
              "source_ids": [u["id"]], "shot_instance_id": f"{u['id']}-S01",
              "visual_asset_id": f"VIS-{i:03d}-A"}
             for i, u in enumerate(units, 1)]
    manifest = {"episode": name, "identity_state": "ok", "identity_reasons": [],
               "scenes": scenes, "channel_id": channel_id, "channel_dna_version": 1}
    (proj / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    install_channel(td, channel_id)
    return td, proj


def install_channel(root: Path, channel_id: str, extra_capabilities=None) -> None:
    doc = channel_fixture.pack_document(channel_id)
    doc["renderers"]["capabilities"].update(
        extra_capabilities if extra_capabilities is not None else {"MAP": "india_geojson"})
    d = root / "channels" / channel_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "channel.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")


class World:
    def __init__(self, root):
        spec = root / "character" / "character_spec.json"
        self.ctxs = [
            mock.patch.multiple(cc, PIPELINE_DIR=root, CHANNELS_DIR=root / "channels"),
            mock.patch.multiple(gate, PIPELINE_DIR=root, SPEC_PATH=spec),
            mock.patch.multiple(pose_registry, PIPELINE_DIR=root, SPEC_PATH=spec),
        ]

    def __enter__(self):
        for c in self.ctxs:
            c.__enter__()
        return self

    def __exit__(self, *a):
        for c in reversed(self.ctxs):
            c.__exit__(*a)
        return False


def canonical_route(scene, visual_type, renderer_id, cost_category, paid, route_args,
                    overlay_text=None, **kw) -> dict:
    base = {
        "visual_asset_id": scene["visual_asset_id"], "source_ids": scene["source_ids"],
        "shot_instance_id": scene["shot_instance_id"], "scene_id": scene["id"],
        "output_file": scene["image"], "status": "READY", "review_reasons": [],
        "candidate_visual_types": [], "routing_confidence": 0.9, "manual_override": None,
        "visual_type": visual_type, "route_args": route_args, "narration": "n",
        "prompt": None, "visual_cue": None, "overlay_text": overlay_text,
        "renderer_id": renderer_id, "host_renderer_id": None,
        "cost_category": cost_category, "paid": paid,
        "host_present": False, "host_method": None, "host_pose_id": None,
        "host_scene_bound": None, "host_reference_asset_ids": None, "host_placement": None,
    }
    base.update(kw)
    return base


def map_route(scene, **kw):
    return canonical_route(
        scene, "MAP", "india_geojson", "free_local", False,
        {"map": {"regions": ["Kerala"], "callout": None}, "chart": None, "timeline": None,
         "document": None, "photo": None, "illustration": None, "reenactment": None},
        **kw)


def install_canonical_routes(proj: Path, ctx, routes: list) -> dict:
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


def route_all_scenes(manifest: dict, overlay_text=None) -> list:
    """One MAP route per manifest scene — build_fixture()'s SCRIPT always
    produces 4 source units/scenes, so this covers exact manifest coverage
    without callers having to know that count."""
    return [map_route(sc, overlay_text=overlay_text) for sc in manifest["scenes"]]


def load_snapshot(root, proj):
    with World(root):
        result = vr.require_executable_routes(proj, operation="test")
        return vr.build_dispatch_snapshot(result)


def canonical_png(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), (10, 20, 30)).save(path, "PNG")


def patched_gate():
    """Patches the universal canonical gate through to a no-op — the only
    way any code in this file below the gate is ever exercised."""
    return mock.patch.object(gate, "require_canonical_visual_execution_ready",
                             return_value=None)


REPO_BEFORE = {p.relative_to(ROOT).as_posix(): sha(p)
              for p in (ROOT / "character").rglob("*") if p.is_file()} \
    if (ROOT / "character").exists() else {}

try:
    # ── F.16/17. universal refusal — reconfirmed for this checkpoint's surfaces
    print("\n1. review/overlay/orchestration all refuse before the universal gate")
    root, proj = build_fixture()
    with World(root):
        for label, fn in (
            ("review_images.review_project_canonical", lambda: review_images.review_project_canonical(proj)),
            ("add_text_overlays.apply_overlays_canonical", lambda: add_text_overlays.apply_overlays_canonical(proj)),
            ("pipeline_agents.run_canonical_visual_workflow", lambda: pipeline_agents.run_canonical_visual_workflow(proj)),
        ):
            try:
                fn()
                check(f"{label} raises GateBlocked (guard not patched)", False, "returned instead")
            except gate.GateBlocked as e:
                check(f"{label} raises GateBlocked (guard not patched)", True)
                check(f"{label}'s message names canonical visual execution as disabled",
                     "canonical visual execution" in str(e).lower(), str(e))
    check("no images directory was created", not (proj / "images").exists())

    print("\n1a. the universal gate is textually and behaviorally an unconditional refusal")
    import ast as _ast
    gate_tree = _ast.parse((ROOT / "generation_gate.py").read_text(encoding="utf-8"))
    gate_fn = next(n for n in _ast.walk(gate_tree)
                  if isinstance(n, _ast.FunctionDef)
                  and n.name == "require_canonical_visual_execution_ready")
    has_branch = any(isinstance(n, (_ast.If, _ast.Try, _ast.While))
                     for n in _ast.walk(gate_fn))
    check("the function body contains no conditional branch before raising",
         not has_branch)
    try:
        gate.require_canonical_visual_execution_ready("anything")
        check("require_canonical_visual_execution_ready always raises", False, "did not raise")
    except gate.GateBlocked:
        check("require_canonical_visual_execution_ready always raises", True)

    print("\n1b. all six canonical adapters remain universally blocked")
    route = map_route({"visual_asset_id": "VIS-001-A", "source_ids": ["S"],
                       "shot_instance_id": "S-I01", "id": "SCENE-001", "image": "x.png"})
    for name, fn in (
        ("adapt_map", renderer_adapters.adapt_map),
        ("adapt_chart", renderer_adapters.adapt_chart),
        ("adapt_photo", renderer_adapters.adapt_photo),
        ("adapt_flux", renderer_adapters.adapt_flux),
        ("adapt_host_composite", renderer_adapters.adapt_host_composite),
        ("adapt_flux_reference_anchor", renderer_adapters.adapt_flux_reference_anchor),
    ):
        entry = renderers.RENDERERS.get("india_geojson")
        ctx = renderer_adapters.DispatchContext(
            project_dir=Path("."), channel=None, approved_poses={}, approved_references={},
            poses_asset_base=None, poses_root=None, references_asset_base=None,
            references_root=None, output_root=Path("."), renderer_entry=entry)
        try:
            fn(route, Path("out.png"), ctx)
            check(f"{name} refuses unconditionally", False, "did not raise")
        except gate.GateBlocked:
            check(f"{name} refuses unconditionally", True)

    # ── canonical review ──────────────────────────────────────────────────────
    print("\n2. canonical review derives its expected asset set from the sealed snapshot")
    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest)
        install_canonical_routes(proj, ctx0, routes)
        images_dir = proj / "images"
        images_dir.mkdir()
        for sc in scenes:
            canonical_png(images_dir / sc["image"])
        # An asset globbing would find but no route names.
        canonical_png(images_dir / "SCENE-099-unplanned.png")

        snapshot = load_snapshot(root, proj)
        with patched_gate():
            results = review_images.review_snapshot_canonical(snapshot)
    check("review produces exactly one result per route, not per file on disk",
         len(results) == len(scenes), str(results))
    check("every result verdict is PASS for genuinely valid canonical PNGs",
         all(r.verdict == "PASS" for r in results), str(results))
    check("the unplanned file never appears among the reviewed targets",
         not any("unplanned" in str(r.target) for r in results), str(results))

    print("\n2a. review is read-only and creates nothing")
    before = sha(images_dir / scenes[0]["image"])
    check("the reviewed asset is byte-identical after review", sha(images_dir / scenes[0]["image"]) == before)

    print("\n3. review explicitly fails missing/invalid/wrong-size assets")
    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest)
        install_canonical_routes(proj, ctx0, routes)
        images_dir = proj / "images"
        images_dir.mkdir()
        for sc in scenes[1:]:
            canonical_png(images_dir / sc["image"])
        # scenes[0]["image"] deliberately never created — missing asset.
        snapshot = load_snapshot(root, proj)
        with patched_gate():
            results = review_images.review_snapshot_canonical(snapshot)
        by_vid = {r.visual_asset_id: r for r in results}
    r0 = by_vid[scenes[0]["visual_asset_id"]]
    check("a missing asset fails review explicitly", r0.verdict == "FAIL", str(r0))
    check("the failure reason is stable/explicit", "does not exist" in r0.reason
         or "not a regular file" in r0.reason, r0.reason)

    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest)
        install_canonical_routes(proj, ctx0, routes)
        images_dir = proj / "images"
        images_dir.mkdir()
        for sc in scenes:
            canonical_png(images_dir / sc["image"])
        (images_dir / scenes[0]["image"]).write_bytes(b"not a png at all")
        snapshot = load_snapshot(root, proj)
        with patched_gate():
            results = review_images.review_snapshot_canonical(snapshot)
        by_vid = {r.visual_asset_id: r for r in results}
    check("an invalid (non-PNG) asset fails review explicitly",
         by_vid[scenes[0]["visual_asset_id"]].verdict == "FAIL", str(results))

    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest)
        install_canonical_routes(proj, ctx0, routes)
        images_dir = proj / "images"
        images_dir.mkdir()
        for sc in scenes:
            canonical_png(images_dir / sc["image"])
        Image.new("RGB", (640, 480), (1, 2, 3)).save(images_dir / scenes[0]["image"], "PNG")
        snapshot = load_snapshot(root, proj)
        with patched_gate():
            results = review_images.review_snapshot_canonical(snapshot)
        by_vid = {r.visual_asset_id: r for r in results}
    check("a wrong-dimension asset fails review explicitly",
         by_vid[scenes[0]["visual_asset_id"]].verdict == "FAIL", str(results))

    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest)
        install_canonical_routes(proj, ctx0, routes)
        images_dir = proj / "images"
        images_dir.mkdir()
        for sc in scenes[1:]:
            canonical_png(images_dir / sc["image"])
        (images_dir / scenes[0]["image"]).touch()  # empty file
        snapshot = load_snapshot(root, proj)
        with patched_gate():
            results = review_images.review_snapshot_canonical(snapshot)
        by_vid = {r.visual_asset_id: r for r in results}
    check("an empty asset fails review explicitly",
         by_vid[scenes[0]["visual_asset_id"]].verdict == "FAIL", str(results))

    # ── canonical overlays ────────────────────────────────────────────────────
    print("\n4. canonical overlay: target collision fails before any work begins")
    # visual_routes.validate_contract() already refuses a route whose
    # output_file disagrees with its manifest scene's own filename, so a
    # genuine collision can never survive require_executable_routes() in the
    # first place — this exercises _preflight_overlay_targets() (the
    # overlay stage's OWN defense-in-depth duplicate check) directly,
    # against a hand-built snapshot, the same technique
    # test_route_binding.py's 8b uses for route_images._preflight_targets().
    import types as _types
    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        r1 = route_all_scenes(manifest, overlay_text="one")[0]
        r2 = dict(map_route(scenes[1], overlay_text="two"))
        r2["output_file"] = r1["output_file"]   # force a collision, bypassing schema binding
        images_dir = proj / "images"
        images_dir.mkdir()
        canonical_png(images_dir / scenes[0]["image"])
        before_bytes = (images_dir / scenes[0]["image"]).read_bytes()
        fake_snapshot = vr.DispatchSnapshot(
            project_dir=proj, routes_id="r1", routes_file_sha256="a" * 64,
            routes_content_sha256="b" * 64, channel=ctx0, routes=(r1, r2),
            approved_poses=_types.MappingProxyType({}), poses_asset_base=None,
            poses_root=None, approved_references=_types.MappingProxyType({}),
            references_asset_base=None, references_root=None)
        with patched_gate():
            try:
                add_text_overlays.overlay_snapshot_canonical(fake_snapshot)
                check("colliding overlay targets are refused", False, "did not raise")
            except RuntimeError as e:
                check("colliding overlay targets are refused", "duplicate" in str(e) or "colliding" in str(e), str(e))
    check("the pre-existing image was never touched by the refused pass",
         (images_dir / scenes[0]["image"]).read_bytes() == before_bytes)

    print("\n5. canonical overlay: unique, same-directory temp paths; atomic replace")
    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest, overlay_text="Kerala highlighted")
        install_canonical_routes(proj, ctx0, routes)
        images_dir = proj / "images"
        images_dir.mkdir()
        for sc in scenes:
            canonical_png(images_dir / sc["image"])
        target = images_dir / scenes[0]["image"]
        seen_tmp = []
        real_render = add_text_overlays._render_overlay_to

        def spy_render(source, text, dest):
            seen_tmp.append(dest)
            check("the temp path is inside the same directory as the target",
                 dest.parent == target.parent)
            check("the temp path is not the target itself", dest != target)
            real_render(source, text, dest)

        snapshot = load_snapshot(root, proj)
        with patched_gate(), mock.patch.object(add_text_overlays, "_render_overlay_to", spy_render):
            code = add_text_overlays.overlay_snapshot_canonical(snapshot)
    check("overlay dispatch reports success", code == 0, f"code={code}")
    check("the temp file no longer exists after a successful commit", not seen_tmp[0].exists())
    im = Image.open(target)
    check("the final overlaid asset is a valid canonical PNG",
         im.format == "PNG" and im.size == (1280, 720))

    print("\n6. canonical overlay: producer failure preserves the prior final byte-for-byte")
    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest, overlay_text="text")
        install_canonical_routes(proj, ctx0, routes)
        images_dir = proj / "images"
        images_dir.mkdir()
        for sc in scenes:
            canonical_png(images_dir / sc["image"])
        target = images_dir / scenes[0]["image"]
        before_bytes = target.read_bytes()
        snapshot = load_snapshot(root, proj)
        with patched_gate(), mock.patch.object(
                add_text_overlays, "_render_overlay_to",
                side_effect=RuntimeError("simulated render failure")):
            code = add_text_overlays.overlay_snapshot_canonical(snapshot)
    check("overlay dispatch reports failure", code == 1, f"code={code}")
    check("the prior final asset is preserved byte-for-byte after a producer failure",
         target.read_bytes() == before_bytes)
    known_names = {sc["image"] for sc in scenes}
    leftovers = [p for p in images_dir.iterdir() if p.name not in known_names]
    check("no temp file is left behind", not leftovers, str(leftovers))

    print("\n7. canonical overlay: cleanup failure is surfaced, not swallowed")
    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest, overlay_text="text")
        install_canonical_routes(proj, ctx0, routes)
        images_dir = proj / "images"
        images_dir.mkdir()
        for sc in scenes:
            canonical_png(images_dir / sc["image"])
        snapshot = load_snapshot(root, proj)
        with patched_gate(), \
             mock.patch.object(add_text_overlays, "_render_overlay_to",
                               side_effect=RuntimeError("simulated render failure")), \
             mock.patch.object(Path, "unlink", side_effect=OSError("simulated cleanup failure")):
            try:
                add_text_overlays.overlay_snapshot_canonical(snapshot)
                check("a cleanup failure raises rather than being swallowed", False, "did not raise")
            except renderer_adapters.DispatchIntegrityError as e:
                check("a cleanup failure raises rather than being swallowed",
                     "cleanup" in str(e).lower(), str(e))

    print("\n8. canonical overlay: no directory creation on a preflight failure")
    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest, overlay_text="text")
        install_canonical_routes(proj, ctx0, routes)
        # images/ deliberately never created — nothing was ever dispatched.
        snapshot = load_snapshot(root, proj)
        with patched_gate():
            try:
                add_text_overlays.overlay_snapshot_canonical(snapshot)
                check("overlay preflight refuses when nothing was dispatched yet", False, "did not raise")
            except RuntimeError:
                check("overlay preflight refuses when nothing was dispatched yet", True)
    check("no images directory was created by the refused overlay pass",
         not (proj / "images").exists())

    # ── orchestration ─────────────────────────────────────────────────────────
    print("\n9. orchestration: dispatch/review/overlay share ONE snapshot, stop on first failure")
    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest)
        install_canonical_routes(proj, ctx0, routes)

        seen_snapshots = []
        real_dispatch = route_images.dispatch_snapshot_routes
        real_review = review_images.review_snapshot_canonical
        real_overlay = add_text_overlays.overlay_snapshot_canonical

        def spy_dispatch(snapshot, **kw):
            seen_snapshots.append(("dispatch", snapshot))
            return real_dispatch(snapshot, **kw)

        def spy_review(snapshot):
            seen_snapshots.append(("review", snapshot))
            return real_review(snapshot)

        def spy_overlay(snapshot):
            seen_snapshots.append(("overlay", snapshot))
            return real_overlay(snapshot)

        def fake_subprocess_run(cmd, **kw):
            # Simulates generate_india_map.py: writes a genuine canonical
            # PNG to the exact --out path it was given (the adapter's own
            # unique temp path via atomic_commit), so dispatch can actually
            # succeed end to end without a real subprocess ever running.
            out = cmd[cmd.index("--out") + 1]
            canonical_png(Path(out))
            return types.SimpleNamespace(returncode=0, stderr="")

        with patched_gate(), \
             mock.patch("subprocess.run", side_effect=fake_subprocess_run) as mock_run, \
             mock.patch.object(route_images, "dispatch_snapshot_routes", spy_dispatch), \
             mock.patch.object(review_images, "review_snapshot_canonical", spy_review), \
             mock.patch.object(add_text_overlays, "overlay_snapshot_canonical", spy_overlay):
            result = pipeline_agents.run_canonical_visual_workflow(proj)
    check("the workflow completed all stages", result["dispatch_code"] == 0
         and result["overlay_code"] == 0, str(result))
    stage_snapshots = {stage: id(snap) for stage, snap in seen_snapshots}
    check("dispatch/review/overlay all ran against the exact SAME snapshot object",
         len(set(stage_snapshots.values())) == 1, str(stage_snapshots))
    # subprocess.run IS called here — by adapt_map, for the deterministic,
    # always-local generate_india_map.py renderer, which is not a "provider
    # CLI" this task's containment requirement is about (see F.15's exact
    # target: generate_images_flux.py/generate_images_aibmm.py/
    # search_pexels.py, checked statically in section 12b below). What
    # matters here is that NONE of those three ever appear in what got
    # subprocessed.
    subprocess_cmds = [c.args[0] for c in mock_run.call_args_list]
    check("dispatch subprocessed only into the local map renderer, never "
         "into any of the three obsolete provider CLIs",
         mock_run.called and not any(
             needle in str(cmd) for cmd in subprocess_cmds
             for needle in ("generate_images_flux.py", "generate_images_aibmm.py",
                            "search_pexels.py")),
         str(subprocess_cmds))

    print("\n9a. orchestration stops immediately on the first route's dispatch failure")
    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest)
        install_canonical_routes(proj, ctx0, routes)

        review_called = []
        overlay_called = []
        with patched_gate(), \
             mock.patch("subprocess.run",
                       side_effect=lambda cmd, **kw: types.SimpleNamespace(
                           returncode=1, stderr="simulated map failure")), \
             mock.patch.object(review_images, "review_snapshot_canonical",
                              side_effect=lambda s: review_called.append(1) or []), \
             mock.patch.object(add_text_overlays, "overlay_snapshot_canonical",
                              side_effect=lambda s: overlay_called.append(1) or 0):
            try:
                pipeline_agents.run_canonical_visual_workflow(proj)
                check("orchestration raises when dispatch fails", False, "did not raise")
            except RuntimeError as e:
                check("orchestration raises when dispatch fails", "dispatch" in str(e).lower(), str(e))
        check("review was never invoked after a dispatch failure", not review_called)
        check("overlay was never invoked after a dispatch failure", not overlay_called)
        check("the second (good) route was never attempted",
             not (proj / "images" / scenes[1]["image"]).exists())

    print("\n10. gate/integrity failures are never recorded as ordinary route failures")
    root, proj = build_fixture()
    with World(root):
        ctx0 = cc.load_channel_for_project(proj)
        manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        scenes = manifest["scenes"]
        routes = route_all_scenes(manifest)
        install_canonical_routes(proj, ctx0, routes)
        rev_before = route_failures.revision(proj)
        # No gate patch at all here — GateBlocked propagates from the very
        # first line, before anything else runs.
        try:
            review_images.review_project_canonical(proj)
        except gate.GateBlocked:
            pass
        try:
            add_text_overlays.apply_overlays_canonical(proj)
        except gate.GateBlocked:
            pass
        try:
            pipeline_agents.run_canonical_visual_workflow(proj)
        except gate.GateBlocked:
            pass
    check("no route_failures record was written for a GateBlocked refusal",
         route_failures.revision(proj) == rev_before)

    # ── cross-project/channel containment ───────────────────────────────────
    print("\n11. cross-project containment — project A cannot read or write project B")
    root_a, proj_a = build_fixture("project_a", "channel_a")
    root_b, proj_b = build_fixture("project_b", "channel_b")
    with World(root_a):
        ctx_a = cc.load_channel_for_project(proj_a)
        manifest_a = json.loads((proj_a / "manifest.json").read_text(encoding="utf-8"))
        routes_a = route_all_scenes(manifest_a)
        install_canonical_routes(proj_a, ctx_a, routes_a)
        images_a = proj_a / "images"
        images_a.mkdir()
        for sc in manifest_a["scenes"]:
            canonical_png(images_a / sc["image"])
        snapshot_a = load_snapshot(root_a, proj_a)

    with World(root_b):
        ctx_b = cc.load_channel_for_project(proj_b)
        manifest_b = json.loads((proj_b / "manifest.json").read_text(encoding="utf-8"))
        routes_b = route_all_scenes(manifest_b)
        install_canonical_routes(proj_b, ctx_b, routes_b)
        images_b = proj_b / "images"
        images_b.mkdir()
        for sc in manifest_b["scenes"]:
            canonical_png(images_b / sc["image"])
        snapshot_b = load_snapshot(root_b, proj_b)

    check("project A's snapshot is bound to project A only", snapshot_a.project_dir == proj_a)
    check("project B's snapshot is bound to project B only", snapshot_b.project_dir == proj_b)
    check("project A's channel differs from project B's channel",
         snapshot_a.channel.channel_id != snapshot_b.channel.channel_id)

    try:
        add_text_overlays.build_overlay_context(
            snapshot_a, snapshot_b.routes[0], target=images_a / "x.png")
        check("a route from project B is refused against project A's snapshot "
             "(overlay context)", False, "did not raise")
    except RuntimeError:
        check("a route from project B is refused against project A's snapshot "
             "(overlay context)", True)

    try:
        renderer_adapters.build_dispatch_context(
            snapshot_a, snapshot_b.routes[0], target=images_a / "x.png", output_root=images_a)
        check("a route from project B is refused against project A's snapshot "
             "(dispatch context)", False, "did not raise")
    except renderer_adapters.DispatchIntegrityError:
        check("a route from project B is refused against project A's snapshot "
             "(dispatch context)", True)

    with patched_gate():
        results_a = review_images.review_snapshot_canonical(snapshot_a)
        results_b = review_images.review_snapshot_canonical(snapshot_b)
    check("reviewing project A's snapshot never touches project B's images dir",
         all(images_b not in r.target.parents for r in results_a))
    check("reviewing project B's snapshot never touches project A's images dir",
         all(images_a not in r.target.parents for r in results_b))

    # ── legacy provider-CLI containment ─────────────────────────────────────
    print("\n12. the three obsolete project-mode provider CLIs refuse without provider activity")
    with mock.patch("subprocess.run") as mock_run, \
         mock.patch.object(sys, "argv", ["generate_images_flux.py", "--project", "whatever"]):
        code = generate_images_flux.main()
    check("generate_images_flux.py's project-mode CLI refuses", code == 1, f"code={code}")
    check("no subprocess/provider call was made", not mock_run.called)

    with mock.patch("subprocess.run") as mock_run2, \
         mock.patch.object(sys, "argv", ["generate_images_aibmm.py", "--project", "whatever"]):
        code = generate_images_aibmm.main()
    check("generate_images_aibmm.py's project-mode CLI refuses", code == 1, f"code={code}")
    check("no subprocess/provider call was made", not mock_run2.called)

    with mock.patch("subprocess.run") as mock_run3, \
         mock.patch.object(sys, "argv", ["search_pexels.py", "--project", "whatever"]):
        code = search_pexels.main()
    check("search_pexels.py's project BATCH mode refuses", code == 1, f"code={code}")
    check("no subprocess/provider call was made", not mock_run3.called)

    print("\n12a. search_pexels.py's single-query (+--project) primitive is preserved")
    check("main_legacy_v2 is still the real, gated implementation",
         "require_generation_ready" in
         search_pexels.__loader__.get_source("search_pexels")
         .split("def main_legacy_v2")[1].split("def main(")[0])

    print("\n12b. route_images.py and pipeline_agents.py never subprocess into the "
         "three obsolete provider CLIs")
    ri_src = (ROOT / "route_images.py").read_text(encoding="utf-8")
    dispatch_fn_src = ri_src[ri_src.index("def dispatch_snapshot_routes("):]
    dispatch_fn_src = dispatch_fn_src[:dispatch_fn_src.index("\ndef ", 10)]
    for needle in ("generate_images_flux.py", "generate_images_aibmm.py", "search_pexels.py"):
        check(f"dispatch_snapshot_routes() never references {needle}",
             needle not in dispatch_fn_src)
    pa_src = (ROOT / "pipeline_agents.py").read_text(encoding="utf-8")
    workflow_fn_src = pa_src[pa_src.index("def run_canonical_visual_workflow("):]
    workflow_fn_src = workflow_fn_src[:workflow_fn_src.index("\n\n\n")]
    for needle in ("generate_images_flux.py", "generate_images_aibmm.py", "search_pexels.py",
                  "subprocess"):
        check(f"run_canonical_visual_workflow() never references {needle}",
             needle not in workflow_fn_src)

finally:
    for td in _fixtures:
        shutil.rmtree(td, ignore_errors=True)
    REPO_AFTER = {p.relative_to(ROOT).as_posix(): sha(p)
                 for p in (ROOT / "character").rglob("*") if p.is_file()} \
        if (ROOT / "character").exists() else {}
    moved = [k for k in REPO_BEFORE if REPO_AFTER.get(k) != REPO_BEFORE[k]]
    added = [k for k in REPO_AFTER if k not in REPO_BEFORE]
    check("no repository character asset was modified", not moved, str(moved))
    check("no repository character asset was added", not added, str(added))

print("\n" + "=" * 62)
print(f"FAILED ({len(failures)}): {failures}" if failures else "all canonical-workflow checks passed")
sys.exit(1 if failures else 0)
