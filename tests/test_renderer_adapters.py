"""Task 2B-B2a — the single execution registry, DispatchContext, and the
typed adapters themselves.

No real provider/API/GPU/download call anywhere in this file: every
provider client and every subprocess boundary is mocked. Nothing here wires
an adapter into a live dispatcher — that is Task 2B-B2b — these tests
exercise the adapters directly, against mocked boundaries only.

    python tests/test_renderer_adapters.py
"""

import hashlib
import inspect
import sys
import tempfile
from pathlib import Path
from types import MappingProxyType
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import channel_context as cc               # noqa: E402
import generation_gate                     # noqa: E402
import prompt_policy                       # noqa: E402
import renderer_adapters as ra             # noqa: E402
import renderers                           # noqa: E402
import route_failures                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def run(title, fn):
    print(f"\n{title}")
    try:
        fn()
    except Exception as e:
        import traceback
        check(title, False, f"{type(e).__name__}: {e}")
        traceback.print_exc()


def temp_dir() -> Path:
    return Path(tempfile.mkdtemp())


def _base_route(**kw) -> dict:
    base = {
        "visual_asset_id": "VIS-001-A", "source_ids": ["SRC-001"],
        "shot_instance_id": "SRC-001-I01", "scene_id": "SCENE-001",
        "output_file": "shot-01.png",
        "status": "READY", "review_reasons": [], "candidate_visual_types": [],
        "routing_confidence": 0.9, "manual_override": None,
        "visual_type": "ILLUSTRATION",
        "route_args": {"map": None, "chart": None, "timeline": None, "document": None,
                       "photo": None,
                       "illustration": {"prompt": "a minister at a podium", "style_tags": []},
                       "reenactment": None},
        "narration": "n", "prompt": "a minister at a podium",
        "visual_cue": None, "overlay_text": None,
        "renderer_id": "flux_illustration", "host_renderer_id": None,
        "cost_category": "paid_api", "paid": True,
        "host_present": False, "host_method": None, "host_pose_id": None,
        "host_scene_bound": None, "host_reference_asset_ids": None, "host_placement": None,
    }
    base.update(kw)
    return base


# ── 1. one execution registry, no second mapping ────────────────────────────

def s1_single_registry_no_second_mapping():
    check("renderers.py declares no separate ADAPTERS mapping",
          not hasattr(renderers, "ADAPTERS"))
    check("dispatch_adapter() reads the exact object stored under RENDERERS",
          renderers.dispatch_adapter("flux_illustration")
          is renderers.RENDERERS["flux_illustration"]["adapter"])
    proj = renderers.projection_for_hash({"flux_illustration"})
    check("projection_for_hash() and dispatch_adapter() agree on the same entry's identity",
          proj["flux_illustration"]["adapter_qualname"]
          == f"{ra.adapt_flux.__module__}.{ra.adapt_flux.__qualname__}")


def s1_registry_entries_are_deeply_immutable():
    entry = renderers.RENDERERS["flux_illustration"]
    check("a RENDERERS entry is a MappingProxyType",
          isinstance(entry, MappingProxyType))
    try:
        entry["provider"] = "someone_else"
        check("mutating a top-level field of a registry entry is refused", False,
              "assignment succeeded")
    except TypeError:
        check("mutating a top-level field of a registry entry is refused", True)
    try:
        entry["provider_parameters"]["n"] = 99
        check("mutating a nested provider_parameters dict is refused", False,
              "assignment succeeded")
    except TypeError:
        check("mutating a nested provider_parameters dict is refused", True)
    check("provider_parameters is itself frozen",
          isinstance(entry["provider_parameters"], MappingProxyType))


def s1_dispatch_context_renderer_entry_is_the_frozen_entry():
    entry = renderers.RENDERERS["flux_illustration"]
    ctx = ra.DispatchContext(
        project_dir=Path("."), channel=None, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None, references_asset_base=None,
        references_root=None, output_root=Path("."), renderer_entry=entry)
    check("DispatchContext.renderer_entry is exactly this route's registry entry, "
          "still frozen", ctx.renderer_entry is entry
          and isinstance(ctx.renderer_entry, MappingProxyType))


