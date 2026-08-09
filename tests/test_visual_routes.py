"""Task 2B-B1 — the canonical `visual_routes.json` contract, foundation only.

What this covers, and why each one is not obvious:

  - **Integrity is not executability.** A NEEDS_REVIEW route can pass every
    hash/binding/schema check there is and still be forbidden to dispatch.
    Section 5a/5j prove validate_contract() never calls that "executable,"
    and that execution_blockers()/is_executable() are the only functions
    that answer the dispatch question.
  - **A schema that accepts everything is not a schema.** Section 1 proves
    every required/forbidden field combination — including the corrected
    host/migration invariant (READY requires an explicit host_method; a
    migrated legacy HOST route stays schema-valid under NEEDS_REVIEW with
    host_method=null, and fails the moment it is marked READY without one).
  - **CHART and TIMELINE are not the same shape.** Section 1 proves a CHART
    route can never carry a "timeline" chart_type — that value now belongs
    to its own canonical visual_type.
  - **A renderer-registry hash that only covers cost/implemented would miss
    a redirect.** Section 2 proves module/entry participate in the hash too,
    and section 5 proves the MAIN validate_contract() path catches a
    redirect, a stale manifest hash and a stale content hash — not only the
    standalone check_renderer_registry_drift() helper.
  - **"Replace JSON last" is a detectable protocol, not true pairwise
    atomicity.** Section 4 simulates an actual failure on the second
    replacement (not just a hand-edited file afterwards) and proves no temp
    file is left behind, the mismatch is detected, and the next build
    repairs it.
  - **The pure contract validator uses the real character-registry
    vocabulary** (status=approved/approved_scene_bound, path, sha256,
    generic_compositing_allowed) and does real file-hash verification against
    real temp files — missing file, missing hash evidence, and a genuine
    hash mismatch are three distinct, separately tested failures.
  - **Migration preserves ambiguity; it never resolves it** — and never
    writes a READY route with a null renderer. Section 6 proves an
    unambiguous MAP/CHART/PHOTO route gets a real renderer/cost binding from
    the governing Channel Pack, downgrading to NEEDS_REVIEW when the pack
    has nothing to render it with, and that the full migrated artifact must
    pass validate_contract() before a single byte is written.

Fixtures and mocks only. No paid calls, no synthesis, no live-project writes.

    python tests/test_visual_routes.py
"""

import hashlib
import json
import os
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
import route_images                      # noqa: E402
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


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


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
    for i in range(1, 10):  # every legacy row resolves to a real manifest identity
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


MANIFEST_SHA = "m" * 64


def _doc(routes, *, channel_id="c1", manifest_sha256=MANIFEST_SHA,
        renderer_ids=(), registry=None) -> dict:
    d = {
        "schema_version": 1, "project_id": "p", "routes_id": "r1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "inputs": {"manifest_sha256": manifest_sha256},
        "channel": _channel_binding(channel_id),
        "renderer_registry_sha256": (
            vr.compute_renderer_registry_sha256(renderer_ids, registry)
            if renderer_ids else "h" * 64),
        "routes_content_sha256": "h" * 64,
        "routes": routes,
    }
    return d


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
    "deterministic_timeline": {"module": "generate_timeline.py", "entry": "main",
                              "cost_category": "free_local", "implemented": False,
                              "supports_reference_input": False},
}


def _finalize(doc: dict) -> dict:
    """Fill in a self-consistent renderer_registry_sha256/routes_content_sha256
    for a doc built from _doc()/_base_route(), so 'no problems expected' tests
    don't spuriously fail on the new hash checks."""
    ids = vr.referenced_renderer_ids(doc["routes"])
    doc["renderer_registry_sha256"] = vr.compute_renderer_registry_sha256(ids, REGISTRY)
    doc["routes_content_sha256"] = vr.compute_routes_content_sha256(doc)
    return doc


def _exec_kwargs(route, *, project_id="p", renderer_capabilities=None):
    """Common kwargs for execution_blockers()/is_executable(), which (unlike
    the old status-only pair) always require the full validation context."""
    return dict(
        manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id=project_id,
        renderer_capabilities=renderer_capabilities or {"MAP": "india_geojson"},
        renderer_registry=REGISTRY)


def _manifest_for(route):
    return {"scenes": [{"id": route["scene_id"], "image": route["output_file"],
                        "visual_asset_id": route["visual_asset_id"],
                        "source_ids": route["source_ids"],
                        "shot_instance_id": route["shot_instance_id"]}]}


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


def s1_ready_forbids_review_reasons_and_candidates():
    route = _base_route(review_reasons=[{"code": "OTHER", "detail": "x"}])
    errs = vr.schema_errors(_doc([route]))
    check("READY with a non-empty review_reasons is rejected", len(errs) > 0)
    route2 = _base_route(candidate_visual_types=["CHART"])
    errs2 = vr.schema_errors(_doc([route2]))
    check("READY with a non-empty candidate_visual_types is rejected", len(errs2) > 0)


def s1_ready_requires_nonempty_source_ids():
    route = _base_route(source_ids=[])
    errs = vr.schema_errors(_doc([route]))
    check("READY with empty source_ids is rejected", len(errs) > 0)


def s1_ready_requires_renderer_id_and_cost_category():
    route = _base_route(renderer_id=None)
    errs = vr.schema_errors(_doc([route]))
    check("READY with renderer_id=null is rejected", len(errs) > 0)
    route2 = _base_route(cost_category=None)
    errs2 = vr.schema_errors(_doc([route2]))
    check("READY with cost_category=null is rejected", len(errs2) > 0)


def s1_strict_variants_for_all_seven_types():
    cases = {
        "MAP": _base_route(),
        "CHART": _base_route(visual_type="CHART", renderer_id="matplotlib_chart",
                             route_args={"map": None, "chart": {"chart_type": "bar",
                                         "data": [{"label": "a", "value": 1}]},
                                         "timeline": None, "document": None, "photo": None,
                                         "illustration": None, "reenactment": None}),
        "TIMELINE": _base_route(visual_type="TIMELINE", renderer_id="deterministic_timeline",
                                cost_category="free_local",
                                route_args={"map": None, "chart": None,
                                            "timeline": {"events": [{"label": "a",
                                                                     "order_or_date": "2024"}]},
                                            "document": None, "photo": None,
                                            "illustration": None, "reenactment": None}),
        "DOCUMENT": _base_route(visual_type="DOCUMENT", renderer_id="deterministic_document",
                                cost_category="free_local",
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


def s1_host_present_false_nulls_every_host_field_independently():
    """Six independent regressions, one per host-dependent field — each doc
    is otherwise clean (host_present=false) with exactly one field non-null,
    so a leak in any single field is caught on its own."""
    per_field = {
        "host_method": "approved_pose_composite",
        "host_pose_id": "some-pose",
        "host_scene_bound": True,
        "host_reference_asset_ids": ["r1"],
        "host_renderer_id": "approved_pose_compositor",
        "host_placement": {"position": "left"},
    }
    for field, bad_value in per_field.items():
        route = _base_route(host_present=False, **{field: bad_value})
        errs = vr.schema_errors(_doc([route]))
        check(f"host_present=false with a non-null {field} alone is rejected", len(errs) > 0,
              str(errs))


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


def s1_approved_pose_composite_requires_pose_scene_bound_and_host_renderer():
    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id=None, host_scene_bound=None, host_renderer_id=None)
    errs = vr.schema_errors(_doc([route]))
    check("approved_pose_composite without pose_id/scene_bound/host_renderer_id is rejected",
          len(errs) > 0)
    good = dict(route)
    good["host_pose_id"] = "neutral_presenter"
    good["host_scene_bound"] = False
    good["host_renderer_id"] = "approved_pose_compositor"
    errs2 = vr.schema_errors(_doc([good]))
    check("approved_pose_composite with pose_id/scene_bound/host_renderer_id validates",
          errs2 == [], str(errs2))


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


def s1_reference_anchored_forbids_pose_fields_and_host_renderer_id():
    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["r1"], host_pose_id="p")
    errs = vr.schema_errors(_doc([route]))
    check("reference_anchored_generation forbids host_pose_id", len(errs) > 0)
    route2 = _base_route(host_present=True, host_method="reference_anchored_generation",
                         host_reference_asset_ids=["r1"], host_renderer_id="approved_pose_compositor")
    errs2 = vr.schema_errors(_doc([route2]))
    check("reference_anchored_generation forbids a non-null host_renderer_id", len(errs2) > 0)


def s1_ready_document_requires_source_refs():
    route = _base_route(visual_type="DOCUMENT", renderer_id="deterministic_document",
                        cost_category="free_local",
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


def s1_output_file_rejects_paths_and_traversal():
    for bad_name in ("../x.png", "a/b.png", "a\\b.png", "C:\\evil.png", "..", "."):
        route = _base_route(output_file=bad_name)
        errs = vr.schema_errors(_doc([route]))
        check(f"output_file {bad_name!r} is rejected by the schema", len(errs) > 0)
    good = _base_route(output_file="shot-01.png")
    errs = vr.schema_errors(_doc([good]))
    check("a bare contained filename validates", errs == [], str(errs))


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
    doc = _finalize(_doc([_base_route(renderer_id="india_geojson")]))
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


def s4_write_atomic_refuses_a_stale_content_hash():
    root = temp_root()
    proj = root / "proj"
    proj.mkdir()
    doc = _doc([_base_route()])
    doc["routes_content_sha256"] = "not-the-real-hash"
    try:
        vr.write_atomic(doc, proj)
        check("write_atomic refuses a stale routes_content_sha256", False, "it wrote anyway")
    except vr.VisualRoutesError:
        check("write_atomic refuses a stale routes_content_sha256", True)
    check("nothing was written when the content hash was stale",
          not (proj / vr.ROUTES_NAME).exists() and not (proj / vr.ROUTES_MD_NAME).exists())


def s4_adapter_drift_detects_hand_edit():
    root = temp_root()
    proj = root / "proj"
    proj.mkdir()
    doc = _doc([_base_route()])
    doc["routes_content_sha256"] = vr.compute_routes_content_sha256(doc)
    vr.write_atomic(doc, proj)
    (proj / vr.ROUTES_MD_NAME).write_text("hand-edited nonsense", encoding="utf-8")
    check("a hand-edited adapter is detected as drift", vr.adapter_drift(proj) is not None)


def s4_adapter_escapes_table_breaking_content():
    root = temp_root()
    proj = root / "proj"
    proj.mkdir()
    route = _base_route(narration='a | pipe\nand a newline', overlay_text="x|y")
    doc = _doc([route])
    doc["routes_content_sha256"] = vr.compute_routes_content_sha256(doc)
    vr.write_atomic(doc, proj)
    text = (proj / vr.ROUTES_MD_NAME).read_text(encoding="utf-8")
    check("pipe-breaking narration does not corrupt the routes table",
          "| a \\| pipe and a newline |" in text or "a \\| pipe and a newline" in text, text)
    check("adapter includes typed route_args/renderer/host/prompt/cue/overlay detail",
          "route_args:" in text and "renderer_id:" in text and "host_present:" in text
          and "cue:" in text)


def s4_simulated_second_replacement_failure_cleans_temp_files_and_is_detected():
    """An ACTUAL failure during the JSON replacement (not a hand-edit
    afterwards): the adapter replacement succeeds for real, then os.replace
    is made to fail only for the JSON file. No .tmp file may remain, the
    mismatch must be detected, and the next successful build must repair it.
    """
    root = temp_root()
    proj = root / "proj"
    proj.mkdir()
    old_doc = _doc([_base_route(narration="before")])
    old_doc["routes_content_sha256"] = vr.compute_routes_content_sha256(old_doc)
    vr.write_atomic(old_doc, proj)

    new_doc = _doc([_base_route(narration="after")])
    new_doc["routes_content_sha256"] = vr.compute_routes_content_sha256(new_doc)

    real_replace = os.replace

    def failing_replace(src, dst):
        if Path(dst).name == vr.ROUTES_NAME:
            raise OSError("simulated failure replacing the canonical JSON")
        return real_replace(src, dst)

    with mock.patch("os.replace", side_effect=failing_replace):
        try:
            vr.write_atomic(new_doc, proj)
            check("write_atomic propagates a failed JSON replacement", False, "it succeeded")
        except OSError:
            check("write_atomic propagates a failed JSON replacement", True)

    check("the adapter was actually replaced before the failure",
          (proj / vr.ROUTES_MD_NAME).read_text(encoding="utf-8") == vr.render_routes_md(new_doc))
    check("the canonical JSON on disk is still the old one",
          json.loads((proj / vr.ROUTES_NAME).read_text(encoding="utf-8"))["routes"][0]["narration"]
          == "before")
    leftover = list(proj.glob("*.tmp"))
    check("no temporary file remains after the failed replacement", leftover == [], str(leftover))
    check("the mismatch (new adapter, old JSON) is detected", vr.adapter_drift(proj) is not None)

    vr.write_atomic(new_doc, proj)
    check("the next successful build repairs the mismatch", vr.adapter_drift(proj) is None)


# ── 5. pure contract validator: integrity vs executability ─────────────────

def s5_ready_route_no_problems():
    route = _base_route()
    doc = _finalize(_doc([route]))
    problems = vr.validate_contract(
        doc, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a fully consistent READY route has no integrity problems", problems == [], str(problems))


def s5_integrity_valid_but_needs_review_is_not_executable():
    route = _base_route(status="NEEDS_REVIEW", visual_type=None, renderer_id=None,
                        cost_category=None,
                        review_reasons=[{"code": "OTHER", "detail": "x"}],
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": None, "photo": None, "illustration": None,
                                    "reenactment": None})
    doc = _finalize(_doc([route]))
    problems = vr.validate_contract(
        doc, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a schema/integrity-valid NEEDS_REVIEW artifact has no integrity problems",
          problems == [], str(problems))
    check("but it is NOT executable", not vr.is_executable(doc, **_exec_kwargs(route)))
    check("execution_blockers names the NEEDS_REVIEW route",
          any("NEEDS_REVIEW" in b for b in vr.execution_blockers(doc, **_exec_kwargs(route))))
    ready_route = _base_route()
    ready_doc = _finalize(_doc([ready_route]))
    check("a fully READY artifact IS executable",
          vr.is_executable(ready_doc, **_exec_kwargs(ready_route)))
    check("a fully READY artifact has no execution blockers",
          vr.execution_blockers(ready_doc, **_exec_kwargs(ready_route)) == [])


def s5_execution_blockers_catches_a_schema_invalid_doc_even_if_status_says_ready():
    """The core bug this round fixes: a hand-built dict claiming
    status=READY with renderer_id=null must not report zero blockers just
    because nothing checked the schema. execution_blockers() must run
    schema_errors() first and return them immediately."""
    route = _base_route()
    doc = _finalize(_doc([route]))
    doc["routes"][0]["renderer_id"] = None  # now schema-invalid: READY requires it
    problems = vr.execution_blockers(doc, **_exec_kwargs(route))
    check("a schema-invalid READY route produces execution blockers", problems != [],
          str(problems))
    check("is_executable is False for the schema-invalid doc",
          not vr.is_executable(doc, **_exec_kwargs(route)))


def s5_execution_blockers_catches_a_wrong_project_id():
    route = _base_route()
    doc = _finalize(_doc([route]))
    problems = vr.execution_blockers(doc, **_exec_kwargs(route, project_id="not-the-doc-project"))
    check("execution_blockers is blocked by a project_id mismatch, via validate_contract",
          any("project_id" in p for p in problems), str(problems))


def s5_stale_manifest_hash_rejected_by_main_validator():
    route = _base_route()
    doc = _finalize(_doc([route]))
    problems = vr.validate_contract(
        doc, manifest=_manifest_for(route), manifest_sha256="different-hash",
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a stale manifest hash is caught by validate_contract itself",
          any("manifest_sha256" in p for p in problems), str(problems))


def s5_stale_content_hash_rejected_by_main_validator():
    route = _base_route()
    doc = _finalize(_doc([route]))
    doc["routes_content_sha256"] = "stale"
    problems = vr.validate_contract(
        doc, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a stale routes_content_sha256 is caught by validate_contract itself",
          any("routes_content_sha256" in p for p in problems), str(problems))


def s5_renderer_redirect_rejected_by_main_validator():
    route = _base_route()
    doc = _finalize(_doc([route]))
    redirected = json.loads(json.dumps(REGISTRY))
    redirected["india_geojson"]["module"] = "some_other_module.py"
    problems = vr.validate_contract(
        doc, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=redirected)
    check("a renderer module redirect is caught by validate_contract itself, "
          "not only check_renderer_registry_drift",
          any("registry" in p.lower() or "drift" in p.lower() for p in problems), str(problems))


def s5_channel_binding_mismatch():
    route = _base_route()
    doc = _finalize(_doc([route], channel_id="c1"))
    problems = vr.validate_contract(
        doc, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding("c2"),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a channel binding mismatch is caught",
          any("channel binding" in p for p in problems), str(problems))


def s5_renderer_id_must_match_current_capability():
    route = _base_route(renderer_id="india_geojson")
    doc = _finalize(_doc([route]))
    problems = vr.validate_contract(
        doc, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "some_other_renderer"}, renderer_registry=REGISTRY)
    check("a renderer_id that no longer matches the pack's capability is caught",
          len(problems) > 0, str(problems))


