"""A channel is a property of the episode, and nothing about it is inferable.

What this covers, and why each one is not obvious:

  - **Containment under the pipeline root is not a boundary.** Every pack and
    every episode folder lives beneath it, so a naive "is it under PIPELINE_DIR"
    check would happily accept `channels/other/...`. The legacy path kind
    resolves against an allowlist instead, and section 6 proves the difference.
  - **A generated document that nobody re-renders is a second source of truth.**
    CHANNEL_DNA.md and the root adapters are re-rendered on every load.
  - **The root adapters belong to exactly one pack.** Loading any other channel
    must not read, compare against or write them — section 4 watches every file
    the loader opens.
  - **A pending voice must be enforceable, not merely described.** Section 8
    proves it blocks approval, blocks generation, and confines synthesis output
    to the preview directory — while leaving planning reachable.

Fixtures and mocks only. No paid calls, no synthesis, no repository writes.

    python tests/test_channel_context.py
"""

import ast
import builtins
import hashlib
import json
import os
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

import approve_checkpoint as ac          # noqa: E402
import channel_context as cc             # noqa: E402
import channel_fixture                  # noqa: E402
import composite_character               # noqa: E402
import generation_gate as gate           # noqa: E402
import plan_visuals                      # noqa: E402
import pose_registry                     # noqa: E402
import render_channel_dna as rd          # noqa: E402
import renderers                         # noqa: E402
import route_images                      # noqa: E402
import source_ids                        # noqa: E402

FIXTURE_PACK = ROOT / "tests" / "fixtures" / "channels" / "test_channel"
REAL_CHANNEL = "interested_indian"

# Shared with make_pack()'s "voice_approved" branch below, so a project built
# by _ready_project() can carry a narration binding that genuinely matches the
# pack it was planned against.
FIXTURE_APPROVED_PROFILE = {
    "provider": "edge",
    "settings": {"voice": "v", "rate": "+0%", "pitch": "+0Hz"},
    "approved_by": "test",
    "approved_at": "2026-01-01T00:00:00+00:00",
}

failures = []
_temps = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def census(root: Path) -> dict:
    """Hash every file under character/. Whole tree, not selected subdirectories.

    Narrower versions of this have missed real writes before — a preview path
    computed at import time did not move when the pipeline root was patched.
    """
    out = {}
    d = root / "character"
    if d.exists():
        for f in sorted(d.rglob("*")):
            if f.is_file():
                out[f.relative_to(root).as_posix()] = sha(f)
    return out


def blocked_on(rep, fragment):
    return any(fragment in b for b in rep.blockers)


# ── world building ───────────────────────────────────────────────────────────

SCRIPT = ("The minister resigned in July. Nobody expected it.\n\n"
          "The exam was cancelled that week. Then it was rescheduled.\n")

PROMPTS = (
    '**SHOT 01** · SCENE-001 · standalone → `SCENE-001.png` TYPE: PHOTO '
    'NARRATION: "one" PROMPT: a building\n'
    '**SHOT 02** · SCENE-002 · standalone → `SCENE-002.png` TYPE: HOST '
    'HOST_POSE: neutral_presenter NARRATION: "two" PROMPT: host\n'
)


def _png(path: Path, alpha=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA" if alpha else "RGB", (256, 256),
                   (250, 247, 242, 0) if alpha else (250, 247, 242))
    im.paste(Image.new("RGBA", (80, 160), (60, 60, 60, 255)), (88, 60))
    im.save(path)


def _character_tree(base: Path) -> dict:
    """Write a minimal approved character package under `base`, return the spec."""
    for m in ("body-master.png", "face-master.png"):
        _png(base / "canonical" / m, alpha=False)
    registry = {}
    for pid, fname in (("neutral_presenter", "host_neutral_presenter.png"),
                       ("open_hand_explaining", "host_open_hand_explaining.png")):
        f = base / "poses" / fname
        _png(f)
        registry[pid] = {"id": pid, "path": f"{base.name}/poses/{fname}",
                         "sha256": sha(f), "dimensions": "256x256",
                         "status": "approved", "direction": "front",
                         "negative_space": "both_sides", "includes_geometry": [],
                         "generic_compositing_allowed": True}
    spec = {
        "version": 4, "identity": {"prompt_block": "x"}, "immutable": ["a"],
        "variable": ["b"], "negative_prompt": "none",
        "negative_prompt_by_framing": {"full_body": ""}, "style": {"rules": ["flat"]},
        "references": {"body_master": f"{base.name}/canonical/body-master.png",
                       "face_master": f"{base.name}/canonical/face-master.png"},
        "master_authority": {"body_master": ["clothing"], "face_master": ["face"]},
        "masters": {k: {"path": f"{base.name}/canonical/{v}",
                        "sha256": sha(base / "canonical" / v), "status": "approved"}
                    for k, v in (("body_master", "body-master.png"),
                                 ("face_master", "face-master.png"))},
        "pose_library": {"version": 2, "status": "approved", "approved_batches": [1],
                         "registry": registry},
    }
    (base / "character_spec.json").write_text(json.dumps(spec, indent=2),
                                              encoding="utf-8")
    return spec