def s1_adapter_signature_has_no_override_parameters():
    for name, fn in (("adapt_map", ra.adapt_map), ("adapt_chart", ra.adapt_chart),
                     ("adapt_photo", ra.adapt_photo), ("adapt_flux", ra.adapt_flux),
                     ("adapt_host_composite", ra.adapt_host_composite),
                     ("adapt_flux_reference_anchor", ra.adapt_flux_reference_anchor)):
        params = list(inspect.signature(fn).parameters)
        check(f"{name}() accepts exactly (route, target, ctx), no provider/model/"
              f"backend/prompt override parameter", params == ["route", "target", "ctx"],
              str(params))


# ── 2. widened projection: every field participates in the hash ────────────

_MINIMAL_ENTRY = {
    "module": "m.py", "entry": "main", "adapter": ra.adapt_map,
    "provider": "xai", "model_id": "grok-imagine-image", "contract_version": 1,
    "prompt_policy_version": "abc", "provider_parameters": {"n": 1},
    "supports_reference_input": False, "output_transform_version": 1,
    "implemented": True, "cost_category": "paid_api",
}


def s2_every_projected_field_changes_the_hash():
    import visual_routes as vr
    base_registry = {"r": dict(_MINIMAL_ENTRY)}
    base_hash = vr.compute_renderer_registry_sha256({"r"}, base_registry)
    for field, new_value in (
        ("provider", "openai"), ("model_id", "other-model"), ("contract_version", 2),
        ("prompt_policy_version", "different"), ("provider_parameters", {"n": 2}),
        ("output_transform_version", 2), ("implemented", False),
        ("cost_category", "free_local"),
    ):
        mutated = {"r": dict(_MINIMAL_ENTRY)}
        mutated["r"][field] = new_value
        h = vr.compute_renderer_registry_sha256({"r"}, mutated)
        check(f"changing {field!r} changes renderer_registry_sha256", h != base_hash,
              f"{field}: {new_value!r} did not change the hash")
    mutated_adapter = {"r": dict(_MINIMAL_ENTRY)}
    mutated_adapter["r"]["adapter"] = ra.adapt_chart
    h_adapter = vr.compute_renderer_registry_sha256({"r"}, mutated_adapter)
    check("changing the adapter callable itself changes renderer_registry_sha256",
          h_adapter != base_hash)


def s2_old_narrow_registry_hash_fails_closed_against_the_new_one():
    import visual_routes as vr
    narrow_registry = {"r": {"module": "m.py", "entry": "main",
                             "cost_category": "paid_api", "implemented": True,
                             "supports_reference_input": False}}
    rich_registry = {"r": dict(_MINIMAL_ENTRY)}
    h_narrow = vr.compute_renderer_registry_sha256({"r"}, narrow_registry)
    h_rich = vr.compute_renderer_registry_sha256({"r"}, rich_registry)
    check("a visual_routes.json hashed under the old narrow projection does not "
          "match the new, richer one — fail-closed by construction",
          h_narrow != h_rich)


# ── 3. prompt policy: no child-mascot text, correct branching ──────────────

_FORBIDDEN_CHILD_MASCOT_TERMS = (
    "chubby", "spiky", "short arms", "gathered ankle", "cartoon boy",
)


def s3_no_obsolete_child_mascot_text_anywhere_in_prompt_policy():
    import prompt_policy as pp
    haystack = " ".join((
        pp.STYLE_POLICY, pp.HOST_IDENTITY_POLICY, pp.REFERENCE_HOST_POLICY,
        pp.COMPOSITE_PLACEMENT_POLICY_TEMPLATE, pp.STYLE_SUFFIX,
    )).lower()
    for term in _FORBIDDEN_CHILD_MASCOT_TERMS:
        check(f"{term!r} does not appear anywhere in the executable prompt policy",
              term not in haystack)
    check("the adult host contract is present instead",
          "stylized indian male" in pp.HOST_IDENTITY_POLICY.lower()
          and "25" in pp.HOST_IDENTITY_POLICY)


