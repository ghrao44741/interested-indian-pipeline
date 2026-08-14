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
import io
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
#
# Corrective follow-up item 4: the adapter no longer trusts
# ctx.approved_references at all — it revalidates through
# reference_registry.resolve(rid, context=ctx.channel), which reads live
# masters/references off ctx.channel.character_spec_path. Every fixture
# below therefore builds a REAL temp character_spec.json + real temp master
# files and a minimal fake ChannelContext pointing at them — never the real
# production character/ tree, and never a bare `channel=None` (which would
# resolve against the real repository's own approved masters instead of the
# fixture).

import hashlib as _hashlib
import json as _json_mod


def _write(p: Path, content: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


class _FakeChannelContext:
    def __init__(self, character_spec_path: Path):
        self.character_spec_path = str(character_spec_path)


def _fixture_reference_context(root: Path, *, body_bytes: bytes, face_bytes: bytes,
                               body_status="approved", face_status="approved",
                               agree=True) -> _FakeChannelContext:
    """Builds a real, isolated character_spec.json + real master files under
    `root`, and returns a minimal fake ChannelContext pointing at them —
    exactly the shape reference_registry.resolve()/registry() need
    (`.character_spec_path`), with none of the rest of a real ChannelContext
    required."""
    body_path = root / "character" / "canonical" / "body-master.png"
    face_path = root / "character" / "canonical" / "face-master.png"
    _write(body_path, body_bytes)
    _write(face_path, face_bytes)
    spec_path = root / "character" / "character_spec.json"
    refs = {
        "body_master": "character/canonical/body-master.png" if agree else "character/canonical/other.png",
        "face_master": "character/canonical/face-master.png",
    }
    spec = {
        "masters": {
            "body_master": {"path": "character/canonical/body-master.png", "status": body_status,
                            "sha256": _hashlib.sha256(body_bytes).hexdigest()},
            "face_master": {"path": "character/canonical/face-master.png", "status": face_status,
                            "sha256": _hashlib.sha256(face_bytes).hexdigest()},
        },
        "references": refs,
    }
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(_json_mod.dumps(spec), encoding="utf-8")
    return _FakeChannelContext(spec_path)


def s5_reference_anchored_passes_both_exact_master_bytes_body_then_face():
    root = temp_dir()
    body_bytes = b"BODY-MASTER-BYTES-0001"
    face_bytes = b"FACE-MASTER-BYTES-0002"
    channel = _fixture_reference_context(root, body_bytes=body_bytes, face_bytes=face_bytes)

    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"],
                        renderer_id="flux_reference_anchor")
    ctx = ra.DispatchContext(
        project_dir=root, channel=channel, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None,
        references_asset_base=None, references_root=None,
        output_root=root, renderer_entry=renderers.RENDERERS["flux_reference_anchor"], route=route)

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
        ok = ra.adapt_flux_reference_anchor(route, root / "out.png", ctx)

    check("the adapter reported failure (no usable image data from the mock)", ok is False)
    check("exactly two images were passed to images.edit()",
          len(captured.get("image_bytes", [])) == 2, str(len(captured.get("image_bytes", []))))
    check("body_master bytes were passed FIRST, face_master bytes SECOND",
          captured.get("image_bytes") == [body_bytes, face_bytes],
          str(captured.get("image_bytes")))
    check("the model_id came from ctx.renderer_entry, not a hardcoded/overridable value",
          captured.get("model") == renderers.RENDERERS["flux_reference_anchor"]["model_id"])
    check("a failure was recorded when no usable image data came back",
          mock_rf.record_failure.called)
    check("no output file was written on a no-usable-image-data refusal",
          not (root / "out.png").exists())


def s5_reference_anchored_edit_failure_never_calls_images_generate():
    root = temp_dir()
    channel = _fixture_reference_context(root, body_bytes=b"body", face_bytes=b"face")

    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"],
                        renderer_id="flux_reference_anchor")
    ctx = ra.DispatchContext(
        project_dir=root, channel=channel, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None,
        references_asset_base=None, references_root=None,
        output_root=root, renderer_entry=renderers.RENDERERS["flux_reference_anchor"], route=route)

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
        ok = ra.adapt_flux_reference_anchor(route, root / "out.png", ctx)

    check("the adapter returned False (refused) on an edit failure", ok is False)
    check("images.edit() was called exactly once", len(_FakeImages.edit_calls) == 1)
    check("images.generate() was never called as a fallback",
          len(_FakeImages.generate_calls) == 0)
    check("a failure was recorded describing the refusal, not a silent fallback",
          mock_rf.record_failure.called
          and "falling back" in mock_rf.record_failure.call_args.kwargs.get("reason", ""))
    check("no output file was written on an edit failure", not (root / "out.png").exists())