def s5_ready_without_capability_rejected_even_with_null_renderer_id():
    route = _base_route(renderer_id=None, cost_category=None)
    # This route is schema-invalid on its own (READY requires renderer_id),
    # but validate_contract is exercised directly (not via schema_errors) to
    # prove ITS OWN capability-missing check fires independent of schema.
    doc = _doc([route])
    doc["renderer_registry_sha256"] = vr.compute_renderer_registry_sha256(set(), REGISTRY)
    doc["routes_content_sha256"] = vr.compute_routes_content_sha256(doc)
    problems = vr.validate_contract(
        doc, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={}, renderer_registry=REGISTRY)
    check("a missing capability is an error even when renderer_id is null",
          any("declares no renderer capability" in p for p in problems), str(problems))


def s5_unimplemented_renderer_refused():
    route = _base_route(visual_type="DOCUMENT", renderer_id="deterministic_document",
                        cost_category="free_local",
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": {"doc_kind": "notification",
                                                "fields": [{"name": "a", "value": "b"}],
                                                "source_refs": [{"label": "a", "citation": "b"}]},
                                    "photo": None, "illustration": None, "reenactment": None})
    doc = _finalize(_doc([route]))
    problems = vr.validate_contract(
        doc, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"DOCUMENT": "deterministic_document"}, renderer_registry=REGISTRY)
    check("an unimplemented renderer is refused", any("not implemented" in p for p in problems),
          str(problems))


def s5_cost_and_paid_disagreement():
    route = _base_route(renderer_id="india_geojson", cost_category="paid_api", paid=False)
    doc = _finalize(_doc([route]))
    problems = vr.validate_contract(
        doc, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a cost_category disagreement with the registry is caught",
          any("cost_category" in p for p in problems), str(problems))
    route2 = _base_route(renderer_id="flux_illustration", visual_type="ILLUSTRATION",
                         cost_category="paid_api", paid=False,
                         route_args={"map": None, "chart": None, "timeline": None,
                                     "document": None, "photo": None,
                                     "illustration": {"prompt": "x", "style_tags": []},
                                     "reenactment": None})
    doc2 = _finalize(_doc([route2]))
    problems2 = vr.validate_contract(
        doc2, manifest=_manifest_for(route2), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"ILLUSTRATION": "flux_illustration"}, renderer_registry=REGISTRY)
    check("paid disagreeing with a paid_api renderer is caught",
          any("paid=" in p for p in problems2), str(problems2))


def s5_manifest_exact_equality_all_fields():
    route = _base_route()
    manifest = _manifest_for(route)
    doc = _finalize(_doc([route]))
    problems = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a fully matching route has no manifest problems", problems == [], str(problems))

    for field, wrong in (("visual_asset_id", "VIS-999-A"), ("source_ids", ["SRC-999"]),
                         ("shot_instance_id", "SRC-999-I01"), ("output_file", "wrong.png"),
                         ("scene_id", "SCENE-999")):
        bad_manifest = json.loads(json.dumps(manifest))
        key = {"output_file": "image", "scene_id": "id"}.get(field, field)
        bad_manifest["scenes"][0][key] = wrong
        problems = vr.validate_contract(
            doc, manifest=bad_manifest, manifest_sha256=MANIFEST_SHA,
            governing_channel_binding=_channel_binding(),
            expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
        check(f"a manifest mismatch on {field} is caught", len(problems) > 0, str(problems))


def s5_manifest_coverage_empty_missing_extra_duplicates():
    route = _base_route()
    manifest = _manifest_for(route)

    empty_doc = _finalize(_doc([]))
    problems = vr.validate_contract(
        empty_doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("an empty routing document against a non-empty manifest is rejected",
          any("no routes" in p for p in problems), str(problems))

    two_scene_manifest = json.loads(json.dumps(manifest))
    two_scene_manifest["scenes"].append({"id": "SCENE-002", "image": "shot-02.png",
                                         "visual_asset_id": "VIS-002-A",
                                         "source_ids": ["SRC-002"],
                                         "shot_instance_id": "SRC-002-I01"})
    doc_missing = _finalize(_doc([route]))
    problems2 = vr.validate_contract(
        doc_missing, manifest=two_scene_manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a manifest identity with no corresponding route is rejected",
          any("VIS-002-A" in p and "no corresponding route" in p for p in problems2), str(problems2))

    extra_route = _base_route(visual_asset_id="VIS-999-A", scene_id="SCENE-999",
                              output_file="shot-999.png", shot_instance_id="SRC-999-I01")
    doc_extra = _finalize(_doc([route, extra_route]))
    problems3 = vr.validate_contract(
        doc_extra, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a route with no corresponding manifest identity is rejected",
          any("VIS-999-A" in p and "does not correspond" in p for p in problems3), str(problems3))

    dup_vid = _base_route()
    doc_dup_vid = _finalize(_doc([route, dup_vid]))
    problems4 = vr.validate_contract(
        doc_dup_vid, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
    check("a duplicate visual_asset_id across routes is rejected",
          any("duplicate visual_asset_id" in p for p in problems4), str(problems4))

    dup_file = _base_route(visual_asset_id="VIS-001-A")
    dup_file["output_file"] = route["output_file"]
    dup_file["shot_instance_id"] = "SRC-002-I01"
    dup_manifest = json.loads(json.dumps(manifest))
    problems5 = vr.check_manifest_coverage([route, dict(route, visual_asset_id="VIS-002-A")],
                                           dup_manifest)
    check("a duplicate output_file across routes is rejected",
          any("duplicate output_file" in p for p in problems5), str(problems5))

    dup_identity_manifest = json.loads(json.dumps(manifest))
    dup_identity_manifest["scenes"].append(dict(dup_identity_manifest["scenes"][0]))
    problems6 = vr.check_manifest_coverage([route], dup_identity_manifest)
    check("a manifest identity appearing more than once in the manifest is rejected",
          any("appears more than once in the manifest" in p for p in problems6), str(problems6))


def s5_manifest_coverage_rejects_synthetic_identities_even_if_matching_on_both_sides():
    """A synthesized UNRESOLVED-* identity is rejected directly — even in
    the pathological case where the manifest happens to carry the exact
    same placeholder string as the route. Coincidence of a sentinel value
    is not identity."""
    manifest = {"scenes": [{"id": "SCENE-001", "image": "shot-01.png",
                            "visual_asset_id": "UNRESOLVED-shot-01.png",
                            "source_ids": [], "shot_instance_id": "UNRESOLVED-shot-01.png"}]}
    route = _base_route(visual_asset_id="UNRESOLVED-shot-01.png",
                        shot_instance_id="UNRESOLVED-shot-01.png", source_ids=[])
    problems = vr.check_manifest_coverage([route], manifest)
    check("a synthesized UNRESOLVED-* identity is rejected even when the manifest "
          "coincidentally carries the identical sentinel string",
          len(problems) >= 2, str(problems))  # both the route AND the manifest scene flagged


def s5_manifest_coverage_rejects_padded_placeholder_identities():
    """A padded UNRESOLVED-* sentinel (leading space, tab, or trailing
    spaces) is exactly as synthetic as the unpadded form and must not
    bypass the placeholder check merely by carrying whitespace.
    visual_asset_id and shot_instance_id are tested independently, on both
    the manifest side and the route side, for three distinct padding shapes."""
    for padded in ("  UNRESOLVED-shot.png", "\tUNRESOLVED-shot.png", "UNRESOLVED-shot.png  "):
        manifest_vid = {"scenes": [{"id": "SCENE-001", "image": "shot.png",
                                    "visual_asset_id": padded,
                                    "source_ids": [], "shot_instance_id": "SRC-001-I01"}]}
        problems = vr.check_manifest_coverage([], manifest_vid)
        check(f"a manifest scene with padded placeholder visual_asset_id {padded!r} is rejected",
              any("visual_asset_id" in p for p in problems), str(problems))

        manifest_sid = {"scenes": [{"id": "SCENE-001", "image": "shot.png",
                                    "visual_asset_id": "VIS-001-A",
                                    "source_ids": [], "shot_instance_id": padded}]}
        problems2 = vr.check_manifest_coverage([], manifest_sid)
        check(f"a manifest scene with padded placeholder shot_instance_id {padded!r} is rejected",
              any("shot_instance_id" in p for p in problems2), str(problems2))

        route_vid = _base_route(visual_asset_id=padded, source_ids=[])
        problems3 = vr.check_manifest_coverage([route_vid], {"scenes": []})
        check(f"a route with padded placeholder visual_asset_id {padded!r} is rejected",
              any("visual_asset_id" in p and "placeholder" in p for p in problems3), str(problems3))


def s5_full_path_padded_placeholder_visual_asset_id_blocks_execution():
    """The reproduction from the review: a manifest and route both carrying
    the same padded UNRESOLVED-* sentinel. Proves the full path, not just
    check_manifest_coverage in isolation: schema validation alone may accept
    the string (minLength:1 says nothing about content), but
    validate_contract must report the identity problem and
    execution_blockers/is_executable must both refuse to call it safe."""
    for padded in ("  UNRESOLVED-shot.png", "\tUNRESOLVED-shot.png"):
        route = _base_route(visual_asset_id=padded)
        manifest = {"scenes": [{"id": route["scene_id"], "image": route["output_file"],
                                "visual_asset_id": padded,
                                "source_ids": route["source_ids"],
                                "shot_instance_id": route["shot_instance_id"]}]}
        doc = _finalize(_doc([route]))

        schema_errs = vr.schema_errors(doc)
        check(f"schema validation alone accepts a padded UNRESOLVED- visual_asset_id {padded!r}",
              schema_errs == [], str(schema_errs))

        kwargs = dict(manifest=manifest, manifest_sha256=MANIFEST_SHA,
                      governing_channel_binding=_channel_binding(), expected_project_id="p",
                      renderer_capabilities={"MAP": "india_geojson"}, renderer_registry=REGISTRY)
        problems = vr.validate_contract(doc, **kwargs)
        check(f"validate_contract reports the identity problem for padded {padded!r}",
              len(problems) > 0, str(problems))

        blockers = vr.execution_blockers(doc, **kwargs)
        check(f"execution_blockers is non-empty for padded {padded!r}",
              len(blockers) > 0, str(blockers))
        check(f"is_executable is false for padded {padded!r}",
              vr.is_executable(doc, **kwargs) is False)


def s5_manifest_coverage_rejects_missing_or_blank_identities():
    manifest = {"scenes": [{"id": "SCENE-001", "image": "shot-01.png",
                            "visual_asset_id": None, "shot_instance_id": "SRC-001-I01"}]}
    problems = vr.check_manifest_coverage([], manifest)
    check("a manifest scene with a null visual_asset_id is rejected",
          any("visual_asset_id" in p for p in problems), str(problems))

    manifest2 = {"scenes": [{"id": "SCENE-001", "image": "shot-01.png",
                             "visual_asset_id": "VIS-001-A", "shot_instance_id": "   "}]}
    problems2 = vr.check_manifest_coverage([], manifest2)
    check("a manifest scene with a blank shot_instance_id is rejected",
          any("shot_instance_id" in p for p in problems2), str(problems2))


# ── real approved-asset vocabulary: pose/reference resolution ───────────────

def _write_asset(base: Path, rel: str, content: bytes = b"asset-bytes") -> tuple[Path, str]:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p, sha(p)


def s5_host_pose_real_vocabulary_approved_and_approved_scene_bound():
    root = temp_root()
    asset_base = root / "pack"
    poses_root = asset_base / "poses"
    _, good_hash = _write_asset(asset_base, "poses/neutral.png")
    _, scene_hash = _write_asset(asset_base, "poses/desk.png")

    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id="neutral", host_scene_bound=False,
                        host_renderer_id="approved_pose_compositor")
    caps = {"MAP": "india_geojson", "HOST_COMPOSITE": "approved_pose_compositor"}
    manifest = _manifest_for(route)

    approved = {"neutral": {"status": "approved", "path": "poses/neutral.png",
                            "sha256": good_hash, "generic_compositing_allowed": True,
                            "includes_geometry": []}}
    doc = _finalize(_doc([route]))
    problems = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_poses=approved,
        poses_asset_base=asset_base, poses_root=poses_root)
    check("a real approved pose record (status=approved) resolves cleanly",
          problems == [], str(problems))

    scene_route = _base_route(host_present=True, host_method="approved_pose_composite",
                              host_pose_id="desk", host_scene_bound=True,
                              host_renderer_id="approved_pose_compositor")
    scene_manifest = _manifest_for(scene_route)
    scene_doc = _finalize(_doc([scene_route]))
    scene_approved = {"desk": {"status": "approved_scene_bound", "path": "poses/desk.png",
                              "sha256": scene_hash, "generic_compositing_allowed": False,
                              "includes_geometry": ["desk", "chair"]}}
    problems2 = vr.validate_contract(
        scene_doc, manifest=scene_manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_poses=scene_approved,
        poses_asset_base=asset_base, poses_root=poses_root)
    check("a real approved_scene_bound pose with host_scene_bound=true resolves cleanly",
          problems2 == [], str(problems2))

    scene_route_unbound = dict(scene_route)
    scene_route_unbound["host_scene_bound"] = False
    unbound_doc = _finalize(_doc([scene_route_unbound]))
    problems3 = vr.validate_contract(
        unbound_doc, manifest=_manifest_for(scene_route_unbound), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_poses=scene_approved,
        poses_asset_base=asset_base, poses_root=poses_root)
    check("approved_scene_bound with host_scene_bound=false is refused (mirrors pose_registry.resolve())",
          any("scene-bound tableau" in p for p in problems3), str(problems3))


def s5_host_pose_missing_record_and_bad_status_and_traversal():
    root = temp_root()
    asset_base = root / "pack"
    poses_root = asset_base / "poses"
    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id="neutral", host_scene_bound=False,
                        host_renderer_id="approved_pose_compositor")
    caps = {"MAP": "india_geojson", "HOST_COMPOSITE": "approved_pose_compositor"}
    manifest = _manifest_for(route)
    doc = _finalize(_doc([route]))

    problems = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_poses={},
        poses_asset_base=asset_base, poses_root=poses_root)
    check("a pose with no registered record at all is rejected (this is what 'cross-channel' "
          "collapses to — a pose from another channel simply is not in this registry)",
          any("not a registered asset" in p for p in problems), str(problems))

    bad_status = {"neutral": {"status": "candidate", "path": "poses/neutral.png",
                              "sha256": "x", "generic_compositing_allowed": True}}
    problems2 = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_poses=bad_status,
        poses_asset_base=asset_base, poses_root=poses_root)
    check("a non-approved status (e.g. candidate) is rejected",
          any("not approved" in p for p in problems2), str(problems2))

    traversal = {"neutral": {"status": "approved", "path": "../../escape.png",
                             "sha256": "x", "generic_compositing_allowed": True,
                             "includes_geometry": []}}
    problems3 = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_poses=traversal,
        poses_asset_base=asset_base, poses_root=poses_root)
    check("a traversing registered path is rejected",
          any("traverses upward" in p for p in problems3), str(problems3))

    forbidden_dir = {"neutral": {"status": "approved", "path": "poses/archive/neutral.png",
                                 "sha256": "x", "generic_compositing_allowed": True,
                                 "includes_geometry": []}}
    problems4 = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_poses=forbidden_dir,
        poses_asset_base=asset_base, poses_root=poses_root)
    check("a path through archive/ (candidate/raw/pending/archive) is rejected",
          any("not approved output" in p for p in problems4), str(problems4))