def s3_host_present_false_produces_no_host_instructions():
    route = _base_route(host_present=False, host_method=None)
    prompt = prompt_policy.build_effective_prompt(route)
    check("no HOST_IDENTITY_POLICY text", "stylized indian male" not in prompt.lower())
    check("no reference-host instruction", "match the attached reference" not in prompt.lower())
    check("no composite placement instruction",
          "do not draw any human figure" not in prompt.lower())
    check("the route's own prompt is present", "a minister at a podium" in prompt)


def s3_approved_composite_reserves_space_but_never_asks_for_a_host():
    route = _base_route(host_present=True, host_method="approved_pose_composite",
                        host_pose_id="neutral", host_scene_bound=False,
                        host_placement={"framing": "lower third, facing right"})
    prompt = prompt_policy.build_effective_prompt(route)
    check("a deterministic placement instruction is present",
          "do not draw any human figure" in prompt.lower())
    check("the placement instruction names the route's own host_placement.framing",
          "lower third, facing right" in prompt)
    check("no adult host identity description is present — the provider never draws "
          "the host for a composited route", "stylized indian male" not in prompt.lower())


def s3_reference_anchored_uses_the_adult_identity_and_reference_instruction():
    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"])
    prompt = prompt_policy.build_effective_prompt(route)
    check("the adult host identity description is present",
          "stylized indian male" in prompt.lower())
    check("the reference-matching instruction is present",
          "match the attached reference" in prompt.lower())
    check("no placement-reservation instruction is present here",
          "do not draw any human figure" not in prompt.lower())


def s3_build_effective_prompt_reads_the_typed_route_args_prompt():
    route = _base_route(prompt="STALE TOP-LEVEL TEXT")
    route["route_args"]["illustration"]["prompt"] = "the typed, authoritative text"
    prompt = prompt_policy.build_effective_prompt(route)
    check("build_effective_prompt uses the typed route_args prompt, not a stale "
          "top-level route['prompt']",
          "the typed, authoritative text" in prompt and "STALE TOP-LEVEL TEXT" not in prompt)


# ── 4. top-level vs typed prompt authority (visual_routes.py) ──────────────

def s4_prompt_authority_agreement_required_for_ready_illustration_reenactment():
    import visual_routes as vr
    ok_route = _base_route()
    check("matching top-level/typed prompt on a READY ILLUSTRATION route has no problem",
          vr.prompt_authority_problems(ok_route) == [])

    mismatched = _base_route()
    mismatched["route_args"]["illustration"]["prompt"] = "a different sentence entirely"
    probs = vr.prompt_authority_problems(mismatched)
    check("a mismatched typed prompt is a problem", len(probs) == 1, str(probs))

    null_typed = _base_route()
    null_typed["route_args"]["illustration"]["prompt"] = None
    probs2 = vr.prompt_authority_problems(null_typed)
    check("a null typed prompt on a READY illustration route is a problem",
          len(probs2) == 1, str(probs2))

    needs_review = _base_route(status="NEEDS_REVIEW",
                               review_reasons=[{"code": "OTHER", "detail": "x"}])
    needs_review["route_args"]["illustration"]["prompt"] = None
    check("a NEEDS_REVIEW route is exempt from the prompt-authority check "
          "(status alone already blocks it)",
          vr.prompt_authority_problems(needs_review) == [])

    non_illustration = _base_route(visual_type="MAP",
                                   route_args={"map": {"regions": ["Kerala"]}, "chart": None,
                                               "timeline": None, "document": None,
                                               "photo": None, "illustration": None,
                                               "reenactment": None})
    check("a MAP route (no typed prompt slot) is exempt",
          vr.prompt_authority_problems(non_illustration) == [])


# ── 5. reference-anchored adapter: real bytes, deterministic order, no fallback

