"""
approve_checkpoint.py — the only way to grant Checkpoint 3 approval.

Approval is a human act, so it lives in its own command with its own confirmation
phrase. It writes `{project}/checkpoint_3_approval.json`, recording the SHA-256 of
the manifest and of the visual plan as they stood when a person read them.

That dual-hash binding is the whole point. The previous gate treated "a visual
plan exists, has no review items and matches the manifest" as approval — but
plan_visuals.py writes that file itself, so the check only proved a program
agreed with itself, and anyone could clear the checkpoint by running a free
command. Here, editing either the manifest or the plan changes a hash and the
approval stops applying. Re-running the planner cannot restore it; only a person
running this command again can.

    python approve_checkpoint.py --project ep02 \\
        --approver "Giri" \\
        --confirm "I approve paid generation for ep02"

    python approve_checkpoint.py --project ep02 --show
    python approve_checkpoint.py --project ep02 --revoke
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import generation_gate as gg

PIPELINE_DIR = Path(__file__).parent
SCHEMA_VERSION = 1

# Modules that must never be able to grant approval, even indirectly. The planner
# writes the very artifact being approved, and the orchestrator runs unattended —
# either one calling this would turn a human checkpoint back into a formality.
FORBIDDEN_CALLERS = ("plan_visuals", "pipeline_agents", "route_images",
                     "generate_image_prompts")


class ApprovalRefused(RuntimeError):
    """Approval could not be granted. Nothing was written."""


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def confirmation_phrase(project: str) -> str:
    """Project-specific on purpose: a phrase that names the episode cannot be
    pasted from one approval into another by habit."""
    return f"I approve paid generation for {project}"


def _reject_automated_caller() -> None:
    """Refuse if any frame on the stack belongs to a module that must not approve.

    A static test asserts those modules never import this one; this is the
    runtime half, which also catches an indirect call through a helper.
    """
    import inspect
    for frame in inspect.stack():
        mod = inspect.getmodule(frame.frame)
        name = (getattr(mod, "__name__", "") or "").rsplit(".", 1)[-1]
        if name in FORBIDDEN_CALLERS:
            raise ApprovalRefused(
                f"approval cannot be granted from {name}.py — Checkpoint 3 is a "
                f"human decision and must be made by running approve_checkpoint.py")


def _write_atomic(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_approval(project, approver: str, confirmation: str) -> Path:
    """Validate, then record approval. Raises ApprovalRefused without writing.

    Everything is checked before the file is created: an approval that exists but
    is invalid is worse than none, because it looks like a decision was made.
    """
    _reject_automated_caller()

    project_dir = Path(project)
    if not project_dir.is_absolute():
        project_dir = (PIPELINE_DIR / project).resolve()
    if not project_dir.is_dir():
        raise ApprovalRefused(f"no such project: {project_dir}")

    expected = confirmation_phrase(project_dir.name)
    if (confirmation or "").strip() != expected:
        raise ApprovalRefused(
            f"confirmation phrase does not match. Expected exactly:\n  {expected}")
    if not (approver or "").strip():
        raise ApprovalRefused("--approver must name the person approving")

    # Identity must be sound, or approval would authorise spending against
    # artwork that may be attached to the wrong words.
    rep = gg.require_identity_ready(project_dir, "Checkpoint 3 approval",
                                    raise_on_block=False)
    if rep.blockers:
        raise ApprovalRefused("identity is not ready:\n"
                              + "\n".join(f"  - {b}" for b in rep.blockers))

    plan_path = project_dir / gg.VISUAL_PLAN_NAME
    manifest_path = project_dir / "manifest.json"
    if not plan_path.exists():
        raise ApprovalRefused(f"no {gg.VISUAL_PLAN_NAME} — run plan_visuals.py and "
                              f"read it before approving")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    review = plan.get("needs_review", [])
    if review:
        raise ApprovalRefused(
            f"the plan still has {len(review)} unresolved review item(s); resolve "
            f"them and re-plan before approving:\n"
            + "\n".join(f"  - shot {r.get('shot')}: {r.get('reason')}" for r in review[:8]))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    planned = plan.get("manifest_identity", {})
    want_ids = sorted(s.get("shot_instance_id") for s in manifest.get("scenes", []))
    if (planned.get("scenes") != len(manifest.get("scenes", []))
            or planned.get("shot_instance_ids") != want_ids):
        raise ApprovalRefused("the plan was built against a different split — "
                              "re-run plan_visuals.py before approving")

    record = {
        "schema_version": SCHEMA_VERSION,
        "project": project_dir.name,
        "manifest_sha256": _sha(manifest_path),
        "visual_plan_sha256": _sha(plan_path),
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "approved_by": approver.strip(),
        "confirmation": expected,
        "paid_generation": plan.get("paid_generation", {}),
        "approved_mix": plan.get("mix", {}),
        "host_presence_pct": plan.get("host_presence_pct"),
        "_note": "Binds this approval to the exact bytes of manifest.json and "
                 "visual_plan.json. Editing either invalidates it; re-running "
                 "plan_visuals.py cannot restore it.",
    }
    out = project_dir / gg.APPROVAL_NAME
    _write_atomic(out, record)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--approver", default=None, help="Who is approving")
    ap.add_argument("--confirm", default=None,
                    help="Exact confirmation phrase (printed by --show)")
    ap.add_argument("--show", action="store_true",
                    help="Show the current approval state and the required phrase")
    ap.add_argument("--revoke", action="store_true",
                    help="Delete the approval record")
    args = ap.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = (PIPELINE_DIR / args.project).resolve()
    approval = project_dir / gg.APPROVAL_NAME

    if args.show:
        print(f"project      : {project_dir.name}")
        print(f"approval     : {'present' if approval.exists() else 'none'}")
        if approval.exists():
            rec = json.loads(approval.read_text(encoding="utf-8"))
            print(f"approved by  : {rec.get('approved_by')} at {rec.get('approved_at')}")
            rep = gg.require_generation_ready(project_dir, "approval check",
                                              raise_on_block=False)
            print(f"still valid  : {'yes' if not rep.blockers else 'NO'}")
            for b in rep.blockers:
                print(f"               - {b}")
        print(f"\nto approve, run:\n  python approve_checkpoint.py --project "
              f"{project_dir.name} \\\n    --approver \"<your name>\" \\\n"
              f"    --confirm \"{confirmation_phrase(project_dir.name)}\"")
        return 0

    if args.revoke:
        if approval.exists():
            approval.unlink()
            print(f"  approval revoked for {project_dir.name}")
        else:
            print(f"  no approval to revoke for {project_dir.name}")
        return 0

    try:
        out = write_approval(project_dir, args.approver or "", args.confirm or "")
    except ApprovalRefused as e:
        print(f"\napproval refused: {e}", file=sys.stderr)
        print("\nNothing was written.", file=sys.stderr)
        return 1
    rec = json.loads(out.read_text(encoding="utf-8"))
    print(f"  Checkpoint 3 approved for {rec['project']}")
    print(f"    manifest    {rec['manifest_sha256'][:16]}…")
    print(f"    visual plan {rec['visual_plan_sha256'][:16]}…")
    print(f"    paid shots  {rec['paid_generation'].get('shots')}")
    print(f"  Editing either file invalidates this approval.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
