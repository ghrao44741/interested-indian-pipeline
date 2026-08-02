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
import json
import sys
from collections import Counter
from pathlib import Path

import generation_gate
import route_images
import source_ids

PIPELINE_DIR = Path(__file__).parent
PLAN_JSON = "visual_plan.json"
PLAN_MD = "visual_plan.md"

# Which declared TYPEs cost money. MAP and CHART render locally (geopandas,
# matplotlib); PHOTO is a free-tier fetch; HOST composites an already-paid-for
# pose asset. Only AI generation spends per shot.
PAID_TYPES = {"CARTOON", "REENACTMENT"}


def _pricing(entry: str) -> dict | None:
    cfg = PIPELINE_DIR / "channel_config.json"
    if not cfg.exists():
        return None
    return (json.loads(cfg.read_text(encoding="utf-8"))
            .get("image_pricing", {}).get(entry))


def build_plan(project_dir: Path) -> dict:
    prompts = project_dir / route_images.PROMPTS_FILE
    shots = route_images.parse_shots(prompts) if prompts.exists() else []

    manifest_path = project_dir / "manifest.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {})
    scenes = manifest.get("scenes", [])

    gate = generation_gate.require_generation_ready(
        project_dir, "visual planning", require_visual_plan=False,
        raise_on_block=False)

    needs_review = []
    if not prompts.exists():
        needs_review.append({"shot": None,
                             "reason": f"{route_images.PROMPTS_FILE} not found"})
    for s in shots:
        if not s["type"]:
            needs_review.append({"shot": s["shot_num"], "file": s["file"],
                                 "reason": "no TYPE declared and no keyword match"})
        if s.get("pose_id") and s["pose_id"] not in route_images.approved_pose_ids():
            needs_review.append({"shot": s["shot_num"], "file": s["file"],
                                 "reason": f"HOST_POSE {s['pose_id']!r} is not an "
                                           f"approved, generically compositable pose"})

    types = Counter(s["type"] or "UNCLASSIFIED" for s in shots)
    host_shots = [s for s in shots if s["type"] == "HOST"]
    host_pct = round(100 * len(host_shots) / len(shots), 1) if shots else 0.0

    paid = [s for s in shots if s["type"] in PAID_TYPES]
    price = _pricing("episode_shot")
    cost = ({"unit_usd": price["cost_usd"], "shots": len(paid),
             "estimate_usd": round(price["cost_usd"] * len(paid), 2),
             "basis": price}
            if price else
            {"shots": len(paid),
             "estimate_usd": None,
             "basis": "no image_pricing entry in channel_config.json — any figure "
                      "here would be invented, so none is given"})

    return {
        "project": project_dir.name,
        "generated_by": "plan_visuals.py",
        "read_only": True,
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
            {"shot": s["shot_num"], "file": s["file"], "visual_type": s["type"],
             "host_present": s["type"] == "HOST", "pose_id": s.get("pose_id"),
             "scene_bound": s.get("scene_bound", False),
             "paid": s["type"] in PAID_TYPES}
            for s in shots
        ],
        "mix": dict(sorted(types.items())),
        "host_presence_pct": host_pct,
        "paid_generation": cost,
        "needs_review": needs_review,
    }


def render_md(plan: dict) -> str:
    L = [f"# Visual plan — {plan['project']}", ""]
    ident = plan["identity"]
    L += [f"**Identity**: `{ident['state']}`"]
    for r in ident["reasons"]:
        L.append(f"- {r}")
    if ident["gate_blockers"]:
        L += ["", "**Generation gate blockers:**"]
        L += [f"- {b}" for b in ident["gate_blockers"]]
    L += ["", "## Mix", "", "| visual_type | shots |", "|---|---|"]
    for t, n in plan["mix"].items():
        L.append(f"| {t} | {n} |")
    L += ["", f"Host presence: **{plan['host_presence_pct']}%** "
              f"(target 25–30%, soft ceiling 35%)", ""]
    c = plan["paid_generation"]
    L += ["## Paid generation", "",
          f"- shots that would spend: **{c['shots']}**",
          f"- estimate: " + (f"**${c['estimate_usd']}**" if c["estimate_usd"] is not None
                             else "_not priced_ — " + str(c["basis"])), ""]
    L += ["## Needs review", ""]
    if not plan["needs_review"]:
        L.append("_none_")
    for r in plan["needs_review"]:
        L.append(f"- shot {r.get('shot')}: {r['reason']}")
    L += ["", "## Shots", "", "| shot | file | type | host | pose |", "|---|---|---|---|---|"]
    for s in plan["shots"]:
        L.append(f"| {s['shot']} | `{s['file']}` | {s['visual_type']} | "
                 f"{'yes' if s['host_present'] else ''} | {s['pose_id'] or ''} |")
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
