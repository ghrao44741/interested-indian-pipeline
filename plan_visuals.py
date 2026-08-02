"""
plan_visuals.py — read-only visual plan. Never calls a paid API.

Produces `{project}/visual_plan.json` (machine-readable, consumed by the
generation gate) and `{project}/visual_plan.md` (for a human to read before
authorising spend). It plans; it does not generate, and it holds no API client.

If the project's identity is blocked it writes the report anyway — the report is
the diagnosis — and exits nonzero. It never continues into routing or
generation, because a plan built on uncertain identity would authorise artwork
against the wrong words.

    python plan_visuals.py --project test_2min

Semantic routing (MAP/CHART/TIMELINE/DOCUMENT/REENACTMENT selection by meaning,
host ratio targets) is Task 3. This plan reports the routing that the current
prompts already declare, and queues anything undeclared for review rather than
guessing.
"""

import argparse
import hashlib
import json
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import channel_context
import generation_gate
import route_failures
import route_images

PIPELINE_DIR = Path(__file__).parent
PLAN_JSON = "visual_plan.json"
PLAN_MD = "visual_plan.md"

# Which declared TYPEs cost money. MAP and CHART render locally (geopandas,
# matplotlib); PHOTO is a free-tier fetch; HOST composites an already-paid-for
# pose asset. Only AI generation spends per shot.
PAID_TYPES = {"CARTOON", "REENACTMENT"}


def _pricing(entry: str, context=None) -> dict | None:
    """Per-shot price, from the channel that is paying for it.

    Read from the pack rather than a root-level file: what a shot costs is a
    property of the channel's own arrangements, and two channels on different
    plans must not read each other's figures.
    """
    if context is None:
        return None
    return channel_context._thaw(
        context.config.get("economics", {}).get("image_pricing", {})).get(entry)


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reconcile(shots: list[dict], scenes: list[dict]) -> list[dict]:
    """Attach persistent identity from the manifest to each parsed shot.

    Matched on scene id first, filename second. `visual_asset_id` is the artwork
    identity and the only thing a failure record or an approved plan entry may key
    on; `scene_id` is display order and moves on every re-split, so it is carried
    for readability and never relied upon downstream.

    An unmatched or ambiguous shot gets no identity and is marked for review
    rather than guessed at — attaching approved artwork to an uncertain match is
    the failure the whole identity layer exists to prevent.
    """
    by_scene, by_file = {}, {}
    for sc in scenes:
        by_scene.setdefault(sc.get("id"), []).append(sc)
        by_file.setdefault(sc.get("image"), []).append(sc)

    out = []
    for s in shots:
        rec = dict(s)
        matches = by_scene.get(s.get("scene_id")) or by_file.get(s.get("file")) or []
        if len(matches) == 1:
            sc = matches[0]
            rec.update({
                "visual_asset_id": sc.get("visual_asset_id"),
                "source_ids": sc.get("source_ids", []),
                "shot_instance_id": sc.get("shot_instance_id"),
                "scene_id": sc.get("id"),
            })
            if not sc.get("visual_asset_id"):
                rec["needs_review"] = True
                rec["review_reason"] = (rec.get("review_reason") or
                                        f"manifest scene {sc.get('id')} has no "
                                        f"visual_asset_id")
        else:
            rec.update({"visual_asset_id": None, "source_ids": [],
                        "shot_instance_id": None})
            rec["needs_review"] = True
            rec["review_reason"] = (
                rec.get("review_reason") or
                (f"no manifest scene matches {s.get('scene_id') or s.get('file')!r}"
                 if not matches else
                 f"{len(matches)} manifest scenes match "
                 f"{s.get('scene_id') or s.get('file')!r} — ambiguous"))
        out.append(rec)
    return out


