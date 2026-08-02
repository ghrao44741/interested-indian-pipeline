"""
composite_character.py — place an approved host pose over a rendered background.

The compositor never sees a file path. It takes a pose *id* and hands it to
pose_registry.resolve(), which is the only code allowed to turn an id into
bytes: it checks approval status, path containment and the SHA-256 recorded at
approval. That is what makes it impossible for a raw generator dump, an
unreviewed replacement candidate or a superseded archived identity to reach a
render — none of them are registered, and a tampered approved file fails its
hash.

Placement is driven by the pose's own recorded metadata rather than by a caller
guess. `negative_space` says which side of the artwork is open, and the host is
placed so that open side faces frame centre: a host pointing toward viewer-left
belongs on the right of the frame, or they point off the edge at nothing.

    composite("pointing_viewer_right", bg, out)          # placed left, faces in
Two public entry points, because resolving a pose and being allowed to write an
episode are different permissions. Registry resolution proves the bytes are
approved artwork; it says nothing about whether anyone approved spending on this
episode:

    render_production(project, pose_id, bg, out)   requires Checkpoint 3 approval
    render_preview(pose_id, bg, "name")            character work, previews only

CLI:
    python composite_character.py --project ep02 --pose-id neutral_presenter \\
        --background ep02/images/SCENE-004.png --out ep02/images/SCENE-004.png
    python composite_character.py --pose-id neutral_presenter \\
        --background bg.png --preview pose-check
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageFilter

import channel_context
import pose_registry
from generation_gate import (GateBlocked, require_character_ready,
                             require_generation_ready)

PIPELINE_DIR = Path(__file__).parent
PREVIEW_DIR = PIPELINE_DIR / "character" / "previews"

CANVAS = (1280, 720)
HOST_HEIGHT_FRAC = 0.78      # host height as a fraction of frame height
SIDE_MARGIN_FRAC = 0.05
SHADOW_BLUR = 14
SHADOW_ALPHA = 70


def placement_side(meta: dict, override: str | None = None) -> str:
    """Which side of the frame the host occupies: 'left', 'right' or 'center'.

    Reads the pose's recorded negative_space so the open side of the artwork
    faces frame centre.
    """
    if override:
        if override not in ("left", "right", "center"):
            raise ValueError(f"side must be left/right/center, got {override!r}")
        return override
    ns = meta.get("negative_space")
    if ns == "viewer_left":
        return "right"
    if ns == "viewer_right":
        return "left"
    if ns == "none":
        # A tableau with its own geometry has no open side to orient — it is the
        # scene, so it sits centred rather than pushed against an edge.
        return "center"
    return "right"


def _ground_shadow(pose: Image.Image) -> Image.Image:
    """A soft ellipse under the feet, derived from the pose's own silhouette."""
    alpha = pose.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return Image.new("RGBA", pose.size, (0, 0, 0, 0))
    left, _, right, bottom = bbox
    width = right - left
    shadow = Image.new("RGBA", pose.size, (0, 0, 0, 0))
    from PIL import ImageDraw
    d = ImageDraw.Draw(shadow)
    h = max(6, int(width * 0.12))
    d.ellipse([left + width * 0.08, bottom - h // 2,
               right - width * 0.08, bottom + h // 2],
              fill=(0, 0, 0, SHADOW_ALPHA))
    return shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR))


def _composite(pose_id: str,
               background: Path | str,
               out: Path | str,
               *,
               scene_bound: bool = False,
               side: str | None = None,
               height_frac: float = HOST_HEIGHT_FRAC,
               shadow: bool = True,
               context=None) -> dict:
    """The rendering itself. Private: it performs no authorisation.

    Reachable only through render_production() (episode artwork, Checkpoint 3
    gated) or render_preview() (character development, confined to
    character/previews/). Resolving a pose through the registry proves the bytes
    are approved artwork — it says nothing about whether anyone approved spending
    on this episode, and this function used to be the public entry point, so a
    direct call could write production artwork with neither question asked.

    `scene_bound` is passed straight through to the resolver and must be set
    explicitly by the caller — defaulting it True would re-open the hole the
    registry exists to close, by letting a router drop a desk into a shot that
    never asked for one.
    """
    pose_path = pose_registry.resolve(pose_id, scene_bound=scene_bound, context=context)
    meta = pose_registry.metadata(pose_id, context=context)
    chosen = placement_side(meta, side)

    bg = Image.open(background).convert("RGBA")
    if bg.size != CANVAS:
        bg = bg.resize(CANVAS, Image.LANCZOS)

    pose = Image.open(pose_path).convert("RGBA")
    bbox = pose.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError(f"{pose_id}: pose asset is fully transparent")
    pose = pose.crop(bbox)

    target_h = int(CANVAS[1] * height_frac)
    scale = target_h / pose.height
    pose = pose.resize((max(1, int(pose.width * scale)), target_h), Image.LANCZOS)

    margin = int(CANVAS[0] * SIDE_MARGIN_FRAC)
    if chosen == "left":
        x = margin
    elif chosen == "right":
        x = CANVAS[0] - pose.width - margin
    else:
        x = (CANVAS[0] - pose.width) // 2
    y = CANVAS[1] - pose.height

    if shadow:
        sh = _ground_shadow(pose)
        bg.alpha_composite(sh, (x, y))
    bg.alpha_composite(pose, (x, y))

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(out, "PNG")

    return {
        "pose_id": pose_id,          # the id is the record — never the path
        "side": chosen,
        "direction": meta.get("direction"),
        "negative_space": meta.get("negative_space"),
        "scene_bound": scene_bound,
        "height_frac": height_frac,
        "output": out.name,
    }