def make_pack(channels_dir: Path, channel_id: str, *, host: bool = True,
              voice_approved: bool = True, dna_version: int = 1,
              path_kind: str = "pack_relative", spec_path: str | None = None,
              capabilities: dict | None = None) -> Path:
    """A schema-valid pack, built inline so each test can vary exactly one thing."""
    d = channels_dir / channel_id
    d.mkdir(parents=True, exist_ok=True)
    if host:
        _character_tree(d / "character")
    doc = {
        "schema_version": 1,
        "channel_id": channel_id,
        "channel_dna_version": dna_version,
        "brand": {"name": channel_id.replace("_", " ").title(),
                  "promise": "A promise.", "language": "English"},
        "audience": {"primary": "Some people", "secondary": "Other people"},
        "editorial": {"tone": ["dry"], "principles": ["Say what is true."]},
        "narrative": {
            "structure": [{"step": 1, "beat": "Open."}],
            "portfolio_planning_guidance": {
                "note": "Guidance, not a quota.",
                "shares": [{"share_pct": 100, "label": "Everything"}]}},
        "visual_style": {"palette": {"ink": "#101010"}, "pad_color": "#101010",
                         "rules": ["Flat."], "ken_burns_zoom": 1.05},
        "host": {"enabled": host, **({"target_presence_pct": {"min": 20, "max": 30},
                                      "soft_ceiling_pct": 35, "max_consecutive": 2}
                                     if host else {})},
        "voice": ({"selection_status": "approved",
                   "approved_profile": FIXTURE_APPROVED_PROFILE,
                   "working_default": None,
                   "preview_dir": {"path": "voice_previews",
                                   "path_kind": "legacy_pipeline_root"}}
                  if voice_approved else
                  {"selection_status": "pending", "approved_profile": None,
                   "working_default": {"provider": "edge",
                                       "settings": {"voice": "v", "rate": "+0%",
                                                    "pitch": "+0Hz"},
                                       "approved": False},
                   "preview_dir": {"path": "voice_previews",
                                   "path_kind": "legacy_pipeline_root"}}),
        "routing": {"policy_version": 1},
        "renderers": {"capabilities": capabilities
                      or {"PHOTO": "pexels", "HOST_COMPOSITE": "approved_pose_compositor"}},
        "evidence": {"require_provenance_for": ["PHOTO"]},
        "safety": {"rules": ["Attribute claims."]},
        "economics": {"currency": "USD", "image_pricing": {}},
    }
    if host:
        doc["character"] = {"spec_path": spec_path or "character/character_spec.json",
                            "path_kind": path_kind}
    (d / "channel.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")
    return d