def build_plan(project_dir: Path) -> dict:
    # The channel decides the character, the poses, the renderers and the price,
    # so the plan must record which one it was built against. Loaded without
    # raising: a plan for an episode with no loadable channel is still worth
    # writing, because the report is the diagnosis.
    try:
        context = channel_context.load_channel_for_project(project_dir)
        channel_error = None
    except channel_context.ChannelError as e:
        context, channel_error = None, str(e)

    prompts = project_dir / route_images.PROMPTS_FILE
    shots = route_images.parse_shots(prompts, context) if prompts.exists() else []

    manifest_path = project_dir / "manifest.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {})
    scenes = manifest.get("scenes", [])
    shots = reconcile(shots, scenes)

    # Identity only. plan_visuals.py produces the artifact a human approves, so
    # it must be runnable before approval exists — and it must never create,
    # modify or imply approval. Granting Checkpoint 3 is a separate,
    # human-only command.
    gate = generation_gate.require_identity_ready(
        project_dir, "visual planning", raise_on_block=False)

    # A failure whose route or arguments have actually changed is resolved by
    # that change; the record is kept and the revision moves, so the approval
    # granted before the failure cannot come back to life.
    auto_resolved = route_failures.auto_resolve_changed(project_dir, shots)
    outstanding = route_failures.unresolved(project_dir)

    needs_review = []
    if not prompts.exists():
        needs_review.append({"shot": None,
                             "reason": f"{route_images.PROMPTS_FILE} not found"})
    for s in shots:
        # The router's own verdict comes first. It is the code that knows a route
        # cannot be executed, and the plan must carry that forward — a plan whose
        # needs_review list disagreed with the router would let a human approve
        # shots the router will then refuse to generate.
        if s.get("needs_review"):
            needs_review.append({"shot": s["shot_num"], "file": s["file"],
                                 "visual_asset_id": s.get("visual_asset_id"),
                                 "planned_route": s["planned_type"],
                                 "route_candidates": s.get("route_candidates", []),
                                 "reason": s["review_reason"]})

    seen = {}
    for s in shots:
        vid = s.get("visual_asset_id")
        if vid:
            seen.setdefault(vid, []).append(s["shot_num"])
    for vid, nums in seen.items():
        if len(nums) > 1:
            needs_review.append({"shot": nums[0], "visual_asset_id": vid,
                                 "reason": f"visual_asset_id {vid} claimed by shots "
                                           f"{nums} — artwork identity must be unique"})

    for f in outstanding:
        needs_review.append({
            "shot": f.get("shot"), "file": f.get("file"),
            "visual_asset_id": f["visual_asset_id"],
            "planned_route": f["planned_route"],
            "reason": f"unresolved route failure from {f['failed_at']}: {f['reason']}",
        })

    types = Counter(s["type"] or route_images.UNCLASSIFIED for s in shots)
    host_shots = [s for s in shots if s["type"] == "HOST"]
    host_pct = round(100 * len(host_shots) / len(shots), 1) if shots else 0.0

    if channel_error:
        needs_review.append({"shot": None, "reason": channel_error})

    paid = [s for s in shots if s["type"] in PAID_TYPES]
    price = _pricing("episode_shot", context)
    cost = ({"unit_usd": price["cost_usd"], "shots": len(paid),
             "estimate_usd": round(price["cost_usd"] * len(paid), 2),
             "basis": price}
            if price else
            {"shots": len(paid),
             "estimate_usd": None,
             "basis": "no image_pricing entry in the channel pack — any figure "
                      "here would be invented, so none is given"})

    return {
        "schema_version": generation_gate.PLAN_SCHEMA_VERSION,
        "project": project_dir.name,
        "generated_by": "plan_visuals.py",
        "read_only": True,
        # A plan is a point-in-time artifact: approval attaches to the specific
        # run a human read, identified here. Without this, editing the plan by
        # hand and re-running the planner restored the old approval, because the
        # bytes round-tripped back to the approved ones.
        "plan_id": uuid.uuid4().hex,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Dispatch executes this plan, not the prompts file. The hash is what
        # proves the plan still describes the routing input it was built from.
        "inputs": {
            "prompts_sha256": _sha(prompts) if prompts.exists() else None,
            "manifest_sha256": _sha(manifest_path) if manifest_path.exists() else None,
        },
        # Which channel's DNA, character and voice this plan was built under.
        # Bound by the gate and copied into the approval, so a plan for one
        # channel can never be approved or dispatched under another, and a change
        # to either invalidates work approved before it.
        "channel": context.plan_binding() if context else None,
        "failure_revision": route_failures.revision(project_dir),
        "auto_resolved_failures": [f["visual_asset_id"] for f in auto_resolved],
        "identity": {
            "state": manifest.get("identity_state"),
            "reasons": manifest.get("identity_reasons", []),
            "gate_blockers": gate.blockers,
        },
        "manifest_identity": {
            "scenes": len(scenes),
            "shot_instance_ids": sorted(s.get("shot_instance_id") for s in scenes),
        },
        "shots": [
            {
                # persistent identity first — this is what dispatch and any
                # failure record key on
                "visual_asset_id": s.get("visual_asset_id"),
                "source_ids": s.get("source_ids", []),
                "shot_instance_id": s.get("shot_instance_id"),
                "scene_id": s.get("scene_id"),          # display order only
                "shot": s["shot_num"],                  # readability only
                "file": s["file"],
                # executable routing fields
                "visual_type": s["type"],
                "planned_type": s["planned_type"],
                "route_candidates": s.get("route_candidates", []),
                "map_args": s.get("map_args", ""),
                "chart_args": s.get("chart_args", ""),
                "narration": s.get("narration", ""),
                "pose_id": s.get("pose_id", ""),
                "scene_bound": s.get("scene_bound", False),
                "host_present": s["type"] == "HOST",
                "paid": s["type"] in PAID_TYPES,
                "needs_review": bool(s.get("needs_review")),
                "route_args_sha256": route_failures.route_args_digest(
                    {**s, "visual_type": s["type"]}),
            }
            for s in shots
        ],
        "mix": dict(sorted(types.items())),
        "host_presence_pct": host_pct,
        "paid_generation": cost,
        "needs_review": needs_review,
    }


