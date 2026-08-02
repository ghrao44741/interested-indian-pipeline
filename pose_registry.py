"""
pose_registry.py — the only supported way to resolve a host pose asset.

Runtime must never discover poses by globbing `character/poses/*.png`. That
directory also receives in-progress and rejected work, and a stray raw file
picked up by a glob would put an opaque, unapproved, wrong-identity image into a
render. Resolution goes through the registry in character_spec.json: exact paths,
verified hashes, explicit approval status.

    resolve("neutral_presenter")            -> Path, or raises
    resolve("seated_reading_document")      -> raises unless scene_bound=True
    list_poses(generic_only=True)           -> ids the router may choose freely
"""

import hashlib
import json
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent
SPEC_PATH = PIPELINE_DIR / "character" / "character_spec.json"

APPROVED = "approved"
APPROVED_SCENE_BOUND = "approved_scene_bound"


class PoseError(RuntimeError):
    """Raised when a pose cannot be resolved safely."""


def _registry() -> dict:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return spec.get("pose_library", {}).get("registry", {})


def list_poses(generic_only: bool = False) -> list[str]:
    """Approved pose ids. With generic_only, excludes scene-bound tableaux."""
    out = []
    for pid, e in _registry().items():
        if e.get("status") not in (APPROVED, APPROVED_SCENE_BOUND):
            continue
        if generic_only and not e.get("generic_compositing_allowed", False):
            continue
        out.append(pid)
    return sorted(out)


def metadata(pose_id: str) -> dict:
    reg = _registry()
    if pose_id not in reg:
        raise PoseError(f"unknown pose {pose_id!r}; registered: {sorted(reg)}")
    return reg[pose_id]


def resolve(pose_id: str, scene_bound: bool = False) -> Path:
    """Absolute path to an approved pose asset.

    `scene_bound` must be set explicitly to use a tableau such as the seated
    desk pose: its furniture is baked into the image and cannot be separated, so
    it is only valid where the scene already calls for that setting. Defaulting
    it to False keeps a router from dropping a desk into an unrelated shot.
    """
    e = metadata(pose_id)
    status = e.get("status")
    if status not in (APPROVED, APPROVED_SCENE_BOUND):
        raise PoseError(f"{pose_id}: status is {status!r}, not approved")
    if status == APPROVED_SCENE_BOUND and not scene_bound:
        raise PoseError(
            f"{pose_id} is a scene-bound tableau (includes "
            f"{', '.join(e.get('includes_geometry', []))}). Pass scene_bound=True "
            f"only when the scene requires that setting.")

    # Containment before anything else. A registry entry is data, and data can be
    # wrong: an absolute path, a ../ traversal or a symlink pointing outside the
    # pose directory would all resolve to bytes nobody approved. Checked against
    # the fully resolved path so symlinks cannot smuggle a target past it.
    raw = e["path"]
    if Path(raw).is_absolute() or ".." in Path(raw).parts:
        raise PoseError(f"{pose_id}: registered path {raw!r} is absolute or traverses upward")

    poses_root = (PIPELINE_DIR / "character" / "poses").resolve()
    path = (PIPELINE_DIR / raw)
    resolved = path.resolve()
    if not resolved.is_relative_to(poses_root):
        raise PoseError(f"{pose_id}: resolved path escapes {poses_root} "
                        f"(resolved to {resolved})")

    if not path.exists():
        raise PoseError(f"{pose_id}: registered asset missing at {path}")
    # Integrity is always verified. There is deliberately no bypass: an
    # opt-out parameter is an invitation for a caller to skip the one check that
    # proves the bytes are the approved ones.
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != e["sha256"]:
        raise PoseError(
            f"{pose_id}: hash mismatch — the file has changed since approval "
            f"(expected {e['sha256'][:12]}…, found {digest[:12]}…)")
    return path


def audit() -> dict:
    """Every approved asset checked for presence and hash integrity.

    Unapproved entries are reported separately rather than as problems. A pose
    awaiting promotion is a normal state — it is already unreachable, because
    resolve() refuses it — and treating it as a fault would block every paid
    operation channel-wide for the duration of one review.
    """
    ok, problems, unapproved = [], [], []
    for pid, e in _registry().items():
        if e.get("status") not in (APPROVED, APPROVED_SCENE_BOUND):
            unapproved.append(f"{pid} ({e.get('status')})")
            continue
        try:
            resolve(pid, scene_bound=True)
            ok.append(pid)
        except PoseError as err:
            problems.append(str(err))
    return {"ok": ok, "problems": problems, "unapproved": unapproved}


if __name__ == "__main__":
    a = audit()
    print(f"registered : {len(a['ok']) + len(a['problems']) + len(a['unapproved'])}")
    print(f"verified   : {a['ok']}")
    print(f"problems   : {a['problems'] or 'none'}")
    print(f"unapproved : {a['unapproved'] or 'none'}")
    print(f"generic    : {list_poses(generic_only=True)}")