def _write(p: Path, content: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def s5_reference_anchored_passes_both_exact_master_bytes_body_then_face():
    root = temp_dir()
    body_bytes = b"BODY-MASTER-BYTES-0001"
    face_bytes = b"FACE-MASTER-BYTES-0002"
    body_path = root / "character" / "canonical" / "body-master.png"
    face_path = root / "character" / "canonical" / "face-master.png"
    _write(body_path, body_bytes)
    _write(face_path, face_bytes)

    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"],
                        renderer_id="flux_reference_anchor")
    ctx = ra.DispatchContext(
        project_dir=root, channel=None,
        approved_poses={},
        approved_references={
            "body_master": {"path": "character/canonical/body-master.png"},
            "face_master": {"path": "character/canonical/face-master.png"},
        },
        poses_asset_base=None, poses_root=None,
        references_asset_base=root, references_root=root / "character",
        output_root=root, renderer_entry=renderers.RENDERERS["flux_reference_anchor"])

    captured = {}

    class _FakeImages:
        @staticmethod
        def edit(**kw):
            captured.update(kw)
            captured["image_bytes"] = [f.read() for f in kw["image"]]

            class _Resp:
                data = [type("D", (), {"b64_json": None, "url": None})()]
            return _Resp()

    class _FakeClient:
        images = _FakeImages()

    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("openai.OpenAI", return_value=_FakeClient()), \
        mock.patch.object(ra, "route_failures") as mock_rf:
        with mock.patch("generate_images_aibmm._get_api_key", return_value="fake-key"), \
            mock.patch("generate_images_aibmm._extract_image", return_value=None):
            ok = ra.adapt_flux_reference_anchor(route, root / "out.png", ctx)

    check("the adapter reported failure (no usable image data from the mock)", ok is False)
    check("exactly two images were passed to images.edit()",
          len(captured.get("image_bytes", [])) == 2, str(captured.get("image_bytes")))
    check("body_master bytes were passed FIRST, face_master bytes SECOND",
          captured.get("image_bytes") == [body_bytes, face_bytes],
          str(captured.get("image_bytes")))
    check("the model_id came from ctx.renderer_entry, not a hardcoded/overridable value",
          captured.get("model") == renderers.RENDERERS["flux_reference_anchor"]["model_id"])
    check("a failure was recorded when no usable image data came back",
          mock_rf.record_failure.called)


def s5_reference_anchored_edit_failure_never_calls_images_generate():
    root = temp_dir()
    body_path = root / "character" / "canonical" / "body-master.png"
    face_path = root / "character" / "canonical" / "face-master.png"
    _write(body_path, b"body")
    _write(face_path, b"face")

    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"],
                        renderer_id="flux_reference_anchor")
    ctx = ra.DispatchContext(
        project_dir=root, channel=None, approved_poses={},
        approved_references={
            "body_master": {"path": "character/canonical/body-master.png"},
            "face_master": {"path": "character/canonical/face-master.png"},
        },
        poses_asset_base=None, poses_root=None,
        references_asset_base=root, references_root=root / "character",
        output_root=root, renderer_entry=renderers.RENDERERS["flux_reference_anchor"])

    class _FakeImages:
        edit_calls = []
        generate_calls = []

        @classmethod
        def edit(cls, **kw):
            cls.edit_calls.append(kw)
            raise RuntimeError("simulated provider failure")

        @classmethod
        def generate(cls, **kw):
            cls.generate_calls.append(kw)
            raise AssertionError("images.generate() must never be called as a fallback "
                                 "from a failed reference-anchored edit")

    class _FakeClient:
        images = _FakeImages()

    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("openai.OpenAI", return_value=_FakeClient()), \
        mock.patch.object(ra, "route_failures") as mock_rf:
        with mock.patch("generate_images_aibmm._get_api_key", return_value="fake-key"):
            ok = ra.adapt_flux_reference_anchor(route, root / "out.png", ctx)

    check("the adapter returned False (refused) on an edit failure", ok is False)
    check("images.edit() was called exactly once", len(_FakeImages.edit_calls) == 1)
    check("images.generate() was never called as a fallback",
          len(_FakeImages.generate_calls) == 0)
    check("a failure was recorded describing the refusal, not a silent fallback",
          mock_rf.record_failure.called
          and "falling back" in mock_rf.record_failure.call_args.kwargs.get("reason", ""))