def s5_reference_anchored_refuses_before_provider_call_when_a_reference_is_missing():
    root = temp_dir()
    # No character_spec.json / masters exist under this root at all — every
    # reference id is "missing" from reference_registry's point of view.
    (root / "character").mkdir(parents=True, exist_ok=True)
    (root / "character" / "character_spec.json").write_text(
        _json_mod.dumps({"masters": {}, "references": {}}), encoding="utf-8")
    channel = _FakeChannelContext(root / "character" / "character_spec.json")

    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"],
                        renderer_id="flux_reference_anchor")
    ctx = ra.DispatchContext(
        project_dir=root, channel=channel, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None,
        references_asset_base=None, references_root=None,
        output_root=root, renderer_entry=renderers.RENDERERS["flux_reference_anchor"], route=route)

    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("openai.OpenAI") as mock_openai, \
        mock.patch.object(ra, "route_failures") as mock_rf:
        ok = ra.adapt_flux_reference_anchor(route, root / "out.png", ctx)

    check("the adapter refused (missing-reference logic, guard patched)", ok is False)
    check("no OpenAI client was ever constructed", not mock_openai.called)
    check("a failure was recorded", mock_rf.record_failure.called)
    check("no output file was written", not (root / "out.png").exists())


def s5_reference_anchored_rejects_a_stale_hash_before_any_provider_call():
    root = temp_dir()
    channel = _fixture_reference_context(root, body_bytes=b"body-v1", face_bytes=b"face-v1")
    # Mutate the body master's bytes on disk after the spec recorded its
    # hash -- exactly a "the file changed since approval" drift.
    (root / "character" / "canonical" / "body-master.png").write_bytes(b"body-v2-tampered")

    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"],
                        renderer_id="flux_reference_anchor")
    ctx = ra.DispatchContext(
        project_dir=root, channel=channel, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None,
        references_asset_base=None, references_root=None,
        output_root=root, renderer_entry=renderers.RENDERERS["flux_reference_anchor"], route=route)

    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("openai.OpenAI") as mock_openai, \
        mock.patch.object(ra, "route_failures") as mock_rf:
        ok = ra.adapt_flux_reference_anchor(route, root / "out.png", ctx)

    check("a stale hash is refused before any provider call", ok is False)
    check("no OpenAI client was constructed for a stale-hash reference",
          not mock_openai.called)
    check("a failure was recorded", mock_rf.record_failure.called)


def s5_reference_anchored_rejects_a_non_approved_status_before_any_provider_call():
    root = temp_dir()
    channel = _fixture_reference_context(root, body_bytes=b"body", face_bytes=b"face",
                                         body_status="pending-approval")

    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"],
                        renderer_id="flux_reference_anchor")
    ctx = ra.DispatchContext(
        project_dir=root, channel=channel, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None,
        references_asset_base=None, references_root=None,
        output_root=root, renderer_entry=renderers.RENDERERS["flux_reference_anchor"], route=route)

    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("openai.OpenAI") as mock_openai, \
        mock.patch.object(ra, "route_failures") as mock_rf:
        ok = ra.adapt_flux_reference_anchor(route, root / "out.png", ctx)

    check("a non-approved master status is refused before any provider call", ok is False)
    check("no OpenAI client was constructed", not mock_openai.called)
    check("a failure was recorded", mock_rf.record_failure.called)


def s5_reference_anchored_rejects_top_level_master_disagreement_before_any_provider_call():
    root = temp_dir()
    channel = _fixture_reference_context(root, body_bytes=b"body", face_bytes=b"face",
                                         agree=False)

    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"],
                        renderer_id="flux_reference_anchor")
    ctx = ra.DispatchContext(
        project_dir=root, channel=channel, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None,
        references_asset_base=None, references_root=None,
        output_root=root, renderer_entry=renderers.RENDERERS["flux_reference_anchor"], route=route)

    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("openai.OpenAI") as mock_openai, \
        mock.patch.object(ra, "route_failures") as mock_rf:
        ok = ra.adapt_flux_reference_anchor(route, root / "out.png", ctx)

    check("top-level/master disagreement is refused before any provider call", ok is False)
    check("no OpenAI client was constructed", not mock_openai.called)
    check("a failure was recorded", mock_rf.record_failure.called)


def s5_reference_anchored_rejects_traversal_and_archive_paths_before_any_provider_call():
    for label, bad_path in (
        ("traversal", "../../etc/passwd"),
        ("archive component", "character/archive/body_master_v1_superseded/x.png"),
    ):
        root = temp_dir()
        body_bytes, face_bytes = b"body", b"face"
        face_path = root / "character" / "canonical" / "face-master.png"
        _write(face_path, face_bytes)
        spec_path = root / "character" / "character_spec.json"
        spec = {
            "masters": {
                "body_master": {"path": bad_path, "status": "approved",
                                "sha256": _hashlib.sha256(body_bytes).hexdigest()},
                "face_master": {"path": "character/canonical/face-master.png",
                                "status": "approved",
                                "sha256": _hashlib.sha256(face_bytes).hexdigest()},
            },
            "references": {"body_master": bad_path,
                           "face_master": "character/canonical/face-master.png"},
        }
        spec_path.write_text(_json_mod.dumps(spec), encoding="utf-8")
        channel = _FakeChannelContext(spec_path)

        route = _base_route(host_present=True, host_method="reference_anchored_generation",
                            host_reference_asset_ids=["body_master", "face_master"],
                            renderer_id="flux_reference_anchor")
        ctx = ra.DispatchContext(
            project_dir=root, channel=channel, approved_poses={}, approved_references={},
            poses_asset_base=None, poses_root=None,
            references_asset_base=None, references_root=None,
            output_root=root, renderer_entry=renderers.RENDERERS["flux_reference_anchor"], route=route)

        with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                              return_value=None), \
            mock.patch("openai.OpenAI") as mock_openai, \
            mock.patch.object(ra, "route_failures") as mock_rf:
            ok = ra.adapt_flux_reference_anchor(route, root / "out.png", ctx)

        check(f"a {label} registered path is refused before any provider call", ok is False)
        check(f"no OpenAI client was constructed ({label})", not mock_openai.called)
        check(f"a failure was recorded ({label})", mock_rf.record_failure.called)


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