def s5_host_pose_missing_file_missing_hash_evidence_and_hash_mismatch():
    root = temp_root()
    asset_base = root / "pack"
    poses_root = asset_base / "poses"
    _, real_hash = _write_asset(asset_base, "poses/neutral.png")

    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id="neutral", host_scene_bound=False,
                        host_renderer_id="approved_pose_compositor")
    caps = {"MAP": "india_geojson", "HOST_COMPOSITE": "approved_pose_compositor"}
    manifest = _manifest_for(route)
    doc = _finalize(_doc([route]))

    missing_file = {"neutral": {"status": "approved", "path": "poses/does_not_exist.png",
                                "sha256": real_hash, "generic_compositing_allowed": True,
                                "includes_geometry": []}}
    problems = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_poses=missing_file,
        poses_asset_base=asset_base, poses_root=poses_root)
    check("a registered pose whose file is missing is rejected",
          any("registered asset missing" in p for p in problems), str(problems))

    missing_hash = {"neutral": {"status": "approved", "path": "poses/neutral.png",
                                "generic_compositing_allowed": True, "includes_geometry": []}}
    problems2 = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_poses=missing_hash,
        poses_asset_base=asset_base, poses_root=poses_root)
    check("a pose record with no sha256 evidence is rejected outright, not silently skipped",
          any("no valid sha256 evidence" in p for p in problems2), str(problems2))

    wrong_hash = {"neutral": {"status": "approved", "path": "poses/neutral.png",
                             "sha256": "0" * 64, "generic_compositing_allowed": True,
                             "includes_geometry": []}}
    problems3 = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_poses=wrong_hash,
        poses_asset_base=asset_base, poses_root=poses_root)
    check("a genuine live hash mismatch is rejected, distinct from a missing file",
          any("hash mismatch" in p for p in problems3), str(problems3))

    if hasattr(os, "symlink"):
        outside = root / "outside"
        outside.mkdir()
        _, outside_hash = _write_asset(outside, "evil.png")
        link = asset_base / "poses" / "linked.png"
        try:
            os.symlink(outside / "evil.png", link)
        except OSError:
            print("  SKIP  pose symlink escape — this account cannot create symlinks")
        else:
            linked = {"neutral": {"status": "approved", "path": "poses/linked.png",
                                  "sha256": outside_hash, "generic_compositing_allowed": True,
                                  "includes_geometry": []}}
            problems4 = vr.validate_contract(
                doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
                governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
                renderer_registry=REGISTRY, approved_poses=linked,
                poses_asset_base=asset_base, poses_root=poses_root)
            check("a symlinked pose escaping poses_root is rejected",
                  any("escapes" in p for p in problems4), str(problems4))


def s5_asset_directory_path_malformed_hash_and_read_failure_pose_and_reference():
    """Registry paths can identify things that are not regular files, hash
    evidence can be malformed rather than merely absent, and the filesystem
    can fail mid-read — none of these may raise out of validate_contract or
    execution_blockers; each must come back as a blocker string instead.
    Pose and reference verification share _verify_asset_path_and_hash, so
    both are exercised independently rather than assuming the shared
    helper's behaviour carries over untested."""
    root = temp_root()
    asset_base = root / "pack"
    poses_root = asset_base / "poses"
    (poses_root / "a_directory").mkdir(parents=True, exist_ok=True)
    _, real_hash = _write_asset(asset_base, "poses/neutral.png")

    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id="neutral", host_scene_bound=False,
                        host_renderer_id="approved_pose_compositor")
    caps = {"MAP": "india_geojson", "HOST_COMPOSITE": "approved_pose_compositor"}
    manifest = _manifest_for(route)
    doc = _finalize(_doc([route]))
    kwargs = dict(manifest=manifest, manifest_sha256=MANIFEST_SHA,
                 governing_channel_binding=_channel_binding(), expected_project_id="p",
                 renderer_capabilities=caps, renderer_registry=REGISTRY,
                 poses_asset_base=asset_base, poses_root=poses_root)

    dir_pose = {"neutral": {"status": "approved", "path": "poses/a_directory",
                            "sha256": real_hash, "generic_compositing_allowed": True,
                            "includes_geometry": []}}
    problems = vr.validate_contract(doc, approved_poses=dir_pose, **kwargs)
    check("a pose whose registered path resolves to a directory is rejected, not crashed on",
          any("not a regular file" in p for p in problems), str(problems))
    blockers = vr.execution_blockers(doc, approved_poses=dir_pose, **kwargs)
    check("execution_blockers also returns a blocker (not a raise) for a directory pose path",
          len(blockers) > 0, str(blockers))

    for bad_hash in (12345, "not-a-hex-string", "0" * 63, ""):
        malformed = {"neutral": {"status": "approved", "path": "poses/neutral.png",
                                 "sha256": bad_hash, "generic_compositing_allowed": True,
                                 "includes_geometry": []}}
        problems2 = vr.validate_contract(doc, approved_poses=malformed, **kwargs)
        check(f"a pose with malformed sha256 evidence {bad_hash!r} is rejected, not crashed on",
              any("no valid sha256 evidence" in p for p in problems2), str(problems2))

    good_pose = {"neutral": {"status": "approved", "path": "poses/neutral.png",
                             "sha256": real_hash, "generic_compositing_allowed": True,
                             "includes_geometry": []}}
    real_read_bytes = Path.read_bytes

    def failing_read_bytes(self, *a, **kw):
        if self.name == "neutral.png":
            raise OSError("simulated filesystem read failure")
        return real_read_bytes(self, *a, **kw)

    with mock.patch.object(Path, "read_bytes", failing_read_bytes):
        problems3 = vr.validate_contract(doc, approved_poses=good_pose, **kwargs)
    check("a simulated read failure on an otherwise-valid pose is caught as a problem, "
          "not raised out of validate_contract",
          any("could not read" in p for p in problems3), str(problems3))
    with mock.patch.object(Path, "read_bytes", failing_read_bytes):
        blockers3 = vr.execution_blockers(doc, approved_poses=good_pose, **kwargs)
    check("execution_blockers does not raise on a simulated read failure either",
          len(blockers3) > 0, str(blockers3))

    # ── the same three failure modes, for reference resolution ──────────
    ref_base = root / "refs"
    refs_root = ref_base / "reference"
    (refs_root / "a_dir").mkdir(parents=True, exist_ok=True)
    _, ref_hash = _write_asset(ref_base, "reference/ref1.png")

    ref_route = _base_route(visual_type="ILLUSTRATION", renderer_id="flux_illustration",
                            cost_category="paid_api", paid=True,
                            route_args={"map": None, "chart": None, "timeline": None,
                                        "document": None, "photo": None,
                                        "illustration": {"prompt": "x", "style_tags": []},
                                        "reenactment": None},
                            host_present=True, host_method="reference_anchored_generation",
                            host_reference_asset_ids=["ref1"])
    ref_manifest = _manifest_for(ref_route)
    ref_caps = {"ILLUSTRATION": "flux_illustration"}
    ref_doc = _finalize(_doc([ref_route]))
    ref_kwargs = dict(manifest=ref_manifest, manifest_sha256=MANIFEST_SHA,
                      governing_channel_binding=_channel_binding(), expected_project_id="p",
                      renderer_capabilities=ref_caps, renderer_registry=REGISTRY,
                      references_asset_base=ref_base, references_root=refs_root)

    dir_ref = {"ref1": {"status": "approved", "path": "reference/a_dir", "sha256": ref_hash}}
    problems4 = vr.validate_contract(ref_doc, approved_references=dir_ref, **ref_kwargs)
    check("a reference whose registered path resolves to a directory is rejected, not crashed on",
          any("not a regular file" in p for p in problems4), str(problems4))
    blockers4 = vr.execution_blockers(ref_doc, approved_references=dir_ref, **ref_kwargs)
    check("execution_blockers also returns a blocker (not a raise) for a directory reference path",
          len(blockers4) > 0, str(blockers4))

    for bad_hash in (12345, "not-a-hex-string", "0" * 63, ""):
        malformed_ref = {"ref1": {"status": "approved", "path": "reference/ref1.png",
                                  "sha256": bad_hash}}
        problems5 = vr.validate_contract(ref_doc, approved_references=malformed_ref, **ref_kwargs)
        check(f"a reference with malformed sha256 evidence {bad_hash!r} is rejected, not "
              f"crashed on", any("no valid sha256 evidence" in p for p in problems5),
              str(problems5))

    good_ref = {"ref1": {"status": "approved", "path": "reference/ref1.png", "sha256": ref_hash}}

    def failing_read_bytes_ref(self, *a, **kw):
        if self.name == "ref1.png":
            raise OSError("simulated filesystem read failure")
        return real_read_bytes(self, *a, **kw)

    with mock.patch.object(Path, "read_bytes", failing_read_bytes_ref):
        problems6 = vr.validate_contract(ref_doc, approved_references=good_ref, **ref_kwargs)
    check("a simulated read failure on an otherwise-valid reference is caught as a problem, "
          "not raised out of validate_contract",
          any("could not read" in p for p in problems6), str(problems6))
    with mock.patch.object(Path, "read_bytes", failing_read_bytes_ref):
        blockers6 = vr.execution_blockers(ref_doc, approved_references=good_ref, **ref_kwargs)
    check("execution_blockers does not raise on a simulated reference read failure either",
          len(blockers6) > 0, str(blockers6))