def s5_reference_anchored_refuses_before_provider_call_when_a_reference_is_missing():
    root = temp_dir()
    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"],
                        renderer_id="flux_reference_anchor")
    ctx = ra.DispatchContext(
        project_dir=root, channel=None, approved_poses={},
        approved_references={},  # neither reference is approved
        poses_asset_base=None, poses_root=None,
        references_asset_base=root, references_root=root / "character",
        output_root=root, renderer_entry=renderers.RENDERERS["flux_reference_anchor"])

    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("openai.OpenAI") as mock_openai, \
        mock.patch.object(ra, "route_failures") as mock_rf:
        ok = ra.adapt_flux_reference_anchor(route, root / "out.png", ctx)

    check("the adapter refused (missing-reference logic, guard patched)", ok is False)
    check("no OpenAI client was ever constructed", not mock_openai.called)
    check("a failure was recorded", mock_rf.record_failure.called)


# ── 6. regenerated Channel Pack passes drift validation ─────────────────────

def s6_illustration_reference_capability_is_reachable_in_the_real_channel():
    ctx = cc.load_channel("interested_indian")
    check("channel_context.load_channel() succeeds (generated files match channel.json)",
          ctx.channel_id == "interested_indian")
    check("ILLUSTRATION_REFERENCE is declared and points at flux_reference_anchor",
          ctx.renderer_capabilities.get("ILLUSTRATION_REFERENCE") == "flux_reference_anchor")
    check("REENACTMENT_REFERENCE is deliberately left undeclared (amendment 4, option B)",
          "REENACTMENT_REFERENCE" not in ctx.renderer_capabilities)


# ── 7. universal refusal — every adapter, unpatched, blocked before any side effect

def _minimal_ctx(root: Path, renderer_id="india_geojson") -> "ra.DispatchContext":
    return ra.DispatchContext(
        project_dir=root, channel=None, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None, references_asset_base=None,
        references_root=None, output_root=root, renderer_entry=renderers.RENDERERS[renderer_id])


def s7_all_six_adapters_refuse_normally_before_any_side_effect():
    root = temp_dir()
    route = _base_route()

    with mock.patch("subprocess.run") as mock_run, \
        mock.patch("openai.OpenAI") as mock_openai:
        for name, fn, renderer_id in (
            ("adapt_map", ra.adapt_map, "india_geojson"),
            ("adapt_chart", ra.adapt_chart, "matplotlib_chart"),
            ("adapt_photo", ra.adapt_photo, "pexels"),
            ("adapt_flux", ra.adapt_flux, "flux_illustration"),
            ("adapt_host_composite", ra.adapt_host_composite, "approved_pose_compositor"),
            ("adapt_flux_reference_anchor", ra.adapt_flux_reference_anchor,
             "flux_reference_anchor"),
        ):
            ctx = _minimal_ctx(root, renderer_id)
            try:
                fn(route, root / "out.png", ctx)
                check(f"{name}() raises GateBlocked (guard not patched)", False,
                      "returned instead of raising")
            except generation_gate.GateBlocked as e:
                check(f"{name}() raises GateBlocked (guard not patched)", True)
                check(f"{name}()'s GateBlocked message names canonical visual execution "
                      f"as disabled", "canonical visual execution" in str(e).lower(), str(e))
        check("no subprocess was ever run across all six adapters", not mock_run.called)
        check("no OpenAI client was ever constructed across all six adapters",
              not mock_openai.called)
    check("no output file was written by any of the six refused adapters",
          not (root / "out.png").exists())


def s7_a_valid_legacy_v2_approval_cannot_unlock_a_canonical_adapter():
    """Even if require_generation_ready() — the legacy, v2-approval-consuming
    gate — is mocked to report a clean pass (simulating a fully valid legacy
    approval), the canonical adapters must still refuse: they never consult
    that gate at all, only the always-refusing canonical guard."""
    root = temp_dir()
    route = _base_route()
    ctx = _minimal_ctx(root, "india_geojson")

    clean_report = generation_gate.GateReport(operation="x", project="p", scope="generation")
    with mock.patch.object(generation_gate, "require_generation_ready",
                          return_value=clean_report) as mock_legacy_gate:
        try:
            ra.adapt_map(route, root / "out.png", ctx)
            check("adapt_map still refuses even with a simulated clean legacy v2 approval",
                  False, "did not raise")
        except generation_gate.GateBlocked:
            check("adapt_map still refuses even with a simulated clean legacy v2 approval",
                  True)
        check("adapt_map never even calls the legacy require_generation_ready() gate",
              not mock_legacy_gate.called)


