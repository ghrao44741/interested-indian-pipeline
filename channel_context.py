"""channel_context.py — load the Channel Pack an episode belongs to.

Generic pipeline code must not carry a channel's name, palette, character,
terminology or editorial rules in its own source. It asks for a ChannelContext
and reads what the pack declares. This module is the only supported way to get
one, and it deliberately contains no channel-specific content of its own — a test
asserts that, so the abstraction cannot rot back into a hardcoded default.

    context = load_channel_for_project(project_dir)   # the runtime path
    context = load_channel(channel_id)                # setup and migration only

A project's channel is a property of the episode, read from its manifest. There
is no `--channel` override on a runtime command: an episode whose channel could
be changed from the command line has no stable identity, and every hash bound to
it would be describing a different channel than the one that produced the work.

Three things this module refuses, each because the obvious version of the check
does not actually hold:

  - **Containment under the pipeline root is not a boundary.** Every pack and
    every episode lives beneath it, so `channels/other/...` would pass a naive
    containment test. `legacy_pipeline_root` therefore resolves against an
    explicit allowlist of approved subtrees, not against the root.
  - **A pack that agrees with itself proves nothing.** CHANNEL_DNA.md and the
    generated root adapters are re-rendered from the pack on every load and must
    match byte for byte, or the document a human maintains has drifted from the
    configuration the code reads.
  - **A declared version is not a loaded version.** An episode records the DNA
    version it was built against; loading a pack at a different version blocks
    rather than quietly continuing.

    python channel_context.py --list
    python channel_context.py --show <channel_id>
    python channel_context.py --migrate-project <project> --channel <channel_id>
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import renderers

PIPELINE_DIR = Path(__file__).parent
CHANNELS_DIR = PIPELINE_DIR / "channels"
SCHEMA_PATH = CHANNELS_DIR / "schema" / "channel.schema.json"
PACK_NAME = "channel.json"
DNA_NAME = "CHANNEL_DNA.md"
MANIFEST_NAME = "manifest.json"

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
CHANNEL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

# Subtrees a pack may reach with path_kind "legacy_pipeline_root". This is an
# allowlist, not a containment root: PIPELINE_DIR contains channels/, every
# episode project, archive/, pose_candidates/ and pose_sources/, so "is it under
# the pipeline root" would admit all of them. Only what is named here is
# reachable, and only until Task 2B moves the assets into their pack.
LEGACY_ASSET_ROOTS = ("character",)


class ChannelError(RuntimeError):
    """A channel could not be loaded, or a pack declares something unusable."""


class VoiceNotApproved(RuntimeError):
    """Synthesis was attempted with no approved voice profile.

    Carried separately from ChannelError because callers act on it differently:
    an unapproved voice is a legitimate mid-selection state, not a broken pack.
    """


# ── hashing and freezing ─────────────────────────────────────────────────────

def canonical_sha256(obj) -> str:
    """Hash of the parsed object, not the file bytes.

    Reformatting or reordering the pack must not invalidate every plan, but any
    semantic edit must. Hashing the serialised bytes would get that backwards.
    """
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _freeze(obj):
    if isinstance(obj, dict):
        return MappingProxyType({k: _freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_freeze(v) for v in obj)
    return obj


def _thaw(obj):
    if isinstance(obj, (dict, MappingProxyType)):
        return {k: _thaw(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_thaw(v) for v in obj]
    return obj


def _write_atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ── the context ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChannelContext:
    channel_id: str
    channel_dna_version: int
    pack_dir: Path
    config: MappingProxyType
    channel_json_sha256: str
    host_enabled: bool
    character_spec_path: Path | None
    character_spec_sha256: str | None
    poses_root: Path | None
    voice_selection_status: str
    voice_profile_sha256: str | None
    voice_preview_dir: Path
    renderer_capabilities: MappingProxyType
    legacy_adapter: bool

    @property
    def voice_approved(self) -> bool:
        return self.voice_selection_status == "approved"

    def renderer_for(self, visual_type: str) -> str:
        rid = self.renderer_capabilities.get(visual_type)
        if rid is None:
            raise ChannelError(
                f"{self.channel_id} declares no renderer for {visual_type!r}; "
                f"declared: {sorted(self.renderer_capabilities)}")
        return rid

    def plan_binding(self) -> dict:
        """The block a visual plan and an approval both record.

        voice_profile_sha256 is None while selection is pending. It is recorded
        as absent rather than filled in from the working default, which would
        make an unapproved voice look approved to every later check.
        """
        return {
            "channel_id": self.channel_id,
            "channel_dna_version": self.channel_dna_version,
            "channel_json_sha256": self.channel_json_sha256,
            "character_spec_sha256": self.character_spec_sha256,
            "voice_profile_sha256": self.voice_profile_sha256,
        }


# ── pack discovery and asset resolution ──────────────────────────────────────

def channel_support_available() -> bool:
    """Whether this checkout has Channel Packs at all.

    The split stage is shared with sibling channels whose repositories have no
    channels/ directory. They must keep working untouched; this repository must
    never produce an episode with no channel.
    """
    return CHANNELS_DIR.is_dir()


def list_channels() -> list[str]:
    if not CHANNELS_DIR.is_dir():
        return []
    return sorted(d.name for d in CHANNELS_DIR.iterdir()
                  if d.is_dir() and (d / PACK_NAME).is_file())


def pack_dir(channel_id: str) -> Path:
    """Validate the id before it is ever joined to a path.

    A channel id reaches this from a manifest and from the command line, so it
    is untrusted input. Checking the charset first means traversal never gets as
    far as a join.
    """
    if not isinstance(channel_id, str) or not CHANNEL_ID_RE.match(channel_id):
        raise ChannelError(
            f"invalid channel id {channel_id!r} — expected lowercase letters, "
            f"digits and underscores, 3-64 characters")
    return CHANNELS_DIR / channel_id


def _resolve_asset(context_pack_dir: Path, declared: str, path_kind: str,
                   *, label: str, channel_id: str) -> Path:
    """Resolve a declared asset path, or refuse.

    Rejects absolute and upward-traversing declarations before resolution, then
    resolves fully — so a symlink cannot smuggle a target past the check — and
    requires the result to sit inside the one root this path_kind permits.
    """
    if not isinstance(declared, str) or not declared:
        raise ChannelError(f"{channel_id}: {label} path must be a non-empty string")
    p = Path(declared)
    if p.is_absolute() or ".." in p.parts:
        raise ChannelError(
            f"{channel_id}: {label} path {declared!r} is absolute or traverses upward")

    if path_kind == "pack_relative":
        roots = [context_pack_dir.resolve()]
        base = context_pack_dir
    elif path_kind == "legacy_pipeline_root":
        roots = [(PIPELINE_DIR / r).resolve() for r in LEGACY_ASSET_ROOTS]
        base = PIPELINE_DIR
    else:
        raise ChannelError(f"{channel_id}: unknown path_kind {path_kind!r}")

    resolved = (base / p).resolve()
    if not any(resolved.is_relative_to(r) for r in roots):
        allowed = ", ".join(str(r) for r in roots)
        raise ChannelError(
            f"{channel_id}: {label} path {declared!r} resolves to {resolved}, "
            f"which is outside the only location {path_kind!r} permits ({allowed}). "
            f"Being underneath the pipeline root is not sufficient — every other "
            f"channel pack and every episode folder is underneath it too.")
    if not resolved.exists():
        raise ChannelError(f"{channel_id}: {label} not found at {resolved}")
    return resolved


# ── schema ───────────────────────────────────────────────────────────────────

def _validator():
    """Build the validator, checking the schema itself first.

    A malformed schema silently accepts everything, which is worse than having
    no schema: it reports success. check_schema() turns that into a loud failure
    before any pack is looked at.
    """
    try:
        from jsonschema import Draft202012Validator
    except ImportError as e:  # pragma: no cover - environment problem, not logic
        raise ChannelError(
            "jsonschema is required to load a Channel Pack "
            "(pip install -r requirements.txt)") from e
    if not SCHEMA_PATH.is_file():
        raise ChannelError(f"schema not found at {SCHEMA_PATH}")
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ChannelError(f"schema at {SCHEMA_PATH} is not valid JSON: {e}") from e
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as e:
        raise ChannelError(f"schema at {SCHEMA_PATH} is itself invalid: {e}") from e
    return Draft202012Validator(schema)


def validate_document(doc: dict, *, source: str) -> None:
    if not isinstance(doc, dict):
        raise ChannelError(f"{source}: pack must be a JSON object")
    version = doc.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ChannelError(
            f"{source}: schema_version {version!r} is not supported by this build "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})")
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        detail = "\n".join(
            f"  - {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in errors[:10])
        raise ChannelError(f"{source}: pack does not satisfy the schema:\n{detail}")


# ── loading ──────────────────────────────────────────────────────────────────

def load_channel(channel_id: str, *, check_drift: bool = True) -> ChannelContext:
    """Load and validate one pack. Setup and migration only.

    Runtime code uses load_channel_for_project(), which derives the id from the
    episode instead of accepting one.
    """
    d = pack_dir(channel_id)
    path = d / PACK_NAME
    if not path.is_file():
        known = list_channels()
        raise ChannelError(
            f"unknown channel {channel_id!r} — no {PACK_NAME} at {path}"
            + (f". Known: {known}" if known else ". No channel packs are installed."))
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ChannelError(f"{path} is not valid JSON: {e}") from e

    validate_document(doc, source=str(path))

    if doc["channel_id"] != channel_id:
        raise ChannelError(
            f"{path} declares channel_id {doc['channel_id']!r} but lives in "
            f"{channel_id!r} — a pack cannot claim a directory that is not its own")

    renderers.validate_capabilities(dict(doc["renderers"]["capabilities"]))

    host_enabled = bool(doc["host"]["enabled"])
    spec_path = spec_sha = poses_root = None
    if host_enabled:
        char = doc.get("character")
        if not char:
            raise ChannelError(
                f"{channel_id}: host is enabled but no character section is declared")
        spec_path = _resolve_asset(d, char["spec_path"], char["path_kind"],
                                   label="character specification",
                                   channel_id=channel_id)
        spec_sha = _sha_file(spec_path)
        poses_root = spec_path.parent / "poses"

    voice = doc["voice"]
    status = voice["selection_status"]
    profile = voice.get("approved_profile")
    # Hashed from the approved profile alone. Deriving it from working_default
    # would hand a pending selection a hash that every later check reads as an
    # approved one.
    voice_sha = canonical_sha256(profile) if (status == "approved" and profile) else None
    preview_dir = (PIPELINE_DIR / voice.get("preview_dir", "voice_previews")).resolve()

    legacy = bool(doc.get("legacy_adapter", False))
    if legacy:
        others = [c for c in list_channels() if c != channel_id]
        for other in others:
            try:
                o = json.loads((pack_dir(other) / PACK_NAME).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ChannelError):
                continue
            if o.get("legacy_adapter"):
                raise ChannelError(
                    f"both {channel_id!r} and {other!r} claim legacy_adapter — the "
                    f"generated root-level files can only have one owner")

    ctx = ChannelContext(
        channel_id=channel_id,
        channel_dna_version=int(doc["channel_dna_version"]),
        pack_dir=d,
        config=_freeze(doc),
        channel_json_sha256=canonical_sha256(doc),
        host_enabled=host_enabled,
        character_spec_path=spec_path,
        character_spec_sha256=spec_sha,
        poses_root=poses_root,
        voice_selection_status=status,
        voice_profile_sha256=voice_sha,
        voice_preview_dir=preview_dir,
        renderer_capabilities=_freeze(dict(doc["renderers"]["capabilities"])),
        legacy_adapter=legacy,
    )

    if check_drift:
        _check_generated_artifacts(ctx, doc)
    return ctx


def _check_generated_artifacts(ctx: ChannelContext, doc: dict) -> None:
    """Every generated document must still be what this pack renders to.

    CHANNEL_DNA.md is generated for every pack. The root-level compatibility
    files are generated only for the one pack that owns them, and a pack that
    does not own them must never read, compare against or write them.
    """
    import render_channel_dna

    dna_path = ctx.pack_dir / DNA_NAME
    if not dna_path.is_file():
        raise ChannelError(
            f"{ctx.channel_id}: {DNA_NAME} is missing — run render_channel_dna.py")
    fresh = render_channel_dna.render_dna(doc)
    if dna_path.read_text(encoding="utf-8") != fresh:
        raise ChannelError(
            f"{ctx.channel_id}: {DNA_NAME} does not match a fresh render of "
            f"{PACK_NAME}. It is generated, not maintained — edit {PACK_NAME} and "
            f"re-run render_channel_dna.py.")

    if not ctx.legacy_adapter:
        return
    for name, text in render_channel_dna.render_adapters(doc).items():
        target = PIPELINE_DIR / name
        if not target.is_file():
            raise ChannelError(
                f"{ctx.channel_id}: generated {name} is missing — "
                f"run render_channel_dna.py")
        if target.read_text(encoding="utf-8") != text:
            raise ChannelError(
                f"{ctx.channel_id}: {name} has been edited by hand. It is generated "
                f"from {PACK_NAME}; change the pack and re-run render_channel_dna.py.")


def read_manifest_channel(project_dir) -> tuple[str | None, int | None]:
    path = Path(project_dir) / MANIFEST_NAME
    if not path.is_file():
        return None, None
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ChannelError(f"{path} is not valid JSON: {e}") from e
    return m.get("channel_id"), m.get("channel_dna_version")


def load_channel_for_project(project_dir) -> ChannelContext:
    """The single supported runtime path. The episode names its own channel."""
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise ChannelError(f"no such project folder: {project_dir}")
    channel_id, dna_version = read_manifest_channel(project_dir)
    if not channel_id:
        raise ChannelError(
            f"{project_dir.name} has no channel_id in its manifest. Assign one with "
            f"channel_context.py --migrate-project {project_dir.name} --channel <id>. "
            f"It is never inferred: guessing an episode's channel would attach one "
            f"channel's identity, character and rules to another's work.")
    ctx = load_channel(channel_id)
    if dna_version is not None and int(dna_version) != ctx.channel_dna_version:
        raise ChannelError(
            f"{project_dir.name} was built against Channel DNA v{dna_version}, and "
            f"{channel_id} is now at v{ctx.channel_dna_version}. Re-plan the episode "
            f"deliberately rather than loading a different version underneath it.")
    return ctx


# ── channel assignment ───────────────────────────────────────────────────────

def resolve_creation_channel(requested: str | None) -> tuple[str | None, int | None]:
    """Decide the channel for a project being created. Four cases, no silent path.

        packs present, id given    -> validated and used
        packs present, no id       -> refused; the choice must be made explicitly
        no packs,      no id       -> legacy invocation, unchanged
        no packs,      id given    -> refused as unsupported

    The last case matters most: quietly discarding an explicit assignment would
    produce a manifest that lacks the channel the operator asked for, and nothing
    downstream would ever report it.
    """
    supported = channel_support_available()
    if requested:
        if not supported:
            raise ChannelError(
                f"--channel {requested!r} was given, but this checkout has no "
                f"channels/ directory and cannot honour a channel assignment. "
                f"Refusing rather than dropping it silently.")
        ctx = load_channel(requested)
        return ctx.channel_id, ctx.channel_dna_version
    if supported:
        known = list_channels()
        raise ChannelError(
            f"--channel is required in this checkout. Known channels: {known or 'none'}. "
            f"There is deliberately no default: an episode's channel is chosen once, "
            f"by a person, and never changes afterwards.")
    return None, None


def assign_channel(manifest: dict, channel_id: str | None,
                   dna_version: int | None) -> dict:
    """Stamp a manifest at creation. Existing assignment always wins."""
    if channel_id is None:
        return manifest
    existing = manifest.get("channel_id")
    if existing and existing != channel_id:
        raise ChannelError(
            f"this manifest already belongs to {existing!r}; refusing to reassign it "
            f"to {channel_id!r}")
    manifest["channel_id"] = channel_id
    manifest["channel_dna_version"] = dna_version
    return manifest


def migrate_project(project_dir, channel_id: str) -> tuple[bool, str]:
    """Add channel identity to an existing manifest. Idempotent.

    Returns (changed, message). Preserves everything else byte-for-byte in
    meaning: scene order, every persistent id, and the identity state — a
    blocked episode stays blocked, because migration is a bookkeeping change and
    has no business clearing a content-identity failure.
    """
    project_dir = Path(project_dir)
    path = project_dir / MANIFEST_NAME
    if not path.is_file():
        raise ChannelError(f"no manifest at {path}")
    ctx = load_channel(channel_id)
    manifest = json.loads(path.read_text(encoding="utf-8"))

    existing = manifest.get("channel_id")
    if existing and existing != channel_id:
        raise ChannelError(
            f"{project_dir.name} already belongs to {existing!r}. A channel is "
            f"assigned once; changing it would reinterpret every hash, pose and "
            f"rule already applied to this episode.")
    if existing == channel_id and manifest.get("channel_dna_version") == ctx.channel_dna_version:
        return False, f"{project_dir.name} already at {channel_id} v{ctx.channel_dna_version}"

    before = (len(manifest.get("scenes", [])),
              [s.get("shot_instance_id") for s in manifest.get("scenes", [])],
              [s.get("visual_asset_id") for s in manifest.get("scenes", [])],
              manifest.get("identity_state"))

    manifest["channel_id"] = channel_id
    manifest["channel_dna_version"] = ctx.channel_dna_version

    after = (len(manifest.get("scenes", [])),
             [s.get("shot_instance_id") for s in manifest.get("scenes", [])],
             [s.get("visual_asset_id") for s in manifest.get("scenes", [])],
             manifest.get("identity_state"))
    if before != after:  # pragma: no cover - guard against a future careless edit
        raise ChannelError("migration would alter scene identity; refusing")

    _write_atomic_text(path, json.dumps(manifest, indent=2, ensure_ascii=False))
    return True, (f"{project_dir.name} -> {channel_id} "
                  f"(DNA v{ctx.channel_dna_version}), {before[0]} scenes preserved")


# ── voice enforcement ────────────────────────────────────────────────────────

def channel_for_voice(project_dir=None, requested: str | None = None) -> ChannelContext:
    """Find the pack governing a synthesis run.

    Narration genuinely runs before the manifest exists, so unlike every other
    runtime path this one cannot always read the channel off the episode. It
    tries the manifest, then an explicit id, then the sole installed pack — and
    refuses when more than one exists rather than picking.
    """
    if requested:
        return load_channel(requested)
    if project_dir is not None:
        cid, _ = read_manifest_channel(project_dir)
        if cid:
            return load_channel_for_project(project_dir)
    known = list_channels()
    if len(known) == 1:
        return load_channel(known[0])
    raise ChannelError(
        f"cannot tell which channel this narration belongs to (installed: {known}). "
        f"Pass --channel explicitly.")


def require_voice_output_allowed(context: ChannelContext, out_path) -> Path:
    """Refuse synthesis whose output would land anywhere it should not.

    An allowlist, not a blocklist. Blocking only the project's audio folder still
    lets `--out ../elsewhere/narration.mp3` produce a usable file from an
    unapproved voice, which someone then copies into place by hand. So while
    selection is pending the resolved output must sit inside the pack's preview
    directory and nowhere else.

    Call this before reading credentials, building a client, making a request,
    creating a directory or writing a byte.
    """
    resolved = Path(out_path).expanduser()
    if not resolved.is_absolute():
        resolved = (Path.cwd() / resolved)
    # Resolve the parent: the output file itself does not exist yet, but a
    # symlinked directory in its path would otherwise carry the write outside.
    resolved = (resolved.parent.resolve() / resolved.name)

    if context.voice_approved:
        return resolved

    root = context.voice_preview_dir
    if not resolved.is_relative_to(root):
        raise VoiceNotApproved(
            f"{context.channel_id} has no approved voice profile "
            f"(selection_status={context.voice_selection_status!r}), so synthesis may "
            f"only write inside {root}.\n"
            f"  refused: {resolved}\n"
            f"Evaluate candidates there, then record the choice as "
            f"voice.approved_profile in {context.pack_dir / PACK_NAME} and re-render.")
    return resolved


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="Installed channel packs")
    ap.add_argument("--show", metavar="CHANNEL_ID", default=None)
    ap.add_argument("--migrate-project", metavar="PROJECT", default=None)
    ap.add_argument("--channel", default=None, help="Channel id, for --migrate-project")
    args = ap.parse_args()

    if args.list:
        known = list_channels()
        if not known:
            print("no channel packs installed")
            return 1
        for cid in known:
            try:
                c = load_channel(cid)
                print(f"  {cid:24} DNA v{c.channel_dna_version}  "
                      f"host={'yes' if c.host_enabled else 'no':3}  "
                      f"voice={c.voice_selection_status}  "
                      f"{c.channel_json_sha256[:12]}…")
            except ChannelError as e:
                print(f"  {cid:24} BROKEN: {e}")
        return 0

    if args.show:
        try:
            c = load_channel(args.show)
        except ChannelError as e:
            print(f"{e}", file=sys.stderr)
            return 1
        print(f"channel        : {c.channel_id}")
        print(f"DNA version    : {c.channel_dna_version}")
        print(f"pack hash      : {c.channel_json_sha256}")
        print(f"host enabled   : {c.host_enabled}")
        print(f"character spec : {c.character_spec_path}")
        print(f"                 {c.character_spec_sha256}")
        print(f"voice          : {c.voice_selection_status} "
              f"(profile hash: {c.voice_profile_sha256 or 'none — pending'})")
        print(f"preview dir    : {c.voice_preview_dir}")
        print(f"legacy adapter : {c.legacy_adapter}")
        print("capabilities   :")
        for vt, rid in sorted(c.renderer_capabilities.items()):
            e = renderers.RENDERERS[rid]
            print(f"  {vt:16} {rid:26} {e['cost_category']}"
                  + ("" if e["implemented"] else "  NOT WRITTEN"))
        return 0

    if args.migrate_project:
        if not args.channel:
            print("--migrate-project requires --channel", file=sys.stderr)
            return 2
        project = Path(args.migrate_project)
        if not project.is_absolute():
            project = (PIPELINE_DIR / args.migrate_project).resolve()
        try:
            changed, msg = migrate_project(project, args.channel)
        except ChannelError as e:
            print(f"migration refused: {e}", file=sys.stderr)
            return 1
        print(("  migrated: " if changed else "  no change: ") + msg)
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