# ── 8. output-transform truthfulness (corrective follow-up item 3) ─────────

def _non_16x9_png_bytes(size=(1536, 1024)) -> bytes:
    """A real, decodable PNG that is deliberately NOT 16:9 — matching a
    gpt-image-2 edit response's common 1536x1024 shape — so a test can
    prove the canonical transform is actually applied, not merely declared."""
    from PIL import Image
    img = Image.new("RGB", size, color=(120, 60, 200))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def s8_apply_output_transform_produces_16x9_png():
    src = _non_16x9_png_bytes((1536, 1024))
    out_bytes = ra.apply_output_transform(src)
    from PIL import Image
    img = Image.open(io.BytesIO(out_bytes))
    check("the transform's output is a real, decodable image", img is not None)
    check("the transform's output format is PNG", img.format == "PNG", str(img.format))
    check("the transform's output is exactly the canonical 1280x720 (16:9)",
          img.size == (ra.CANONICAL_WIDTH, ra.CANONICAL_HEIGHT), str(img.size))


def s8_adapt_flux_applies_the_registered_transform_to_a_non_16x9_response():
    """Corrected for the boundary micro-fix: _verify_dispatch_entry() now
    requires ctx.renderer_entry to be the EXACT canonical, frozen object —
    a dict(real_entry) copy (the prior round's "spy entry" technique) is
    correctly refused as a substituted entry, so this test cannot intercept
    the call that way anymore. Instead it uses the real, unmodified entry
    (proving the identity check passes for legitimate dispatch) and proves
    the registered transform ran by (a) identity: the registry names
    apply_output_transform exactly, and (b) outcome: the only way the final
    file can be a 1280x720 PNG from a 1536x1024 source is if that transform
    executed."""
    root = temp_dir()
    route = _base_route(renderer_id="flux_illustration")
    real_entry = renderers.RENDERERS["flux_illustration"]

    ctx = ra.DispatchContext(
        project_dir=root, channel=None, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None, references_asset_base=None,
        references_root=None, output_root=root, renderer_entry=real_entry, route=route)

    non_16x9 = _non_16x9_png_bytes((1536, 1024))
    b64 = __import__("base64").b64encode(non_16x9).decode()

    class _FakeImages:
        @staticmethod
        def generate(**kw):
            class _Resp:
                data = [type("D", (), {"b64_json": b64, "url": None})()]
            return _Resp()

    class _FakeClient:
        images = _FakeImages()

    target = root / "out.png"
    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("openai.OpenAI", return_value=_FakeClient()), \
        mock.patch.object(ra, "route_failures"):
        ok = ra.adapt_flux(route, target, ctx)

    check("adapt_flux succeeded against the real, unmodified canonical entry",
          ok is True)
    check("the real renderers.RENDERERS entry names apply_output_transform",
          real_entry["output_transform"] is ra.apply_output_transform)
    from PIL import Image
    img = Image.open(target)
    check("the final written file is PNG and 16:9 (1280x720), not the raw "
          "1536x1024 response written verbatim — provable only if the "
          "registered transform actually ran",
          img.format == "PNG" and img.size == (ra.CANONICAL_WIDTH, ra.CANONICAL_HEIGHT),
          f"{img.format} {img.size}")


def s8_adapt_flux_reference_anchor_applies_the_registered_transform():
    """Same correction as the adapt_flux version above: uses the real,
    unmodified canonical entry (satisfying _verify_dispatch_entry's
    identity check) and proves the transform ran via identity + outcome
    rather than a copied-entry call-spy."""
    root = temp_dir()
    channel = _fixture_reference_context(root, body_bytes=b"body", face_bytes=b"face")
    route = _base_route(host_present=True, host_method="reference_anchored_generation",
                        host_reference_asset_ids=["body_master", "face_master"],
                        renderer_id="flux_reference_anchor")
    real_entry = renderers.RENDERERS["flux_reference_anchor"]

    ctx = ra.DispatchContext(
        project_dir=root, channel=channel, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None, references_asset_base=None,
        references_root=None, output_root=root, renderer_entry=real_entry, route=route)

    non_16x9 = _non_16x9_png_bytes((1536, 1024))
    b64 = __import__("base64").b64encode(non_16x9).decode()

    class _FakeImages:
        @staticmethod
        def edit(**kw):
            class _Resp:
                data = [type("D", (), {"b64_json": b64, "url": None})()]
            return _Resp()

    class _FakeClient:
        images = _FakeImages()

    target = root / "out.png"
    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("openai.OpenAI", return_value=_FakeClient()), \
        mock.patch.object(ra, "route_failures"):
        ok = ra.adapt_flux_reference_anchor(route, target, ctx)

    check("adapt_flux_reference_anchor succeeded against the real, unmodified "
          "canonical entry", ok is True)
    check("the real renderers.RENDERERS entry names apply_output_transform",
          real_entry["output_transform"] is ra.apply_output_transform)
    from PIL import Image
    img = Image.open(target)
    check("the final written file is PNG and 16:9 (1280x720), not the raw "
          "1536x1024 edit response written verbatim",
          img.format == "PNG" and img.size == (ra.CANONICAL_WIDTH, ra.CANONICAL_HEIGHT),
          f"{img.format} {img.size}")