def render_md(plan: dict) -> str:
    """Deterministic render of the plan JSON. The human reads this.

    Deterministic so approve_checkpoint.py can re-render it and refuse unless the
    file on disk matches byte-for-byte — otherwise a human could review a document
    that no longer describes the plan the gate will execute.
    """
    L = [f"# Visual plan — {plan['project']}", ""]
    L += [f"- **plan id**: `{plan.get('plan_id')}`",
          f"- **generated**: {plan.get('generated_at')}",
          f"- **schema**: {plan.get('schema_version')}",
          f"- **routing input**: `{str(plan.get('inputs', {}).get('prompts_sha256'))[:16]}…`",
          f"- **failure revision**: {plan.get('failure_revision')}"]
    ch = plan.get("channel")
    if ch:
        L += [f"- **channel**: `{ch['channel_id']}` (DNA v{ch['channel_dna_version']}, "
              f"pack `{str(ch['channel_json_sha256'])[:16]}…`)",
              f"- **character spec**: `{str(ch.get('character_spec_sha256'))[:16]}…`",
              f"- **voice profile**: "
              + (f"`{str(ch['voice_profile_sha256'])[:16]}…`"
                 if ch.get("voice_profile_sha256")
                 else "_none approved — paid generation is blocked until one is_")]
    else:
        L += ["- **channel**: _not resolved_ — this plan cannot be approved"]
    L += [""]
    ident = plan["identity"]
    L += [f"**Identity**: `{ident['state']}`"]
    for r in ident["reasons"]:
        L.append(f"- {r}")
    if ident["gate_blockers"]:
        L += ["", "**Identity gate blockers:**"]
        L += [f"- {b}" for b in ident["gate_blockers"]]
    L += ["", "## Mix", "", "| visual_type | shots |", "|---|---|"]
    for ty, n in plan["mix"].items():
        L.append(f"| {ty} | {n} |")
    L += ["", f"Host presence: **{plan['host_presence_pct']}%** "
              f"(target 25–30%, soft ceiling 35%)", ""]
    c = plan["paid_generation"]
    L += ["## Paid generation", "",
          f"- shots that would spend: **{c['shots']}**",
          "- estimate: " + (f"**${c['estimate_usd']}**" if c["estimate_usd"] is not None
                            else "_not priced_ — " + str(c["basis"])), ""]
    if plan.get("auto_resolved_failures"):
        L += ["## Failures resolved by a route change", ""]
        L += [f"- `{v}`" for v in plan["auto_resolved_failures"]]
        L += [""]
    L += ["## Needs review", ""]
    if not plan["needs_review"]:
        L.append("_none_")
    for r in plan["needs_review"]:
        vid = f" `{r['visual_asset_id']}`" if r.get("visual_asset_id") else ""
        L.append(f"- shot {r.get('shot')}{vid}: {r['reason']}")
    L += ["", "## Shots", "",
          "| shot | visual_asset_id | file | type | host | pose |",
          "|---|---|---|---|---|---|"]
    for s in plan["shots"]:
        L.append(f"| {s['shot']} | `{s['visual_asset_id'] or '—'}` | `{s['file']}` | "
                 f"{s['visual_type']} | {'yes' if s['host_present'] else ''} | "
                 f"{s['pose_id'] or ''} |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--stdout", action="store_true", help="Print the report instead of writing")
    args = ap.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = (PIPELINE_DIR / args.project).resolve()
    if not project_dir.is_dir():
        print(f"project folder not found: {project_dir}", file=sys.stderr)
        return 2

    plan = build_plan(project_dir)
    md = render_md(plan)
    if args.stdout:
        print(md)
    else:
        (project_dir / PLAN_JSON).write_text(
            json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        (project_dir / PLAN_MD).write_text(md, encoding="utf-8")
        print(f"  wrote {PLAN_JSON} and {PLAN_MD} in {project_dir.name}")

    blocked = plan["identity"]["gate_blockers"] or plan["needs_review"]
    if blocked:
        print(f"\n  BLOCKED: {len(plan['identity']['gate_blockers'])} identity "
              f"blocker(s), {len(plan['needs_review'])} item(s) needing review. "
              f"No paid generation may run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