def s7_all_six_functions_are_registered_in_paid_entry_points():
    entry = next((e for e in generation_gate.PAID_ENTRY_POINTS
                 if e["module"] == "renderer_adapters.py"), None)
    check("renderer_adapters.py has a PAID_ENTRY_POINTS entry", entry is not None)
    if entry is not None:
        registered_functions = {g["function"] for g in entry["gates"]}
        registered_kinds = {g["kind"] for g in entry["gates"]}
        check("all six adapter functions are registered",
              registered_functions == {"adapt_map", "adapt_chart", "adapt_photo",
                                       "adapt_flux", "adapt_host_composite",
                                       "adapt_flux_reference_anchor"},
              str(registered_functions))
        check("every registered gate uses the canonical_visual_execution kind "
              "(resolving to require_canonical_visual_execution_ready), not "
              "'generation' (which would resolve to the legacy v2-approval gate)",
              registered_kinds == {"canonical_visual_execution"}, str(registered_kinds))


for title, fn in (
    ("1a. one execution registry, no second mapping", s1_single_registry_no_second_mapping),
    ("1b. registry entries are deeply immutable", s1_registry_entries_are_deeply_immutable),
    ("1c. DispatchContext.renderer_entry is the frozen entry",
     s1_dispatch_context_renderer_entry_is_the_frozen_entry),
    ("1d. adapter signatures carry no override parameters",
     s1_adapter_signature_has_no_override_parameters),
    ("2a. every projected field changes the hash", s2_every_projected_field_changes_the_hash),
    ("2b. old narrow registry hash fails closed",
     s2_old_narrow_registry_hash_fails_closed_against_the_new_one),
    ("3a. no obsolete child-mascot text anywhere",
     s3_no_obsolete_child_mascot_text_anywhere_in_prompt_policy),
    ("3b. host_present=false produces no host instructions",
     s3_host_present_false_produces_no_host_instructions),
    ("3c. approved_composite reserves space, never asks for a host",
     s3_approved_composite_reserves_space_but_never_asks_for_a_host),
    ("3d. reference_anchored uses the adult identity + reference instruction",
     s3_reference_anchored_uses_the_adult_identity_and_reference_instruction),
    ("3e. build_effective_prompt reads the typed route_args prompt",
     s3_build_effective_prompt_reads_the_typed_route_args_prompt),
    ("4. top-level vs typed prompt authority",
     s4_prompt_authority_agreement_required_for_ready_illustration_reenactment),
    ("5a. reference-anchored: exact bytes, body then face",
     s5_reference_anchored_passes_both_exact_master_bytes_body_then_face),
    ("5b. reference-anchored: edit failure never calls images.generate",
     s5_reference_anchored_edit_failure_never_calls_images_generate),
    ("5c. reference-anchored: missing reference refuses before any provider call",
     s5_reference_anchored_refuses_before_provider_call_when_a_reference_is_missing),
    ("6. regenerated Channel Pack: ILLUSTRATION_REFERENCE reachable, drift-clean",
     s6_illustration_reference_capability_is_reachable_in_the_real_channel),
    ("7a. all six adapters refuse normally, before any side effect",
     s7_all_six_adapters_refuse_normally_before_any_side_effect),
    ("7b. a valid legacy v2 approval cannot unlock a canonical adapter",
     s7_a_valid_legacy_v2_approval_cannot_unlock_a_canonical_adapter),
    ("7c. all six functions are registered in PAID_ENTRY_POINTS under the "
     "canonical_visual_execution kind",
     s7_all_six_functions_are_registered_in_paid_entry_points),
):
    run(title, fn)

print("\n" + "=" * 62)
print(f"FAILED ({len(failures)}): {failures}" if failures else "all renderer-adapter checks passed")
sys.exit(1 if failures else 0)