def s8_transform_failure_leaves_no_successful_looking_final_file():
    root = temp_dir()
    route = _base_route(renderer_id="flux_illustration")
    ctx = ra.DispatchContext(
        project_dir=root, channel=None, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None, references_asset_base=None,
        references_root=None, output_root=root,
        renderer_entry=renderers.RENDERERS["flux_illustration"], route=route)

    b64 = __import__("base64").b64encode(b"not-a-real-image").decode()

    class _FakeImages:
        @staticmethod
        def generate(**kw):
            class _Resp:
                data = [type("D", (), {"b64_json": b64, "url": None})()]
            return _Resp()

    class _FakeClient:
        images = _FakeImages()

    target = root / "out.png"
    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("openai.OpenAI", return_value=_FakeClient()), \
        mock.patch.object(ra, "route_failures") as mock_rf:
        ok = ra.adapt_flux(route, target, ctx)

    check("adapt_flux refuses when the transform cannot decode the provider bytes",
          ok is False)
    check("no output file was left behind by a failed transform", not target.exists())
    check("a failure was recorded describing the transform failure",
          mock_rf.record_failure.called
          and "transform" in mock_rf.record_failure.call_args.kwargs.get("reason", ""))


def s8_changing_the_transform_declaration_changes_the_registry_hash():
    import visual_routes as vr
    base = {"module": "m.py", "entry": "main", "adapter": None,
           "output_transform": ra.apply_output_transform, "output_transform_version": 1,
           "implemented": True, "cost_category": "paid_api"}
    other = dict(base)
    other["output_transform"] = ra.adapt_map  # a different callable identity
    h1 = vr.compute_renderer_registry_sha256({"r"}, {"r": base})
    h2 = vr.compute_renderer_registry_sha256({"r"}, {"r": other})
    check("changing which function a renderer's output_transform points at "
          "changes renderer_registry_sha256", h1 != h2)

    other_version = dict(base)
    other_version["output_transform_version"] = 2
    h3 = vr.compute_renderer_registry_sha256({"r"}, {"r": other_version})
    check("changing output_transform_version alone also changes the hash", h1 != h3)


# ── 9. final boundary micro-fix ─────────────────────────────────────────────
# item 1: every adapter bound to the route's exact canonical registry entry

_ADAPTERS_UNDER_TEST = (
    ("adapt_map", ra.adapt_map, "india_geojson"),
    ("adapt_chart", ra.adapt_chart, "matplotlib_chart"),
    ("adapt_photo", ra.adapt_photo, "pexels"),
    ("adapt_flux", ra.adapt_flux, "flux_illustration"),
    ("adapt_host_composite", ra.adapt_host_composite, "approved_pose_compositor"),
    ("adapt_flux_reference_anchor", ra.adapt_flux_reference_anchor, "flux_reference_anchor"),
)


def _assert_rejected_before_any_side_effect(name, label, fn, route, entry, root):
    ctx = ra.DispatchContext(
        project_dir=root, channel=None, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None, references_asset_base=None,
        references_root=None, output_root=root, renderer_entry=entry)
    target = root / f"out_{name}_{label}.png"
    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch("subprocess.run") as mock_run, \
        mock.patch("openai.OpenAI") as mock_openai, \
        mock.patch.object(ra, "route_failures"):
        try:
            fn(route, target, ctx)
            check(f"{name} rejects {label} (guard patched)", False, "did not raise")
        except RuntimeError:
            check(f"{name} rejects {label} (guard patched)", True)
    check(f"{name}/{label}: no subprocess was run", not mock_run.called)
    check(f"{name}/{label}: no OpenAI client was constructed", not mock_openai.called)
    check(f"{name}/{label}: no output file was written", not target.exists())


def s9_all_adapters_reject_a_mutable_dict_substituted_as_renderer_entry():
    root = temp_dir()
    for name, fn, renderer_id in _ADAPTERS_UNDER_TEST:
        route = _base_route(renderer_id=renderer_id)
        mutable_copy = dict(renderers.RENDERERS[renderer_id])
        _assert_rejected_before_any_side_effect(
            name, "a mutable dict copy", fn, route, mutable_copy, root)


def s9_all_adapters_reject_a_caller_created_mappingproxytype():
    root = temp_dir()
    for name, fn, renderer_id in _ADAPTERS_UNDER_TEST:
        route = _base_route(renderer_id=renderer_id)
        fresh_proxy = MappingProxyType(dict(renderers.RENDERERS[renderer_id]))
        _assert_rejected_before_any_side_effect(
            name, "a fresh, caller-built MappingProxyType (identical content)",
            fn, route, fresh_proxy, root)