def make_project(root: Path, name: str, channel_id: str | None,
                 dna_version: int | None = 1, prompts: str = PROMPTS) -> Path:
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
        manifest["channel_dna_version"] = dna_version
    (proj / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (proj / route_images.PROMPTS_FILE).write_text(prompts, encoding="utf-8")
    return proj


class World:
    """Point every module's roots at a temporary tree."""

    def __init__(self, root: Path, spec: Path | None = None):
        self.root = root
        channels = root / "channels"
        self.ctxs = [
            mock.patch.multiple(cc, PIPELINE_DIR=root, CHANNELS_DIR=channels),
            mock.patch.multiple(gate, PIPELINE_DIR=root,
                                SPEC_PATH=spec or (root / "character" /
                                                   "character_spec.json")),
            mock.patch.multiple(pose_registry, PIPELINE_DIR=root,
                                SPEC_PATH=spec or (root / "character" /
                                                   "character_spec.json")),
            mock.patch.multiple(composite_character, PIPELINE_DIR=root,
                                PREVIEW_DIR=root / "character" / "previews"),
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


def temp_root() -> Path:
    td = Path(tempfile.mkdtemp())
    _temps.append(td)
    (td / "channels").mkdir()
    return td


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


def run(title, fn):
    print(f"\n{title}")
    try:
        fn()
    except Exception as e:                                   # a crash is a failure
        import traceback
        check(title, False, f"{type(e).__name__}: {e}")
        traceback.print_exc()


# ── 1-2. valid packs load ────────────────────────────────────────────────────

def s1_real_pack():
    c = cc.load_channel(REAL_CHANNEL)
    check("the live pack loads", c.channel_id == REAL_CHANNEL)
    check("it resolves its character specification through the legacy allowlist",
          c.character_spec_path is not None and c.character_spec_path.is_file(),
          str(c.character_spec_path))
    check("the character spec hash is recorded",
          c.character_spec_sha256 == sha(c.character_spec_path))
    check("the pack hash is content-addressed, not byte-addressed",
          c.channel_json_sha256 == cc.canonical_sha256(cc._thaw(c.config)))
    check("its declared capabilities are all registered renderers",
          all(r in renderers.RENDERERS for r in c.renderer_capabilities.values()),
          str(dict(c.renderer_capabilities)))
    check("the config is immutable", isinstance(c.config, type(cc._freeze({}))))
    try:
        c.config["brand"] = {}
        check("mutating the loaded config raises", False, "it succeeded")
    except TypeError:
        check("mutating the loaded config raises", True)


def s2_synthetic_pack():
    root = temp_root()
    shutil.copytree(FIXTURE_PACK, root / "channels" / "harbourline")
    with World(root):
        c = cc.load_channel("harbourline")
        check("the committed synthetic pack loads", c.channel_id == "harbourline")
        check("it has no host", c.host_enabled is False)
        check("a host-less pack resolves no character assets",
              c.character_spec_path is None and c.character_spec_sha256 is None)
        check("it declares no map capability",
              "MAP" not in c.renderer_capabilities, str(dict(c.renderer_capabilities)))
        check("it declares no paid renderer",
              all(renderers.RENDERERS[r]["cost_category"] != "paid_api"
                  for r in c.renderer_capabilities.values()))
        check("its approved voice yields a profile hash",
              c.voice_approved and c.voice_profile_sha256 is not None)
        check("it emits no root adapters", rd.render_adapters(cc._thaw(c.config)) == {})

    live = cc.load_channel(REAL_CHANNEL)
    text = (FIXTURE_PACK / "channel.json").read_text(encoding="utf-8").lower()
    leaked = [w for w in ("interested", "india", "neet", "mascot", "kurta", "1a2b4c")
              if w in text]
    check("no live-channel identity leaks into the synthetic pack", not leaked,
          f"found {leaked}")
    check("the two packs share no palette value",
          not (set(live.config["visual_style"]["palette"].values())
               & set(json.loads((FIXTURE_PACK / "channel.json").read_text(
                   encoding="utf-8"))["visual_style"]["palette"].values())))


# ── 3-5. refusals ────────────────────────────────────────────────────────────

def s3_missing_channel_id():
    root = temp_root()
    make_pack(root / "channels", "beacon")
    proj = make_project(root, "ep_x", channel_id=None)
    with World(root):
        try:
            cc.load_channel_for_project(proj)
            check("a manifest with no channel_id is refused", False, "it loaded")
        except cc.ChannelError as e:
            check("a manifest with no channel_id is refused", True)
            check("the refusal says it is never inferred", "never inferred" in str(e),
                  str(e)[:120])
        rep = gate.require_identity_ready(proj, "t", raise_on_block=False)
        check("the identity gate blocks on it",
              blocked_on(rep, "episode names a loadable channel"), str(rep.blockers))


def s4_unknown_channel():
    root = temp_root()
    make_pack(root / "channels", "beacon")
    proj = make_project(root, "ep_x", channel_id="nosuchchannel")
    with World(root):
        try:
            cc.load_channel_for_project(proj)
            check("an unknown channel is refused", False, "it loaded")
        except cc.ChannelError as e:
            check("an unknown channel is refused", True)
            check("it lists what does exist", "beacon" in str(e), str(e)[:150])
        for bad in ("../escape", "Beacon", "ab", "a" * 80, "", None):
            try:
                cc.pack_dir(bad)
                check(f"channel id {bad!r} refused before any path join", False)
            except cc.ChannelError:
                check(f"channel id {bad!r} refused before any path join", True)


def s5_schema():
    root = temp_root()
    d = make_pack(root / "channels", "beacon")
    good = json.loads((d / "channel.json").read_text(encoding="utf-8"))

    def refuse(label, mutate):
        doc = json.loads(json.dumps(good))
        mutate(doc)
        try:
            cc.validate_document(doc, source="t")
            check(label, False, "it validated")
        except cc.ChannelError:
            check(label, True)

    refuse("a missing required field is refused", lambda d: d.pop("safety"))
    refuse("a wrong type is refused",
           lambda d: d.__setitem__("channel_dna_version", "one"))
    refuse("an unknown top-level field is refused",
           lambda d: d.__setitem__("mystery", 1))
    refuse("an unknown nested field is refused",
           lambda d: d["brand"].__setitem__("slogan", "x"))
    refuse("an unsupported schema_version is refused",
           lambda d: d.__setitem__("schema_version", 99))
    refuse("a host-enabled pack with no character section is refused",
           lambda d: d.pop("character"))
    refuse("an approved voice with no profile is refused",
           lambda d: d["voice"].__setitem__("approved_profile", None))
    refuse("a pending voice claiming a profile is refused",
           lambda d: (d["voice"].__setitem__("selection_status", "pending")))
    refuse("a working default marked approved is refused",
           lambda d: d["voice"].__setitem__(
               "working_default", {"provider": "m", "voice": "v", "approved": True}))
    refuse("a malformed colour is refused",
           lambda d: d["visual_style"].__setitem__("pad_color", "navy"))
    refuse("a lowercase visual type is refused",
           lambda d: d["renderers"]["capabilities"].__setitem__("map", "pexels"))

    # An unregistered renderer id is schema-valid (it is a string) and is caught
    # a layer up, at load time, before anything can be planned against it.
    doc = json.loads(json.dumps(good))
    doc["renderers"]["capabilities"]["MAP"] = "hand_drawn"
    cc.validate_document(doc, source="t")
    check("an unregistered renderer passes the schema but fails the load", True)
    (d / "channel.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")
    with World(root):
        try:
            cc.load_channel("beacon")
            check("an unregistered renderer is refused at load", False, "it loaded")
        except (cc.ChannelError, renderers.RendererError):
            check("an unregistered renderer is refused at load", True)
    (d / "channel.json").write_text(json.dumps(good, indent=2), encoding="utf-8")
    (d / cc.DNA_NAME).write_text(rd.render_dna(good), encoding="utf-8")

    # A malformed schema must fail loudly rather than accept everything.
    bad_schema = root / "bad.schema.json"
    bad_schema.write_text(json.dumps({"type": "not-a-type"}), encoding="utf-8")
    with mock.patch.object(cc, "SCHEMA_PATH", bad_schema):
        try:
            cc.validate_document(good, source="t")
            check("a malformed schema is refused, not silently trusted", False,
                  "it validated against a broken schema")
        except cc.ChannelError as e:
            check("a malformed schema is refused, not silently trusted",
                  "itself invalid" in str(e), str(e)[:120])
    missing = root / "gone.schema.json"
    with mock.patch.object(cc, "SCHEMA_PATH", missing):
        try:
            cc.validate_document(good, source="t")
            check("a missing schema is refused", False, "it validated")
        except cc.ChannelError:
            check("a missing schema is refused", True)


# ── 6-7. containment ─────────────────────────────────────────────────────────

def s6_containment():
    root = temp_root()
    channels = root / "channels"
    make_pack(channels, "beacon")
    _character_tree(root / "character")

    with World(root):
        for bad, why in (
                ("../character/character_spec.json", "upward traversal"),
                ("../../etc/passwd", "traversal beyond the root"),
                (str(root / "character" / "character_spec.json"), "an absolute path")):
            try:
                cc._resolve_asset(channels / "beacon", bad, "pack_relative",
                                  label="character specification", channel_id="beacon")
                check(f"{why} is refused", False, "it resolved")
            except cc.ChannelError:
                check(f"{why} is refused", True)

        # THE case containment-under-the-root would miss: another pack's assets
        # are beneath PIPELINE_DIR, so only an explicit allowlist rejects them.
        other = make_pack(channels, "rival")
        rel = "channels/rival/character/character_spec.json"
        check("the cross-channel target really is under the pipeline root",
              (root / rel).resolve().is_relative_to(root.resolve())
              and (root / rel).is_file())
        try:
            cc._resolve_asset(channels / "beacon", rel, "legacy_pipeline_root",
                              label="character specification", channel_id="beacon")
            check("a cross-channel reference is refused despite being under the root",
                  False, "it resolved — containment under PIPELINE_DIR is not a boundary")
        except cc.ChannelError as e:
            check("a cross-channel reference is refused despite being under the root",
                  True)
            check("the refusal explains why nesting is not enough",
                  "not sufficient" in str(e), str(e)[:200])

        # The legacy allowlist admits its one approved subtree and nothing else.
        p = cc._resolve_asset(channels / "beacon", "character/character_spec.json",
                              "legacy_pipeline_root", label="spec", channel_id="beacon")
        check("the approved legacy subtree still resolves", p.is_file())
        for outside in ("channels/beacon/channel.json", "ep01/manifest.json"):
            (root / outside).parent.mkdir(parents=True, exist_ok=True)
            if not (root / outside).exists():
                (root / outside).write_text("{}", encoding="utf-8")
            try:
                cc._resolve_asset(channels / "beacon", outside, "legacy_pipeline_root",
                                  label="spec", channel_id="beacon")
                check(f"legacy kind refuses {outside}", False, "it resolved")
            except cc.ChannelError:
                check(f"legacy kind refuses {outside}", True)

        try:
            cc._resolve_asset(channels / "beacon", "x.json", "somewhere_else",
                              label="spec", channel_id="beacon")
            check("an unknown path_kind is refused", False, "it resolved")
        except cc.ChannelError:
            check("an unknown path_kind is refused", True)


def s7_symlink_escape():
    root = temp_root()
    channels = root / "channels"
    d = make_pack(channels, "beacon")
    outside = root / "outside"
    outside.mkdir()
    (outside / "character_spec.json").write_text("{}", encoding="utf-8")
    link = d / "linked.json"
    try:
        os.symlink(outside / "character_spec.json", link)
    except (OSError, NotImplementedError, AttributeError):
        print("  SKIP  symlink escape — this account cannot create symlinks")
        return
    with World(root):
        try:
            cc._resolve_asset(d, "linked.json", "pack_relative",
                              label="spec", channel_id="beacon")
            check("a symlink escaping the pack is refused", False, "it resolved")
        except cc.ChannelError:
            check("a symlink escaping the pack is refused", True)


# ── 4b. the root adapters belong to one pack only ────────────────────────────

def s_adapters_scoped():
    before = {n: sha(ROOT / n) for n in ("channel_config.json", "brand.json")}
    opened = []
    real_open, real_read = builtins.open, Path.read_text

    def spy_open(file, *a, **kw):
        opened.append(str(file))
        return real_open(file, *a, **kw)

    def spy_read(self, *a, **kw):
        opened.append(str(self))
        return real_read(self, *a, **kw)

    root = temp_root()
    shutil.copytree(FIXTURE_PACK, root / "channels" / "harbourline")
    with World(root), mock.patch.object(builtins, "open", spy_open), \
            mock.patch.object(Path, "read_text", spy_read):
        cc.load_channel("harbourline")

    touched = [p for p in opened
               if Path(p).name in ("channel_config.json", "brand.json")]
    check("loading another channel never opens the root adapters", not touched,
          f"opened {touched[:3]}")
    after = {n: sha(ROOT / n) for n in ("channel_config.json", "brand.json")}
    check("loading another channel does not modify the root adapters",
          before == after)

    live = json.loads((ROOT / "channels" / REAL_CHANNEL / "channel.json")
                      .read_text(encoding="utf-8"))
    check("only the owning pack emits adapters",
          set(rd.render_adapters(live)) == {"channel_config.json", "brand.json"})
    stripped = dict(live)
    stripped.pop("legacy_adapter", None)
    check("a pack that does not claim ownership emits none",
          rd.render_adapters(stripped) == {})

    for name in ("channel_config.json", "brand.json"):
        text = (ROOT / name).read_text(encoding="utf-8")
        d = json.loads(text)
        check(f"{name} carries its provenance",
              {"_do_not_edit", "_generated_from", "_source_sha256"} <= set(d))
        check(f"{name} records the pack it came from",
              d["_source_sha256"] == cc.load_channel(REAL_CHANNEL).channel_json_sha256)
    check("brand.json keeps the one key its consumer reads",
          "pad_color" in json.loads((ROOT / "brand.json").read_text(encoding="utf-8")))
    cfg = json.loads((ROOT / "channel_config.json").read_text(encoding="utf-8"))
    check("channel_config.json keeps the keys its consumers read",
          {"voice", "stitch"} <= set(cfg) and "ken_burns_zoom" in cfg["stitch"])


# ── drift ────────────────────────────────────────────────────────────────────

def s_drift():
    root = temp_root()
    d = make_pack(root / "channels", "beacon")
    with World(root):
        cc.load_channel("beacon")
        check("a freshly rendered pack loads", True)

        (d / cc.DNA_NAME).write_text("hand-written notes\n", encoding="utf-8")
        try:
            cc.load_channel("beacon")
            check("a hand-edited CHANNEL_DNA.md blocks the load", False, "it loaded")
        except cc.ChannelError as e:
            check("a hand-edited CHANNEL_DNA.md blocks the load", True)
            check("the message says it is generated", "generated" in str(e),
                  str(e)[:120])

        (d / cc.DNA_NAME).unlink()
        try:
            cc.load_channel("beacon")
            check("a missing CHANNEL_DNA.md blocks the load", False, "it loaded")
        except cc.ChannelError:
            check("a missing CHANNEL_DNA.md blocks the load", True)

        doc = json.loads((d / "channel.json").read_text(encoding="utf-8"))
        (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")
        check("re-rendering restores it", bool(cc.load_channel("beacon")))
        check("the render is deterministic",
              rd.render_dna(doc) == rd.render_dna(json.loads(json.dumps(doc))))

    # The live pack's own generated files are currently in sync.
    drifted = [p for p, t in rd.generated_files(REAL_CHANNEL).items()
               if not p.is_file() or p.read_text(encoding="utf-8") != t]
    check("every generated file for the live channel is in sync", not drifted,
          f"{[str(p.name) for p in drifted]}")


# ── 8-11. plan and approval binding ──────────────────────────────────────────

def _ready_project(voice_approved=True, channel="beacon"):
    root = temp_root()
    make_pack(root / "channels", channel, voice_approved=voice_approved)
    spec = root / "channels" / channel / "character" / "character_spec.json"
    proj = make_project(root, "ep_x", channel_id=channel)
    if voice_approved:
        # A narration binding that genuinely matches FIXTURE_APPROVED_PROFILE,
        # so "everything is fine, approval holds" tests are exercising a real
        # narration_binding_problems() pass, not one exempted from the check.
        m = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        m.update(channel_fixture.write_narration_fixture(
            proj, effective_profile=FIXTURE_APPROVED_PROFILE))
        (proj / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    return root, proj, spec


def s8_pack_change_invalidates():
    root, proj, _ = _ready_project()
    p = plan(root, proj)
    check("the plan records its channel", p["channel"]["channel_id"] == "beacon")
    approve(root, proj)
    check("an unedited approval holds", not verdict(root, proj).blockers,
          str(verdict(root, proj).blockers))

    d = root / "channels" / "beacon"
    doc = json.loads((d / "channel.json").read_text(encoding="utf-8"))
    doc["visual_style"]["pad_color"] = "#FFFFFF"
    (d / "channel.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")

    check("changing the channel pack invalidates the plan",
          blocked_on(verdict(root, proj), "visual plan channel pack is unchanged"),
          str(verdict(root, proj).blockers))
    check("and invalidates the approval",
          blocked_on(verdict(root, proj), "approval channel pack is unchanged"))

    # Reformatting alone must not, or every whitespace change would invalidate work.
    doc["visual_style"]["pad_color"] = "#101010"
    (d / "channel.json").write_text(
        json.dumps(doc, indent=6, sort_keys=True) + "\n\n", encoding="utf-8")
    (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")
    check("reformatting the pack does not invalidate anything",
          not verdict(root, proj).blockers, str(verdict(root, proj).blockers))


def s9_character_change_invalidates():
    root, proj, spec = _ready_project()
    plan(root, proj)
    approve(root, proj)
    check("baseline holds", not verdict(root, proj).blockers)

    doc = json.loads(spec.read_text(encoding="utf-8"))
    doc["style"]["rules"].append("now with a hat")
    spec.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    check("changing the character specification invalidates a host-enabled plan",
          blocked_on(verdict(root, proj),
                     "visual plan character specification is unchanged"),
          str(verdict(root, proj).blockers))


def s10_mismatch_blocks():
    root, proj, _ = _ready_project()
    make_pack(root / "channels", "rival")
    plan(root, proj)

    p = json.loads((proj / plan_visuals.PLAN_JSON).read_text(encoding="utf-8"))
    p["channel"]["channel_id"] = "rival"
    (proj / plan_visuals.PLAN_JSON).write_text(json.dumps(p, indent=2), encoding="utf-8")
    check("a plan naming another channel blocks",
          blocked_on(verdict(root, proj), "visual plan names this episode's channel"),
          str(verdict(root, proj).blockers))
    try:
        with World(root):
            ac.write_approval(proj, "R", ac.confirmation_phrase(proj.name, p["plan_id"]))
        check("approval refuses a plan built for another channel", False, "it approved")
    except ac.ApprovalRefused:
        check("approval refuses a plan built for another channel", True)


def s11_cross_channel_dispatch():
    root, proj, _ = _ready_project()
    make_pack(root / "channels", "rival")
    plan(root, proj)
    approve(root, proj)
    check("baseline holds", not verdict(root, proj).blockers)

    executable = json.loads((proj / plan_visuals.PLAN_JSON).read_text(encoding="utf-8"))
    m = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
    m["channel_id"] = "rival"
    (proj / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")

    calls = {"n": 0}
    with World(root), \
            mock.patch.object(route_images, "run_pexels",
                              lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)), \
            mock.patch.object(route_images, "run_ai_batch",
                              lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)):
        try:
            route_images.validate_plan(proj, executable)
            check("channel A's approved plan cannot dispatch under channel B",
                  False, "validate_plan accepted it")
        except (route_images.RouteError, cc.ChannelError):
            check("channel A's approved plan cannot dispatch under channel B", True)
    check("no generator ran", calls["n"] == 0)
    check("the generation gate also refuses the switched episode",
          bool(verdict(root, proj).blockers))


# ── 8b. voice enforcement ────────────────────────────────────────────────────

def s_voice_enforced():
    root, proj, _ = _ready_project(voice_approved=False)

    with World(root):
        c = cc.load_channel("beacon")
    check("a pending selection yields no profile hash", c.voice_profile_sha256 is None)
    check("the unapproved working default is not hashed into it",
          c.voice_selection_status == "pending" and not c.voice_approved)

    with World(root):
        rep = gate.require_identity_ready(proj, "planning", raise_on_block=False)
    check("planning stays reachable while the voice is pending", not rep.blockers,
          str(rep.blockers))
    p = plan(root, proj)
    check("a plan can still be produced", p["channel"]["voice_profile_sha256"] is None)
    check("the plan document says paid generation is blocked",
          "none approved" in plan_visuals.render_md(p))

    try:
        approve(root, proj)
        check("approval is refused while the voice is pending", False, "it approved")
    except ac.ApprovalRefused as e:
        check("approval is refused while the voice is pending", True)
        check("the refusal names the missing profile",
              "approved voice profile" in str(e), str(e)[:120])
    check("nothing was written",
          not (proj / gate.APPROVAL_NAME).exists())

    # Defense in depth: even with an approval record in place, the gate refuses.
    forged = {"schema_version": 2, "project": proj.name, "plan_id": p["plan_id"],
              "channel": p["channel"], "manifest_sha256": sha(proj / "manifest.json"),
              "visual_plan_sha256": sha(proj / plan_visuals.PLAN_JSON),
              "visual_plan_md_sha256": sha(proj / plan_visuals.PLAN_MD),
              "prompts_sha256": sha(proj / route_images.PROMPTS_FILE),
              "failure_revision": 0, "approved_at": "2026-01-01T00:00:00+00:00",
              "approved_by": "forged", "confirmation": "x",
              "paid_generation": {"shots": 0}}
    (proj / gate.APPROVAL_NAME).write_text(json.dumps(forged, indent=2), encoding="utf-8")
    check("the generation gate blocks independently of the approval record",
          blocked_on(verdict(root, proj), "channel has an approved voice profile"),
          str(verdict(root, proj).blockers))
    (proj / gate.APPROVAL_NAME).unlink()


def s_voice_output_allowlist():
    root, proj, _ = _ready_project(voice_approved=False)
    with World(root):
        c = cc.load_channel("beacon")
        preview = c.voice_preview_dir
        preview.mkdir(parents=True, exist_ok=True)

        def refused(label, target, project_dir=None):
            try:
                cc.classify_voice_output(c, target, project_dir=project_dir)
                check(label, False, f"allowed {target}")
            except cc.VoiceNotApproved:
                check(label, True)

        refused("project narration is refused while pending",
                proj / "source_audio" / "narration.mp3", project_dir=proj)
        refused("an arbitrary external path is refused",
                root / "elsewhere" / "n.mp3", project_dir=proj)
        refused("a path outside the tree entirely is refused",
                Path(tempfile.gettempdir()) / "escape.mp3", project_dir=proj)
        refused("upward traversal out of the preview dir is refused",
                preview / ".." / "source_audio" / "n.mp3", project_dir=proj)
        refused("traversal that lands back in a project is refused",
                preview / ".." / "ep_x" / "source_audio" / "n.mp3", project_dir=proj)
        refused("no destination at all (no project) still refuses non-preview paths",
                root / "elsewhere" / "n.mp3", project_dir=None)

        mode, ok = cc.classify_voice_output(c, preview / "cand_a.mp3")
        check("a preview-directory target is evaluation",
              mode == "evaluation" and ok.name == "cand_a.mp3")
        mode, ok = cc.classify_voice_output(c, preview / "sub" / "cand_b.mp3")
        check("a nested preview target is evaluation",
              mode == "evaluation" and ok.name == "cand_b.mp3")

        link = root / "sneaky"
        try:
            os.symlink(proj / "source_audio", link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            print("  SKIP  symlink escape from the preview dir — cannot create symlinks")
        else:
            (proj / "source_audio").mkdir(parents=True, exist_ok=True)
            refused("a symlinked directory escaping the preview dir is refused",
                    link / "narration.mp3", project_dir=proj)

    root2, proj2, _ = _ready_project(voice_approved=True)
    with World(root2):
        c2 = cc.load_channel("beacon")
        mode, out = cc.classify_voice_output(
            c2, proj2 / "source_audio" / "narration.mp3", project_dir=proj2)
        check("an approved voice may write project narration as production",
              mode == "production" and out.name == "narration.mp3")

        # Boundary 1: --preview (a truncated run) targeting project audio with
        # an approved voice is STILL production — destination decides, not the
        # flag. (The CLI-level conflict refusal itself is exercised against
        # generate_source_audio.py in tests/test_narration_binding.py; this
        # confirms the primitive classifies the destination correctly.)
        mode, out = cc.classify_voice_output(
            c2, proj2 / "source_audio" / "preview_x.mp3", project_dir=proj2)
        check("--preview targeting project audio still classifies as production",
              mode == "production")

        # Boundary 2: a full run (no truncation implied at this layer — this
        # primitive has no notion of --preview at all) targeting the preview
        # root remains evaluation even though the voice is approved.
        mode, out = cc.classify_voice_output(
            c2, c2.voice_preview_dir / "full_script_probe.mp3", project_dir=proj2)
        check("a run targeting voice_previews/ remains evaluation even when approved",
              mode == "evaluation")


# ── 12-15. the real repository is unchanged ──────────────────────────────────

def s12_migration_preserves():
    root = temp_root()
    make_pack(root / "channels", "beacon")
    proj = make_project(root, "ep_x", channel_id=None)
    before = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))

    with World(root):
        changed, _ = cc.migrate_project(proj, "beacon")
        check("migration reports a change", changed)
        after = json.loads((proj / "manifest.json").read_text(encoding="utf-8"))
        check("scene count is preserved", len(after["scenes"]) == len(before["scenes"]))
        check("scene order is preserved",
              [s["id"] for s in after["scenes"]] == [s["id"] for s in before["scenes"]])
        for key in ("source_ids", "shot_instance_id", "visual_asset_id"):
            check(f"{key} preserved",
                  [s.get(key) for s in after["scenes"]]
                  == [s.get(key) for s in before["scenes"]])
        check("identity state preserved",
              after["identity_state"] == before["identity_state"])
        check("only the channel keys were added",
              set(after) - set(before) == {"channel_id", "channel_dna_version"})

        changed2, msg = cc.migrate_project(proj, "beacon")
        check("replaying migration changes nothing", changed2 is False, msg)

        make_pack(root / "channels", "rival")
        try:
            cc.migrate_project(proj, "rival")
            check("switching an assigned channel is refused", False, "it switched")
        except cc.ChannelError as e:
            check("switching an assigned channel is refused", True)
            check("the refusal explains why", "assigned once" in str(e), str(e)[:120])

        # A blocked episode stays blocked: migration is bookkeeping, not a fix.
        blocked = make_project(root, "ep_blocked", channel_id=None)
        m = json.loads((blocked / "manifest.json").read_text(encoding="utf-8"))
        m["identity_state"] = "blocked"
        m["identity_reasons"] = ["SCENE-002 not matched to the script"]
        (blocked / "manifest.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
        cc.migrate_project(blocked, "beacon")
        m2 = json.loads((blocked / "manifest.json").read_text(encoding="utf-8"))
        check("migration does not clear a blocked identity",
              m2["identity_state"] == "blocked" and m2["identity_reasons"] == m["identity_reasons"])


def s_dna_version_enforced():
    root = temp_root()
    make_pack(root / "channels", "beacon", dna_version=1)
    proj = make_project(root, "ep_x", channel_id="beacon", dna_version=1)
    with World(root):
        check("matching DNA versions load",
              cc.load_channel_for_project(proj).channel_dna_version == 1)
    make_pack(root / "channels", "beacon", dna_version=2)
    with World(root):
        try:
            cc.load_channel_for_project(proj)
            check("a DNA version mismatch blocks", False, "it loaded")
        except cc.ChannelError as e:
            check("a DNA version mismatch blocks", True)
            check("the message names both versions",
                  "v1" in str(e) and "v2" in str(e), str(e)[:150])
        rep = gate.require_identity_ready(proj, "t", raise_on_block=False)
        check("the identity gate reports it",
              blocked_on(rep, "episode names a loadable channel"))


def s_creation_four_cases():
    root = temp_root()
    make_pack(root / "channels", "beacon")
    with World(root):
        cid, ver = cc.resolve_creation_channel("beacon")
        check("packs present + id given -> accepted", cid == "beacon" and ver == 1)
        try:
            cc.resolve_creation_channel(None)
            check("packs present + no id -> refused", False, "it returned")
        except cc.ChannelError as e:
            check("packs present + no id -> refused", True)
            check("the refusal says there is no default",
                  "no default" in str(e), str(e)[:120])

    bare = Path(tempfile.mkdtemp())
    _temps.append(bare)
    with mock.patch.multiple(cc, PIPELINE_DIR=bare, CHANNELS_DIR=bare / "channels"):
        check("no packs + no id -> legacy invocation unchanged",
              cc.resolve_creation_channel(None) == (None, None))
        try:
            cc.resolve_creation_channel("beacon")
            check("no packs + id given -> refused as unsupported", False,
                  "the explicit assignment was silently discarded")
        except cc.ChannelError as e:
            check("no packs + id given -> refused as unsupported", True)
            check("the refusal says it will not drop the request",
                  "rather than dropping it silently" in str(e), str(e)[:150])

    m = cc.assign_channel({"episode": "x"}, "beacon", 1)
    check("assign_channel stamps a new manifest", m["channel_id"] == "beacon")
    check("assign_channel is a no-op with no channel",
          "channel_id" not in cc.assign_channel({"episode": "x"}, None, None))
    try:
        cc.assign_channel({"channel_id": "rival"}, "beacon", 1)
        check("assign_channel refuses to overwrite an assignment", False, "it wrote")
    except cc.ChannelError:
        check("assign_channel refuses to overwrite an assignment", True)


def s13_14_live_projects():
    for name, ready in (("pilot_neet_scandal", False), ("test_2min", True)):
        proj = ROOT / name
        if not proj.is_dir():
            print(f"  SKIP  {name} — not present in this checkout")
            continue
        rep = gate.require_identity_ready(proj, "audit", raise_on_block=False)
        check(f"{name} identity gate: {'ready' if ready else 'blocked'}",
              (not rep.blockers) == ready, str(rep.blockers)[:200])
        gen = gate.require_generation_ready(proj, "audit", raise_on_block=False)
        check(f"{name} generation gate is blocked", bool(gen.blockers))
        check(f"{name} carries a channel assignment",
              cc.read_manifest_channel(proj)[0] == REAL_CHANNEL)

    pilot = ROOT / "pilot_neet_scandal"
    if pilot.is_dir():
        rep = gate.require_generation_ready(pilot, "audit", raise_on_block=False)
        check("the pilot is still blocked on SCENE-066",
              any("SCENE-066" in b for b in rep.blockers), str(rep.blockers)[:200])
        # The stale schema-1 plan must not mask the identity block, nor be masked by it.
        check("the pilot's stale plan is reported separately from the identity block",
              any("schema_version=1" in b for b in rep.blockers), str(rep.blockers)[:300])
        check("the pilot was not re-planned into schema 2",
              json.loads((pilot / "visual_plan.json").read_text(
                  encoding="utf-8")).get("schema_version") == 1)
        check("the pilot has no approval", not (pilot / gate.APPROVAL_NAME).exists())


def s15_character_untouched():
    c = cc.load_channel(REAL_CHANNEL)
    spec = json.loads(c.character_spec_path.read_text(encoding="utf-8"))
    base = c.character_spec_path.parent.parent
    for key, m in spec["masters"].items():
        check(f"master {key} still matches its approved hash",
              sha(base / m["path"]) == m["sha256"])
    a = pose_registry.audit(context=c)
    check("every approved pose still resolves and hashes correctly",
          not a["problems"], str(a["problems"]))
    check("the pose count is unchanged", len(a["ok"]) == 11, str(len(a["ok"])))


# ── 17 + statics ─────────────────────────────────────────────────────────────

def s17_no_channel_identity_in_generic_code():
    forbidden = ("interested indian", "india", "neet", "mascot", "kurta",
                 "chubby", "1a2b4c", "prabhat")
    for name in ("channel_context.py", "render_channel_dna.py"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        found = [w for w in forbidden if w in text]
        check(f"{name} carries no channel identity", not found, f"found {found}")

    # renderers.py is an implementation registry: naming a concrete renderer and
    # its module is the point of it. What it must not carry is the channel's own
    # identity — its name, its character, or anything episode-specific.
    text = (ROOT / "renderers.py").read_text(encoding="utf-8").lower()
    found = [w for w in ("interested indian", "neet", "mascot", "kurta", "chubby",
                         "1a2b4c") if w in text]
    check("renderers.py carries no channel identity", not found, f"found {found}")
    geo = [ln for ln in text.splitlines() if "india" in ln]
    check("any geography named in renderers.py is a renderer id or module name",
          all(("india_geojson" in ln or "generate_india_map" in ln
               or "india-specific" in ln) for ln in geo), str(geo))

    check("the loader does not name the channel that owns the adapters",
          "legacy_adapter" in (ROOT / "channel_context.py").read_text(encoding="utf-8"),
          "ownership must be declared by the pack, not hardcoded here")


def s_pose_context_static():
    """Every episode-facing pose resolution passes a ChannelContext.

    A static check because the runtime fallback is real and legitimate for
    character-setup work. Without pinning the caller list, that narrow exception
    would quietly become the general case again.
    """
    watched = ("resolve", "metadata", "list_poses", "audit")
    legacy = set(pose_registry.LEGACY_CONTEXTLESS_CALLERS)
    check("the legacy exception list is pinned to character-setup modules",
          legacy == {"generate_character.py", "generate_poses.py", "validate_poses.py",
                     "check_character_consistency.py", "export_character_package.py"},
          str(sorted(legacy)))
    for m in legacy:
        check(f"legacy caller {m} still exists", (ROOT / m).is_file())

    offenders = []
    for f in sorted(ROOT.glob("*.py")):
        if f.name in legacy or f.name == "pose_registry.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name == "main":      # CLI surface, interactive, no episode
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in watched
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "pose_registry"):
                    continue
                if not any(kw.arg == "context" for kw in node.keywords):
                    offenders.append(f"{f.name}:{node.lineno} in {fn.name}()")
    check("every episode-facing pose call passes a context", not offenders,
          "; ".join(offenders))


def s_pose_ids_do_not_collide():
    """The same pose id in two packs resolves to two different assets."""
    root = temp_root()
    a = make_pack(root / "channels", "beacon")
    b = make_pack(root / "channels", "rival")
    with World(root):
        ca, cb = cc.load_channel("beacon"), cc.load_channel("rival")
        pa = pose_registry.resolve("neutral_presenter", context=ca)
        pb = pose_registry.resolve("neutral_presenter", context=cb)
    check("both packs register the same local id",
          "neutral_presenter" in pose_registry.list_poses(context=ca)
          and "neutral_presenter" in pose_registry.list_poses(context=cb))
    check("they resolve to different files", pa != pb, f"{pa} == {pb}")
    check("each stays inside its own pack",
          pa.is_relative_to(a.resolve()) and pb.is_relative_to(b.resolve()))

    # A pack cannot reach another's asset even by registering its path.
    spec_b = json.loads((b / "character" / "character_spec.json").read_text(encoding="utf-8"))
    spec_a_path = a / "character" / "character_spec.json"
    spec_a = json.loads(spec_a_path.read_text(encoding="utf-8"))
    spec_a["pose_library"]["registry"]["neutral_presenter"]["path"] = (
        "../rival/character/poses/host_neutral_presenter.png")
    spec_a["pose_library"]["registry"]["neutral_presenter"]["sha256"] = (
        spec_b["pose_library"]["registry"]["neutral_presenter"]["sha256"])
    spec_a_path.write_text(json.dumps(spec_a, indent=2), encoding="utf-8")
    with World(root):
        ca = cc.load_channel("beacon")
        try:
            pose_registry.resolve("neutral_presenter", context=ca)
            check("a pack cannot register a path into another pack", False, "it resolved")
        except pose_registry.PoseError:
            check("a pack cannot register a path into another pack", True)


def s_renderer_contract():
    check("every registered renderer declares a known cost category",
          all(e["cost_category"] in renderers.COST_CATEGORIES
              for e in renderers.RENDERERS.values()))
    absent = [rid for rid, e in renderers.RENDERERS.items()
              if not e["implemented"] and (ROOT / e["module"]).exists()]
    check("renderers marked unwritten really are absent", not absent, str(absent))
    present = [rid for rid, e in renderers.RENDERERS.items()
               if e["implemented"] and not (ROOT / e["module"]).exists()]
    check("renderers marked implemented really exist", not present, str(present))
    try:
        renderers.validate_capabilities({"MAP": "hand_drawn"})
        check("an unregistered renderer id is refused", False, "it validated")
    except renderers.RendererError:
        check("an unregistered renderer id is refused", True)
    try:
        renderers.validate_capabilities({"MAP": {"module": "anything.py"}})
        check("a renderer declared as an object is refused", False, "it validated")
    except renderers.RendererError:
        check("a renderer declared as an object is refused", True)
    src = (ROOT / "renderers.py").read_text(encoding="utf-8")
    check("no dynamic import from a configuration string",
          "importlib" not in src and "__import__" not in src)
    check("the live pack's unwritten capabilities are reported",
          set(renderers.unimplemented(
              dict(cc.load_channel(REAL_CHANNEL).renderer_capabilities)))
          == {"deterministic_timeline", "deterministic_document"})


# ── run ──────────────────────────────────────────────────────────────────────

def main() -> int:
    before = census(ROOT)
    try:
        run("1. the live channel pack loads", s1_real_pack)
        run("2. the synthetic pack loads and shares nothing", s2_synthetic_pack)
        run("3. a manifest with no channel is refused", s3_missing_channel_id)
        run("4. an unknown channel is refused", s4_unknown_channel)
        run("5. the schema is authoritative", s5_schema)
        run("6. containment: the pipeline root is not a boundary", s6_containment)
        run("7. symlinks cannot escape a pack", s7_symlink_escape)
        run("8. the root adapters belong to one pack only", s_adapters_scoped)
        run("9. generated documents cannot drift", s_drift)
        run("10. a channel-pack change invalidates plan and approval",
            s8_pack_change_invalidates)
        run("11. a character-spec change invalidates a host-enabled plan",
            s9_character_change_invalidates)
        run("12. plan and manifest must name the same channel", s10_mismatch_blocks)
        run("13. one channel's approval cannot dispatch another's",
            s11_cross_channel_dispatch)
        run("14. a pending voice is enforced, not just described", s_voice_enforced)
        run("15. synthesis output is allowlisted while the voice is pending",
            s_voice_output_allowlist)
        run("16. migration preserves every persistent id", s12_migration_preserves)
        run("17. the DNA version an episode records is enforced", s_dna_version_enforced)
        run("18. channel assignment at creation: four cases", s_creation_four_cases)
        run("19. the live projects are where they should be", s13_14_live_projects)
        run("20. the approved character package is untouched", s15_character_untouched)
        run("21. generic code carries no channel identity",
            s17_no_channel_identity_in_generic_code)
        run("22. pose resolution is channel-scoped", s_pose_context_static)
        run("23. pose ids do not collide across packs", s_pose_ids_do_not_collide)
        run("24. the renderer contract holds", s_renderer_contract)
    finally:
        after = census(ROOT)
        print("\n25. the repository's character assets are unchanged")
        check("no character file was added, removed or rewritten", before == after,
              f"changed: {sorted(set(before) ^ set(after)) or [k for k in before if before[k] != after.get(k)]}")
        for td in _temps:
            shutil.rmtree(td, ignore_errors=True)

    print(f"\n{'=' * 62}")
    if failures:
        print(f"FAILED: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all channel-context checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
