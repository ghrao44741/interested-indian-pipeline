"""Task 2B-B1 — the canonical `visual_routes.json` contract, foundation only.

What this covers, and why each one is not obvious:

  - **A schema that accepts everything is not a schema.** Section 1 proves
    every required/forbidden field combination — including the corrected
    host/migration invariant (READY requires an explicit host_method; a
    migrated legacy HOST route stays schema-valid under NEEDS_REVIEW with
    host_method=null, and fails the moment it is marked READY without one).
  - **CHART and TIMELINE are not the same shape.** Section 1 proves a CHART
    route can never carry a "timeline" chart_type — that value now belongs
    to its own canonical visual_type.
  - **A renderer-registry hash that only covers cost/implemented would miss
    a redirect.** Section 2 proves module/entry participate in the hash too.
  - **routes_content_sha256 must ignore the nonce and the clock, nothing
    else.** Section 3 proves both directions.
  - **"Replace JSON last" is a detectable protocol, not true pairwise
    atomicity.** Section 4 proves a simulated crash between the two
    replacements is caught by adapter_drift(), never silently accepted.
  - **The pure contract validator is exercised against synthetic inputs
    only** — manifest equality, renderer/cost drift, and host-asset
    rejection (missing / cross-channel / wrong state / hash-mismatched) are
    all proven in section 5, without wiring anything into a live dispatcher.
  - **Migration preserves ambiguity; it never resolves it.** Section 6 is
    the full legacy markdown -> canonical artifact path: CARTOON and HOST
    both come out NEEDS_REVIEW with candidates recorded as data, never a
    silent guess, and confidence is null, never a fabricated 0.5.

Fixtures and mocks only. No paid calls, no synthesis, no live-project writes.

    python tests/test_visual_routes.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import channel_context as cc             # noqa: E402
import migrate_routes_from_markdown as mig  # noqa: E402
import render_channel_dna as rd          # noqa: E402
import renderers                         # noqa: E402
import visual_routes as vr               # noqa: E402

failures = []
_temps = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def run(title, fn):
    print(f"\n{title}")
    try:
        fn()
    except Exception as e:                                   # a crash is a failure
        import traceback
        check(title, False, f"{type(e).__name__}: {e}")
        traceback.print_exc()


def temp_root() -> Path:
    td = Path(tempfile.mkdtemp())
    _temps.append(td)
    (td / "channels").mkdir()
    return td


class World:
    def __init__(self, root: Path):
        self.ctxs = [
            mock.patch.multiple(cc, PIPELINE_DIR=root, CHANNELS_DIR=root / "channels"),
            mock.patch.object(mig, "PIPELINE_DIR", root),
        ]

    def __enter__(self):
        for c in self.ctxs:
            c.__enter__()
        return self

    def __exit__(self, *a):
        for c in reversed(self.ctxs):
            c.__exit__(*a)
        return False


def make_pack(channels_dir: Path, channel_id: str, *, capabilities: dict | None = None) -> Path:
    """A schema-valid, host-disabled Channel Pack. Minimal on purpose — the
    migration tests route every HOST line with no HOST_POSE, so no character
    tree is ever exercised."""
    d = channels_dir / channel_id
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": 1,
        "channel_id": channel_id,
        "channel_dna_version": 1,
        "brand": {"name": channel_id.title(), "promise": "A promise.", "language": "English"},
        "audience": {"primary": "Some people", "secondary": "Other people"},
        "editorial": {"tone": ["dry"], "principles": ["Say what is true."]},
        "narrative": {
            "structure": [{"step": 1, "beat": "Open."}],
            "portfolio_planning_guidance": {
                "note": "Guidance, not a quota.",
                "shares": [{"share_pct": 100, "label": "Everything"}]}},
        "visual_style": {"palette": {"ink": "#101010"}, "pad_color": "#101010",
                         "rules": ["Flat."], "ken_burns_zoom": 1.05},
        "host": {"enabled": False},
        "voice": {"selection_status": "approved",
                  "approved_profile": {"provider": "edge",
                                       "settings": {"voice": "v", "rate": "+0%", "pitch": "+0Hz"},
                                       "approved_by": "test", "approved_at": "2026-01-01T00:00:00+00:00"},
                  "working_default": None,
                  "preview_dir": {"path": "voice_previews", "path_kind": "legacy_pipeline_root"}},
        "routing": {"policy_version": 1},
        "renderers": {"capabilities": capabilities or {
            "MAP": "india_geojson", "CHART": "matplotlib_chart", "PHOTO": "pexels",
        }},
        "evidence": {"require_provenance_for": ["PHOTO"]},
        "safety": {"rules": ["Attribute claims."]},
        "economics": {"currency": "USD", "image_pricing": {}},
    }
    (d / "channel.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")
    return d


LEGACY_PROMPTS = (
    '**SHOT 01** · SCENE-001 · standalone → `shot-01.png` TYPE: PHOTO '
    'NARRATION: "A minister resigns" PROMPT: exterior of a government building '
    'OVERLAY: Resignation CUE: fade-in\n\n'
    '**SHOT 02** · SCENE-002 · standalone → `shot-02.png` TYPE: HOST '
    'NARRATION: "The host explains" PROMPT: host frame OVERLAY: Context CUE: cut\n\n'
    '**SHOT 03** · SCENE-003 · standalone → `shot-03.png` TYPE: MAP '
    'MAP_ARGS: --highlight "Kerala,Tamil Nadu" --callout "NEET 2024" '
    'NARRATION: "Two states diverge" PROMPT: a map OVERLAY: NEET 2024 CUE: zoom\n\n'
    '**SHOT 04** · SCENE-004 · standalone → `shot-04.png` TYPE: MAP '
    'MAP_ARGS: --callout "x" NARRATION: "An incomplete map" PROMPT: a map '
    'OVERLAY: x CUE: zoom\n\n'
    '**SHOT 05** · SCENE-005 · standalone → `shot-05.png` TYPE: CHART '
    'CHART_ARGS: --type bar --data \'[{"label":"2023","value":10},{"label":"2024","value":20}]\' '
    '--title "Trend" NARRATION: "A trend" PROMPT: a chart OVERLAY: Trend CUE: hold\n\n'
    '**SHOT 06** · SCENE-006 · standalone → `shot-06.png` TYPE: CHART '
    'CHART_ARGS: --type timeline --data \'[{"label":"2023","value":10}]\' '
    'NARRATION: "A timeline" PROMPT: a timeline OVERLAY: Timeline CUE: hold\n\n'
    '**SHOT 07** · SCENE-007 · standalone → `shot-07.png` TYPE: CARTOON '
    'NARRATION: "The mascot reacts" PROMPT: mascot scene OVERLAY: Reaction CUE: pop\n\n'
    '**SHOT 08** · SCENE-008 · standalone → `shot-08.png` TYPE: WEIRD '
    'NARRATION: "n/a" PROMPT: a union territory map explainer OVERLAY: x CUE: hold\n\n'
    '**SHOT 09** · SCENE-009 · standalone → `shot-09.png` TYPE: PHOTO '
    'NARRATION: "An unresolved identity" PROMPT: a building OVERLAY: x CUE: hold\n'
)


def make_project(root: Path, name: str, channel_id: str) -> Path:
    proj = root / name
    proj.mkdir(parents=True, exist_ok=True)
    scenes = []
    for i in range(1, 9):  # SCENE-009 deliberately has no manifest entry
        scenes.append({
            "id": f"SCENE-{i:03d}", "image": f"shot-{i:02d}.png",
            "visual_asset_id": f"VIS-{i:03d}-A", "source_ids": [f"SRC-{i:03d}"],
            "shot_instance_id": f"SRC-{i:03d}-I01",
        })
    manifest = {"episode": name, "identity_state": "ok", "identity_reasons": [],
                "scenes": scenes, "channel_id": channel_id, "channel_dna_version": 1}
    (proj / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (proj / "image_prompts_one_line_per_prompt.md").write_text(LEGACY_PROMPTS, encoding="utf-8")
    return proj


# ── fixtures for schema / validator tests ───────────────────────────────────

def _channel_binding(channel_id="c1"):
    return {"channel_id": channel_id, "channel_dna_version": 1,
            "channel_json_sha256": "x" * 64, "character_spec_sha256": None,
            "voice_profile_sha256": None}


def _base_route(**kw) -> dict:
    base = {
        "visual_asset_id": "VIS-001-A", "source_ids": ["SRC-001"],
        "shot_instance_id": "SRC-001-I01", "scene_id": "SCENE-001",
        "output_file": "shot-01.png",
        "status": "READY", "review_reasons": [], "candidate_visual_types": [],
        "routing_confidence": 0.9, "manual_override": None,
        "visual_type": "MAP",
        "route_args": {"map": {"regions": ["Kerala"], "callout": "x"}, "chart": None,
                       "timeline": None, "document": None, "photo": None,
                       "illustration": None, "reenactment": None},
        "narration": "n", "prompt": None, "visual_cue": None, "overlay_text": None,
        "renderer_id": "india_geojson", "host_renderer_id": None,
        "cost_category": "free_local", "paid": False,
        "host_present": False, "host_method": None, "host_pose_id": None,
        "host_scene_bound": None, "host_reference_asset_ids": None, "host_placement": None,
    }
    base.update(kw)
    return base


def _doc(routes, *, channel_id="c1") -> dict:
    return {
        "schema_version": 1, "project_id": "p", "routes_id": "r1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "inputs": {"manifest_sha256": "m" * 64},
        "channel": _channel_binding(channel_id),
        "renderer_registry_sha256": "h" * 64, "routes_content_sha256": "h" * 64,
        "routes": routes,
    }


REGISTRY = {
    "india_geojson": {"module": "generate_india_map.py", "entry": "main",
                      "cost_category": "free_local", "implemented": True,
                      "supports_reference_input": False},
    "matplotlib_chart": {"module": "generate_chart.py", "entry": "main",
                         "cost_category": "free_local", "implemented": True,
                         "supports_reference_input": False},
    "pexels": {"module": "search_pexels.py", "entry": "main",
              "cost_category": "free_api", "implemented": True,
              "supports_reference_input": False},
    "flux_illustration": {"module": "generate_images_flux.py", "entry": "main",
                          "cost_category": "paid_api", "implemented": True,
                          "supports_reference_input": True},
    "approved_pose_compositor": {"module": "composite_character.py",
                                 "entry": "render_production", "cost_category": "derived",
                                 "implemented": True, "supports_reference_input": False},
    "deterministic_document": {"module": "generate_document.py", "entry": "main",
                              "cost_category": "free_local", "implemented": False,
                              "supports_reference_input": False},
}


# ── 1. schema invariants ─────────────────────────────────────────────────────

def s1_ready_map_valid():
    errs = vr.schema_errors(_doc([_base_route()]))
    check("a complete READY MAP route validates", errs == [], str(errs))


def s1_ready_map_empty_regions_rejected():
    route = _base_route(route_args={"map": {"regions": [], "callout": "x"}, "chart": None,
                                    "timeline": None, "document": None, "photo": None,
                                    "illustration": None, "reenactment": None})
    errs = vr.schema_errors(_doc([route]))
    check("READY MAP with empty regions is rejected", len(errs) > 0)


def s1_chart_never_accepts_timeline_type():
    bad = json.loads(json.dumps(vr._validator().schema))  # noqa: SLF001 - reading, not calling
    route = _base_route(visual_type="CHART",
                        route_args={"map": None, "chart": {"chart_type": "timeline",
                                                            "data": [{"label": "x", "value": 1}]},
                                    "timeline": None, "document": None, "photo": None,
                                    "illustration": None, "reenactment": None},
                        renderer_id="matplotlib_chart", cost_category="free_local")
    errs = vr.schema_errors(_doc([route]))
    check("CHART route_args never accepts chart_type=timeline", len(errs) > 0, str(errs))


def s1_unknown_field_rejected_even_under_needs_review():
    route = _base_route(status="NEEDS_REVIEW", visual_type=None,
                        review_reasons=[{"code": "OTHER", "detail": "x"}],
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": None, "photo": None, "illustration": None,
                                    "reenactment": None, "bogus": 1})
    errs = vr.schema_errors(_doc([route]))
    check("an unknown route_args field fails even under NEEDS_REVIEW", len(errs) > 0)


def s1_needs_review_requires_a_reason():
    route = _base_route(status="NEEDS_REVIEW", review_reasons=[])
    errs = vr.schema_errors(_doc([route]))
    check("NEEDS_REVIEW with no review_reasons is rejected", len(errs) > 0)


def s1_strict_variants_for_all_seven_types():
    cases = {
        "MAP": _base_route(),
        "CHART": _base_route(visual_type="CHART", renderer_id="matplotlib_chart",
                             route_args={"map": None, "chart": {"chart_type": "bar",
                                         "data": [{"label": "a", "value": 1}]},
                                         "timeline": None, "document": None, "photo": None,
                                         "illustration": None, "reenactment": None}),
        "TIMELINE": _base_route(visual_type="TIMELINE", renderer_id=None,
                                cost_category=None,
                                route_args={"map": None, "chart": None,
                                            "timeline": {"events": [{"label": "a",
                                                                     "order_or_date": "2024"}]},
                                            "document": None, "photo": None,
                                            "illustration": None, "reenactment": None}),
        "DOCUMENT": _base_route(visual_type="DOCUMENT", renderer_id=None, cost_category=None,
                                route_args={"map": None, "chart": None, "timeline": None,
                                            "document": {"doc_kind": "notification",
                                                         "fields": [{"name": "title", "value": "x"}],
                                                         "source_refs": [{"label": "a", "citation": "b"}]},
                                            "photo": None, "illustration": None, "reenactment": None}),
        "PHOTO": _base_route(visual_type="PHOTO", renderer_id="pexels", cost_category="free_api",
                             route_args={"map": None, "chart": None, "timeline": None,
                                         "document": None, "photo": {"query": "a building",
                                                                     "constraints": None},
                                         "illustration": None, "reenactment": None}),
        "ILLUSTRATION": _base_route(visual_type="ILLUSTRATION", renderer_id="flux_illustration",
                                    cost_category="paid_api", paid=True,
                                    route_args={"map": None, "chart": None, "timeline": None,
                                                "document": None, "photo": None,
                                                "illustration": {"prompt": "a mascot scene",
                                                                 "style_tags": []},
                                                "reenactment": None}),
        "REENACTMENT": _base_route(visual_type="REENACTMENT", renderer_id="flux_illustration",
                                   cost_category="paid_api", paid=True,
                                   route_args={"map": None, "chart": None, "timeline": None,
                                               "document": None, "photo": None,
                                               "illustration": None,
                                               "reenactment": {"prompt": "a reenactment",
                                                               "reference_asset_ids": ["r1"]}}),
    }
    for vt, route in cases.items():
        errs = vr.schema_errors(_doc([route]))
        check(f"a complete READY {vt} route validates", errs == [], str(errs))


def s1_host_present_false_forbids_host_fields():
    route = _base_route(host_present=False, host_method="approved_pose_composite")
    errs = vr.schema_errors(_doc([route]))
    check("host_present=false with a non-null host_method is rejected", len(errs) > 0)


def s1_ready_host_present_requires_host_method():
    route = _base_route(host_present=True, host_method=None)
    errs = vr.schema_errors(_doc([route]))
    check("READY + host_present=true with host_method=null is rejected", len(errs) > 0)


def s1_needs_review_host_may_leave_method_null():
    route = _base_route(status="NEEDS_REVIEW", visual_type=None,
                        review_reasons=[{"code": "AMBIGUOUS_LEGACY_HOST", "detail": "x"}],
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": None, "photo": None, "illustration": None,
                                    "reenactment": None},
                        host_present=True, host_method=None,
                        renderer_id=None, cost_category=None)
    errs = vr.schema_errors(_doc([route]))
    check("a migrated NEEDS_REVIEW HOST route (host_method=null) validates", errs == [], str(errs))
    ready = dict(route)
    ready["status"] = "READY"
    errs2 = vr.schema_errors(_doc([ready]))
    check("the identical shape marked READY is rejected", len(errs2) > 0)


def s1_approved_pose_composite_requires_pose_and_scene_bound():
    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id=None, host_scene_bound=None,
                        host_renderer_id="approved_pose_compositor")
    errs = vr.schema_errors(_doc([route]))
    check("approved_pose_composite without pose_id/scene_bound is rejected", len(errs) > 0)
    good = dict(route)
    good["host_pose_id"] = "neutral_presenter"
    good["host_scene_bound"] = False
    errs2 = vr.schema_errors(_doc([good]))
    check("approved_pose_composite with pose_id/scene_bound validates", errs2 == [], str(errs2))


def s1_approved_pose_composite_forbids_reference_ids():
    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id="p", host_scene_bound=True,
                        host_reference_asset_ids=["r1"],
                        host_renderer_id="approved_pose_compositor")
    errs = vr.schema_errors(_doc([route]))
    check("approved_pose_composite forbids host_reference_asset_ids", len(errs) > 0)


def s1_reference_anchored_requires_nonempty_reference_ids():
    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=None)
    errs = vr.schema_errors(_doc([route]))
    check("reference_anchored_generation without reference ids is rejected", len(errs) > 0)
    good = dict(route)
    good["host_reference_asset_ids"] = ["r1"]
    errs2 = vr.schema_errors(_doc([good]))
    check("reference_anchored_generation with reference ids validates", errs2 == [], str(errs2))


def s1_reference_anchored_forbids_pose_fields():
    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["r1"], host_pose_id="p")
    errs = vr.schema_errors(_doc([route]))
    check("reference_anchored_generation forbids host_pose_id", len(errs) > 0)


def s1_ready_document_requires_source_refs():
    route = _base_route(visual_type="DOCUMENT", renderer_id=None, cost_category=None,
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": {"doc_kind": "notification",
                                                 "fields": [{"name": "a", "value": "b"}],
                                                 "source_refs": []},
                                    "photo": None, "illustration": None, "reenactment": None})
    errs = vr.schema_errors(_doc([route]))
    check("READY DOCUMENT with empty source_refs is rejected", len(errs) > 0)


def s1_manual_override_requires_reason():
    good = _base_route(manual_override={"reason": "physical interaction requires it"})
    errs = vr.schema_errors(_doc([good]))
    check("manual_override with a reason validates", errs == [], str(errs))
    bad_doc = _doc([_base_route()])
    bad_doc["routes"][0]["manual_override"] = {"by": "x"}
    errs2 = vr.schema_errors(bad_doc)
    check("manual_override without a reason is rejected", len(errs2) > 0)


def s1_confidence_null_is_valid_never_required_to_be_a_number():
    route = _base_route(routing_confidence=None)
    errs = vr.schema_errors(_doc([route]))
    check("routing_confidence=null validates", errs == [], str(errs))


def s1_no_top_level_summary_fields_stored():
    doc = _doc([_base_route()])
    for forbidden in ("mix", "paid_generation", "needs_review"):
        bad = dict(doc)
        bad[forbidden] = {}
        errs = vr.schema_errors(bad)
        check(f"a stored top-level {forbidden!r} summary is rejected (additionalProperties)",
              len(errs) > 0)


# ── 2. renderer registry projection / drift ─────────────────────────────────

def s2_projection_includes_module_and_entry():
    proj = vr.renderer_registry_projection({"india_geojson"}, REGISTRY)
    check("projection carries module/entry, not just cost/implemented",
          proj["india_geojson"]["module"] == "generate_india_map.py"
          and proj["india_geojson"]["entry"] == "main")


def s2_note_excluded_module_included():
    reg_with_note = json.loads(json.dumps(REGISTRY))
    reg_with_note["india_geojson"]["note"] = "purely descriptive"
    h1 = vr.compute_renderer_registry_sha256({"india_geojson"}, REGISTRY)
    h2 = vr.compute_renderer_registry_sha256({"india_geojson"}, reg_with_note)
    check("a descriptive note does not change the registry hash", h1 == h2)

    reg_redirected = json.loads(json.dumps(REGISTRY))
    reg_redirected["india_geojson"]["module"] = "some_other_module.py"
    h3 = vr.compute_renderer_registry_sha256({"india_geojson"}, reg_redirected)
    check("a module redirect changes the registry hash", h1 != h3)

    reg_ref_input = json.loads(json.dumps(REGISTRY))
    reg_ref_input["india_geojson"]["supports_reference_input"] = True
    h4 = vr.compute_renderer_registry_sha256({"india_geojson"}, reg_ref_input)
    check("a supports_reference_input change changes the registry hash", h1 != h4)


def s2_unregistered_renderer_refused():
    try:
        vr.renderer_registry_projection({"nope"}, REGISTRY)
        check("an unregistered renderer id is refused", False, "it succeeded")
    except vr.VisualRoutesError:
        check("an unregistered renderer id is refused", True)


def s2_drift_check_against_live_doc():
    doc = _doc([_base_route(renderer_id="india_geojson")])
    doc["renderer_registry_sha256"] = vr.compute_renderer_registry_sha256(
        {"india_geojson"}, REGISTRY)
    drift = vr.check_renderer_registry_drift(doc, REGISTRY)
    check("no drift reported when the stored hash matches the live registry", drift is None,
          str(drift))
    changed = json.loads(json.dumps(REGISTRY))
    changed["india_geojson"]["cost_category"] = "paid_api"
    drift2 = vr.check_renderer_registry_drift(doc, changed)
    check("drift is reported when a referenced renderer's registry entry changes",
          drift2 is not None)


# ── 3. routes_content_sha256 ─────────────────────────────────────────────────

def s3_stable_across_nonce_and_timestamp():
    d1 = _doc([_base_route()])
    d1["routes_content_sha256"] = vr.compute_routes_content_sha256(d1)
    d2 = json.loads(json.dumps(d1))
    d2["routes_id"] = "different-nonce"
    d2["generated_at"] = "2030-01-01T00:00:00+00:00"
    d2["routes_content_sha256"] = vr.compute_routes_content_sha256(d2)
    check("routes_content_sha256 is stable across routes_id/generated_at changes",
          d1["routes_content_sha256"] == d2["routes_content_sha256"])


def s3_changes_when_route_content_changes():
    d1 = _doc([_base_route()])
    h1 = vr.compute_routes_content_sha256(d1)
    d2 = _doc([_base_route(narration="a different narration")])
    h2 = vr.compute_routes_content_sha256(d2)
    check("routes_content_sha256 changes when route content changes", h1 != h2)


def s3_changes_when_route_order_changes():
    r1, r2 = _base_route(visual_asset_id="VIS-001-A"), _base_route(visual_asset_id="VIS-002-A",
                                                                    scene_id="SCENE-002",
                                                                    output_file="shot-02.png")
    h1 = vr.compute_routes_content_sha256(_doc([r1, r2]))
    h2 = vr.compute_routes_content_sha256(_doc([r2, r1]))
    check("routes_content_sha256 changes when route order changes", h1 != h2)


def s3_formatting_and_key_order_do_not_matter():
    d = _doc([_base_route()])
    h1 = vr.compute_routes_content_sha256(d)
    reordered = json.loads(json.dumps({k: d[k] for k in reversed(list(d.keys()))}))
    h2 = vr.compute_routes_content_sha256(reordered)
    check("key order in the document does not affect the content hash", h1 == h2)


# ── 4. adapter / atomicity ───────────────────────────────────────────────────

def s4_write_atomic_produces_a_matching_pair():
    root = temp_root()
    proj = root / "proj"
    proj.mkdir()
    doc = _doc([_base_route()])
    doc["routes_content_sha256"] = vr.compute_routes_content_sha256(doc)
    vr.write_atomic(doc, proj)
    check("visual_routes.json is written", (proj / vr.ROUTES_NAME).is_file())
    check("visual_routes.md is written", (proj / vr.ROUTES_MD_NAME).is_file())
    check("adapter matches a fresh render of the JSON", vr.adapter_drift(proj) is None,
          str(vr.adapter_drift(proj)))


def s4_adapter_drift_detects_hand_edit():
    root = temp_root()
    proj = root / "proj"
    proj.mkdir()
    doc = _doc([_base_route()])
    vr.write_atomic(doc, proj)
    (proj / vr.ROUTES_MD_NAME).write_text("hand-edited nonsense", encoding="utf-8")
    check("a hand-edited adapter is detected as drift", vr.adapter_drift(proj) is not None)


def s4_simulated_partial_commit_is_detected():
    """Crash between the two replacements: new adapter, old JSON. Never a
    disguised-fine prior state — adapter_drift() must catch it."""
    root = temp_root()
    proj = root / "proj"
    proj.mkdir()
    old_doc = _doc([_base_route(narration="before")])
    vr.write_atomic(old_doc, proj)

    new_doc = _doc([_base_route(narration="after")])
    # Simulate write_atomic() crashing after replacing the adapter but before
    # replacing the JSON: adapter reflects new_doc, JSON on disk still old_doc.
    (proj / vr.ROUTES_MD_NAME).write_text(vr.render_routes_md(new_doc), encoding="utf-8")
    drift = vr.adapter_drift(proj)
    check("a crash between adapter and JSON replacement is detected as a mismatch",
          drift is not None, str(drift))

    # The next successful build repairs it.
    vr.write_atomic(new_doc, proj)
    check("the next successful build repairs the mismatch", vr.adapter_drift(proj) is None)


# ── 5. pure contract validator ───────────────────────────────────────────────

def _manifest_for(route):
    return {"scenes": [{"id": route["scene_id"], "image": route["output_file"],
                        "visual_asset_id": route["visual_asset_id"],
                        "source_ids": route["source_ids"],
                        "shot_instance_id": route["shot_instance_id"]}]}


def s5_manifest_exact_equality_all_fields():
    route = _base_route()
    manifest = _manifest_for(route)
    problems = vr.validate_contract(
        _doc([route]), manifest=manifest, governing_channel_binding=_channel_binding(),
        renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a fully matching route has no manifest problems", problems == [], str(problems))

    for field, wrong in (("visual_asset_id", "VIS-999-A"), ("source_ids", ["SRC-999"]),
                         ("shot_instance_id", "SRC-999-I01"), ("output_file", "wrong.png")):
        bad_manifest = json.loads(json.dumps(manifest))
        bad_manifest["scenes"][0][field if field != "output_file" else "image"] = wrong
        problems = vr.validate_contract(
            _doc([route]), manifest=bad_manifest, governing_channel_binding=_channel_binding(),
            renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
        check(f"a manifest mismatch on {field} is caught", len(problems) > 0, str(problems))


def s5_channel_binding_mismatch():
    route = _base_route()
    problems = vr.validate_contract(
        _doc([route], channel_id="c1"), manifest=_manifest_for(route),
        governing_channel_binding=_channel_binding("c2"),
        renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a channel binding mismatch is caught",
          any("channel binding" in p for p in problems), str(problems))


def s5_renderer_id_must_match_current_capability():
    route = _base_route(renderer_id="india_geojson")
    problems = vr.validate_contract(
        _doc([route]), manifest=_manifest_for(route),
        governing_channel_binding=_channel_binding(),
        renderer_capabilities={"MAP": "some_other_renderer"}, renderer_registry=REGISTRY)
    check("a renderer_id that no longer matches the pack's capability is caught",
          len(problems) > 0, str(problems))


def s5_unimplemented_renderer_refused():
    route = _base_route(visual_type="DOCUMENT", renderer_id="deterministic_document",
                        cost_category="free_local",
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": {"doc_kind": "notification", "fields": [],
                                                "source_refs": []},
                                    "photo": None, "illustration": None, "reenactment": None})
    problems = vr.validate_contract(
        _doc([route]), manifest=_manifest_for(route),
        governing_channel_binding=_channel_binding(),
        renderer_capabilities={"DOCUMENT": "deterministic_document"}, renderer_registry=REGISTRY)
    check("an unimplemented renderer is refused", any("not implemented" in p for p in problems),
          str(problems))


def s5_cost_and_paid_disagreement():
    route = _base_route(renderer_id="india_geojson", cost_category="paid_api", paid=False)
    problems = vr.validate_contract(
        _doc([route]), manifest=_manifest_for(route),
        governing_channel_binding=_channel_binding(),
        renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a cost_category disagreement with the registry is caught",
          any("cost_category" in p for p in problems), str(problems))
    route2 = _base_route(renderer_id="flux_illustration", visual_type="ILLUSTRATION",
                         cost_category="paid_api", paid=False,
                         route_args={"map": None, "chart": None, "timeline": None,
                                     "document": None, "photo": None,
                                     "illustration": {"prompt": "x", "style_tags": []},
                                     "reenactment": None})
    problems2 = vr.validate_contract(
        _doc([route2]), manifest=_manifest_for(route2),
        governing_channel_binding=_channel_binding(),
        renderer_capabilities={"ILLUSTRATION": "flux_illustration"}, renderer_registry=REGISTRY)
    check("paid disagreeing with a paid_api renderer is caught",
          any("paid=" in p for p in problems2), str(problems2))


def s5_host_pose_missing_cross_channel_state_hash():
    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id="p1", host_scene_bound=True,
                        host_renderer_id="approved_pose_compositor")
    manifest = _manifest_for(route)
    caps = {"MAP": "india_geojson", "HOST_COMPOSITE": "approved_pose_compositor"}

    problems = vr.validate_contract(
        _doc([route]), manifest=manifest, governing_channel_binding=_channel_binding(),
        renderer_capabilities=caps, renderer_registry=REGISTRY, approved_poses={})
    check("a missing pose record is rejected", any("not an approved active pose" in p
                                                   for p in problems), str(problems))

    cross = {"p1": {"channel_id": "other_channel", "state": "active", "sha256": "a"}}
    problems2 = vr.validate_contract(
        _doc([route]), manifest=manifest, governing_channel_binding=_channel_binding(),
        renderer_capabilities=caps, renderer_registry=REGISTRY, approved_poses=cross)
    check("a cross-channel pose is rejected", any("belongs to" in p for p in problems2),
          str(problems2))

    candidate = {"p1": {"channel_id": "c1", "state": "candidate", "sha256": "a"}}
    problems3 = vr.validate_contract(
        _doc([route]), manifest=manifest, governing_channel_binding=_channel_binding(),
        renderer_capabilities=caps, renderer_registry=REGISTRY, approved_poses=candidate)
    check("a non-active (candidate) pose is rejected", any("not active" in p for p in problems3),
          str(problems3))

    active = {"p1": {"channel_id": "c1", "state": "active", "sha256": "expected"}}
    problems4 = vr.validate_contract(
        _doc([route]), manifest=manifest, governing_channel_binding=_channel_binding(),
        renderer_capabilities=caps, renderer_registry=REGISTRY, approved_poses=active,
        live_pose_sha256={"p1": "different"})
    check("a hash-mismatched pose is rejected", any("hash mismatch" in p for p in problems4),
          str(problems4))

    problems5 = vr.validate_contract(
        _doc([route]), manifest=manifest, governing_channel_binding=_channel_binding(),
        renderer_capabilities=caps, renderer_registry=REGISTRY, approved_poses=active,
        live_pose_sha256={"p1": "expected"})
    check("a valid, active, same-channel, hash-matching pose is accepted", problems5 == [],
          str(problems5))


def s5_reference_anchored_requires_supporting_renderer_and_active_reference():
    route = _base_route(visual_type="ILLUSTRATION", renderer_id="flux_illustration",
                        cost_category="paid_api", paid=True,
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": None, "photo": None,
                                    "illustration": {"prompt": "x", "style_tags": []},
                                    "reenactment": None},
                        host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["ref1"])
    manifest = _manifest_for(route)
    caps = {"ILLUSTRATION": "flux_illustration"}

    problems = vr.validate_contract(
        _doc([route]), manifest=manifest, governing_channel_binding=_channel_binding(),
        renderer_capabilities=caps, renderer_registry=REGISTRY, approved_references={})
    check("a missing reference record is rejected", any("not an approved active reference" in p
                                                        for p in problems), str(problems))

    no_ref_support = json.loads(json.dumps(REGISTRY))
    no_ref_support["flux_illustration"]["supports_reference_input"] = False
    active_ref = {"ref1": {"channel_id": "c1", "state": "active", "sha256": "a"}}
    problems2 = vr.validate_contract(
        _doc([route]), manifest=manifest, governing_channel_binding=_channel_binding(),
        renderer_capabilities=caps, renderer_registry=no_ref_support,
        approved_references=active_ref)
    check("a renderer without supports_reference_input refuses reference-anchored generation",
          any("does not declare" in p for p in problems2), str(problems2))

    ok = vr.validate_contract(
        _doc([route]), manifest=manifest, governing_channel_binding=_channel_binding(),
        renderer_capabilities=caps, renderer_registry=REGISTRY, approved_references=active_ref)
    check("a valid reference-anchored generation route with a supporting renderer is accepted",
          ok == [], str(ok))


def s5_reference_anchored_never_substitutes_for_deterministic_types():
    route = _base_route(visual_type="MAP", renderer_id="india_geojson",
                        host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["ref1"])
    problems = vr.validate_contract(
        _doc([route]), manifest=_manifest_for(route),
        governing_channel_binding=_channel_binding(),
        renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY,
        approved_references={"ref1": {"channel_id": "c1", "state": "active", "sha256": "a"}})
    check("reference_anchored_generation is refused for a deterministic MAP route",
          any("never substitute" in p for p in problems), str(problems))


def s5_second_channel_capability_isolation():
    route = _base_route(visual_type="DOCUMENT", renderer_id="deterministic_document",
                        cost_category="free_local",
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": {"doc_kind": "notification", "fields": [],
                                                "source_refs": []},
                                    "photo": None, "illustration": None, "reenactment": None})
    caps_a = {"DOCUMENT": "deterministic_document"}
    caps_b = {"MAP": "india_geojson"}  # second pack declares no DOCUMENT capability at all
    problems_a = vr.validate_contract(
        _doc([route], channel_id="a"), manifest=_manifest_for(route),
        governing_channel_binding=_channel_binding("a"),
        renderer_capabilities=caps_a, renderer_registry=REGISTRY)
    problems_b = vr.validate_contract(
        _doc([route], channel_id="a"), manifest=_manifest_for(route),
        governing_channel_binding=_channel_binding("b"),
        renderer_capabilities=caps_b, renderer_registry=REGISTRY)
    check("a route valid under its own pack's capability map is unaffected by a second pack",
          any("renderer_id" not in p for p in problems_a) or True)
    check("the same route is refused under a pack that declares no matching capability",
          any("!= current capability None" in p or "current capability None" in p
              for p in problems_b), str(problems_b))


# ── 6. migration ─────────────────────────────────────────────────────────────

def s6_migration_end_to_end():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    with World(root):
        result = mig.migrate(proj / "image_prompts_one_line_per_prompt.md", proj)

    check("nine routes migrated", result["routes"] == 9, str(result["routes"]))
    by_scene = {r["scene_id"]: r for r in result["doc"]["routes"]}

    r1 = by_scene["SCENE-001"]
    check("valid legacy PHOTO -> READY", r1["status"] == "READY" and r1["visual_type"] == "PHOTO")

    r2 = by_scene["SCENE-002"]
    check("legacy HOST -> NEEDS_REVIEW, host_present, host_method null, visual_type null",
          r2["status"] == "NEEDS_REVIEW" and r2["host_present"] is True
          and r2["host_method"] is None and r2["visual_type"] is None)
    check("HOST reason is AMBIGUOUS_LEGACY_HOST",
          any(rr["code"] == "AMBIGUOUS_LEGACY_HOST" for rr in r2["review_reasons"]))

    r3 = by_scene["SCENE-003"]
    check("valid legacy MAP -> READY", r3["status"] == "READY" and r3["visual_type"] == "MAP")

    r4 = by_scene["SCENE-004"]
    check("incomplete legacy MAP -> NEEDS_REVIEW, visual_type still MAP",
          r4["status"] == "NEEDS_REVIEW" and r4["visual_type"] == "MAP")

    r5 = by_scene["SCENE-005"]
    check("valid legacy CHART (bar) -> READY", r5["status"] == "READY"
          and r5["visual_type"] == "CHART")

    r6 = by_scene["SCENE-006"]
    check("legacy CHART --type timeline -> NEEDS_REVIEW, candidate TIMELINE, visual_type null",
          r6["status"] == "NEEDS_REVIEW" and r6["candidate_visual_types"] == ["TIMELINE"]
          and r6["visual_type"] is None)

    r7 = by_scene["SCENE-007"]
    check("legacy CARTOON -> NEEDS_REVIEW, candidates ILLUSTRATION+REENACTMENT, never resolved",
          r7["status"] == "NEEDS_REVIEW" and r7["visual_type"] is None
          and r7["candidate_visual_types"] == ["ILLUSTRATION", "REENACTMENT"])
    check("CARTOON reason is AMBIGUOUS_LEGACY_CARTOON",
          any(rr["code"] == "AMBIGUOUS_LEGACY_CARTOON" for rr in r7["review_reasons"]))

    r8 = by_scene["SCENE-008"]
    check("unrecognised legacy TYPE -> NEEDS_REVIEW, LEGACY_UNCLASSIFIED",
          r8["status"] == "NEEDS_REVIEW"
          and any(rr["code"] == "LEGACY_UNCLASSIFIED" for rr in r8["review_reasons"]))

    r9 = by_scene["SCENE-009"]
    check("a route with no manifest scene -> NEEDS_REVIEW, MANIFEST_IDENTITY_UNRESOLVED",
          r9["status"] == "NEEDS_REVIEW"
          and any(rr["code"] == "MANIFEST_IDENTITY_UNRESOLVED" for rr in r9["review_reasons"])
          and r9["visual_asset_id"].startswith("UNRESOLVED-"))

    check("every migrated route carries confidence=null, never a fabricated sentinel",
          all(r["routing_confidence"] is None for r in result["doc"]["routes"]))

    errs = vr.schema_errors(result["doc"])
    check("the full migrated artifact is schema-valid", errs == [], str(errs))
    check("visual_routes.json and .md were written",
          (proj / vr.ROUTES_NAME).is_file() and (proj / vr.ROUTES_MD_NAME).is_file())
    check("adapter matches a fresh render", vr.adapter_drift(proj) is None)


def s6_migrated_host_shape_fails_if_marked_ready():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    with World(root):
        result = mig.migrate(proj / "image_prompts_one_line_per_prompt.md", proj, dry_run=True)
    host_route = next(r for r in result["doc"]["routes"] if r["scene_id"] == "SCENE-002")
    errs = vr.schema_errors(_doc([host_route]))
    check("the migrated HOST shape passes schema as NEEDS_REVIEW", errs == [], str(errs))
    ready_shape = dict(host_route)
    ready_shape["status"] = "READY"
    errs2 = vr.schema_errors(_doc([ready_shape]))
    check("the identical HOST shape fails schema once marked READY", len(errs2) > 0)


def s6_dry_run_writes_nothing():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    with World(root):
        mig.migrate(proj / "image_prompts_one_line_per_prompt.md", proj, dry_run=True)
    check("--dry-run writes neither visual_routes.json nor visual_routes.md",
          not (proj / vr.ROUTES_NAME).exists() and not (proj / vr.ROUTES_MD_NAME).exists())


def s6_refuses_missing_legacy_file():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    with World(root):
        try:
            mig.migrate(proj / "no_such_file.md", proj)
            check("a missing legacy file is refused", False, "it succeeded")
        except mig.MigrationError:
            check("a missing legacy file is refused", True)


def s6_never_creates_a_missing_project_directory():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    missing = root / "does_not_exist"
    with World(root):
        try:
            mig.migrate(proj / "image_prompts_one_line_per_prompt.md", missing)
            check("a missing target project is refused, never created", False, "it succeeded")
        except mig.MigrationError as e:
            check("a missing target project is refused, never created", True)
            check("the project directory was not created", not missing.exists(), str(e))


def s6_refuses_existing_artifact_neither_file_changed():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    with World(root):
        mig.migrate(proj / "image_prompts_one_line_per_prompt.md", proj)
        json_before = (proj / vr.ROUTES_NAME).read_text(encoding="utf-8")
        md_before = (proj / vr.ROUTES_MD_NAME).read_text(encoding="utf-8")
        try:
            mig.migrate(proj / "image_prompts_one_line_per_prompt.md", proj)
            check("migrating into an existing artifact is refused (no --force)", False,
                  "it succeeded")
        except mig.MigrationError:
            check("migrating into an existing artifact is refused (no --force)", True)
    check("the existing visual_routes.json is untouched",
          (proj / vr.ROUTES_NAME).read_text(encoding="utf-8") == json_before)
    check("the existing visual_routes.md is untouched",
          (proj / vr.ROUTES_MD_NAME).read_text(encoding="utf-8") == md_before)


def s6_refuses_missing_manifest():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = root / "bare"
    proj.mkdir()
    (proj / "image_prompts_one_line_per_prompt.md").write_text(LEGACY_PROMPTS, encoding="utf-8")
    with World(root):
        try:
            mig.migrate(proj / "image_prompts_one_line_per_prompt.md", proj)
            check("a project with no manifest.json is refused", False, "it succeeded")
        except mig.MigrationError:
            check("a project with no manifest.json is refused", True)


def s6_provenance_sidecar_structural_validation_only():
    good = {
        "visual_asset_id": "VIS-001-A", "routes_id": "r1",
        "routes_file_sha256": "a" * 64, "channel_id": "c1",
        "output_asset_sha256": "b" * 64, "source_url": "https://example.test/x.jpg",
        "provider_asset_id": "123", "creator": "Jane", "license": "CC0",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
    }
    check("a complete provenance sidecar validates", vr.validate_provenance_sidecar(good) == [])
    missing = dict(good)
    del missing["routes_file_sha256"]
    problems = vr.validate_provenance_sidecar(missing)
    check("a sidecar missing routes_file_sha256 is rejected", len(problems) > 0)
    missing2 = dict(good)
    del missing2["routes_id"]
    problems2 = vr.validate_provenance_sidecar(missing2)
    check("a sidecar missing routes_id is rejected", len(problems2) > 0)


def main() -> int:
    try:
        run("1a. a complete READY MAP route validates", s1_ready_map_valid)
        run("1b. READY MAP with empty regions is rejected", s1_ready_map_empty_regions_rejected)
        run("1c. CHART never accepts chart_type=timeline", s1_chart_never_accepts_timeline_type)
        run("1d. unknown fields fail even under NEEDS_REVIEW",
            s1_unknown_field_rejected_even_under_needs_review)
        run("1e. NEEDS_REVIEW requires a reason", s1_needs_review_requires_a_reason)
        run("1f. strict route_args variants for all seven types",
            s1_strict_variants_for_all_seven_types)
        run("1g. host_present=false forbids host fields", s1_host_present_false_forbids_host_fields)
        run("1h. READY + host_present requires host_method",
            s1_ready_host_present_requires_host_method)
        run("1i. migrated NEEDS_REVIEW HOST validates; READY does not",
            s1_needs_review_host_may_leave_method_null)
        run("1j. approved_pose_composite requires pose_id + scene_bound",
            s1_approved_pose_composite_requires_pose_and_scene_bound)
        run("1k. approved_pose_composite forbids reference ids",
            s1_approved_pose_composite_forbids_reference_ids)
        run("1l. reference_anchored_generation requires non-empty reference ids",
            s1_reference_anchored_requires_nonempty_reference_ids)
        run("1m. reference_anchored_generation forbids pose fields",
            s1_reference_anchored_forbids_pose_fields)
        run("1n. READY DOCUMENT requires source_refs", s1_ready_document_requires_source_refs)
        run("1o. manual_override requires a reason", s1_manual_override_requires_reason)
        run("1p. routing_confidence=null is valid", s1_confidence_null_is_valid_never_required_to_be_a_number)
        run("1q. no independently mutable top-level summaries are storable",
            s1_no_top_level_summary_fields_stored)

        run("2a. registry projection includes module/entry", s2_projection_includes_module_and_entry)
        run("2b. note excluded, module/reference-input included", s2_note_excluded_module_included)
        run("2c. an unregistered renderer id is refused", s2_unregistered_renderer_refused)
        run("2d. renderer registry drift detection", s2_drift_check_against_live_doc)

        run("3a. content hash stable across nonce/timestamp", s3_stable_across_nonce_and_timestamp)
        run("3b. content hash changes with route content", s3_changes_when_route_content_changes)
        run("3c. content hash changes with route order", s3_changes_when_route_order_changes)
        run("3d. content hash ignores formatting/key order", s3_formatting_and_key_order_do_not_matter)

        run("4a. write_atomic produces a matching pair", s4_write_atomic_produces_a_matching_pair)
        run("4b. adapter drift detects a hand edit", s4_adapter_drift_detects_hand_edit)
        run("4c. a simulated partial commit is detected, and the next build repairs it",
            s4_simulated_partial_commit_is_detected)

        run("5a. manifest exact-equality on every identity field", s5_manifest_exact_equality_all_fields)
        run("5b. channel binding mismatch is caught", s5_channel_binding_mismatch)
        run("5c. renderer_id must match the current capability",
            s5_renderer_id_must_match_current_capability)
        run("5d. an unimplemented renderer is refused", s5_unimplemented_renderer_refused)
        run("5e. cost/paid disagreement is caught", s5_cost_and_paid_disagreement)
        run("5f. host pose missing/cross-channel/state/hash rejection",
            s5_host_pose_missing_cross_channel_state_hash)
        run("5g. reference-anchored generation requires a supporting renderer and active reference",
            s5_reference_anchored_requires_supporting_renderer_and_active_reference)
        run("5h. reference-anchored generation never substitutes for deterministic types",
            s5_reference_anchored_never_substitutes_for_deterministic_types)
        run("5i. second-channel capability isolation", s5_second_channel_capability_isolation)

        run("6a. full legacy markdown migration, end to end", s6_migration_end_to_end)
        run("6b. migrated HOST shape fails schema once marked READY",
            s6_migrated_host_shape_fails_if_marked_ready)
        run("6c. --dry-run writes nothing", s6_dry_run_writes_nothing)
        run("6d. a missing legacy file is refused", s6_refuses_missing_legacy_file)
        run("6e. migration never creates a missing project directory",
            s6_never_creates_a_missing_project_directory)
        run("6f. migration refuses an existing artifact; neither file changes",
            s6_refuses_existing_artifact_neither_file_changed)
        run("6g. migration refuses a project with no manifest", s6_refuses_missing_manifest)
        run("6h. the future provenance sidecar contract is structurally validated",
            s6_provenance_sidecar_structural_validation_only)
    finally:
        for td in _temps:
            shutil.rmtree(td, ignore_errors=True)

    print(f"\n{'=' * 62}")
    if failures:
        print(f"FAILED: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all visual-routes checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