def render_production(project, pose_id: str, background, out, **kw) -> dict:
    """Write host artwork into an episode. Requires Checkpoint 3 approval.

    The project is a required argument rather than an optional one because there
    is no such thing as an unattributed production render: every host shot
    belongs to an approved plan for a specific episode, and the gate needs to know
    which.
    """
    require_generation_ready(project, f"host composite ({pose_id})",
                             pose_id=pose_id,
                             scene_bound=kw.get("scene_bound", False))
    out = Path(out)
    project_dir = Path(project)
    if not project_dir.is_absolute():
        project_dir = (PIPELINE_DIR / project).resolve()
    # An approved episode authorises writing into that episode, and nowhere else.
    if not out.resolve().is_relative_to(project_dir.resolve()):
        raise ValueError(f"production output {out} is outside the approved project "
                         f"{project_dir.name}")
    # The pose is resolved through the episode's own channel, so an id that names
    # one channel's artwork cannot reach another's, however alike the ids look.
    context = channel_context.load_channel_for_project(project_dir)
    return _composite(pose_id, background, out, context=context, **kw)


def render_preview(pose_id: str, background, name: str, **kw) -> dict:
    """Character-development preview. Never touches an episode.

    Confined to character/previews/ by construction — the caller supplies a name,
    not a path, so there is no argument that can aim this at a project's images
    directory. Character work is channel-scope and must not be able to reach
    episode artwork through a side door.
    """
    require_character_ready(f"host composite preview ({pose_id})",
                            pose_id=pose_id,
                            scene_bound=kw.get("scene_bound", False))
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or "preview"
    out = PREVIEW_DIR / f"{safe}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    rec = _composite(pose_id, background, out, **kw)
    rec["preview"] = True
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pose-id", default=None)
    ap.add_argument("--background", default=None)
    ap.add_argument("--project", default=None,
                    help="Episode this shot belongs to. Required for a production "
                         "render, which needs that episode's Checkpoint 3 approval.")
    ap.add_argument("--out", default=None,
                    help="Output path, inside --project. Production renders only.")
    ap.add_argument("--preview", default=None, metavar="NAME",
                    help="Character-development preview. Writes character/previews/"
                         "<NAME>.png and cannot touch an episode.")
    ap.add_argument("--scene-bound", action="store_true",
                    help="Permit a scene-bound tableau (desk, chair and document "
                         "are baked into the artwork)")
    ap.add_argument("--side", choices=["left", "right", "center"], default=None)
    ap.add_argument("--height-frac", type=float, default=HOST_HEIGHT_FRAC)
    ap.add_argument("--list-poses", action="store_true")
    args = ap.parse_args()

    if args.list_poses:
        for pid in pose_registry.list_poses(generic_only=True):
            print(pid)
        return 0
    if not (args.pose_id and args.background):
        ap.error("--pose-id and --background are required")

    kw = dict(scene_bound=args.scene_bound, side=args.side,
              height_frac=args.height_frac)
    try:
        if args.preview:
            if args.project or args.out:
                ap.error("--preview cannot be combined with --project or --out; "
                         "previews are confined to character/previews/")
            rec = render_preview(args.pose_id, args.background, args.preview, **kw)
        else:
            if not (args.project and args.out):
                ap.error("a production render needs --project and --out "
                         "(or use --preview NAME for character development)")
            rec = render_production(args.project, args.pose_id,
                                    args.background, args.out, **kw)
    except GateBlocked as e:
        print(f"\n{e}", file=sys.stderr)
        print("\nNothing was rendered.", file=sys.stderr)
        return 1
    except (pose_registry.PoseError, ValueError, channel_context.ChannelError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(f"  composited {rec['pose_id']} ({rec['side']}) -> {rec['output']}"
          + ("  [preview]" if rec.get("preview") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
