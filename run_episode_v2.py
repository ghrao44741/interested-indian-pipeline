"""
run_episode_v2.py — Multi-agent episode runner for The Interested Indian.

Three agents work together:
  OrchestratorAgent  Routes the pipeline, decides on failures, human checkpoints only when needed.
  ReviewAgent        Reviews every stage output before proceeding (rule-based + Claude).
  ResearchAgent      Web-searches for verified facts before script generation.

Usage:
    python run_episode_v2.py --project ep02
    python run_episode_v2.py --project ep02 --from-stage script
    python run_episode_v2.py --project ep02 --status

Requires:
    ANTHROPIC_API_KEY  environment variable
    pip install anthropic duckduckgo-search pydub --break-system-packages
    ffmpeg on PATH
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("❌ anthropic not found.\n   Run: pip install anthropic --break-system-packages")
    sys.exit(1)

from pipeline_agents import (
    PIPELINE_DIR,
    STAGE_ORDER,
    OrchestratorAgent,
    ReviewAgent,
    ResearchAgent,
)


def show_status(project_dir: Path):
    import json
    state_path = project_dir / "episode_state.json"
    if not state_path.exists():
        print(f"  No state found for {project_dir.name} — not started yet.")
        return
    state = json.loads(state_path.read_text(encoding="utf-8"))
    completed = state.get("completed", [])
    current   = state.get("stage", "topics")
    labels = {
        "topics":    "Stage 1  — Topic Ideas",
        "script":    "Stage 2  — Narration Script",
        "voice":     "Stage 3a — Voice Generation",
        "split":     "Stage 3b — Scene Splitting",
        "prompts":   "Stage 3c — Image Prompts",
        "images":    "Stage 3d — Image Generation",
        "stitch":    "Stage 4  — Video Stitch",
        "metadata":  "Stage 5  — YouTube Metadata",
        "thumbnail": "Stage 6  — Thumbnail",
        "chapters":  "Stage 7  — Chapter Timestamps",
        "upload":    "Stage 8  — YouTube Upload",
    }
    print(f"\n  Status: {project_dir.name}")
    print(f"  {'─'*40}")
    for stage in STAGE_ORDER:
        icon = "✓" if stage in completed else ("▶" if stage == current else " ")
        print(f"  {icon}  {labels[stage]}")
    data = state.get("data", {})
    if data.get("title"):
        print(f"\n  Title: {data['title']}")
    print()


def main():
    parser = argparse.ArgumentParser(description="The Interested Indian — Multi-Agent Pipeline")
    parser.add_argument("--project",    required=True,  help="Episode folder (e.g. ep02)")
    parser.add_argument("--from-stage", default=None,   help="Force-start from a stage",
                        choices=STAGE_ORDER)
    parser.add_argument("--status",     action="store_true", help="Show pipeline status and exit")
    args = parser.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = PIPELINE_DIR / project_dir
    project_dir.mkdir(parents=True, exist_ok=True)

    if args.status:
        show_status(project_dir)
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY environment variable not set.")
        print("   Set it with: set ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    review_agent   = ReviewAgent(client)
    research_agent = ResearchAgent(client)
    orchestrator   = OrchestratorAgent(client, project_dir, review_agent, research_agent)

    orchestrator.run_pipeline(from_stage=args.from_stage)


if __name__ == "__main__":
    main()
