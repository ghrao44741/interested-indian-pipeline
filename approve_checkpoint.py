"""approve_checkpoint.py — the only way to grant Checkpoint 3 approval.

Task 2B-B2b-3: this is now the schema-v3 canonical approval writer. It writes
`{project}/checkpoint_3_approval.json`, binding the exact bytes of the
canonical routing artifact a person reviewed:

    visual_routes.json   the canonical routes document the gate executes
    visual_routes.md     what a human reads before authorising spend
    manifest.json        the scene identity being generated against

plus the routes_id, a freshly-recomputed routes_content_sha256 and
renderer_registry_sha256 (never trusted from any stored or self-reported
value), the current failure_revision, the current Channel Pack binding, the
current narration binding, and a strict paid-generation summary derived
directly from the routes document's own renderer_id fields against the live
renderer registry — never approximated, never independently authored.

Every one of those is (re)validated or (re)computed fresh, right here, before
anything is written: canonical routes are loaded and required to be fully
executable (schema, contract-integrity, manifest-coverage and status all
clean — visual_routes.require_executable_routes() raises, naming every
blocker, otherwise), manifest identity must be clean, the Channel Pack
binding and narration binding must both be current, and there must be no
unresolved route failure. Any of those failing is a named ApprovalRefused —
never an uncaught exception, and nothing is written.

Approval writing is atomic: a unique same-directory temporary file, complete
serialization before replacement, os.replace() for the final commit, and the
prior approval preserved byte-for-byte if anything fails first.

This module no longer authors or depends on plan_visuals.py in any way —
plan_visuals.py's project-authoring CLI is retired (see plan_visuals.py's own
module docstring); this writer reads only the canonical visual_routes.json /
visual_routes.md pair, never a legacy visual_plan.json/.md.

    python approve_checkpoint.py --project ep02 --show
    python approve_checkpoint.py --project ep02 \\
        --approver "Giri" \\
        --confirm "I approve canonical visual execution for ep02 routes a1b2c3d4"

    python approve_checkpoint.py --project ep02 --revoke

Resolving a route failure is deliberately NOT here — see route_failures.py. This
command approves, shows and revokes; nothing else.
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import channel_context
import generation_gate as gg
import renderers
import route_failures
import source_ids
import visual_routes

PIPELINE_DIR = Path(__file__).parent

# Modules that must never be able to grant approval, even indirectly. The
# planner (legacy or canonical) and the orchestrator run unattended — either
# one calling this would turn a human checkpoint back into a formality.
FORBIDDEN_CALLERS = ("plan_visuals", "pipeline_agents", "route_images",
                     "generate_image_prompts")


class ApprovalRefused(RuntimeError):
    """Approval could not be granted. Nothing was written."""


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


def write_approval_v3(project, approver: str, confirmation: str) -> Path:
    """Validate every canonical blocker, then record the v3 approval.

    Everything is checked before the file is created: an approval that
    exists but is invalid is worse than none, because it looks like a
    decision was made. Never rebuilds, regenerates or authors
    visual_routes.json/visual_routes.md — both must already exist and
    already be internally honest; this only reads and binds them.
    """
    _reject_automated_caller()

    project_dir = Path(project)
    if not project_dir.is_absolute():
        project_dir = (PIPELINE_DIR / project).resolve()
    if not project_dir.is_dir():
        raise ApprovalRefused(f"no such project: {project_dir}")

    if not (approver or "").strip():
        raise ApprovalRefused("--approver must name the person approving")

    # 1. Load and fully validate canonical routes. 2. Require executable
    # route state. 3. Validate manifest coverage. All three come from one
    # call: require_executable_routes() raises, naming every blocker, unless
    # schema, contract-integrity, manifest-coverage AND status are all clean.
    try:
        routes_load = visual_routes.require_executable_routes(
            project_dir, operation="Checkpoint 3 v3 approval")
    except visual_routes.VisualRoutesError as e:
        raise ApprovalRefused(f"canonical routes are not executable:\n{e}")

    doc = routes_load.doc
    manifest = routes_load.manifest
    manifest_path = project_dir / "manifest.json"

    # Refuse unresolved identities before anything else is checked against a
    # manifest whose own identity state cannot be trusted.
    try:
        source_ids.require_clean_identity(manifest_path, "Checkpoint 3 v3 approval")
    except source_ids.IdentityError as e:
        raise ApprovalRefused(f"manifest identity is not clean: {e}")

    # 4. Validate current Channel Pack binding.
    try:
        context = channel_context.load_channel_for_project(project_dir)
    except channel_context.ChannelError as e:
        raise ApprovalRefused(f"the channel could not be resolved: {e}")

    if not context.voice_approved:
        raise ApprovalRefused(
            f"{context.channel_id} has no approved voice profile "
            f"(selection_status={context.voice_selection_status!r}). Canonical "
            f"visual execution cannot be approved against narration produced "
            f"by an unapproved voice.")

    expected_binding = context.plan_binding()
    if doc.get("channel") != expected_binding:
        raise ApprovalRefused(
            "visual_routes.json's recorded channel binding does not match "
            "the channel currently in force — a canonical rebuild is "
            "required before this project can be approved (automatic "
            "canonical route rebuilding is not yet available)")

    # 5. Run narration_binding_problems() — the same shared validator the
    # runtime gate calls, so approval cannot be granted over audio the gate
    # will refuse moments later.
    narration_problems = gg.narration_binding_problems(project_dir, manifest, context)
    if narration_problems:
        raise ApprovalRefused(
            "the narration binding is not verified:\n"
            + "\n".join(f"  - {p}" for p in narration_problems))

    # 8 (route failures). A failure means the routes document no longer
    # describes what can be produced; the approval it would authorise could
    # never actually execute cleanly.
    outstanding = route_failures.unresolved(project_dir)
    if outstanding:
        raise ApprovalRefused(
            f"{len(outstanding)} unresolved route failure(s). Resolve them "
            f"with route_failures.py before approving:\n"
            + "\n".join(f"  - {f['visual_asset_id']}: {f['reason']}"
                        for f in outstanding[:8]))
    current_rev = route_failures.revision(project_dir)

    # 6. Recompute the renderer registry projection/hash — never trusted
    # from any stored value, live against the CURRENT registry.
    try:
        rids = visual_routes.referenced_renderer_ids(doc.get("routes", []))
        fresh_registry_sha256 = visual_routes.compute_renderer_registry_sha256(
            rids, renderers.RENDERERS)
    except Exception as e:
        raise ApprovalRefused(
            f"could not recompute the renderer registry projection: "
            f"{type(e).__name__}: {e}")

    # 7. Recompute the strict paid-generation summary. Refuses on any
    # unknown/malformed renderer or cost category (8) rather than silently
    # omitting or approximating a spend figure.
    try:
        paid_summary = gg.canonical_paid_generation_summary(doc)
    except gg.CanonicalSummaryError as e:
        raise ApprovalRefused(
            f"could not derive a strict paid-generation summary: {e}")

    routes_path = routes_load.routes_path
    routes_md_path = routes_load.routes_md_path
    if not routes_md_path.exists():
        raise ApprovalRefused(
            f"no {visual_routes.ROUTES_MD_NAME} — the reviewed document is missing")

    # Fresh, exact-byte hashes. Never trusted from any stored derived value —
    # the routes document's own self-reported routes_content_sha256 is not
    # read here, exactly like visual_routes.validate_contract() itself never
    # trusts it.
    routes_file_sha256 = visual_routes.file_sha256(routes_path)
    routes_md_sha256 = visual_routes.file_sha256(routes_md_path)
    manifest_sha256 = visual_routes.file_sha256(manifest_path)
    fresh_content_sha256 = visual_routes.compute_routes_content_sha256(doc)

    routes_id = doc.get("routes_id")
    if not routes_id:
        raise ApprovalRefused("visual_routes.json has no routes_id")

    # 9. Require the exact approval confirmation expected by the existing
    # contract — the same phrase generation_gate._check_approval_v3() checks
    # against, computed by the one shared implementation
    # (canonical_confirmation_phrase) so the two can never drift apart.
    expected = gg.canonical_confirmation_phrase(project_dir.name, str(routes_id))
    if (confirmation or "").strip() != expected:
        raise ApprovalRefused(
            f"confirmation phrase does not match. Expected exactly:\n  {expected}\n"
            f"(if you typed the phrase for an earlier routes document, re-read "
            f"the current one first — it has changed)")

    record = {
        "schema_version": gg.APPROVAL_V3_SCHEMA_VERSION,
        "project": project_dir.name,
        "routes_id": routes_id,
        "routes_file_sha256": routes_file_sha256,
        "routes_md_sha256": routes_md_sha256,
        "routes_content_sha256": fresh_content_sha256,
        "renderer_registry_sha256": fresh_registry_sha256,
        "manifest_sha256": manifest_sha256,
        "channel": expected_binding,
        "failure_revision": current_rev,
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "approved_by": approver.strip(),
        "confirmation": expected,
        "paid_generation": paid_summary,
        "_note": "Binds this approval to the exact bytes of visual_routes.json, "
                 "visual_routes.md and manifest.json, plus a freshly recomputed "
                 "content hash, renderer-registry hash and paid-generation "
                 "summary. Editing any bound file, changing the live renderer "
                 "registry, or any route failure or resolution invalidates it. "
                 "There is no way to rebuild visual_routes.json and have this "
                 "approval carry over — canonical route authoring/rebuilding "
                 "is a separate, not-yet-available milestone, and any rebuild "
                 "requires a fresh approval.",
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
                    help="Show the current canonical routing and approval state, "
                         "and the required confirmation phrase")
    ap.add_argument("--revoke", action="store_true",
                    help="Delete the approval record")
    args = ap.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = (PIPELINE_DIR / args.project).resolve()
    approval = project_dir / gg.APPROVAL_NAME

    if args.show:
        load = visual_routes.inspect_project_routes(project_dir, operation="approval --show")
        print(f"project        : {project_dir.name}")
        if load.execution_blockers:
            print(f"routes         : NOT executable —")
            for b in load.execution_blockers:
                print(f"                 - {b}")
            return 1
        doc = load.doc
        ch = doc.get("channel") or {}
        print(f"channel        : {ch.get('channel_id') or 'UNRESOLVED'} "
              f"(DNA v{ch.get('channel_dna_version')})")
        print(f"routes id      : {doc.get('routes_id')}")
        print(f"routes         : {len(doc.get('routes', []))} route(s)")
        try:
            summary = gg.canonical_paid_generation_summary(doc)
            print(f"paid routes    : {summary['shots']}")
        except gg.CanonicalSummaryError as e:
            print(f"paid routes    : could not be determined — {e}")

        print(f"\napproval       : {'present' if approval.exists() else 'none'}")
        if approval.exists():
            rec = json.loads(approval.read_text(encoding="utf-8"))
            print(f"approved by    : {rec.get('approved_by')} at {rec.get('approved_at')}")
            print(f"for routes     : {rec.get('routes_id')}")
            rep = gg._canonical_execution_problems(project_dir, "approval check")
            print(f"still valid    : {'yes' if not rep.blockers else 'NO'}")
            for b in rep.blockers:
                print(f"                 - {b}")

        expected = gg.canonical_confirmation_phrase(
            project_dir.name, str(doc.get("routes_id") or ""))
        print(f"\nRead {visual_routes.ROUTES_MD_NAME} first. Then, to approve this "
              f"routes document:\n"
              f"  python approve_checkpoint.py --project {project_dir.name} \\\n"
              f"    --approver \"<your name>\" \\\n"
              f"    --confirm \"{expected}\"")
        return 0

    if args.revoke:
        if approval.exists():
            approval.unlink()
            print(f"  approval revoked for {project_dir.name}")
        else:
            print(f"  no approval to revoke for {project_dir.name}")
        return 0

    try:
        out = write_approval_v3(project_dir, args.approver or "", args.confirm or "")
    except ApprovalRefused as e:
        print(f"\napproval refused: {e}", file=sys.stderr)
        print("\nNothing was written.", file=sys.stderr)
        return 1
    rec = json.loads(out.read_text(encoding="utf-8"))
    print(f"  Checkpoint 3 approved for {rec['project']}, routes {rec['routes_id'][:8]}")
    print(f"    manifest        {rec['manifest_sha256'][:16]}…")
    print(f"    routes (json)   {rec['routes_file_sha256'][:16]}…")
    print(f"    routes (md)     {rec['routes_md_sha256'][:16]}…")
    print(f"    failure rev     {rec['failure_revision']}")
    print(f"    paid shots      {rec['paid_generation'].get('shots')}")
    print(f"\n  Editing any of those files, or the live renderer registry, "
          f"invalidates this approval, as does any route failure or resolution.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