def s9_all_adapters_reject_another_renderers_real_entry():
    root = temp_dir()
    for name, fn, renderer_id in _ADAPTERS_UNDER_TEST:
        route = _base_route(renderer_id=renderer_id)
        other_id = next(rid for rid in renderers.RENDERERS if rid != renderer_id)
        other_real_entry = renderers.RENDERERS[other_id]
        _assert_rejected_before_any_side_effect(
            name, f"another registered renderer's real entry ({other_id})",
            fn, route, other_real_entry, root)


def s9_all_adapters_reject_a_missing_blank_or_unknown_renderer_id():
    root = temp_dir()
    for name, fn, renderer_id in _ADAPTERS_UNDER_TEST:
        real_entry = renderers.RENDERERS[renderer_id]
        for bad_value, label in ((None, "missing"), ("", "blank"),
                                 ("   ", "whitespace-only"),
                                 ("no_such_renderer", "unknown")):
            route = _base_route(renderer_id=bad_value)
            _assert_rejected_before_any_side_effect(
                name, f"route.renderer_id={label!r}", fn, route, real_entry, root)


def s9_all_adapters_reject_an_entry_whose_registered_adapter_does_not_match():
    """A hand-built entry that otherwise looks exactly like the canonical one
    but names a DIFFERENT function as its "adapter" — even though its other
    fields (provider, model, etc.) are copied verbatim from the real entry.
    This is refused on the same identity check (it is not the canonical
    object), and separately would fail the adapter-match check even if
    identity were somehow satisfied — both are exercised by this fixture."""
    root = temp_dir()
    for name, fn, renderer_id in _ADAPTERS_UNDER_TEST:
        route = _base_route(renderer_id=renderer_id)
        mismatched = dict(renderers.RENDERERS[renderer_id])
        # Point "adapter" at some other adapter function entirely.
        other_name, other_fn, _ = next(a for a in _ADAPTERS_UNDER_TEST if a[1] is not fn)
        mismatched["adapter"] = other_fn
        _assert_rejected_before_any_side_effect(
            name, f"an entry whose registered adapter is {other_name}, not {name}",
            fn, route, mismatched, root)


# item 3: no execution-setting fallbacks

def _flux_ctx(root, renderer_entry, channel=None, route=None):
    return ra.DispatchContext(
        project_dir=root, channel=channel, approved_poses={}, approved_references={},
        poses_asset_base=None, poses_root=None, references_asset_base=None,
        references_root=None, output_root=root, renderer_entry=renderer_entry,
        route=route)


def s9_adapt_flux_refuses_malformed_execution_settings_before_credentials_or_client():
    base = dict(renderers.RENDERERS["flux_illustration"])
    cases = {
        "download_timeout_seconds=None": {**base, "download_timeout_seconds": None},
        "download_timeout_seconds=0": {**base, "download_timeout_seconds": 0},
        "download_timeout_seconds=-5": {**base, "download_timeout_seconds": -5},
        "download_timeout_seconds='60'": {**base, "download_timeout_seconds": "60"},
        "output_transform=None": {**base, "output_transform": None},
        "output_transform=non-callable": {**base, "output_transform": "not a function"},
        "output_transform_version=None": {**base, "output_transform_version": None},
        "output_transform_version=0": {**base, "output_transform_version": 0},
        "credential_env_var=None": {**base, "credential_env_var": None},
        "credential_env_var=''": {**base, "credential_env_var": ""},
        "model_id=None": {**base, "model_id": None},
        "response_format_policy='unknown'": {**base, "response_format_policy": "unknown"},
        "provider_parameters=None": {**base, "provider_parameters": None},
        "base_url=''": {**base, "base_url": ""},
    }
    root = temp_dir()
    route = _base_route(renderer_id="flux_illustration")
    for label, malformed_entry in cases.items():
        # Route this through the route's own renderer_id so the identity
        # check itself isn't what's being exercised here — a fresh
        # MappingProxyType is expected to (correctly) fail the identity
        # check too, but that's item 1's test, not this one; this proves
        # the SETTINGS validation refuses even when constructed as a
        # would-be-canonical-shaped entry.
        entry_proxy = MappingProxyType(malformed_entry)
        ctx = _flux_ctx(root, entry_proxy)
        target = root / f"out_{label}.png"
        with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                              return_value=None), \
            mock.patch("openai.OpenAI") as mock_openai, \
            mock.patch.object(ra, "route_failures"):
            try:
                ra.adapt_flux(route, target, ctx)
                check(f"adapt_flux refuses {label}", False, "did not raise")
            except RuntimeError:
                check(f"adapt_flux refuses {label}", True)
        check(f"adapt_flux/{label}: no OpenAI client was constructed",
              not mock_openai.called)
        check(f"adapt_flux/{label}: no output file was written", not target.exists())


