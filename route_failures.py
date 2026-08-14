"""
route_failures.py — persistent record of routes that failed at dispatch time.

A route that fails while generating is not a transient console message. It means
the approved plan no longer describes what can actually be produced, so it must
outlive the process that hit it. Before this existed, a Pexels failure printed a
line, the run continued into the paid AI batch, the approval stayed valid, and
the next plan looked clean — three separate ways for a failure to disappear.

Records key on `visual_asset_id`, the persistent artwork identity. Shot number,
scene id and filename are carried for readability only: they change whenever the
narration is re-split, and a failure that loses track of which artwork it refers
to is worse than no record.

Failures are never deleted. Resolving one marks it resolved and says who did it,
why, and whether the route actually changed or someone chose to retry. Every
change bumps a monotonic revision that the visual plan and the approval both
record, which is what stops a resolution from quietly reactivating the approval
that was granted before the failure happened.

    python route_failures.py --project ep02
    python route_failures.py --project ep02 --resolve VIS-004-A \\
        --approver "Giri" --reason "Pexels was returning 503s; retrying"
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

PIPELINE_DIR = Path(__file__).parent
FAILURES_NAME = "route_failures.json"
SCHEMA_VERSION = 1

UNRESOLVED = "unresolved"
RESOLVED = "resolved"
ROUTE_CHANGED = "route_changed"
TRANSIENT_RETRY = "transient_retry"


class FailureError(RuntimeError):
    """A failure record could not be read or resolved."""


def _thaw_for_digest(obj):
    """Recursively converts a MappingProxyType/tuple-based frozen structure
    (as found in a DispatchSnapshot's routes — visual_routes.py's deep-frozen
    route_args, host_reference_asset_ids, host_placement) back into plain
    dict/list so json.dumps can serialize it; the stdlib json module does not
    natively handle MappingProxyType. Read-only — never mutates `obj`, and the
    caller never reuses the returned structure as though it were still
    canonical/immutable."""
    if isinstance(obj, (MappingProxyType, dict)):
        return {k: _thaw_for_digest(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_thaw_for_digest(v) for v in obj]
    return obj


def route_args_digest(route) -> str:
    """Stable digest of the complete canonical execution input a route will
    produce (Task 2B-B2b-2a): visual_type, renderer_id, host_renderer_id,
    route_args, prompt, output_file, host_present, host_method,
    host_pose_id, host_scene_bound, host_reference_asset_ids, and
    host_placement. Changing any one of these changes the digest.

    Deliberately excludes display-only fields — scene_id, shot number,
    narration text used only for legacy classification/display — so
    re-splitting the narration or reordering shots never changes this value
    on its own. There is no map_args/chart_args/pose_id fallback: those are
    legacy visual_plan.json fields, not canonical execution inputs, and
    mixing them into this digest would let a legacy-shaped shot and a
    canonical route that execute completely differently hash identically (or
    vice versa) purely by coincidence of which fields happen to be set.

    `route` may be a plain dict or an immutable MappingProxyType (a
    DispatchSnapshot route) — both work identically; this function never
    mutates its input. A caller passing a legacy visual_plan.json shot (which
    carries none of the canonical field names above except a route-type
    label) still gets a deterministic digest — every canonical field reads
    as absent except the route type — it is simply not a meaningful one for
    detecting a legacy route's own change; auto_resolve_changed() is not
    wired against canonical routes by this task.

    `visual_type` alone reads `route.get("visual_type") or route.get("type")`
    — not a route_args/pose_id-style legacy fallback (there is deliberately
    none of those), but plain compatibility with the two different dict
    shapes this module's own EXISTING callers already pass for the identical
    concept: a plan_visuals.py-built shot names it `visual_type`, while a
    freshly parse_shots()-parsed shot (route_images.py's legacy classifier,
    which auto_resolve_changed() is called against) names the same thing
    `type`. Reading only `visual_type` would make every fresh legacy shot
    hash as though it had no route at all, causing auto_resolve_changed() to
    treat an UNCHANGED legacy route as if it had changed — the opposite of
    what that function exists to detect. A canonical route always carries
    `visual_type`, never `type`, so this reads identically for it either
    way."""
    payload = json.dumps({
        "visual_type": route.get("visual_type") or route.get("type"),
        "renderer_id": route.get("renderer_id"),
        "host_renderer_id": route.get("host_renderer_id"),
        "route_args": _thaw_for_digest(route.get("route_args")),
        "prompt": route.get("prompt"),
        "output_file": route.get("output_file"),
        "host_present": bool(route.get("host_present")),
        "host_method": route.get("host_method"),
        "host_pose_id": route.get("host_pose_id"),
        "host_scene_bound": route.get("host_scene_bound"),
        "host_reference_asset_ids": _thaw_for_digest(route.get("host_reference_asset_ids")),
        "host_placement": _thaw_for_digest(route.get("host_placement")),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve_dir(project) -> Path:
    p = Path(project)
    return p if p.is_absolute() else (PIPELINE_DIR / project).resolve()


def path_for(project) -> Path:
    return _resolve_dir(project) / FAILURES_NAME


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "revision": 0, "failures": []}


def load(project) -> dict:
    p = path_for(project)
    if not p.exists():
        return _empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FailureError(f"{p} is unreadable: {e}") from None
    if data.get("schema_version") != SCHEMA_VERSION:
        raise FailureError(f"{p} has schema_version {data.get('schema_version')!r}, "
                           f"expected {SCHEMA_VERSION}")
    return data


def _write_atomic(project, data: dict) -> None:
    p = path_for(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def revision(project) -> int:
    return load(project)["revision"]


def unresolved(project) -> list[dict]:
    return [f for f in load(project)["failures"] if f["status"] == UNRESOLVED]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_failure(project, shot: dict, reason: str) -> dict:
    """Persist one failed route. Returns the record written.

    An existing unresolved failure for the same artwork is updated rather than
    duplicated — the same shot failing twice is one outstanding problem, and a
    growing pile of identical records would obscure that.
    """
    vid = shot.get("visual_asset_id")
    if not vid:
        raise FailureError(
            f"cannot record a failure for shot {shot.get('shot')!r}: no "
            f"visual_asset_id. Route failures key on persistent artwork identity.")
    data = load(project)
    digest = route_args_digest(shot)
    rec = {
        "visual_asset_id": vid,
        "status": UNRESOLVED,
        "planned_route": shot.get("visual_type") or shot.get("planned_type"),
        "route_args_sha256": digest,
        "reason": reason,
        "failed_at": _now(),
        # readability only — these move when the narration is re-split
        "shot": shot.get("shot"),
        "file": shot.get("file"),
        "scene_id": shot.get("scene_id"),
    }
    for i, f in enumerate(data["failures"]):
        if f["visual_asset_id"] == vid and f["status"] == UNRESOLVED:
            rec["failed_at"] = f["failed_at"]      # keep when it first broke
            rec["recurrences"] = f.get("recurrences", 1) + 1
            data["failures"][i] = rec
            break
    else:
        data["failures"].append(rec)
    data["revision"] += 1
    _write_atomic(project, data)
    return rec


def resolve(project, visual_asset_id: str, approver: str, reason: str,
            resolution_type: str = TRANSIENT_RETRY,
            new_route_args_sha256: str | None = None) -> dict:
    """Mark one failure resolved, preserving the record. Bumps the revision."""
    if resolution_type not in (ROUTE_CHANGED, TRANSIENT_RETRY):
        raise FailureError(f"resolution_type must be {ROUTE_CHANGED!r} or "
                           f"{TRANSIENT_RETRY!r}, got {resolution_type!r}")
    if not (approver or "").strip():
        raise FailureError("an approver must be named — a resolution is a human act")
    if not (reason or "").strip():
        raise FailureError("a reason must be given")

    data = load(project)
    for f in data["failures"]:
        if f["visual_asset_id"] == visual_asset_id and f["status"] == UNRESOLVED:
            f.update({
                "status": RESOLVED,
                "resolved_at": _now(),
                "resolved_by": approver.strip(),
                "resolution_reason": reason.strip(),
                "resolution_type": resolution_type,
                "old_route_args_sha256": f["route_args_sha256"],
                "new_route_args_sha256": new_route_args_sha256,
            })
            data["revision"] += 1
            _write_atomic(project, data)
            return f
    raise FailureError(f"no unresolved failure for {visual_asset_id!r} in "
                       f"{path_for(project).name}")


def auto_resolve_changed(project, plan_shots: list[dict]) -> list[dict]:
    """Resolve failures whose route arguments have actually changed.

    Called by the planner. Changing a route is itself the evidence that someone
    addressed the problem, so it needs no separate confirmation — but the record
    is kept, the revision moves, and a new approval is therefore required.
    """
    data = load(project)
    by_id = {s["visual_asset_id"]: s for s in plan_shots if s.get("visual_asset_id")}
    changed = []
    for f in data["failures"]:
        if f["status"] != UNRESOLVED:
            continue
        shot = by_id.get(f["visual_asset_id"])
        if shot is None:
            continue
        digest = route_args_digest(shot)
        if digest != f["route_args_sha256"]:
            f.update({
                "status": RESOLVED,
                "resolved_at": _now(),
                "resolved_by": "plan_visuals.py (automatic)",
                "resolution_reason": "the route or its arguments changed",
                "resolution_type": ROUTE_CHANGED,
                "old_route_args_sha256": f["route_args_sha256"],
                "new_route_args_sha256": digest,
            })
            changed.append(f)
    if changed:
        data["revision"] += 1
        _write_atomic(project, data)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--resolve", metavar="VISUAL_ASSET_ID", default=None)
    ap.add_argument("--approver", default=None)
    ap.add_argument("--reason", default=None)
    ap.add_argument("--all", action="store_true",
                    help="Show resolved history as well as outstanding failures")
    args = ap.parse_args()

    if args.resolve:
        try:
            rec = resolve(args.project, args.resolve, args.approver or "",
                          args.reason or "", TRANSIENT_RETRY)
        except FailureError as e:
            print(f"\nnot resolved: {e}", file=sys.stderr)
            return 1
        print(f"  resolved {rec['visual_asset_id']} ({rec['planned_route']})")
        print(f"    by     : {rec['resolved_by']}")
        print(f"    reason : {rec['resolution_reason']}")
        print("\n  This does NOT restore the previous approval. Re-run "
              "plan_visuals.py, review the new plan, and approve it before retrying.")
        return 0

    try:
        data = load(args.project)
    except FailureError as e:
        print(e, file=sys.stderr)
        return 1
    shown = data["failures"] if args.all else [f for f in data["failures"]
                                               if f["status"] == UNRESOLVED]
    print(f"  {path_for(args.project).name}  revision {data['revision']}")
    if not shown:
        print("  no outstanding route failures")
        return 0
    for f in shown:
        print(f"\n  {f['visual_asset_id']}  [{f['status']}]  {f['planned_route']}")
        print(f"    shot {f.get('shot')} · {f.get('file')} · failed {f['failed_at']}")
        print(f"    {f['reason']}")
        if f["status"] == RESOLVED:
            print(f"    resolved {f['resolved_at']} by {f['resolved_by']} "
                  f"({f['resolution_type']}): {f['resolution_reason']}")
    return 1 if any(f["status"] == UNRESOLVED for f in shown) else 0


if __name__ == "__main__":
    sys.exit(main())