def s5_pose_and_reference_path_resolution_exceptions_are_caught():
    """Path.resolve() can raise, not just fail cleanly: a symlink loop raises
    RuntimeError, and a registered path with an embedded NUL character raises
    ValueError. Neither may escape validate_contract()/execution_blockers()/
    is_executable() -- both must come back as ordinary blockers, exactly like
    a missing file or a hash mismatch, and the asset must never be read."""
    root = temp_root()
    asset_base = root / "pack"
    poses_root = asset_base / "poses"
    poses_root.mkdir(parents=True, exist_ok=True)

    def spying_read_bytes(self, *a, **kw):
        raise AssertionError(f"read_bytes() was called on {self} -- resolution should have "
                             f"failed before any read was attempted")

    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id="neutral", host_scene_bound=False,
                        host_renderer_id="approved_pose_compositor")
    caps = {"MAP": "india_geojson", "HOST_COMPOSITE": "approved_pose_compositor"}
    manifest = _manifest_for(route)
    doc = _finalize(_doc([route]))
    kwargs = dict(manifest=manifest, manifest_sha256=MANIFEST_SHA,
                 governing_channel_binding=_channel_binding(), expected_project_id="p",
                 renderer_capabilities=caps, renderer_registry=REGISTRY,
                 poses_asset_base=asset_base, poses_root=poses_root)

    # ── symlink loop ────────────────────────────────────────────────────
    if hasattr(os, "symlink"):
        loop_a = poses_root / "loop_a.png"
        loop_b = poses_root / "loop_b.png"
        try:
            os.symlink(loop_b, loop_a)
            os.symlink(loop_a, loop_b)
        except OSError:
            print("  SKIP  pose symlink-loop resolution — this account cannot create symlinks")
        else:
            loop_pose = {"neutral": {"status": "approved", "path": "poses/loop_a.png",
                                     "sha256": "a" * 64, "generic_compositing_allowed": True,
                                     "includes_geometry": []}}
            with mock.patch.object(Path, "read_bytes", spying_read_bytes):
                problems = vr.validate_contract(doc, approved_poses=loop_pose, **kwargs)
                check("a symlink-loop pose path is rejected via validate_contract, not raised",
                      len(problems) > 0, str(problems))
                blockers = vr.execution_blockers(doc, approved_poses=loop_pose, **kwargs)
                check("execution_blockers also returns a blocker (not a raise) for a "
                      "symlink-loop pose path", len(blockers) > 0, str(blockers))
                check("is_executable is False for a symlink-loop pose path",
                      vr.is_executable(doc, approved_poses=loop_pose, **kwargs) is False)
            check("no routing artifact was created by a symlink-loop pose failure",
                  not (root / vr.ROUTES_NAME).exists())

    # ── embedded NUL byte in the registered path ───────────────────────
    nul_pose = {"neutral": {"status": "approved", "path": "poses/bad\x00asset.png",
                            "sha256": "a" * 64, "generic_compositing_allowed": True,
                            "includes_geometry": []}}
    with mock.patch.object(Path, "read_bytes", spying_read_bytes):
        problems2 = vr.validate_contract(doc, approved_poses=nul_pose, **kwargs)
        check("a pose path with an embedded NUL byte is rejected via validate_contract, "
              "not raised", len(problems2) > 0, str(problems2))
        blockers2 = vr.execution_blockers(doc, approved_poses=nul_pose, **kwargs)
        check("execution_blockers also returns a blocker (not a raise) for an embedded-NUL "
              "pose path", len(blockers2) > 0, str(blockers2))
        check("is_executable is False for an embedded-NUL pose path",
              vr.is_executable(doc, approved_poses=nul_pose, **kwargs) is False)
    check("no routing artifact was created by an embedded-NUL pose failure",
          not (root / vr.ROUTES_NAME).exists())

    # ── the same two failure modes, for reference resolution ───────────
    ref_base = root / "refs"
    refs_root = ref_base / "reference"
    refs_root.mkdir(parents=True, exist_ok=True)
    ref_route = _base_route(visual_type="ILLUSTRATION", renderer_id="flux_illustration",
                            cost_category="paid_api", paid=True,
                            route_args={"map": None, "chart": None, "timeline": None,
                                        "document": None, "photo": None,
                                        "illustration": {"prompt": "x", "style_tags": []},
                                        "reenactment": None},
                            host_present=True, host_method="reference_anchored_generation",
                            host_reference_asset_ids=["ref1"])
    ref_manifest = _manifest_for(ref_route)
    ref_caps = {"ILLUSTRATION": "flux_illustration"}
    ref_doc = _finalize(_doc([ref_route]))
    ref_kwargs = dict(manifest=ref_manifest, manifest_sha256=MANIFEST_SHA,
                      governing_channel_binding=_channel_binding(), expected_project_id="p",
                      renderer_capabilities=ref_caps, renderer_registry=REGISTRY,
                      references_asset_base=ref_base, references_root=refs_root)

    if hasattr(os, "symlink"):
        rloop_a = refs_root / "rloop_a.png"
        rloop_b = refs_root / "rloop_b.png"
        try:
            os.symlink(rloop_b, rloop_a)
            os.symlink(rloop_a, rloop_b)
        except OSError:
            print("  SKIP  reference symlink-loop resolution — this account cannot create "
                 "symlinks")
        else:
            loop_ref = {"ref1": {"status": "approved", "path": "reference/rloop_a.png",
                                 "sha256": "a" * 64}}
            with mock.patch.object(Path, "read_bytes", spying_read_bytes):
                problems3 = vr.validate_contract(ref_doc, approved_references=loop_ref,
                                                 **ref_kwargs)
                check("a symlink-loop reference path is rejected via validate_contract, "
                      "not raised", len(problems3) > 0, str(problems3))
                blockers3 = vr.execution_blockers(ref_doc, approved_references=loop_ref,
                                                  **ref_kwargs)
                check("execution_blockers also returns a blocker (not a raise) for a "
                      "symlink-loop reference path", len(blockers3) > 0, str(blockers3))
                check("is_executable is False for a symlink-loop reference path",
                      vr.is_executable(ref_doc, approved_references=loop_ref,
                                       **ref_kwargs) is False)
            check("no routing artifact was created by a symlink-loop reference failure",
                  not (ref_base / vr.ROUTES_NAME).exists())

    nul_ref = {"ref1": {"status": "approved", "path": "reference/bad\x00ref.png",
                        "sha256": "a" * 64}}
    with mock.patch.object(Path, "read_bytes", spying_read_bytes):
        problems4 = vr.validate_contract(ref_doc, approved_references=nul_ref, **ref_kwargs)
        check("a reference path with an embedded NUL byte is rejected via validate_contract, "
              "not raised", len(problems4) > 0, str(problems4))
        blockers4 = vr.execution_blockers(ref_doc, approved_references=nul_ref, **ref_kwargs)
        check("execution_blockers also returns a blocker (not a raise) for an embedded-NUL "
              "reference path", len(blockers4) > 0, str(blockers4))
        check("is_executable is False for an embedded-NUL reference path",
              vr.is_executable(ref_doc, approved_references=nul_ref, **ref_kwargs) is False)
    check("no routing artifact was created by an embedded-NUL reference failure",
          not (ref_base / vr.ROUTES_NAME).exists())