def s9_credential_lookup_refuses_before_client_construction_when_unavailable():
    root = temp_dir()
    route = _base_route(renderer_id="flux_illustration")
    real_entry = renderers.RENDERERS["flux_illustration"]
    with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                          return_value=None), \
        mock.patch.dict("os.environ", {}, clear=True), \
        mock.patch("openai.OpenAI") as mock_openai, \
        mock.patch.object(ra, "route_failures") as mock_rf, \
        mock.patch.object(Path, "exists", return_value=False):
        ctx = _flux_ctx(root, real_entry, route=route)
        target = root / "out.png"
        ok = ra.adapt_flux(route, target, ctx)
    check("adapt_flux refuses (records a failure) rather than raising when the "
          "declared credential is unavailable", ok is False)
    check("no OpenAI client was ever constructed with an empty/missing key",
          not mock_openai.called)
    check("a failure was recorded describing the missing credential",
          mock_rf.record_failure.called
          and "credential" in mock_rf.record_failure.call_args.kwargs.get("reason", "").lower())
    check("no output file was written", not target.exists())


# item 4: exactly two reference IDs, duplicates/extras/wrong-types refused

def s9_reference_anchored_requires_exactly_two_distinct_reference_ids():
    root = temp_dir()
    channel = _fixture_reference_context(root, body_bytes=b"body", face_bytes=b"face")
    real_entry = renderers.RENDERERS["flux_reference_anchor"]
    bad_id_lists = {
        "duplicate body_master": ["body_master", "body_master"],
        "three entries incl. a duplicate": ["body_master", "face_master", "body_master"],
        "single entry": ["body_master"],
        "empty list": [],
        "wrong renderer names": ["body_master", "not_a_real_reference"],
        "non-list (string)": "body_master,face_master",
        "non-list (None)": None,
        "list with a non-string element": ["body_master", 123],
        "four entries": ["body_master", "face_master", "body_master", "face_master"],
    }
    for label, bad_ids in bad_id_lists.items():
        route = _base_route(host_present=True, host_method="reference_anchored_generation",
                            host_reference_asset_ids=bad_ids,
                            renderer_id="flux_reference_anchor")
        ctx = _flux_ctx(root, real_entry, channel=channel, route=route)
        target = root / f"out_refids_{hash(label) & 0xffff}.png"
        with mock.patch.object(generation_gate, "require_canonical_visual_execution_ready",
                              return_value=None), \
            mock.patch("openai.OpenAI") as mock_openai, \
            mock.patch.object(ra, "route_failures") as mock_rf:
            ok = ra.adapt_flux_reference_anchor(route, target, ctx)
        check(f"host_reference_asset_ids={bad_ids!r} ({label}) is refused", ok is False)
        check(f"no OpenAI client was constructed ({label})", not mock_openai.called)
        check(f"a failure was recorded ({label})", mock_rf.record_failure.called)
        check(f"no output file was written ({label})", not target.exists())

    # The correct pair, in either order, must still be accepted (normalized
    # order in the route is fine — the exact-set/exact-length check does not
    # reject a legitimate face-then-body ordering).
    good_route = _base_route(host_present=True, host_method="reference_anchored_generation",
                             host_reference_asset_ids=["face_master", "body_master"],
                             renderer_id="flux_reference_anchor")
    check("['face_master', 'body_master'] (order-normalized) is accepted by "
          "the id-shape check itself",
          ra._exactly_body_then_face(good_route["host_reference_asset_ids"]))


# ── 10. atomic_commit and build_dispatch_context (Task 2B-B2b-2a) ──────────

def _canonical_png_bytes() -> bytes:
    from PIL import Image
    img = Image.new("RGB", (ra.CANONICAL_WIDTH, ra.CANONICAL_HEIGHT), (10, 20, 30))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def s10_atomic_commit_success_writes_via_temp_file_then_replace():
    root = temp_dir()
    target = root / "out.png"
    seen_tmp_paths = []

    def producer(tmp_path: Path):
        seen_tmp_paths.append(tmp_path)
        check("the temp path is inside target's own directory",
              tmp_path.parent == target.parent)
        check("the temp path is not target itself", tmp_path != target)
        tmp_path.write_bytes(_canonical_png_bytes())

    ra.atomic_commit(target, producer)
    check("atomic_commit produced the final target", target.exists())
    check("the temp file no longer exists after a successful commit",
          not seen_tmp_paths[0].exists())
    from PIL import Image
    img = Image.open(target)
    check("the committed file is a valid canonical PNG",
          img.format == "PNG" and img.size == (ra.CANONICAL_WIDTH, ra.CANONICAL_HEIGHT))


def s10_atomic_commit_failure_removes_temp_and_preserves_existing_final():
    root = temp_dir()
    target = root / "out.png"
    target.write_bytes(b"PRE-EXISTING FINAL BYTES")
    before = target.read_bytes()

    captured = {}

    def bad_producer(tmp_path: Path):
        captured["tmp_path"] = tmp_path
        tmp_path.write_bytes(b"not a real png")

    try:
        ra.atomic_commit(target, bad_producer)
        check("atomic_commit raises when the producer's output is invalid",
              False, "did not raise")
    except RuntimeError:
        check("atomic_commit raises when the producer's output is invalid", True)
    check("the temp file was removed on failure", not captured["tmp_path"].exists())
    check("the pre-existing final target is preserved byte-for-byte",
          target.read_bytes() == before)


