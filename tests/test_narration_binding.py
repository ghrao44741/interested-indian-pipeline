"""tests/test_narration_binding.py — narration is bound to the approved
profile, and the binding survives from synthesis through split, approval and
generation.

Four defects, reproduced and closed:

  - production narration could still use any provider/voice once a DIFFERENT
    profile happened to be approved — the gate only ever checked the output
    PATH, never what was actually used to synthesise;
  - nothing proved the audio being split was actually produced by the
    approved voice — an old or manually supplied file could be split under a
    freshly approved profile;
  - `--channel` could override a project manifest's own assignment;
  - a sidecar or manifest could name the CURRENT correct approved-profile hash
    while `effective_profile` had been edited to describe different settings
    entirely — a hash that no longer describes what it claims to.

Fixtures and mocks only: `generate_source_audio.py`'s provider calls
(`edge_generate`, `normalize_loudness`, `get_duration`) are monkeypatched to
avoid any real synthesis or ffmpeg call, and the split-stage subprocess tests
run against stub `whisperx`/`torch` modules — this environment has neither
installed, and the script imports both unconditionally, so the stubs are
required just to get the process running, not only to prove a sentinel.

    python tests/test_narration_binding.py
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if "anthropic" not in sys.modules:
    _stub = types.ModuleType("anthropic")
    _stub.Anthropic = object
    sys.modules["anthropic"] = _stub

import approve_checkpoint as ac          # noqa: E402
import channel_context as cc             # noqa: E402
import channel_fixture                  # noqa: E402
import generate_source_audio as gsa      # noqa: E402
import generation_gate as gate           # noqa: E402
import plan_visuals                      # noqa: E402
import route_images                      # noqa: E402
import source_ids                        # noqa: E402

failures = []
_temps = []
STUB_MODULES_DIR = Path(__file__).parent / "fixtures" / "stub_modules"

APPROVED_PROFILE = {
    "provider": "edge",
    "settings": {"voice": "en-IN-PrabhatNeural", "rate": "+12%", "pitch": "+15Hz"},
    "approved_by": "test",
    "approved_at": "2026-01-01T00:00:00+00:00",
}


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(title, fn):
    print(f"\n{title}")
    try:
        fn()
    except Exception as e:
        import traceback
        check(title, False, f"{type(e).__name__}: {e}")
        traceback.print_exc()


def temp_root() -> Path:
    td = Path(tempfile.mkdtemp())
    _temps.append(td)
    (td / "channels").mkdir()
    return td


SCRIPT = ("The minister resigned in July. Nobody expected it.\n\n"
          "The exam was cancelled that week. Then it was rescheduled.\n")

PROMPTS_ONE_PHOTO = (
    '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` TYPE: PHOTO '
    'NARRATION: "one" PROMPT: a building\n'
)


def make_pack(channels_dir: Path, channel_id: str, *, voice_approved: bool = True,
             profile: dict = APPROVED_PROFILE) -> Path:
    """Host disabled — this suite is about narration, not character/pose fixtures."""
    d = channels_dir / channel_id
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": 1,
        "channel_id": channel_id,
        "channel_dna_version": 1,
        "brand": {"name": channel_id, "promise": "A promise.", "language": "English"},
        "audience": {"primary": "Tests", "secondary": "Tests"},
        "editorial": {"tone": ["dry"], "principles": ["Say what is true."]},
        "narrative": {
            "structure": [{"step": 1, "beat": "Open."}],
            "portfolio_planning_guidance": {
                "note": "Guidance, not a quota.",
                "shares": [{"share_pct": 100, "label": "Everything"}]}},
        "visual_style": {"palette": {"ink": "#101010"}, "pad_color": "#101010",
                         "rules": ["Flat."], "ken_burns_zoom": 1.05},
        "host": {"enabled": False},
        "voice": ({"selection_status": "approved", "approved_profile": profile,
                   "working_default": None,
                   "preview_dir": {"path": "voice_previews",
                                   "path_kind": "legacy_pipeline_root"}}
                  if voice_approved else
                  {"selection_status": "pending", "approved_profile": None,
                   "working_default": {"provider": "edge", "voice": "x",
                                       "approved": False},
                   "preview_dir": {"path": "voice_previews",
                                   "path_kind": "legacy_pipeline_root"}}),
        "routing": {"policy_version": 1},
        "renderers": {"capabilities": {"PHOTO": "pexels"}},
        "evidence": {"require_provenance_for": ["PHOTO"]},
        "safety": {"rules": ["Attribute claims."]},
        "economics": {"currency": "USD", "image_pricing": {}},
    }
    (d / "channel.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    import render_channel_dna as rd
    (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")
    return d


def make_project(root: Path, name: str, channel_id: str | None,
                 prompts: str = PROMPTS_ONE_PHOTO) -> Path:
    proj = root / name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "script_demo.txt").write_text(SCRIPT, encoding="utf-8")
    units = source_ids.build_source_units(SCRIPT)
    source_ids.save_units(proj, units)
    scenes = [{"id": f"SCENE-{i:03d}", "image": f"SCENE-{i:03d}.png", "script": u["text"],
               "source_ids": [u["id"]], "shot_instance_id": f"{u['id']}-S01",
               "source_match": "ok", "visual_match": "ok", "visual_state": "planned",
               "visual_asset_id": f"VIS-{i:03d}-A"}
              for i, u in enumerate(units, 1)]
    manifest = {"episode": name, "identity_state": "ok", "identity_reasons": [],
               "scenes": scenes}
    if channel_id:
        manifest["channel_id"] = channel_id
        manifest["channel_dna_version"] = 1
    (proj / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (proj / route_images.PROMPTS_FILE).write_text(prompts, encoding="utf-8")
    return proj


class World:
    def __init__(self, root: Path):
        self.ctxs = [
            mock.patch.multiple(cc, PIPELINE_DIR=root, CHANNELS_DIR=root / "channels"),
            mock.patch.multiple(gate, PIPELINE_DIR=root,
                                SPEC_PATH=root / "character" / "character_spec.json"),
            mock.patch.object(plan_visuals, "PIPELINE_DIR", root),
            mock.patch.object(ac, "PIPELINE_DIR", root),
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


# ── 1. production narration conflicts with the approved profile ────────────

def s1_production_conflict():
    root = temp_root()
    make_pack(root / "channels", "beacon")
    with World(root):
        c = cc.load_channel("beacon")

    args = types.SimpleNamespace(provider=None, voice=None, speaking_rate=None,
                                 speed=None, edge_rate=None, edge_pitch=None)
    eff = gsa._resolve_production_voice(args, c)
    check("no explicit override -> the approved profile is used verbatim",
          eff == {"provider": "edge", **APPROVED_PROFILE["settings"]})

    for attr, value in (("voice", "SomeOtherVoice"), ("edge_rate", "+50%"),
                       ("edge_pitch", "-20Hz"), ("provider", "grok")):
        bad_args = types.SimpleNamespace(provider=None, voice=None, speaking_rate=None,
                                         speed=None, edge_rate=None, edge_pitch=None)
        setattr(bad_args, attr, value)
        try:
            gsa._resolve_production_voice(bad_args, c)
            check(f"conflicting --{attr.replace('_', '-')} is refused", False,
                  "it was accepted")
        except SystemExit as e:
            check(f"conflicting --{attr.replace('_', '-')} is refused", e.code != 0)


# ── 2. classification by destination, integrated through main() ────────────

async def _fake_edge_generate(text, voice, output_path, rate=None, pitch=None):
    Path(output_path).write_bytes(b"ID3fake mp3 bytes for a test\n")


def _run_main(argv):
    with mock.patch.object(sys, "argv", ["generate_source_audio.py", *argv]), \
            mock.patch.object(gsa, "edge_generate", _fake_edge_generate), \
            mock.patch.object(gsa, "normalize_loudness", lambda *a, **k: None), \
            mock.patch.object(gsa, "get_duration", lambda *a, **k: 1.23):
        try:
            asyncio.run(gsa.main())
            return 0
        except SystemExit as e:
            return e.code or 0


def s2_production_uses_approved_profile():
    root = temp_root()
    make_pack(root / "channels", "beacon")
    proj = make_project(root, "ep_x", channel_id="beacon")
    script = proj / "script_demo.txt"

    with World(root):
        code = _run_main(["--project", str(proj), "--script", str(script),
                          "--out", "narration.mp3"])
    check("a production run with no override succeeds", code == 0, f"exit={code}")

    audio = proj / "source_audio" / "narration.mp3"
    sidecar_path = Path(f"{audio}.voice.json")
    check("production audio was written", audio.is_file())
    check("a full-binding sidecar was written", sidecar_path.is_file())
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    check("sidecar records schema_version 1", sidecar.get("schema_version") == 1)
    check("sidecar names the channel", sidecar.get("channel_id") == "beacon")
    check("sidecar's effective_profile is exactly the approved profile",
          sidecar.get("effective_profile") == APPROVED_PROFILE)
    check("sidecar's audio_sha256 matches the actual file",
          sidecar.get("audio_sha256") == sha(audio))
    with World(root):
        c = cc.load_channel("beacon")
    check("sidecar's voice_profile_sha256 matches the channel's",
          sidecar.get("voice_profile_sha256") == c.voice_profile_sha256)


def s3_production_conflict_end_to_end():
    root = temp_root()
    make_pack(root / "channels", "beacon")
    proj = make_project(root, "ep_x", channel_id="beacon")
    script = proj / "script_demo.txt"

    with World(root):
        code = _run_main(["--project", str(proj), "--script", str(script),
                          "--out", "narration.mp3", "--voice", "SomeOtherVoice"])
    check("a conflicting production override exits nonzero", code != 0, f"exit={code}")
    check("nothing was synthesised",
          not (proj / "source_audio" / "narration.mp3").exists())


def s4_boundary_preview_flag_does_not_grant_production_leniency():
    """Boundary 1: --preview targeting project audio with an approved voice is
    still production, and still refuses a conflicting override."""
    root = temp_root()
    make_pack(root / "channels", "beacon")
    proj = make_project(root, "ep_x", channel_id="beacon")
    script = proj / "script_demo.txt"

    with World(root):
        code = _run_main(["--project", str(proj), "--script", str(script),
                          "--preview", "1", "--out", "narration.mp3",
                          "--voice", "SomeOtherVoice"])
    check("--preview targeting project audio still refuses a conflicting voice",
          code != 0, f"exit={code}")
    check("nothing was written",
          not (proj / "source_audio" / "narration.mp3").exists())


def s5_boundary_full_run_to_preview_dir_is_evaluation():
    """Boundary 2: a full script run (no --preview) targeting voice_previews/
    remains evaluation, and any provider/voice combination is allowed."""
    root = temp_root()
    make_pack(root / "channels", "beacon")
    proj = make_project(root, "ep_x", channel_id="beacon")
    script = proj / "script_demo.txt"
    out_path = root / "voice_previews" / "candidate_x.mp3"

    with World(root):
        code = _run_main(["--project", str(proj), "--script", str(script),
                          "--out", str(out_path), "--voice", "AnyVoiceAtAll",
                          "--provider", "edge"])
    check("a full run targeting voice_previews/ succeeds despite an "
         "arbitrary voice", code == 0, f"exit={code}")
    check("evaluation output landed in voice_previews/", out_path.is_file())
    sidecar = json.loads(Path(f"{out_path}.voice.json").read_text(encoding="utf-8"))
    check("evaluation sidecar is the old free-form shape, not channel-bound",
          "channel_id" not in sidecar and sidecar.get("voice") == "AnyVoiceAtAll")


# ── 3. manifest channel outranks --channel ──────────────────────────────────

def s6_manifest_outranks_requested_channel():
    root = temp_root()
    make_pack(root / "channels", "alpha")
    make_pack(root / "channels", "beta")
    proj = make_project(root, "ep_x", channel_id="alpha")

    with World(root):
        try:
            cc.channel_for_voice(project_dir=proj, requested="beta")
            check("a conflicting --channel is refused", False, "it returned")
        except cc.ChannelError as e:
            check("a conflicting --channel is refused", True)
            check("the refusal names both channels",
                  "alpha" in str(e) and "beta" in str(e), str(e))

        for requested in (None, "alpha"):
            got = cc.channel_for_voice(project_dir=proj, requested=requested)
            check(f"requested={requested!r} resolves to the manifest's channel",
                  got.channel_id == "alpha")

        no_manifest_proj = root / "fresh_project"
        no_manifest_proj.mkdir()
        got = cc.channel_for_voice(project_dir=no_manifest_proj, requested="beta")
        check("an explicit --channel is honoured when there is no manifest yet",
              got.channel_id == "beta")


# ── 4. the internal-consistency check: hash right, settings wrong ───────────

def _valid_narration_fixture(proj, profile=APPROVED_PROFILE):
    audio_dir = proj / "source_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / "narration.mp3"
    audio_path.write_bytes(b"real narration bytes\n")
    return {
        "narration_audio_file": "source_audio/narration.mp3",
        "narration_voice_profile_sha256": cc.canonical_sha256(profile),
        "narration_audio_sha256": sha(audio_path),
        "narration_effective_profile": profile,
    }, audio_path


def s7_internal_consistency_defect():
    """A sidecar/manifest naming the CORRECT current hash while
    effective_profile has been edited to different settings — the missing
    link a hash-only check would leave open. Must be refused by the split
    (via a hand-built sidecar), by approve_checkpoint, and by the generation
    gate — all through the one shared validator.
    """
    root = temp_root()
    make_pack(root / "channels", "beacon")
    proj = make_project(root, "ep_x", channel_id="beacon")
    fields, audio_path = _valid_narration_fixture(proj)

    # Corrupt: keep the hash exactly as recorded (it IS the current, correct
    # hash), but change what effective_profile actually claims.
    tampered = dict(fields)
    tampered["narration_effective_profile"] = {
        "provider": "edge",
        "settings": {"voice": "SomeOtherVoice", "rate": "+12%", "pitch": "+15Hz"},
        "approved_by": "test", "approved_at": "2026-01-01T00:00:00+00:00",
    }
    # voice_profile_sha256 deliberately left unchanged — this is the point.

    with World(root):
        ctx = cc.load_channel("beacon")
        problems = gate.narration_binding_problems(proj, tampered, ctx)
    check("the generation gate's shared validator catches the mismatch",
          bool(problems), "no problems reported")
    check("the problem names the disagreement, not just a missing field",
          any("disagree" in p or "does not match" in p for p in problems),
          str(problems))

    manifest = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(tampered)
    (proj / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    p = plan(root, proj)
    try:
        approve(root, proj)
        check("approve_checkpoint refuses the tampered binding", False, "it approved")
    except ac.ApprovalRefused as e:
        check("approve_checkpoint refuses the tampered binding", True)
        check("via the narration binding, not an unrelated reason",
              "narration binding" in str(e), str(e)[:200])

    rep = verdict(root, proj)
    check("require_generation_ready blocks on the tampered binding",
          blocked_on(rep, "narration binding is verified"), str(rep.blockers))

    # And a genuinely valid binding (hash matches AND settings match) passes
    # the same validator, so this isn't just "always block".
    with World(root):
        clean_problems = gate.narration_binding_problems(proj, fields, ctx)
    check("a genuinely consistent binding has no problems", not clean_problems,
          str(clean_problems))


def s8_approval_refuses_missing_binding():
    root = temp_root()
    make_pack(root / "channels", "beacon")
    proj = make_project(root, "ep_x", channel_id="beacon")
    plan(root, proj)
    try:
        approve(root, proj)
        check("approval refuses a plan with no narration binding at all", False,
              "it approved")
    except ac.ApprovalRefused as e:
        check("approval refuses a plan with no narration binding at all", True)
        check("naming the missing binding", "narration binding" in str(e), str(e)[:200])


# ── 5. split-stage: sidecar requirement and verification ───────────────────

def _install_real_channel(channel_id: str, *, voice_approved: bool = True,
                          profile: dict = APPROVED_PROFILE) -> Path:
    """Write a real, temporary Channel Pack into THIS repo's own channels/.

    Subprocess tests below run auto_split_scenes_v1_stage3_export.py as a real
    process against the real repository — nothing here is patched, because
    the whole point is proving the real CLI's exit code. That means the pack
    has to actually exist on disk under ROOT/channels/, not just under a
    temp root a mock would redirect to. Always paired with
    _remove_real_channel() in a try/finally.
    """
    import render_channel_dna as rd
    d = ROOT / "channels" / channel_id
    if d.exists():
        raise RuntimeError(f"refusing to overwrite an existing channel dir: {d}")
    d.mkdir(parents=True)
    doc = {
        "schema_version": 1, "channel_id": channel_id, "channel_dna_version": 1,
        "brand": {"name": channel_id, "promise": "A promise.", "language": "English"},
        "audience": {"primary": "Tests", "secondary": "Tests"},
        "editorial": {"tone": ["dry"], "principles": ["Say what is true."]},
        "narrative": {
            "structure": [{"step": 1, "beat": "Open."}],
            "portfolio_planning_guidance": {
                "note": "Guidance, not a quota.",
                "shares": [{"share_pct": 100, "label": "Everything"}]}},
        "visual_style": {"palette": {"ink": "#101010"}, "pad_color": "#101010",
                         "rules": ["Flat."], "ken_burns_zoom": 1.05},
        "host": {"enabled": False},
        "voice": ({"selection_status": "approved", "approved_profile": profile,
                   "working_default": None,
                   "preview_dir": {"path": "voice_previews",
                                   "path_kind": "legacy_pipeline_root"}}
                  if voice_approved else
                  {"selection_status": "pending", "approved_profile": None,
                   "working_default": {"provider": "edge", "voice": "x",
                                       "approved": False},
                   "preview_dir": {"path": "voice_previews",
                                   "path_kind": "legacy_pipeline_root"}}),
        "routing": {"policy_version": 1},
        "renderers": {"capabilities": {"PHOTO": "pexels"}},
        "evidence": {"require_provenance_for": ["PHOTO"]},
        "safety": {"rules": ["Attribute claims."]},
        "economics": {"currency": "USD", "image_pricing": {}},
    }
    (d / "channel.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")
    return d


def _remove_real_channel(channel_id: str) -> None:
    import shutil
    d = ROOT / "channels" / channel_id
    if d.is_dir():
        shutil.rmtree(d)


def _split_env(sentinel_path: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{STUB_MODULES_DIR}{os.pathsep}{ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["PYTHONIOENCODING"] = "utf-8"
    env["WHISPERX_STUB_SENTINEL"] = str(sentinel_path)
    return env


def _run_split(root, proj_name, *extra_args, sentinel_name="sentinel.txt"):
    sentinel = root / sentinel_name
    cmd = [sys.executable, str(ROOT / "auto_split_scenes_v1_stage3_export.py"),
          "--project", str(root / proj_name), "--audio", "narration.mp3",
          "--allow-missing-script", *extra_args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=_split_env(sentinel), cwd=str(root))
    return r, sentinel


def _split_project(root, channel_id="beacon", profile=APPROVED_PROFILE,
                   narration_fields=None):
    import shutil
    proj = root / "split_proj"
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir()
    audio_dir = proj / "source_audio"
    audio_dir.mkdir()
    audio_path = audio_dir / "narration.mp3"
    audio_path.write_bytes(b"real narration bytes for split test\n")
    manifest = {"episode": "split_proj", "identity_state": "ok",
               "identity_reasons": [], "channel_id": channel_id,
               "channel_dna_version": 1, "scenes": []}
    (proj / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if narration_fields is None:
        narration_fields = {
            "schema_version": 1, "channel_id": channel_id,
            "voice_profile_sha256": cc.canonical_sha256(profile),
            "effective_profile": profile,
            "audio_sha256": sha(audio_path),
            "generated_at": "2026-01-01T00:00:00+00:00",
        }
    if narration_fields is not False:
        Path(f"{audio_path}.voice.json").write_text(
            json.dumps(narration_fields, indent=2), encoding="utf-8")
    return proj, audio_path


def s9_split_requires_and_verifies_sidecar():
    root = temp_root()

    # (a)-(d) below all fail on some earlier, independent problem (missing
    # sidecar, wrong channel named in it, a hash mismatch) regardless of
    # whether "beacon" is actually a resolvable channel, so they don't need
    # the real installer. (e) is the one case that needs a truly loadable
    # channel to reach transcription — that one runs against a temporarily
    # installed real pack, removed immediately after in a finally.

    # (a) missing sidecar
    proj, audio = _split_project(root, narration_fields=False)
    r, sentinel = _run_split(root, "split_proj", sentinel_name="s_missing.txt")
    check("missing sidecar: split refuses", r.returncode != 0, r.stdout[-300:])
    check("missing sidecar: transcription never reached", not sentinel.exists())

    # (b) channel_id mismatch in the sidecar
    proj2, audio2 = _split_project(root, channel_id="beacon")
    Path(f"{audio2}.voice.json").write_text(json.dumps({
        "schema_version": 1, "channel_id": "someone_else",
        "voice_profile_sha256": cc.canonical_sha256(APPROVED_PROFILE),
        "effective_profile": APPROVED_PROFILE,
        "audio_sha256": sha(audio2),
        "generated_at": "2026-01-01T00:00:00+00:00"}, indent=2), encoding="utf-8")
    r, sentinel = _run_split(root, "split_proj", sentinel_name="s_mismatch.txt")
    check("channel mismatch: split refuses", r.returncode != 0, r.stdout[-300:])
    check("channel mismatch: transcription never reached", not sentinel.exists())

    # (c) audio swapped after narration (hash no longer matches)
    proj3, audio3 = _split_project(root)
    audio3.write_bytes(b"a different file entirely, swapped in after the fact\n")
    r, sentinel = _run_split(root, "split_proj", sentinel_name="s_swapped.txt")
    check("swapped audio: split refuses", r.returncode != 0, r.stdout[-300:])
    check("swapped audio: transcription never reached", not sentinel.exists())

    # (d) the internal-consistency defect at split time: hash unchanged,
    # effective_profile edited to different settings
    tampered_profile = {
        "provider": "edge",
        "settings": {"voice": "SomeOtherVoice", "rate": "+12%", "pitch": "+15Hz"},
        "approved_by": "test", "approved_at": "2026-01-01T00:00:00+00:00",
    }
    proj4, audio4 = _split_project(root, channel_id="beacon")
    Path(f"{audio4}.voice.json").write_text(json.dumps({
        "schema_version": 1, "channel_id": "beacon",
        "voice_profile_sha256": cc.canonical_sha256(APPROVED_PROFILE),  # unchanged, correct
        "effective_profile": tampered_profile,                          # but this disagrees
        "audio_sha256": sha(audio4),
        "generated_at": "2026-01-01T00:00:00+00:00"}, indent=2), encoding="utf-8")
    r, sentinel = _run_split(root, "split_proj", sentinel_name="s_tampered.txt")
    check("hash-right-settings-wrong: split refuses", r.returncode != 0, r.stdout[-300:])
    check("hash-right-settings-wrong: transcription never reached", not sentinel.exists())

    # (e) a genuinely valid sidecar reaches transcription (proven by the stub
    # having been called and raised, not by the exit code alone). Needs a
    # real, resolvable channel — nothing in this subprocess is patched.
    real_id = "nb_test_e2e"
    _install_real_channel(real_id)
    try:
        proj5, audio5 = _split_project(root, channel_id=real_id,
                                       profile=APPROVED_PROFILE)
        r, sentinel = _run_split(root, "split_proj", sentinel_name="s_ok.txt")
        check("a verified sidecar reaches transcription", sentinel.exists(),
              r.stdout[-400:] + r.stderr[-400:])
    finally:
        _remove_real_channel(real_id)


def s10_split_channel_assignment_regression():
    """§5: the split-stage's own channel-assignment refusals must produce a
    real nonzero process exit — main() returning 2 with no sys.exit() would
    leave automation seeing exit 0."""
    root = temp_root()
    make_pack(root / "channels", "alpha")
    make_pack(root / "channels", "beta")

    proj = root / "assigned_proj"
    proj.mkdir()
    (proj / "manifest.json").write_text(
        json.dumps({"episode": "assigned_proj", "channel_id": "alpha",
                   "channel_dna_version": 1, "scenes": []}, indent=2),
        encoding="utf-8")
    (proj / "source_audio").mkdir()
    (proj / "source_audio" / "narration.mp3").write_bytes(b"x\n")

    r, sentinel = _run_split(root, "assigned_proj", "--channel", "beta",
                             sentinel_name="s_conflict.txt")
    check("a conflicting --channel exits nonzero", r.returncode != 0, r.stdout[-300:])
    check("transcription never reached", not sentinel.exists())

    r, sentinel = _run_split(root, "assigned_proj", "--channel", "nosuch",
                             sentinel_name="s_unknown.txt")
    check("an unknown --channel exits nonzero", r.returncode != 0, r.stdout[-300:])
    check("transcription never reached", not sentinel.exists())


# ── run ──────────────────────────────────────────────────────────────────────

def main() -> int:
    try:
        run("1. production narration conflicts with the approved profile",
            s1_production_conflict)
        run("2. a production write derives config from the approved profile",
            s2_production_uses_approved_profile)
        run("3. a conflicting production override is refused end-to-end",
            s3_production_conflict_end_to_end)
        run("4. boundary: --preview + project audio is still production",
            s4_boundary_preview_flag_does_not_grant_production_leniency)
        run("5. boundary: a full run to voice_previews/ is still evaluation",
            s5_boundary_full_run_to_preview_dir_is_evaluation)
        run("6. manifest channel outranks a requested --channel",
            s6_manifest_outranks_requested_channel)
        run("7. hash-right-settings-wrong is refused everywhere",
            s7_internal_consistency_defect)
        run("8. approval refuses a plan with no narration binding",
            s8_approval_refuses_missing_binding)
        run("9. the split stage requires and verifies the sidecar",
            s9_split_requires_and_verifies_sidecar)
        run("10. split-stage channel-assignment refusal exits nonzero",
            s10_split_channel_assignment_regression)
    finally:
        for td in _temps:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    print(f"\n{'=' * 62}")
    if failures:
        print(f"FAILED: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all narration-binding checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