def s5_pose_contradictory_records_rejected():
    """A registry record that contradicts itself is rejected before any
    path/hash check runs, independent of whether the route's own
    host_scene_bound would otherwise be a fit."""
    root = temp_root()
    asset_base = root / "pack"
    poses_root = asset_base / "poses"
    _, good_hash = _write_asset(asset_base, "poses/neutral.png")
    caps = {"MAP": "india_geojson", "HOST_COMPOSITE": "approved_pose_compositor"}

    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id="neutral", host_scene_bound=False,
                        host_renderer_id="approved_pose_compositor")
    manifest = _manifest_for(route)
    doc = _finalize(_doc([route]))

    approved_gca_false = {"neutral": {"status": "approved", "path": "poses/neutral.png",
                                      "sha256": good_hash,
                                      "generic_compositing_allowed": False,
                                      "includes_geometry": []}}
    problems = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p",
        renderer_capabilities=caps, renderer_registry=REGISTRY,
        approved_poses=approved_gca_false, poses_asset_base=asset_base, poses_root=poses_root)
    check("status=approved with generic_compositing_allowed=false is a contradictory record",
          any("contradictory record" in p for p in problems), str(problems))

    approved_with_geometry = {"neutral": {"status": "approved", "path": "poses/neutral.png",
                                          "sha256": good_hash,
                                          "generic_compositing_allowed": True,
                                          "includes_geometry": ["desk"]}}
    problems2 = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p",
        renderer_capabilities=caps, renderer_registry=REGISTRY,
        approved_poses=approved_with_geometry, poses_asset_base=asset_base,
        poses_root=poses_root)
    check("status=approved carrying baked scene geometry is a contradictory record",
          any("contradictory record" in p for p in problems2), str(problems2))

    scene_bound_route = _base_route(host_present=True, host_method="approved_pose_composite",
                                    host_pose_id="neutral", host_scene_bound=True,
                                    host_renderer_id="approved_pose_compositor")
    scene_bound_doc = _finalize(_doc([scene_bound_route]))
    approved_ok = {"neutral": {"status": "approved", "path": "poses/neutral.png",
                              "sha256": good_hash, "generic_compositing_allowed": True,
                              "includes_geometry": []}}
    problems3 = vr.validate_contract(
        scene_bound_doc, manifest=_manifest_for(scene_bound_route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p",
        renderer_capabilities=caps, renderer_registry=REGISTRY,
        approved_poses=approved_ok, poses_asset_base=asset_base, poses_root=poses_root)
    check("a generic approved pose used with host_scene_bound=true is rejected "
          "(stricter than pose_registry.resolve())",
          any("generic approved pose" in p for p in problems3), str(problems3))

    scene_bound_missing_geometry = {"neutral": {"status": "approved_scene_bound",
                                                "path": "poses/neutral.png",
                                                "sha256": good_hash,
                                                "generic_compositing_allowed": False,
                                                "includes_geometry": []}}
    problems4 = vr.validate_contract(
        scene_bound_doc, manifest=_manifest_for(scene_bound_route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p",
        renderer_capabilities=caps, renderer_registry=REGISTRY,
        approved_poses=scene_bound_missing_geometry, poses_asset_base=asset_base,
        poses_root=poses_root)
    check("status=approved_scene_bound with empty includes_geometry is a contradictory record",
          any("must be a non-empty list" in p for p in problems4), str(problems4))

    non_bool_scene_bound_route = dict(scene_bound_route)
    non_bool_scene_bound_route["host_scene_bound"] = "true"  # a malformed string, not bool
    problems5 = vr.validate_contract(
        _finalize(_doc([non_bool_scene_bound_route])),
        manifest=_manifest_for(non_bool_scene_bound_route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p",
        renderer_capabilities=caps, renderer_registry=REGISTRY,
        approved_poses={"neutral": {"status": "approved_scene_bound",
                                    "path": "poses/neutral.png", "sha256": good_hash,
                                    "generic_compositing_allowed": False,
                                    "includes_geometry": ["desk"]}},
        poses_asset_base=asset_base, poses_root=poses_root)
    check("a string 'true' for host_scene_bound is never coerced — rejected as not exactly true",
          any("scene-bound tableau" in p for p in problems5), str(problems5))


def s5_host_validation_never_silently_skipped_when_context_is_omitted():
    """approved_poses/approved_references are optional parameters only
    because host-free routes don't need them — a route that DOES request a
    host method must still be blocked when the caller omits the applicable
    registry or containment root, never silently passed."""
    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id="neutral", host_scene_bound=False,
                        host_renderer_id="approved_pose_compositor")
    caps = {"MAP": "india_geojson", "HOST_COMPOSITE": "approved_pose_compositor"}
    manifest = _manifest_for(route)
    doc = _finalize(_doc([route]))

    problems = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p",
        renderer_capabilities=caps, renderer_registry=REGISTRY)
    check("approved_pose_composite with no approved_poses/asset_base/root supplied at all "
          "is still blocked, never silently allowed",
          len(problems) > 0, str(problems))

    ref_route = _base_route(visual_type="ILLUSTRATION", renderer_id="flux_illustration",
                            cost_category="paid_api", paid=True,
                            route_args={"map": None, "chart": None, "timeline": None,
                                        "document": None, "photo": None,
                                        "illustration": {"prompt": "x", "style_tags": []},
                                        "reenactment": None},
                            host_present=True, host_method="reference_anchored_generation",
                            host_reference_asset_ids=["ref1"])
    ref_caps = {"ILLUSTRATION": "flux_illustration"}
    ref_problems = vr.validate_contract(
        _doc([ref_route]), manifest=_manifest_for(ref_route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p",
        renderer_capabilities=ref_caps, renderer_registry=REGISTRY)
    check("reference_anchored_generation with no approved_references/asset_base/root "
          "supplied at all is still blocked, never silently allowed",
          len(ref_problems) > 0, str(ref_problems))


def s5_reference_anchored_requires_supporting_renderer_and_active_reference():
    # Task 2B-B2a: reference-anchored routes now resolve against a
    # capability key suffixed _REFERENCE (mirroring the existing
    # HOST_COMPOSITE precedent) and must name exactly both approved
    # masters — never a single arbitrary reference id — so this fixture's
    # capability map and host_reference_asset_ids are updated to match the
    # newly-approved contract; the behavior under test (missing reference,
    # unsupported renderer, valid route accepted) is unchanged.
    root = temp_root()
    ref_base = root / "refs"
    refs_root = ref_base / "reference"
    _, body_hash = _write_asset(ref_base, "reference/body_master.png")
    _, face_hash = _write_asset(ref_base, "reference/face_master.png")

    route = _base_route(visual_type="ILLUSTRATION", renderer_id="flux_illustration",
                        cost_category="paid_api", paid=True,
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": None, "photo": None,
                                    "illustration": {"prompt": "x", "style_tags": []},
                                    "reenactment": None},
                        host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"])
    manifest = _manifest_for(route)
    caps = {"ILLUSTRATION_REFERENCE": "flux_illustration"}
    doc = _finalize(_doc([route]))

    problems = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_references={},
        references_asset_base=ref_base, references_root=refs_root)
    check("a missing reference record is rejected", any("not a registered asset" in p
                                                        for p in problems), str(problems))

    no_ref_support = json.loads(json.dumps(REGISTRY))
    no_ref_support["flux_illustration"]["supports_reference_input"] = False
    active_ref = {"body_master": {"status": "approved", "path": "reference/body_master.png",
                                  "sha256": body_hash},
                 "face_master": {"status": "approved", "path": "reference/face_master.png",
                                 "sha256": face_hash}}
    problems2 = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=no_ref_support, approved_references=active_ref,
        references_asset_base=ref_base, references_root=refs_root)
    check("a renderer without supports_reference_input refuses reference-anchored generation",
          any("does not declare" in p for p in problems2), str(problems2))

    ok = vr.validate_contract(
        doc, manifest=manifest, manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p", renderer_capabilities=caps,
        renderer_registry=REGISTRY, approved_references=active_ref,
        references_asset_base=ref_base, references_root=refs_root)
    check("a valid reference-anchored generation route with a supporting renderer is accepted",
          ok == [], str(ok))


def s5_reference_anchored_never_substitutes_for_deterministic_types():
    root = temp_root()
    ref_base = root / "refs"
    refs_root = ref_base / "reference"
    _, ref_hash = _write_asset(ref_base, "reference/ref1.png")
    active_ref = {"ref1": {"status": "approved", "path": "reference/ref1.png", "sha256": ref_hash}}

    for vt, renderer_id, caps in (
        ("MAP", "india_geojson", {"MAP": "india_geojson"}),
        ("CHART", "matplotlib_chart", {"CHART": "matplotlib_chart"}),
        ("TIMELINE", "deterministic_timeline", {"TIMELINE": "deterministic_timeline"}),
        ("DOCUMENT", "deterministic_document", {"DOCUMENT": "deterministic_document"}),
        ("PHOTO", "pexels", {"PHOTO": "pexels"}),
    ):
        route = _base_route(visual_type=vt, renderer_id=renderer_id,
                            host_present=True, host_method="reference_anchored_generation",
                            host_reference_asset_ids=["ref1"],
                            route_args={k: None for k in
                                       ("map", "chart", "timeline", "document", "photo",
                                        "illustration", "reenactment")})
        problems = vr.validate_contract(
            _doc([route]), manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
            governing_channel_binding=_channel_binding(),
            expected_project_id="p", renderer_capabilities=caps, renderer_registry=REGISTRY,
            approved_references=active_ref, references_asset_base=ref_base,
            references_root=refs_root)
        check(f"reference_anchored_generation is refused for deterministic {vt}",
              any("only permitted for" in p for p in problems), str(problems))


def s5_second_channel_capability_isolation():
    """Same route, same manifest — only the governing pack's capability map
    differs. channel_id is matched to the binding in both cases so the only
    variable under test is whether MAP is declared, not a channel-binding
    mismatch (which would confound the assertion)."""
    route = _base_route(visual_type="MAP", renderer_id="india_geojson",
                        cost_category="free_local")
    caps_a = {"MAP": "india_geojson"}
    caps_b = {"PHOTO": "pexels"}  # second pack declares no MAP capability at all
    doc_a = _finalize(_doc([route], channel_id="a"))
    doc_b = _finalize(_doc([route], channel_id="b"))
    problems_a = vr.validate_contract(
        doc_a, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding("a"),
        expected_project_id="p", renderer_capabilities=caps_a, renderer_registry=REGISTRY)
    problems_b = vr.validate_contract(
        doc_b, manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding("b"),
        expected_project_id="p", renderer_capabilities=caps_b, renderer_registry=REGISTRY)
    check("a route valid under its own pack's capability map has no integrity problems",
          problems_a == [], str(problems_a))
    check("the identical route is refused under a second pack that declares no matching "
          "capability, and refused specifically because of that (this assertion genuinely "
          "fails if capability-checking is ever accidentally removed)",
          problems_b != [] and any("declares no renderer capability" in p for p in problems_b),
          str(problems_b))


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
    check("valid legacy PHOTO -> READY with a real renderer/cost binding",
          r1["status"] == "READY" and r1["visual_type"] == "PHOTO"
          and r1["renderer_id"] == "pexels" and r1["cost_category"] == "free_api")

    r2 = by_scene["SCENE-002"]
    check("legacy HOST -> NEEDS_REVIEW, host_present, host_method null, visual_type null",
          r2["status"] == "NEEDS_REVIEW" and r2["host_present"] is True
          and r2["host_method"] is None and r2["visual_type"] is None)
    check("HOST reason is AMBIGUOUS_LEGACY_HOST",
          any(rr["code"] == "AMBIGUOUS_LEGACY_HOST" for rr in r2["review_reasons"]))

    r3 = by_scene["SCENE-003"]
    check("valid legacy MAP -> READY with a real renderer/cost binding",
          r3["status"] == "READY" and r3["visual_type"] == "MAP"
          and r3["renderer_id"] == "india_geojson" and r3["cost_category"] == "free_local")

    r4 = by_scene["SCENE-004"]
    check("incomplete legacy MAP -> NEEDS_REVIEW, visual_type still MAP, renderer_id null",
          r4["status"] == "NEEDS_REVIEW" and r4["visual_type"] == "MAP"
          and r4["renderer_id"] is None)

    r5 = by_scene["SCENE-005"]
    check("valid legacy CHART (bar) -> READY with a real renderer/cost binding",
          r5["status"] == "READY" and r5["visual_type"] == "CHART"
          and r5["renderer_id"] == "matplotlib_chart" and r5["cost_category"] == "free_local")

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
    check("a second legacy PHOTO row with a resolved manifest identity -> READY with a "
          "real renderer/cost binding",
          r9["status"] == "READY" and r9["visual_type"] == "PHOTO"
          and r9["renderer_id"] == "pexels" and r9["cost_category"] == "free_api")

    check("every migrated route carries confidence=null, never a fabricated sentinel",
          all(r["routing_confidence"] is None for r in result["doc"]["routes"]))
    check("no READY route was ever written with a null renderer_id or cost_category",
          all(not (r["status"] == "READY" and (r["renderer_id"] is None
                                               or r["cost_category"] is None))
              for r in result["doc"]["routes"]))

    errs = vr.schema_errors(result["doc"])
    check("the full migrated artifact is schema-valid", errs == [], str(errs))
    check("visual_routes.json and .md were written",
          (proj / vr.ROUTES_NAME).is_file() and (proj / vr.ROUTES_MD_NAME).is_file())
    check("adapter matches a fresh render", vr.adapter_drift(proj) is None)


def s6_migration_downgrades_to_needs_review_without_a_usable_renderer():
    root = temp_root()
    # Non-empty (the schema requires minProperties:1) but declares no
    # MAP/CHART/PHOTO capability at all.
    make_pack(root / "channels", "testchan", capabilities={"DOCUMENT": "deterministic_document"})
    proj = make_project(root, "proj", "testchan")
    with World(root):
        result = mig.migrate(proj / "image_prompts_one_line_per_prompt.md", proj, dry_run=True)
    by_scene = {r["scene_id"]: r for r in result["doc"]["routes"]}
    r1 = by_scene["SCENE-001"]  # would otherwise be a clean READY PHOTO
    check("an otherwise-clean PHOTO route downgrades to NEEDS_REVIEW when the pack has no "
          "PHOTO capability", r1["status"] == "NEEDS_REVIEW"
          and any(rr["code"] == "RENDERER_UNAVAILABLE" for rr in r1["review_reasons"]))
    check("the downgraded route never carries a renderer_id or cost_category",
          r1["renderer_id"] is None and r1["cost_category"] is None)


def s6_migrated_output_passes_integrity_validation_before_writing():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    with World(root):
        result = mig.migrate(proj / "image_prompts_one_line_per_prompt.md", proj, dry_run=True)
        ctx = cc.load_channel_for_project(proj)
    manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
    manifest_sha256 = hashlib_sha_file(proj / "manifest.json")
    problems = vr.validate_contract(
        result["doc"], manifest=manifest, manifest_sha256=manifest_sha256,
        governing_channel_binding=ctx.plan_binding(), expected_project_id=proj.name,
        renderer_capabilities=dict(ctx.renderer_capabilities), renderer_registry=renderers.RENDERERS)
    check("the migrated artifact passes full artifact-integrity validation before writing",
          problems == [], str(problems))


def hashlib_sha_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


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