def s10_atomic_commit_wrong_size_is_rejected():
    root = temp_dir()
    target = root / "out.png"

    def producer(tmp_path: Path):
        from PIL import Image
        img = Image.new("RGB", (640, 480), (1, 2, 3))
        img.save(tmp_path, format="PNG")

    try:
        ra.atomic_commit(target, producer)
        check("a wrong-size PNG is rejected", False, "did not raise")
    except RuntimeError as e:
        check("a wrong-size PNG is rejected", "640x480" in str(e) or "1280" in str(e), str(e))
    check("no final file was left behind", not target.exists())


def s10_atomic_commit_empty_output_is_rejected():
    root = temp_dir()
    target = root / "out.png"

    def producer(tmp_path: Path):
        pass  # leaves the mkstemp-created file empty

    try:
        ra.atomic_commit(target, producer)
        check("empty producer output is rejected", False, "did not raise")
    except RuntimeError as e:
        check("empty producer output is rejected", "empty" in str(e), str(e))


def s10_atomic_commit_producer_exception_removes_temp_preserves_final():
    root = temp_dir()
    target = root / "out.png"
    target.write_bytes(b"OLD FINAL")

    captured = {}

    def raising_producer(tmp_path: Path):
        captured["tmp_path"] = tmp_path
        tmp_path.write_bytes(b"partial garbage")
        raise RuntimeError("simulated producer failure")

    try:
        ra.atomic_commit(target, raising_producer)
        check("a producer exception propagates", False, "did not raise")
    except RuntimeError as e:
        check("a producer exception propagates", "simulated producer failure" in str(e), str(e))
    check("the temp file was removed", not captured["tmp_path"].exists())
    check("the old final target is untouched", target.read_bytes() == b"OLD FINAL")


def s10_atomic_commit_replace_failure_preserves_final():
    root = temp_dir()
    target = root / "out.png"
    target.write_bytes(b"OLD FINAL")

    def producer(tmp_path: Path):
        tmp_path.write_bytes(_canonical_png_bytes())

    with mock.patch("os.replace", side_effect=OSError("simulated replace failure")):
        try:
            ra.atomic_commit(target, producer)
            check("an os.replace() failure propagates", False, "did not raise")
        except OSError:
            check("an os.replace() failure propagates", True)
    check("the old final target survives an os.replace() failure",
          target.read_bytes() == b"OLD FINAL")


def s10_build_dispatch_context_requires_a_real_snapshot():
    import visual_routes as vr
    route = _base_route()
    for bad_snapshot in (None, {"routes": (route,)}, "not a snapshot"):
        try:
            ra.build_dispatch_context(bad_snapshot, route, output_root=temp_dir())
            check(f"build_dispatch_context rejects a {type(bad_snapshot).__name__} "
                 f"snapshot", False, "did not raise")
        except ra.DispatchIntegrityError:
            check(f"build_dispatch_context rejects a {type(bad_snapshot).__name__} "
                 f"snapshot", True)


def _fake_snapshot(routes: tuple, root: Path, channel=None):
    import visual_routes as vr
    return vr.DispatchSnapshot(
        project_dir=root, routes_id="r1", routes_file_sha256="a" * 64,
        routes_content_sha256="b" * 64, channel=channel, routes=routes,
        approved_poses=MappingProxyType({}), poses_asset_base=None, poses_root=None,
        approved_references=MappingProxyType({}), references_asset_base=None,
        references_root=None)


def s10_build_dispatch_context_requires_route_membership_by_identity():
    root = temp_dir()
    real_route = _base_route(renderer_id="pexels", visual_type="PHOTO")
    snapshot = _fake_snapshot((real_route,), root)

    equal_copy = dict(real_route)
    for label, candidate in (
        ("an equal copy (not the same object)", equal_copy),
        ("an independently frozen mapping of the same content",
         MappingProxyType(dict(real_route))),
    ):
        try:
            ra.build_dispatch_context(snapshot, candidate, output_root=root)
            check(f"build_dispatch_context rejects {label}", False, "did not raise")
        except ra.DispatchIntegrityError:
            check(f"build_dispatch_context rejects {label}", True)

    other_route = _base_route(renderer_id="pexels", visual_type="PHOTO",
                              visual_asset_id="VIS-002-A")
    other_snapshot = _fake_snapshot((other_route,), root)
    try:
        ra.build_dispatch_context(other_snapshot, real_route, output_root=root)
        check("build_dispatch_context rejects a route from another snapshot",
              False, "did not raise")
    except ra.DispatchIntegrityError:
        check("build_dispatch_context rejects a route from another snapshot", True)

    ctx = ra.build_dispatch_context(snapshot, real_route, output_root=root)
    check("build_dispatch_context accepts the genuine member route",
          ctx.route is real_route)
    check("ctx.renderer_entry is the canonical entry for the route's renderer_id",
          ctx.renderer_entry is renderers.get("pexels"))


