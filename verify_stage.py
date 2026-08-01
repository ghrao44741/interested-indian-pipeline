"""
verify_stage.py — run a stage's contract check without the orchestrator.

Why this exists (see PIPELINE_CONTRACT.md): the review agents only ever fire
inside OrchestratorAgent._run_with_review(). Every episode this channel has
produced was built with direct script calls, so not one review ever ran — and
checks that would have caught the NEET-UG mispronunciation, the caption
mishearings and the stale-voice override sat there unused.

A contract only the orchestrator enforces is not a contract. This runs the same
ReviewAgent checks standalone, against any project, with no episode_state.json
required.

Usage:
    python verify_stage.py --project pilot_neet_scandal --stage split
    python verify_stage.py --project pilot_neet_scandal --all

Exit code 0 = contract met, 1 = violated. Suitable for a pre-publish gate.
"""
import argparse
import json
import sys
from pathlib import Path

from pipeline_agents import ReviewAgent, _pick_production_script

PIPELINE_DIR = Path(__file__).parent

# Stages whose artifacts exist on disk and can be checked after the fact.
CHECKABLE = ["voice", "split", "prompts", "images", "stitch"]


def build_context(project_dir: Path) -> dict:
    """Reconstruct just enough of the orchestrator's context to run reviews.

    Prefers episode_state.json when present, but deliberately works without it —
    projects driven by direct script calls have no state file, and those are
    exactly the ones that have never been checked.
    """
    ctx = {"project_dir": str(project_dir)}
    state_path = project_dir / "episode_state.json"
    if state_path.exists():
        try:
            ctx.update(json.loads(state_path.read_text(encoding="utf-8")).get("data", {}))
        except (json.JSONDecodeError, OSError):
            pass

    script = ctx.get("script_path")
    if not script or not Path(script).exists():
        found = _pick_production_script(project_dir)
        if found:
            ctx["script_path"] = str(found)

    manifest = project_dir / "manifest.json"
    if manifest.exists() and not ctx.get("title"):
        try:
            ctx["title"] = json.loads(manifest.read_text(encoding="utf-8")).get("title", "")
        except (json.JSONDecodeError, OSError):
            pass
    return ctx


def run(project_dir: Path, stages: list) -> int:
    agent = ReviewAgent(None)
    ctx = build_context(project_dir)
    print(f"project : {project_dir.name}")
    print(f"script  : {Path(ctx.get('script_path', '?')).name}")

    violated = []
    for stage in stages:
        print(f"\n{'─' * 58}\nSTAGE: {stage}")
        try:
            result = agent.review(stage, ctx)
        except Exception as e:
            print(f"  ✗ check errored: {e}")
            violated.append(stage)
            continue

        status = "PASS" if result.passed else "FAIL"
        print(f"  {status}  score {result.score}/10")
        for issue in result.issues:
            print(f"    ✗ {issue}")
        for rec in result.recommendations[:5]:
            print(f"    → {rec}")
        # Issues count as a contract violation even when the reviewer is
        # advisory (several return passed=True unconditionally by design).
        if not result.passed or result.issues:
            violated.append(stage)

    print(f"\n{'=' * 58}")
    if violated:
        print(f"CONTRACT VIOLATED: {', '.join(violated)}")
        return 1
    print("CONTRACT MET")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Verify a project against the pipeline stage contract")
    ap.add_argument("--project", required=True, help="Project folder name or path")
    ap.add_argument("--stage", choices=CHECKABLE, help="Single stage to verify")
    ap.add_argument("--all", action="store_true", help="Verify every checkable stage")
    args = ap.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = PIPELINE_DIR / args.project
    if not project_dir.exists():
        print(f"No such project: {project_dir}")
        return 2
    if not args.stage and not args.all:
        ap.error("pass --stage <name> or --all")

    return run(project_dir, CHECKABLE if args.all else [args.stage])


if __name__ == "__main__":
    sys.exit(main())