def s6_refuses_unmatched_legacy_row_before_writing():
    """Exact coverage (§1) means an unresolved legacy row is no longer
    tolerated as a NEEDS_REVIEW placeholder in a written artifact — the
    whole migration must refuse before a single byte is written."""
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = root / "unmatched_proj"
    proj.mkdir(parents=True, exist_ok=True)
    manifest = {"episode": "unmatched_proj", "identity_state": "ok", "identity_reasons": [],
                "scenes": [], "channel_id": "testchan", "channel_dna_version": 1}
    (proj / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    legacy_md = (
        '**SHOT 01** · SCENE-001 · standalone → `shot-01.png` TYPE: PHOTO '
        'NARRATION: "no manifest scene resolves for this row" PROMPT: a building '
        'OVERLAY: x CUE: hold\n'
    )
    (proj / "image_prompts_one_line_per_prompt.md").write_text(legacy_md, encoding="utf-8")
    with World(root):
        try:
            mig.migrate(proj / "image_prompts_one_line_per_prompt.md", proj)
            check("a legacy row with no matching manifest scene refuses migration before "
                  "writing", False, "it succeeded")
        except mig.MigrationError:
            check("a legacy row with no matching manifest scene refuses migration before "
                  "writing", True)
    check("nothing was written for the unmatched-row migration",
          not (proj / vr.ROUTES_NAME).exists() and not (proj / vr.ROUTES_MD_NAME).exists())


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


def s6_refuses_external_legacy_input():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    outside = Path(tempfile.mkdtemp())
    _temps.append(outside)
    external_md = outside / "image_prompts_one_line_per_prompt.md"
    external_md.write_text(LEGACY_PROMPTS, encoding="utf-8")
    with World(root):
        try:
            mig.migrate(external_md, proj)
            check("a legacy input outside the pipeline root is refused", False, "it succeeded")
        except mig.MigrationError:
            check("a legacy input outside the pipeline root is refused", True)


def s6_refuses_unmanifested_legacy_input():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    (root / "no_manifest_here").mkdir()
    orphan_md = root / "no_manifest_here" / "image_prompts_one_line_per_prompt.md"
    orphan_md.write_text(LEGACY_PROMPTS, encoding="utf-8")
    with World(root):
        try:
            mig.migrate(orphan_md, proj)
            check("a legacy input with no manifest.json alongside it is refused", False,
                  "it succeeded")
        except mig.MigrationError:
            check("a legacy input with no manifest.json alongside it is refused", True)


def s6_refuses_cross_channel_legacy_input():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    make_pack(root / "channels", "otherchan")
    proj = make_project(root, "proj", "testchan")
    other_proj = make_project(root, "other_proj", "otherchan")
    with World(root):
        try:
            mig.migrate(other_proj / "image_prompts_one_line_per_prompt.md", proj)
            check("a legacy input belonging to a different channel is refused", False,
                  "it succeeded")
        except mig.MigrationError:
            check("a legacy input belonging to a different channel is refused", True)


def s6_refuses_missing_source_channel_binding():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    orphan = root / "orphan"
    orphan.mkdir(parents=True, exist_ok=True)
    manifest = {"episode": "orphan", "identity_state": "ok", "identity_reasons": [],
                "scenes": []}  # no channel_id/channel_dna_version at all
    (orphan / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (orphan / "image_prompts_one_line_per_prompt.md").write_text(LEGACY_PROMPTS, encoding="utf-8")
    with World(root):
        try:
            mig.migrate(orphan / "image_prompts_one_line_per_prompt.md", proj)
            check("a legacy input with no channel_id/channel_dna_version at all is refused",
                  False, "it succeeded")
        except mig.MigrationError:
            check("a legacy input with no channel_id/channel_dna_version at all is refused",
                  True)


def s6_refuses_source_channel_id_present_but_dna_version_missing():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    orphan = root / "orphan2"
    orphan.mkdir(parents=True, exist_ok=True)
    manifest = {"episode": "orphan2", "identity_state": "ok", "identity_reasons": [],
                "scenes": [], "channel_id": "testchan"}  # channel_dna_version missing
    (orphan / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (orphan / "image_prompts_one_line_per_prompt.md").write_text(LEGACY_PROMPTS, encoding="utf-8")
    with World(root):
        try:
            mig.migrate(orphan / "image_prompts_one_line_per_prompt.md", proj)
            check("a legacy input with channel_id but no channel_dna_version is refused",
                  False, "it succeeded")
        except mig.MigrationError:
            check("a legacy input with channel_id but no channel_dna_version is refused",
                  True)


def s6_refuses_matching_channel_id_but_mismatched_dna_version():
    root = temp_root()
    make_pack(root / "channels", "testchan")
    proj = make_project(root, "proj", "testchan")
    other = root / "other_version"
    other.mkdir(parents=True, exist_ok=True)
    manifest = {"episode": "other_version", "identity_state": "ok", "identity_reasons": [],
                "scenes": [], "channel_id": "testchan", "channel_dna_version": 2}
    (other / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (other / "image_prompts_one_line_per_prompt.md").write_text(LEGACY_PROMPTS, encoding="utf-8")
    with World(root):
        try:
            mig.migrate(other / "image_prompts_one_line_per_prompt.md", proj)
            check("a matching channel_id with a mismatched channel_dna_version is refused",
                  False, "it succeeded")
        except mig.MigrationError:
            check("a matching channel_id with a mismatched channel_dna_version is refused",
                  True)


def s6_refuses_malformed_channel_id():
    """channel_id must be a canonical channel identifier -- a str, non-empty,
    with no leading/trailing whitespace, matching CHANNEL_ID_RE via a full
    match. A non-str value (int, bool) is neither None nor blank so a naive
    _blank()-style check would wave it through; a string with internal
    whitespace or separators is non-empty after stripping the ends but is
    still not a real identifier. Tested on BOTH the source and the target
    manifest, independently, for every malformed shape, and in every case:
    load_channel_for_project/parse_shots are never called and nothing is
    written."""
    for bad in (123, True, "", "   ", " testchan ", "test/chan", "../evil"):
        root = temp_root()
        make_pack(root / "channels", "testchan")
        proj = make_project(root, "proj", "testchan")
        bad_src = root / "bad_src"
        bad_src.mkdir(parents=True, exist_ok=True)
        src_manifest = {"episode": "bad_src", "identity_state": "ok", "identity_reasons": [],
                        "scenes": [], "channel_id": bad, "channel_dna_version": 1}
        (bad_src / "manifest.json").write_text(json.dumps(src_manifest, indent=2), encoding="utf-8")
        (bad_src / "image_prompts_one_line_per_prompt.md").write_text(LEGACY_PROMPTS,
                                                                       encoding="utf-8")
        with World(root), \
             mock.patch.object(cc, "load_channel_for_project") as m_load, \
             mock.patch.object(route_images, "parse_shots") as m_parse:
            try:
                mig.migrate(bad_src / "image_prompts_one_line_per_prompt.md", proj)
                check(f"a source channel_id of {bad!r} is refused", False, "it succeeded")
            except mig.MigrationError:
                check(f"a source channel_id of {bad!r} is refused", True)
            check(f"load_channel_for_project was never called for source channel_id {bad!r}",
                  not m_load.called)
            check(f"parse_shots was never called for source channel_id {bad!r}",
                  not m_parse.called)
        check(f"nothing was written when source channel_id was {bad!r}",
              not (proj / vr.ROUTES_NAME).exists() and not (proj / vr.ROUTES_MD_NAME).exists())

        root2 = temp_root()
        make_pack(root2 / "channels", "testchan")
        good_src_proj = make_project(root2, "good_src", "testchan")
        bad_target = root2 / "bad_target"
        bad_target.mkdir(parents=True, exist_ok=True)
        target_manifest = {"episode": "bad_target", "identity_state": "ok",
                           "identity_reasons": [], "scenes": [],
                           "channel_id": bad, "channel_dna_version": 1}
        (bad_target / "manifest.json").write_text(json.dumps(target_manifest, indent=2),
                                                   encoding="utf-8")
        with World(root2), \
             mock.patch.object(cc, "load_channel_for_project") as m_load2, \
             mock.patch.object(route_images, "parse_shots") as m_parse2:
            try:
                mig.migrate(good_src_proj / "image_prompts_one_line_per_prompt.md", bad_target)
                check(f"a target channel_id of {bad!r} is refused", False, "it succeeded")
            except mig.MigrationError:
                check(f"a target channel_id of {bad!r} is refused", True)
            check(f"load_channel_for_project was never called for target channel_id {bad!r}",
                  not m_load2.called)
            check(f"parse_shots was never called for target channel_id {bad!r}",
                  not m_parse2.called)
        check(f"nothing was written when target channel_id was {bad!r}",
              not (bad_target / vr.ROUTES_NAME).exists()
              and not (bad_target / vr.ROUTES_MD_NAME).exists())

    # the legitimate path must still work with a valid canonical channel_id
    root3 = temp_root()
    make_pack(root3 / "channels", "testchan")
    proj3 = make_project(root3, "proj", "testchan")
    with World(root3):
        result = mig.migrate(proj3 / "image_prompts_one_line_per_prompt.md", proj3)
    check("a valid canonical channel_id on both sides still migrates successfully",
          result.get("written") is True, str(result))


def s6_refuses_blank_or_malformed_dna_version():
    """channel_dna_version must be a real positive integer, not merely
    'truthy'. A digit-only string, a bool, zero, or blank/whitespace all
    look superficially plausible but must be refused before
    load_channel_for_project/parse_shots/any output — not left to crash
    later with int('') or silently coerced. Tested on BOTH the source and
    the target manifest, independently, for every malformed shape, and in
    every case no visual_routes.json/.md is written."""
    for bad in ("", "   ", "1", True, 0):
        root = temp_root()
        make_pack(root / "channels", "testchan")
        proj = make_project(root, "proj", "testchan")
        bad_src = root / "bad_src"
        bad_src.mkdir(parents=True, exist_ok=True)
        src_manifest = {"episode": "bad_src", "identity_state": "ok", "identity_reasons": [],
                        "scenes": [], "channel_id": "testchan", "channel_dna_version": bad}
        (bad_src / "manifest.json").write_text(json.dumps(src_manifest, indent=2), encoding="utf-8")
        (bad_src / "image_prompts_one_line_per_prompt.md").write_text(LEGACY_PROMPTS,
                                                                       encoding="utf-8")
        with World(root):
            try:
                mig.migrate(bad_src / "image_prompts_one_line_per_prompt.md", proj)
                check(f"a source channel_dna_version of {bad!r} is refused", False,
                      "it succeeded")
            except mig.MigrationError:
                check(f"a source channel_dna_version of {bad!r} is refused", True)
        check(f"nothing was written when source channel_dna_version was {bad!r}",
              not (proj / vr.ROUTES_NAME).exists() and not (proj / vr.ROUTES_MD_NAME).exists())

        root2 = temp_root()
        make_pack(root2 / "channels", "testchan")
        good_src_proj = make_project(root2, "good_src", "testchan")
        bad_target = root2 / "bad_target"
        bad_target.mkdir(parents=True, exist_ok=True)
        target_manifest = {"episode": "bad_target", "identity_state": "ok",
                           "identity_reasons": [], "scenes": [],
                           "channel_id": "testchan", "channel_dna_version": bad}
        (bad_target / "manifest.json").write_text(json.dumps(target_manifest, indent=2),
                                                   encoding="utf-8")
        with World(root2):
            try:
                mig.migrate(good_src_proj / "image_prompts_one_line_per_prompt.md", bad_target)
                check(f"a target channel_dna_version of {bad!r} is refused", False,
                      "it succeeded")
            except mig.MigrationError:
                check(f"a target channel_dna_version of {bad!r} is refused", True)
        check(f"nothing was written when target channel_dna_version was {bad!r}",
              not (bad_target / vr.ROUTES_NAME).exists()
              and not (bad_target / vr.ROUTES_MD_NAME).exists())


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


# ── 7. output path containment (resolve_output_target) ─────────────────────

def s7_resolve_output_target_accepts_a_contained_filename():
    root = temp_root()
    images_root = root / "images"
    images_root.mkdir()
    resolved = vr.resolve_output_target(images_root, "shot-01.png")
    check("a bare filename resolves inside images_root",
          resolved.parent.resolve() == images_root.resolve())


def s7_resolve_output_target_rejects_traversal_and_separators():
    root = temp_root()
    images_root = root / "images"
    images_root.mkdir()
    for bad in ("../escape.png", "sub/escape.png", "sub\\escape.png",
               "..\\escape.png", "C:\\evil.png", "", ".", ".."):
        try:
            vr.resolve_output_target(images_root, bad)
            check(f"resolve_output_target rejects {bad!r}", False, "it resolved")
        except vr.VisualRoutesError:
            check(f"resolve_output_target rejects {bad!r}", True)


def s7_resolve_output_target_rejects_symlink_escape():
    root = temp_root()
    images_root = root / "images"
    images_root.mkdir()
    outside = root / "outside"
    outside.mkdir()
    link = images_root / "linked_dir"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        print("  SKIP  output-target symlink escape — this account cannot create symlinks")
        return
    try:
        vr.resolve_output_target(images_root, "linked_dir")
        check("a symlinked escape target is rejected", False, "it resolved")
    except vr.VisualRoutesError:
        check("a symlinked escape target is rejected", True)


# ── 8. Task 2B-B2a: capability-key precision, and the canonical loader ─────

def s8_reference_anchored_requires_exactly_both_masters_not_a_single_id():
    root = temp_root()
    ref_base = root / "refs"
    refs_root = ref_base / "reference"
    _, body_hash = _write_asset(ref_base, "reference/body_master.png")

    route = _base_route(visual_type="ILLUSTRATION", renderer_id="flux_illustration",
                        cost_category="paid_api", paid=True,
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": None, "photo": None,
                                    "illustration": {"prompt": "x", "style_tags": []},
                                    "reenactment": None},
                        host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master"])
    caps = {"ILLUSTRATION_REFERENCE": "flux_illustration"}
    active_ref = {"body_master": {"status": "approved", "path": "reference/body_master.png",
                                  "sha256": body_hash}}
    problems = vr.validate_contract(
        _doc([route]), manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p",
        renderer_capabilities=caps, renderer_registry=REGISTRY,
        approved_references=active_ref, references_asset_base=ref_base, references_root=refs_root)
    check("a single reference id (missing face_master) is rejected — exactly both "
          "approved masters are required",
          any("requires exactly" in p for p in problems), str(problems))


def s8_reenactment_reference_undeclared_stays_blocked():
    root = temp_root()
    ref_base = root / "refs"
    refs_root = ref_base / "reference"
    _, body_hash = _write_asset(ref_base, "reference/body_master.png")
    _, face_hash = _write_asset(ref_base, "reference/face_master.png")

    route = _base_route(visual_type="REENACTMENT", renderer_id="flux_illustration",
                        cost_category="paid_api", paid=True,
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": None, "photo": None, "illustration": None,
                                    "reenactment": {"prompt": "x", "reference_asset_ids": []}},
                        host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"])
    # REENACTMENT_REFERENCE deliberately not declared (amendment 4, option B) —
    # only ILLUSTRATION_REFERENCE is registered.
    caps = {"ILLUSTRATION_REFERENCE": "flux_illustration"}
    active_ref = {"body_master": {"status": "approved", "path": "reference/body_master.png",
                                  "sha256": body_hash},
                 "face_master": {"status": "approved", "path": "reference/face_master.png",
                                 "sha256": face_hash}}
    problems = vr.validate_contract(
        _doc([route]), manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p",
        renderer_capabilities=caps, renderer_registry=REGISTRY,
        approved_references=active_ref, references_asset_base=ref_base, references_root=refs_root)
    check("a REENACTMENT reference-anchored route is blocked when "
          "REENACTMENT_REFERENCE is not declared",
          any("declares no renderer capability for REENACTMENT_REFERENCE" in p
              for p in problems), str(problems))


def s8_ordinary_capability_resolution_is_unaffected():
    """Regression guard: the new _REFERENCE-suffixed branch only applies to
    reference_anchored_generation routes — an ordinary ILLUSTRATION route
    (no host) still resolves against the plain, unsuffixed key exactly as
    before."""
    route = _base_route(visual_type="ILLUSTRATION", renderer_id="flux_illustration",
                        cost_category="paid_api", paid=True,
                        route_args={"map": None, "chart": None, "timeline": None,
                                    "document": None, "photo": None,
                                    "illustration": {"prompt": "x", "style_tags": []},
                                    "reenactment": None})
    caps = {"ILLUSTRATION": "flux_illustration"}
    problems = vr.validate_contract(
        _finalize(_doc([route])), manifest=_manifest_for(route), manifest_sha256=MANIFEST_SHA,
        governing_channel_binding=_channel_binding(), expected_project_id="p",
        renderer_capabilities=caps, renderer_registry=REGISTRY)
    check("an ordinary (non-reference-anchored) ILLUSTRATION route is unaffected "
          "by the new capability-key branch", problems == [], str(problems))

    also_composite = _base_route(host_present=True, host_method="approved_pose_composite",
                                 host_pose_id="neutral", host_scene_bound=False,
                                 host_renderer_id="approved_pose_compositor")
    caps2 = {"MAP": "india_geojson", "HOST_COMPOSITE": "approved_pose_compositor"}
    problems2 = vr.validate_contract(
        _finalize(_doc([also_composite])), manifest=_manifest_for(also_composite),
        manifest_sha256=MANIFEST_SHA, governing_channel_binding=_channel_binding(),
        expected_project_id="p", renderer_capabilities=caps2, renderer_registry=REGISTRY,
        approved_poses={"neutral": {"status": "approved", "generic_compositing_allowed": True,
                                    "includes_geometry": [], "path": "poses/neutral.png",
                                    "sha256": "a" * 64}})
    check("an approved_pose_composite route (a different host_method) is also "
          "unaffected by the new capability-key branch — MAP still resolves "
          "against the plain key",
          not any("MAP_REFERENCE" in p for p in problems2), str(problems2))


# ── canonical loader: real on-disk projects ─────────────────────────────────

def _loader_project(root: Path, *, channel_id="loaderchan"):
    """A minimal, real, on-disk project: one MAP route, host-disabled pack —
    no character tree involved, keeping these tests focused on the loader's
    own load/schema/integrity/status bucketing rather than host verification
    (already covered exhaustively in section 5)."""
    make_pack(root / "channels", channel_id)
    proj = root / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    manifest = {"episode": "proj", "identity_state": "ok", "identity_reasons": [],
               "scenes": [{"id": "SCENE-001", "image": "shot-01.png",
                          "visual_asset_id": "VIS-001-A", "source_ids": ["SRC-001"],
                          "shot_instance_id": "SRC-001-I01"}],
               "channel_id": channel_id, "channel_dna_version": 1}
    (proj / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest_sha256 = sha(proj / "manifest.json")

    # inspect_project_routes() resolves the channel and the REAL
    # renderers.RENDERERS itself — this fixture must bind against those same
    # live sources, not the test-local REGISTRY fixture (narrower than the
    # real registry) or a hand-guessed voice_profile_sha256, or every
    # "fully clean" case would spuriously show drift.
    import renderers as real_renderers
    context = cc.load_channel(channel_id)

    route = _base_route(output_file="shot-01.png")
    doc = {
        "schema_version": 1, "project_id": "proj", "routes_id": "r1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "inputs": {"manifest_sha256": manifest_sha256},
        "channel": context.plan_binding(),
        "renderer_registry_sha256": vr.compute_renderer_registry_sha256(
            {"india_geojson"}, real_renderers.RENDERERS),
        "routes_content_sha256": "placeholder",
        "routes": [route],
    }
    doc["routes_content_sha256"] = vr.compute_routes_content_sha256(doc)
    vr.write_atomic(doc, proj)
    return proj, doc, manifest_sha256


def s8_inspect_missing_artifact_never_raises():
    root = temp_root()
    proj = root / "unmigrated"
    proj.mkdir(parents=True, exist_ok=True)
    result = vr.inspect_project_routes(proj, operation="test")
    check("inspect_project_routes never raises for a missing artifact",
          isinstance(result, vr.ProjectRoutesLoad))
    check("canonical routing is reported unavailable, with no instruction to "
          "run migrate_routes_from_markdown.py (corrective follow-up item 6)",
          any("canonical routing is unavailable" in p and "future canonical rebuild" in p
              and "fresh approval" in p and "migrate_routes_from_markdown" not in p
              for p in result.load_problems),
          str(result.load_problems))
    check("is_executable is False", result.is_executable is False)
    check("doc stayed None — nothing partially loaded", result.doc is None)


def s8_require_executable_raises_for_the_same_missing_case():
    root = temp_root()
    proj = root / "unmigrated2"
    proj.mkdir(parents=True, exist_ok=True)
    try:
        vr.require_executable_routes(proj, operation="test")
        check("require_executable_routes raises for a missing artifact", False, "no raise")
    except vr.VisualRoutesError as e:
        check("require_executable_routes raises for a missing artifact", True)
        check("the raised message carries the same blocker text",
              "canonical routing is unavailable" in str(e), str(e))


def s8_inspect_malformed_json_is_a_load_problem():
    root = temp_root()
    proj = root / "malformed"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / vr.ROUTES_NAME).write_text("{not valid json", encoding="utf-8")
    (proj / vr.ROUTES_MD_NAME).write_text("whatever", encoding="utf-8")
    result = vr.inspect_project_routes(proj, operation="test")
    check("malformed JSON is a load_problem, not a raise",
          any("could not read/parse" in p for p in result.load_problems),
          str(result.load_problems))
    check("is_executable is False", result.is_executable is False)


def s8_inspect_adapter_drift_is_a_load_problem():
    root = temp_root()
    with World(root):
        proj, doc, _ = _loader_project(root)
        # Hand-edit the .md so it no longer matches a fresh render — exactly
        # what adapter_drift() exists to catch.
        (proj / vr.ROUTES_MD_NAME).write_text("hand-edited, no longer generated\n",
                                              encoding="utf-8")
        result = vr.inspect_project_routes(proj, operation="test")
        check("a drifted visual_routes.md is a load_problem",
              any("does not match a fresh render" in p for p in result.load_problems),
              str(result.load_problems))


def s8_inspect_schema_invalid_doc_is_a_schema_problem_not_integrity():
    root = temp_root()
    with World(root):
        proj, doc, _ = _loader_project(root)
        broken = json.loads(json.dumps(doc))
        broken["schema_version"] = 99
        # Keep the .md/.json pair matching (schema_version alone doesn't
        # change render_routes_md()'s output) so adapter_drift() doesn't
        # mask the schema problem this test actually targets.
        vr._write_atomic_text(proj / vr.ROUTES_MD_NAME, vr.render_routes_md(broken))
        vr._write_atomic_text(proj / vr.ROUTES_NAME,
                              json.dumps(broken, indent=2, ensure_ascii=False))
        result = vr.inspect_project_routes(proj, operation="test")
        check("a schema-invalid doc lands in schema_problems, not integrity_problems",
              bool(result.schema_problems) and not result.integrity_problems, str(result))
        check("is_executable is False", result.is_executable is False)


def s8_inspect_stale_manifest_hash_is_an_integrity_problem():
    root = temp_root()
    with World(root):
        proj, doc, _ = _loader_project(root)
        (proj / "manifest.json").write_text(
            json.dumps({"episode": "proj", "identity_state": "ok", "identity_reasons": [],
                       "scenes": [{"id": "SCENE-001", "image": "shot-01.png",
                                  "visual_asset_id": "VIS-001-A", "source_ids": ["SRC-001"],
                                  "shot_instance_id": "SRC-001-I01"}, {"extra": "scene"}],
                       "channel_id": "loaderchan", "channel_dna_version": 1}, indent=2),
            encoding="utf-8")
        result = vr.inspect_project_routes(proj, operation="test")
        check("changing the manifest after the routes were built shows up in "
              "integrity_problems", bool(result.integrity_problems)
              and not result.schema_problems, str(result))


def s8_inspect_needs_review_route_is_a_status_problem_only():
    root = temp_root()
    with World(root):
        proj, doc, manifest_sha256 = _loader_project(root)
        needs_review_doc = json.loads(json.dumps(doc))
        needs_review_doc["routes"][0]["status"] = "NEEDS_REVIEW"
        needs_review_doc["routes"][0]["review_reasons"] = [{"code": "OTHER", "detail": "x"}]
        needs_review_doc["routes_content_sha256"] = vr.compute_routes_content_sha256(
            needs_review_doc)
        vr.write_atomic(needs_review_doc, proj)
        result = vr.inspect_project_routes(proj, operation="test")
        check("a NEEDS_REVIEW route surfaces only in status_problems",
              bool(result.status_problems) and not result.load_problems
              and not result.schema_problems and not result.integrity_problems,
              str(result))
        check("is_executable is False", result.is_executable is False)


def s8_inspect_fully_clean_project_is_executable():
    root = temp_root()
    with World(root):
        proj, doc, _ = _loader_project(root)
        result = vr.inspect_project_routes(proj, operation="test")
        check("a fully clean project has no problems in any bucket",
              result.execution_blockers == [], str(result.execution_blockers))
        check("is_executable is True", result.is_executable is True)
        check("doc is populated", result.doc is not None
              and result.doc["project_id"] == "proj")
        require = vr.require_executable_routes(proj, operation="test")
        check("require_executable_routes returns the same, non-raising result",
              require.doc["routes_id"] == "r1")


def s8_file_sha256_matches_plain_hashlib():
    root = temp_root()
    p = root / "x.txt"
    p.write_bytes(b"some bytes")
    check("file_sha256() matches hashlib.sha256(path.read_bytes())",
          vr.file_sha256(p) == hashlib.sha256(p.read_bytes()).hexdigest())


# ── 9. corrective follow-up: one canonical registry projection ─────────────

_MINIMAL_EXEC_ENTRY = {
    "module": "m.py", "entry": "main", "adapter": None,
    "provider": "xai", "model_id": "grok-imagine-image",
    "credential_env_var": "XAI_API_KEY", "base_url": "https://api.x.ai/v1",
    "contract_version": 1, "prompt_policy_version": "abc",
    "provider_parameters": {"n": 1}, "response_format_policy": "url_or_b64json",
    "download_timeout_seconds": 60, "supports_reference_input": False,
    "output_transform": None, "output_transform_version": 1,
    "implemented": True, "cost_category": "paid_api",
}


def s9_visual_routes_hashing_invokes_the_canonical_renderers_projection():
    import renderers as real_renderers
    sentinel = {"r": {"sentinel": True}}
    with mock.patch.object(real_renderers, "projection_for_hash",
                          return_value=sentinel) as mock_proj:
        h = vr.compute_renderer_registry_sha256({"r"}, {"r": _MINIMAL_EXEC_ENTRY})
    check("visual_routes.compute_renderer_registry_sha256 calls "
          "renderers.projection_for_hash exactly once", mock_proj.call_count == 1,
          str(mock_proj.call_count))
    check("the hash it returns is the canonical_sha256 of what the canonical "
          "projection produced, not an independently-reconstructed value",
          h == cc.canonical_sha256(sentinel), h)


def s9_only_one_field_projection_implementation_exists():
    import inspect as _inspect
    src = _inspect.getsource(vr.renderer_registry_projection)
    check("visual_routes.renderer_registry_projection() delegates to "
          "renderers.projection_for_hash rather than reconstructing the field "
          "list itself", "renderers.projection_for_hash(" in src, src)
    check("it does not re-declare the execution-affecting field names "
          "itself (no independent second implementation)",
          '"credential_env_var"' not in src and '"base_url"' not in src
          and '"response_format_policy"' not in src, src)


def s9_dispatch_and_hashing_read_the_same_renderers_object():
    import renderers as real_renderers
    proj = vr.renderer_registry_projection({"flux_illustration"}, real_renderers.RENDERERS)
    dispatched = real_renderers.dispatch_adapter("flux_illustration")
    check("the projection's adapter_qualname names exactly the function "
          "dispatch_adapter() returns for the same renderer_id",
          proj["flux_illustration"]["adapter_qualname"]
          == f"{dispatched.__module__}.{dispatched.__qualname__}")


def s9_every_execution_field_changes_the_visual_routes_hash():
    base_registry = {"r": dict(_MINIMAL_EXEC_ENTRY)}
    base_hash = vr.compute_renderer_registry_sha256({"r"}, base_registry)
    for field, new_value in (
        ("provider", "openai"), ("model_id", "other-model"),
        ("credential_env_var", "OTHER_KEY"), ("base_url", "https://example.invalid"),
        ("contract_version", 2), ("prompt_policy_version", "different"),
        ("provider_parameters", {"n": 2}), ("response_format_policy", "other"),
        ("download_timeout_seconds", 30), ("output_transform_version", 2),
        ("implemented", False), ("cost_category", "free_local"),
    ):
        mutated = {"r": dict(_MINIMAL_EXEC_ENTRY)}
        mutated["r"][field] = new_value
        h = vr.compute_renderer_registry_sha256({"r"}, mutated)
        check(f"changing {field!r} changes the visual_routes renderer_registry_sha256",
              h != base_hash, f"{field}: {new_value!r} did not change the hash")


def main() -> int:
    try:
        run("1a. a complete READY MAP route validates", s1_ready_map_valid)
        run("1b. READY MAP with empty regions is rejected", s1_ready_map_empty_regions_rejected)
        run("1c. CHART never accepts chart_type=timeline", s1_chart_never_accepts_timeline_type)
        run("1d. unknown fields fail even under NEEDS_REVIEW",
            s1_unknown_field_rejected_even_under_needs_review)
        run("1e. NEEDS_REVIEW requires a reason", s1_needs_review_requires_a_reason)
        run("1e2. READY forbids review_reasons/candidate_visual_types",
            s1_ready_forbids_review_reasons_and_candidates)
        run("1e3. READY requires non-empty source_ids", s1_ready_requires_nonempty_source_ids)
        run("1e4. READY requires renderer_id and cost_category",
            s1_ready_requires_renderer_id_and_cost_category)
        run("1f. strict route_args variants for all seven types",
            s1_strict_variants_for_all_seven_types)
        run("1g. host_present=false forbids host fields", s1_host_present_false_forbids_host_fields)
        run("1g2. host_present=false nulls every host field independently",
            s1_host_present_false_nulls_every_host_field_independently)
        run("1h. READY + host_present requires host_method",
            s1_ready_host_present_requires_host_method)
        run("1i. migrated NEEDS_REVIEW HOST validates; READY does not",
            s1_needs_review_host_may_leave_method_null)
        run("1j. approved_pose_composite requires pose_id + scene_bound + host_renderer_id",
            s1_approved_pose_composite_requires_pose_scene_bound_and_host_renderer)
        run("1k. approved_pose_composite forbids reference ids",
            s1_approved_pose_composite_forbids_reference_ids)
        run("1l. reference_anchored_generation requires non-empty reference ids",
            s1_reference_anchored_requires_nonempty_reference_ids)
        run("1m. reference_anchored_generation forbids pose fields and host_renderer_id",
            s1_reference_anchored_forbids_pose_fields_and_host_renderer_id)
        run("1n. READY DOCUMENT requires source_refs", s1_ready_document_requires_source_refs)
        run("1o. manual_override requires a reason", s1_manual_override_requires_reason)
        run("1p. routing_confidence=null is valid", s1_confidence_null_is_valid_never_required_to_be_a_number)
        run("1q. no independently mutable top-level summaries are storable",
            s1_no_top_level_summary_fields_stored)
        run("1r. output_file rejects paths, separators and traversal",
            s1_output_file_rejects_paths_and_traversal)

        run("2a. registry projection includes module/entry", s2_projection_includes_module_and_entry)
        run("2b. note excluded, module/reference-input included", s2_note_excluded_module_included)
        run("2c. an unregistered renderer id is refused", s2_unregistered_renderer_refused)
        run("2d. renderer registry drift detection", s2_drift_check_against_live_doc)

        run("3a. content hash stable across nonce/timestamp", s3_stable_across_nonce_and_timestamp)
        run("3b. content hash changes with route content", s3_changes_when_route_content_changes)
        run("3c. content hash changes with route order", s3_changes_when_route_order_changes)
        run("3d. content hash ignores formatting/key order", s3_formatting_and_key_order_do_not_matter)

        run("4a. write_atomic produces a matching pair", s4_write_atomic_produces_a_matching_pair)
        run("4a2. write_atomic refuses a stale content hash",
            s4_write_atomic_refuses_a_stale_content_hash)
        run("4b. adapter drift detects a hand edit", s4_adapter_drift_detects_hand_edit)
        run("4b2. adapter escapes table-breaking content and exposes review fields",
            s4_adapter_escapes_table_breaking_content)
        run("4c. an actual second-replacement failure cleans temp files and is detected",
            s4_simulated_second_replacement_failure_cleans_temp_files_and_is_detected)

        run("5a. a fully consistent READY route has no integrity problems", s5_ready_route_no_problems)
        run("5a2. integrity-valid NEEDS_REVIEW is never executable",
            s5_integrity_valid_but_needs_review_is_not_executable)
        run("5a3. a schema-invalid READY route is caught by execution_blockers itself",
            s5_execution_blockers_catches_a_schema_invalid_doc_even_if_status_says_ready)
        run("5a4. a wrong project_id blocks execution via validate_contract",
            s5_execution_blockers_catches_a_wrong_project_id)
        run("5b. a stale manifest hash is rejected by the main validator",
            s5_stale_manifest_hash_rejected_by_main_validator)
        run("5c. a stale content hash is rejected by the main validator",
            s5_stale_content_hash_rejected_by_main_validator)
        run("5d. a renderer redirect is rejected by the main validator",
            s5_renderer_redirect_rejected_by_main_validator)
        run("5e. channel binding mismatch is caught", s5_channel_binding_mismatch)
        run("5f. renderer_id must match the current capability",
            s5_renderer_id_must_match_current_capability)
        run("5f2. a missing capability is an error even with a null renderer_id",
            s5_ready_without_capability_rejected_even_with_null_renderer_id)
        run("5g. an unimplemented renderer is refused", s5_unimplemented_renderer_refused)
        run("5h. cost/paid disagreement is caught", s5_cost_and_paid_disagreement)
        run("5i. manifest exact-equality on every identity field", s5_manifest_exact_equality_all_fields)
        run("5j. manifest coverage: empty/missing/extra/duplicate rejected",
            s5_manifest_coverage_empty_missing_extra_duplicates)
        run("5j2. synthesized UNRESOLVED-* identities are rejected even if matching on "
            "both sides", s5_manifest_coverage_rejects_synthetic_identities_even_if_matching_on_both_sides)
        run("5j2b. padded UNRESOLVED-* placeholder identities are rejected "
            "(visual_asset_id and shot_instance_id independently)",
            s5_manifest_coverage_rejects_padded_placeholder_identities)
        run("5j2c. full path: a padded placeholder visual_asset_id blocks execution "
            "even though schema alone accepts it",
            s5_full_path_padded_placeholder_visual_asset_id_blocks_execution)
        run("5j3. manifest scenes with missing/blank identities are rejected",
            s5_manifest_coverage_rejects_missing_or_blank_identities)
        run("5k. real approved/approved_scene_bound pose vocabulary resolves correctly",
            s5_host_pose_real_vocabulary_approved_and_approved_scene_bound)
        run("5l. missing pose record / bad status / traversal / forbidden dir rejected",
            s5_host_pose_missing_record_and_bad_status_and_traversal)
        run("5m. missing file / missing hash evidence / hash mismatch rejected",
            s5_host_pose_missing_file_missing_hash_evidence_and_hash_mismatch)
        run("5m1b. directory path / malformed hash / simulated read failure rejected for "
            "pose AND reference, without raising",
            s5_asset_directory_path_malformed_hash_and_read_failure_pose_and_reference)
        run("5m1c. symlink-loop and embedded-NUL path resolution is caught, not raised, "
            "for pose AND reference",
            s5_pose_and_reference_path_resolution_exceptions_are_caught)
        run("5m2. contradictory pose registry records are rejected",
            s5_pose_contradictory_records_rejected)
        run("5m3. host validation is never silently skipped when context is omitted",
            s5_host_validation_never_silently_skipped_when_context_is_omitted)
        run("5n. reference-anchored generation requires a supporting renderer and active reference",
            s5_reference_anchored_requires_supporting_renderer_and_active_reference)
        run("5o. reference-anchored generation never substitutes for any deterministic type",
            s5_reference_anchored_never_substitutes_for_deterministic_types)
        run("5p. second-channel capability isolation (a genuinely falsifiable assertion)",
            s5_second_channel_capability_isolation)

        run("6a. full legacy markdown migration, end to end", s6_migration_end_to_end)
        run("6a2. migration downgrades to NEEDS_REVIEW without a usable renderer",
            s6_migration_downgrades_to_needs_review_without_a_usable_renderer)
        run("6a3. migrated output passes integrity validation before writing",
            s6_migrated_output_passes_integrity_validation_before_writing)
        run("6b. migrated HOST shape fails schema once marked READY",
            s6_migrated_host_shape_fails_if_marked_ready)
        run("6b2. an unmatched legacy row refuses migration before writing",
            s6_refuses_unmatched_legacy_row_before_writing)
        run("6c. --dry-run writes nothing", s6_dry_run_writes_nothing)
        run("6d. a missing legacy file is refused", s6_refuses_missing_legacy_file)
        run("6d2. an external legacy input is refused", s6_refuses_external_legacy_input)
        run("6d3. an unmanifested legacy input is refused", s6_refuses_unmanifested_legacy_input)
        run("6d4. a cross-channel legacy input is refused", s6_refuses_cross_channel_legacy_input)
        run("6d5. a legacy input with no channel binding at all is refused",
            s6_refuses_missing_source_channel_binding)
        run("6d6. a legacy input with channel_id but no channel_dna_version is refused",
            s6_refuses_source_channel_id_present_but_dna_version_missing)
        run("6d7. matching channel_id but mismatched channel_dna_version is refused",
            s6_refuses_matching_channel_id_but_mismatched_dna_version)
        run("6d8. blank/whitespace/digit-string/bool/zero channel_dna_version is refused "
            "on both source and target, nothing written",
            s6_refuses_blank_or_malformed_dna_version)
        run("6d9. non-str/blank/padded/separator-bearing channel_id is refused on both "
            "source and target before load_channel_for_project/parse_shots, nothing written",
            s6_refuses_malformed_channel_id)
        run("6e. migration never creates a missing project directory",
            s6_never_creates_a_missing_project_directory)
        run("6f. migration refuses an existing artifact; neither file changes",
            s6_refuses_existing_artifact_neither_file_changed)
        run("6g. migration refuses a project with no manifest", s6_refuses_missing_manifest)
        run("6h. the future provenance sidecar contract is structurally validated",
            s6_provenance_sidecar_structural_validation_only)

        run("7a. resolve_output_target accepts a contained filename",
            s7_resolve_output_target_accepts_a_contained_filename)
        run("7b. resolve_output_target rejects traversal/separators",
            s7_resolve_output_target_rejects_traversal_and_separators)
        run("7c. resolve_output_target rejects a symlink escape",
            s7_resolve_output_target_rejects_symlink_escape)

        run("8a. reference_anchored_generation requires exactly both masters, not a "
            "single id", s8_reference_anchored_requires_exactly_both_masters_not_a_single_id)
        run("8b. REENACTMENT_REFERENCE undeclared stays blocked",
            s8_reenactment_reference_undeclared_stays_blocked)
        run("8c. ordinary capability resolution is unaffected by the new branch",
            s8_ordinary_capability_resolution_is_unaffected)
        run("8d. inspect_project_routes never raises for a missing artifact",
            s8_inspect_missing_artifact_never_raises)
        run("8e. require_executable_routes raises for the same missing case",
            s8_require_executable_raises_for_the_same_missing_case)
        run("8f. malformed JSON is a load_problem, not a raise",
            s8_inspect_malformed_json_is_a_load_problem)
        run("8g. a drifted visual_routes.md is a load_problem",
            s8_inspect_adapter_drift_is_a_load_problem)
        run("8h. a schema-invalid doc lands in schema_problems, not integrity_problems",
            s8_inspect_schema_invalid_doc_is_a_schema_problem_not_integrity)
        run("8i. a stale manifest hash is an integrity_problem",
            s8_inspect_stale_manifest_hash_is_an_integrity_problem)
        run("8j. a NEEDS_REVIEW route is a status_problem only",
            s8_inspect_needs_review_route_is_a_status_problem_only)
        run("8k. a fully clean project is executable",
            s8_inspect_fully_clean_project_is_executable)
        run("8l. file_sha256 matches plain hashlib", s8_file_sha256_matches_plain_hashlib)

        run("9a. visual_routes hashing invokes the canonical renderers projection",
            s9_visual_routes_hashing_invokes_the_canonical_renderers_projection)
        run("9b. only one field-projection implementation exists",
            s9_only_one_field_projection_implementation_exists)
        run("9c. dispatch and hashing read the same RENDERERS object",
            s9_dispatch_and_hashing_read_the_same_renderers_object)
        run("9d. every execution field changes the visual_routes renderer hash",
            s9_every_execution_field_changes_the_visual_routes_hash)
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