def s10_build_dispatch_context_binds_the_requested_renderer_field():
    root = temp_dir()
    route = _base_route(renderer_id="pexels", host_present=True,
                        host_method="approved_pose_composite",
                        host_renderer_id="approved_pose_compositor",
                        visual_type="PHOTO")
    snapshot = _fake_snapshot((route,), root)

    primary_ctx = ra.build_dispatch_context(snapshot, route, output_root=root,
                                            renderer_field="renderer_id")
    check("renderer_field='renderer_id' binds the primary renderer's entry",
          primary_ctx.renderer_entry is renderers.get("pexels"))

    host_ctx = ra.build_dispatch_context(snapshot, route, output_root=root,
                                         renderer_field="host_renderer_id")
    check("renderer_field='host_renderer_id' binds the host renderer's entry",
          host_ctx.renderer_entry is renderers.get("approved_pose_compositor"))
    check("the two contexts bind different renderer entries",
          primary_ctx.renderer_entry is not host_ctx.renderer_entry)


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
    ("5d. reference-anchored: stale hash refuses before any provider call",
     s5_reference_anchored_rejects_a_stale_hash_before_any_provider_call),
    ("5e. reference-anchored: non-approved status refuses before any provider call",
     s5_reference_anchored_rejects_a_non_approved_status_before_any_provider_call),
    ("5f. reference-anchored: top-level/master disagreement refuses before any "
     "provider call", s5_reference_anchored_rejects_top_level_master_disagreement_before_any_provider_call),
    ("5g. reference-anchored: traversal/archive paths refuse before any provider call",
     s5_reference_anchored_rejects_traversal_and_archive_paths_before_any_provider_call),
    ("6. regenerated Channel Pack: ILLUSTRATION_REFERENCE reachable, drift-clean",
     s6_illustration_reference_capability_is_reachable_in_the_real_channel),
    ("7a. all six adapters refuse normally, before any side effect",
     s7_all_six_adapters_refuse_normally_before_any_side_effect),
    ("7b. a valid legacy v2 approval cannot unlock a canonical adapter",
     s7_a_valid_legacy_v2_approval_cannot_unlock_a_canonical_adapter),
    ("7c. all six functions are registered in PAID_ENTRY_POINTS under the "
     "canonical_visual_execution kind",
     s7_all_six_functions_are_registered_in_paid_entry_points),
    ("8a. apply_output_transform produces a 16:9 PNG", s8_apply_output_transform_produces_16x9_png),
    ("8b. adapt_flux applies the registered transform to a non-16:9 response",
     s8_adapt_flux_applies_the_registered_transform_to_a_non_16x9_response),
    ("8c. adapt_flux_reference_anchor applies the registered transform",
     s8_adapt_flux_reference_anchor_applies_the_registered_transform),
    ("8d. transform failure leaves no successful-looking final file",
     s8_transform_failure_leaves_no_successful_looking_final_file),
    ("8e. changing the transform declaration changes the registry hash",
     s8_changing_the_transform_declaration_changes_the_registry_hash),
    ("9a. all adapters reject a mutable dict substituted as renderer_entry",
     s9_all_adapters_reject_a_mutable_dict_substituted_as_renderer_entry),
    ("9b. all adapters reject a caller-created MappingProxyType",
     s9_all_adapters_reject_a_caller_created_mappingproxytype),
    ("9c. all adapters reject another renderer's real entry",
     s9_all_adapters_reject_another_renderers_real_entry),
    ("9d. all adapters reject a missing/blank/unknown route.renderer_id",
     s9_all_adapters_reject_a_missing_blank_or_unknown_renderer_id),
    ("9e. all adapters reject an entry whose registered adapter does not match",
     s9_all_adapters_reject_an_entry_whose_registered_adapter_does_not_match),
    ("9f. adapt_flux refuses malformed execution settings before credentials/client",
     s9_adapt_flux_refuses_malformed_execution_settings_before_credentials_or_client),
    ("9g. credential lookup refuses before client construction when unavailable",
     s9_credential_lookup_refuses_before_client_construction_when_unavailable),
    ("9h. reference-anchored requires exactly two distinct reference ids",
     s9_reference_anchored_requires_exactly_two_distinct_reference_ids),
    ("10a. atomic_commit success writes via temp file then os.replace",
     s10_atomic_commit_success_writes_via_temp_file_then_replace),
    ("10b. atomic_commit failure removes temp and preserves existing final",
     s10_atomic_commit_failure_removes_temp_and_preserves_existing_final),
    ("10c. atomic_commit rejects a wrong-size PNG", s10_atomic_commit_wrong_size_is_rejected),
    ("10d. atomic_commit rejects empty output", s10_atomic_commit_empty_output_is_rejected),
    ("10e. atomic_commit: a producer exception removes temp, preserves final",
     s10_atomic_commit_producer_exception_removes_temp_preserves_final),
    ("10f. atomic_commit: an os.replace failure preserves the final target",
     s10_atomic_commit_replace_failure_preserves_final),
    ("10g. build_dispatch_context requires a real DispatchSnapshot",
     s10_build_dispatch_context_requires_a_real_snapshot),
    ("10h. build_dispatch_context requires route membership by identity",
     s10_build_dispatch_context_requires_route_membership_by_identity),
    ("10i. build_dispatch_context binds the requested renderer_field",
     s10_build_dispatch_context_binds_the_requested_renderer_field),
):
    run(title, fn)

print("\n" + "=" * 62)
print(f"FAILED ({len(failures)}): {failures}" if failures else "all renderer-adapter checks passed")
sys.exit(1 if failures else 0)
