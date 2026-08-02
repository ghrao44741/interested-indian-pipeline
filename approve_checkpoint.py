"""
approve_checkpoint.py — the only way to grant Checkpoint 3 approval.

Approval is a human act, so it lives in its own command with its own confirmation
phrase. It writes `{project}/checkpoint_3_approval.json`, binding four artifacts
as they stood when a person read them:

    manifest.json     the scene identity being generated against
    visual_plan.json  what the gate will execute
    visual_plan.md    what the human actually read
    the prompts file  the routing input the plan was derived from

Plus the plan's id and the route-failure revision. Binding all six is the whole
point. An earlier version checked only that "a visual plan exists, has no review
items and matches the manifest" — but plan_visuals.py writes that file itself, so
the check only proved a program agreed with itself. A later version bound two
hashes, which still left the prompts file free to change underneath a valid
approval, and left the human reading a document nobody had verified against the
plan.

Editing any bound file, or any route failing or being resolved, invalidates the
approval. Re-running the planner cannot restore it — every plan carries a fresh
id, and the confirmation phrase names it.

    python approve_checkpoint.py --project ep02 --show
    python approve_checkpoint.py --project ep02 \\
        --approver "Giri" \\
        --confirm "I approve paid generation for ep02 plan a1b2c3d4"

    python approve_checkpoint.py --project ep02 --revoke

Resolving a route failure is deliberately NOT here — see route_failures.py. This
command approves, shows and revokes; nothing else.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import channel_context
import generation_gate as gg
import plan_visuals
import route_failures

PIPELINE_DIR = Path(__file__).parent
# 2: every approval records the Channel Pack, character specification and voice
# profile it was granted under. A v1 record bound none of them.
SCHEMA_VERSION = 2

# Modules that must never be able to grant approval, even indirectly. The planner
# writes the very artifact being approved, and the orchestrator runs unattended —
# either one calling this would turn a human checkpoint back into a formality.
FORBIDDEN_CALLERS = ("plan_visuals", "pipeline_agents", "route_images",
                     "generate_image_prompts")


class ApprovalRefused(RuntimeError):
    """Approval could not be granted. Nothing was written."""


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def confirmation_phrase(project: str, plan_id: str = "") -> str:
    """Names the project AND the plan being approved.

    Project-specific so a phrase cannot be pasted from one episode into another
    by habit; plan-specific so a confirmation typed against the plan a human read
    cannot approve a plan that was regenerated in the meantime.
    """
    return f"I approve paid generation for {project} plan {(plan_id or '')[:8]}".strip()


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
    md_path = project_dir / gg.VISUAL_PLAN_MD_NAME
    manifest_path = project_dir / "manifest.json"
    prompts_path = project_dir / gg.PROMPTS_NAME
    if not plan_path.exists():
        raise ApprovalRefused(f"no {gg.VISUAL_PLAN_NAME} — run plan_visuals.py and "
                              f"read it before approving")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    if plan.get("schema_version") != gg.PLAN_SCHEMA_VERSION:
        raise ApprovalRefused(
            f"the plan's schema_version is {plan.get('schema_version')!r}, this "
            f"code expects {gg.PLAN_SCHEMA_VERSION} — re-run plan_visuals.py")

    # The channel decides what the artwork is supposed to be, so approval is
    # granted against a specific pack, character specification and voice — not
    # against a plan that merely happens to sit in this folder.
    try:
        context = channel_context.load_channel_for_project(project_dir)
    except channel_context.ChannelError as e:
        raise ApprovalRefused(f"the channel could not be resolved: {e}")

    binding = plan.get("channel")
    if not isinstance(binding, dict) or not binding:
        raise ApprovalRefused("the plan records no channel — re-run plan_visuals.py")
    expected_binding = context.plan_binding()
    if binding != expected_binding:
        differing = sorted(k for k in expected_binding
                           if binding.get(k) != expected_binding[k])
        raise ApprovalRefused(
            f"the plan was built against a different channel state ({', '.join(differing)} "
            f"changed). Re-run plan_visuals.py and read the new plan before approving.")

    # Defense in depth is the gate's job; refusing here is what makes the voice
    # decision a recorded act rather than a config edit nobody has to make.
    if not context.voice_approved:
        raise ApprovalRefused(
            f"{context.channel_id} has no approved voice profile "
            f"(selection_status={context.voice_selection_status!r}). Paid generation "
            f"cannot be approved against narration produced by an unapproved voice. "
            f"Record the decision as voice.approved_profile in "
            f"{context.pack_dir / channel_context.PACK_NAME}, re-render, re-narrate, "
            f"then re-plan.")

    plan_id = plan.get("plan_id")
    if not plan_id:
        raise ApprovalRefused("the plan has no plan_id — re-run plan_visuals.py")
    expected = confirmation_phrase(project_dir.name, plan_id)
    if (confirmation or "").strip() != expected:
        raise ApprovalRefused(
            f"confirmation phrase does not match. Expected exactly:\n  {expected}\n"
            f"(if you typed the phrase for an earlier plan, re-read the current "
            f"plan first — it has changed)")

    # The human reads the markdown; the gate executes the JSON. Refuse unless the
    # document on disk is exactly what this plan renders to, so the two cannot
    # describe different work.
    if not md_path.exists():
        raise ApprovalRefused(f"no {gg.VISUAL_PLAN_MD_NAME} — the reviewed document "
                              f"is missing; re-run plan_visuals.py")
    fresh = plan_visuals.render_md(plan)
    if md_path.read_text(encoding="utf-8") != fresh:
        raise ApprovalRefused(
            f"{gg.VISUAL_PLAN_MD_NAME} does not match a fresh render of "
            f"{gg.VISUAL_PLAN_NAME}. The document you reviewed is not the plan that "
            f"would be executed. Re-run plan_visuals.py and read it again.")

    if not prompts_path.exists():
        raise ApprovalRefused(f"no {gg.PROMPTS_NAME} — the routing input is missing")
    recorded_prompts = plan.get("inputs", {}).get("prompts_sha256")
    if recorded_prompts != _sha(prompts_path):
        raise ApprovalRefused(
            f"{gg.PROMPTS_NAME} has changed since the plan was built. Re-classify, "
            f"re-plan and read the new plan before approving.")

    review = plan.get("needs_review", [])
    if review:
        raise ApprovalRefused(
            f"the plan still has {len(review)} unresolved review item(s); resolve "
            f"them and re-plan before approving:\n"
            + "\n".join(f"  - shot {r.get('shot')}: {r.get('reason')}" for r in review[:8]))

    outstanding = route_failures.unresolved(project_dir)
    if outstanding:
        raise ApprovalRefused(
            f"{len(outstanding)} unresolved route failure(s). Resolve them with "
            f"route_failures.py, re-plan, then approve:\n"
            + "\n".join(f"  - {f['visual_asset_id']}: {f['reason']}" for f in outstanding[:8]))

    current_rev = route_failures.revision(project_dir)
    if plan.get("failure_revision") != current_rev:
        raise ApprovalRefused(
            f"the plan was built at failure revision {plan.get('failure_revision')!r} "
            f"and the record is now at {current_rev} — re-run plan_visuals.py")

    # Every executable entry must be identifiable as artwork and reconcile with
    # the manifest, or an approval could authorise work nobody can attribute.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    known = {s.get("visual_asset_id") for s in manifest.get("scenes", [])}
    ids = [s.get("visual_asset_id") for s in plan.get("shots", [])]
    if not ids:
        raise ApprovalRefused("the plan contains no shots")
    if not all(ids):
        raise ApprovalRefused(
            f"{sum(1 for i in ids if not i)} plan entr(ies) have no visual_asset_id; "
            f"approved work must be identified by persistent artwork identity")
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ApprovalRefused(f"duplicate visual_asset_id in the plan: {dupes}")
    orphans = [i for i in ids if i not in known]
    if orphans:
        raise ApprovalRefused(f"plan entries do not reconcile with the manifest: {orphans[:6]}")

    planned = plan.get("manifest_identity", {})
    want_ids = sorted(s.get("shot_instance_id") for s in manifest.get("scenes", []))
    if (planned.get("scenes") != len(manifest.get("scenes", []))
            or planned.get("shot_instance_ids") != want_ids):
        raise ApprovalRefused("the plan was built against a different split — "
                              "re-run plan_visuals.py before approving")

    record = {
        "schema_version": SCHEMA_VERSION,
        "project": project_dir.name,
        "plan_id": plan_id,
        "channel": expected_binding,
        "manifest_sha256": _sha(manifest_path),
        "visual_plan_sha256": _sha(plan_path),
        "visual_plan_md_sha256": _sha(md_path),
        "prompts_sha256": _sha(prompts_path),
        "failure_revision": current_rev,
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "approved_by": approver.strip(),
        "confirmation": expected,
        "paid_generation": plan.get("paid_generation", {}),
        "approved_mix": plan.get("mix", {}),
        "host_presence_pct": plan.get("host_presence_pct"),
        "approved_visual_asset_ids": sorted(ids),
        "_note": "Binds this approval to the exact bytes of the manifest, the "
                 "executable plan, the document that was read, and the routing "
                 "input. Editing any of them invalidates it; so does any route "
                 "failure or resolution. Re-running plan_visuals.py cannot "
                 "restore it.",
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
        # Everything the approver needs in order to decide, and the exact phrase
        # for the plan currently on disk — so the confirmation they type cannot
        # belong to a plan they are no longer looking at.
        plan_path = project_dir / gg.VISUAL_PLAN_NAME
        plan = (json.loads(plan_path.read_text(encoding="utf-8"))
                if plan_path.exists() else {})
        print(f"project        : {project_dir.name}")
        if not plan:
            print(f"plan           : none — run plan_visuals.py first")
            return 1
        ch = plan.get("channel") or {}
        print(f"channel        : {ch.get('channel_id') or 'UNRESOLVED'} "
              f"(DNA v{ch.get('channel_dna_version')})")
        print(f"voice profile  : "
              + (str(ch.get('voice_profile_sha256'))[:16] + "…"
                 if ch.get("voice_profile_sha256")
                 else "none approved — approval will be refused"))
        print(f"plan id        : {plan.get('plan_id')}")
        print(f"generated      : {plan.get('generated_at')}")
        print(f"routing input  : {str(plan.get('inputs', {}).get('prompts_sha256'))[:16]}…")
        print(f"failure rev    : {plan.get('failure_revision')}")
        print(f"shots          : {len(plan.get('shots', []))}")
        print(f"mix            : "
              + ", ".join(f"{k} {v}" for k, v in plan.get("mix", {}).items()))
        print(f"host presence  : {plan.get('host_presence_pct')}%")
        pg = plan.get("paid_generation", {})
        print(f"paid shots     : {pg.get('shots')}"
              + (f"  (~${pg['estimate_usd']})" if pg.get("estimate_usd") is not None
                 else "  (not priced)"))
        print(f"needs review   : {len(plan.get('needs_review', []))}")
        for r in plan.get("needs_review", [])[:8]:
            print(f"                 - shot {r.get('shot')}: {r.get('reason')}")

        print(f"\napproval       : {'present' if approval.exists() else 'none'}")
        if approval.exists():
            rec = json.loads(approval.read_text(encoding="utf-8"))
            print(f"approved by    : {rec.get('approved_by')} at {rec.get('approved_at')}")
            print(f"for plan       : {rec.get('plan_id')}")
            rep = gg.require_generation_ready(project_dir, "approval check",
                                              raise_on_block=False)
            print(f"still valid    : {'yes' if not rep.blockers else 'NO'}")
            for b in rep.blockers:
                print(f"                 - {b}")

        print(f"\nRead {gg.VISUAL_PLAN_MD_NAME} first. Then, to approve this plan:\n"
              f"  python approve_checkpoint.py --project {project_dir.name} \\\n"
              f"    --approver \"<your name>\" \\\n"
              f"    --confirm \"{confirmation_phrase(project_dir.name, plan.get('plan_id', ''))}\"")
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
    print(f"  Checkpoint 3 approved for {rec['project']}, plan {rec['plan_id'][:8]}")
    print(f"    manifest        {rec['manifest_sha256'][:16]}…")
    print(f"    plan (json)     {rec['visual_plan_sha256'][:16]}…")
    print(f"    plan (md)       {rec['visual_plan_md_sha256'][:16]}…")
    print(f"    routing input   {rec['prompts_sha256'][:16]}…")
    print(f"    failure rev     {rec['failure_revision']}")
    print(f"    paid shots      {rec['paid_generation'].get('shots')}")
    print(f"\n  Editing any of those files invalidates this approval, as does any "
          f"route failure or resolution.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
