"""
generate_chapters.py — Chapter Timestamp Generator for The Interested Indian

Reads manifest.json to get scene timecodes, then uses Claude to group scenes
into 6–8 logical chapters with meaningful names. Output is the YouTube chapter
format that goes at the top of the video description:

    00:00 Introduction
    01:45 The Finance Commission Formula
    04:30 Why Kerala Loses Out
    ...

The chapters.txt output can be pasted directly into the YouTube description.

Usage:
    python generate_chapters.py --project ep01
    python generate_chapters.py --project ep01 --out ep01/chapters.txt
    python generate_chapters.py --project ep01 --num-chapters 8

Requires:
    ANTHROPIC_API_KEY environment variable
    manifest.json inside the project folder
"""

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("❌ anthropic not found.\n   Run: pip install anthropic --break-system-packages")
    sys.exit(1)

PIPELINE_DIR = Path(__file__).parent


def _fmt_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS (omit HH if < 1h) or MM:SS."""
    s = math.floor(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _load_manifest(project_dir: Path) -> list[dict]:
    """Return list of {scene_id, start, narration_text} from manifest.json."""
    path = project_dir / "manifest.json"
    if not path.exists():
        print(f"❌ manifest.json not found at {path}")
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))

    scenes = []
    raw_scenes = data if isinstance(data, list) else data.get("scenes", [])
    for sc in raw_scenes:
        scene_id   = sc.get("scene_id", "")
        start      = float(sc.get("start", sc.get("start_time", 0)))
        text       = sc.get("narration", sc.get("narration_text", sc.get("text", "")))
        scenes.append({"scene_id": scene_id, "start": start, "text": text})

    if not scenes:
        print("❌ No scenes found in manifest.json")
        sys.exit(1)

    scenes.sort(key=lambda s: s["start"])
    return scenes


def _scene_summary_for_prompt(scenes: list[dict]) -> str:
    """Compact representation of all scenes for the Claude prompt."""
    lines = []
    for sc in scenes:
        ts   = _fmt_timestamp(sc["start"])
        text = sc["text"].strip()[:120].replace("\n", " ")
        lines.append(f"[{ts}] {sc['scene_id']}: {text}")
    return "\n".join(lines)


def _generate_chapters(client: anthropic.Anthropic, scenes: list[dict], num_chapters: int, topic: str = "") -> list[tuple[float, str]]:
    """Call Claude to group scenes into chapters. Returns list of (start_seconds, chapter_name)."""

    scene_block = _scene_summary_for_prompt(scenes)
    total_dur   = scenes[-1]["start"]

    prompt = f"""You are creating YouTube chapter timestamps for a video about Indian history/policy.

Video topic: {topic or "Indian administrative/political history"}
Total duration: {_fmt_timestamp(total_dur)}
Number of chapters required: {num_chapters} (first must always be at 00:00)

Here are all the scenes in order (timestamp: scene_id: first 120 chars of narration):

{scene_block}

Group these scenes into exactly {num_chapters} logical chapters based on the narrative arc.
Each chapter should represent a distinct phase of the story (e.g., historical context, the policy itself, unintended consequences, modern reality).

Respond with ONLY a JSON array — no other text:
[
  {{"start": 0, "name": "Introduction"}},
  {{"start": 92.5, "name": "The Finance Commission Formula"}},
  ...
]

Rules:
- "start" must exactly match a scene's start time from the list above (do not invent timestamps)
- First item must have start=0
- Chapter names: 3-6 words, title case, no quotes or special characters
- Names should be specific to THIS video, not generic ("Chapter 1" is not acceptable)
- Spread chapters evenly — no chapter shorter than 60 seconds unless it's the intro
"""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system="You are a YouTube content organizer. Respond only with valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Extract JSON array
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        print("⚠ Claude did not return valid JSON — using evenly spaced fallback chapters")
        return _fallback_chapters(scenes, num_chapters)

    try:
        chapters_raw = json.loads(match.group(0))
        chapters = [(float(c["start"]), str(c["name"])) for c in chapters_raw]
        return chapters
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"⚠ Could not parse Claude's chapter JSON ({e}) — using fallback")
        return _fallback_chapters(scenes, num_chapters)


def _fallback_chapters(scenes: list[dict], num_chapters: int) -> list[tuple[float, str]]:
    """Evenly divide scenes if Claude fails."""
    step  = max(1, len(scenes) // num_chapters)
    picks = scenes[::step][:num_chapters]
    names = ["Introduction", "Context", "The Policy", "Regional Impact",
             "Unintended Consequences", "Modern Reality", "Who Benefits", "Closing Thoughts"]
    return [(s["start"], names[i % len(names)]) for i, s in enumerate(picks)]


def _format_chapters(chapters: list[tuple[float, str]]) -> str:
    """Format chapters as YouTube-ready timestamp block."""
    lines = []
    for start, name in chapters:
        ts = _fmt_timestamp(start)
        lines.append(f"{ts} {name}")
    return "\n".join(lines)


def _read_topic(project_dir: Path) -> str:
    """Try to read episode topic from episode_state.json."""
    p = project_dir / "episode_state.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("data", {}).get("title", "")
        except Exception:
            pass
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project",      required=True, help="Episode folder (e.g. ep01)")
    parser.add_argument("--out",          default=None,  help="Output file path (default: <project>/chapters.txt)")
    parser.add_argument("--num-chapters", type=int, default=7, dest="num_chapters",
                        help="Number of chapters to generate (default: 7)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set")
        sys.exit(1)

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = PIPELINE_DIR / args.project
    if not project_dir.exists():
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    out_path = Path(args.out) if args.out else project_dir / "chapters.txt"

    print(f"  Project: {project_dir.name}")
    print(f"  Loading manifest...")
    scenes = _load_manifest(project_dir)
    print(f"  {len(scenes)} scenes loaded, total {_fmt_timestamp(scenes[-1]['start'])}")

    topic = _read_topic(project_dir)
    if topic:
        print(f"  Topic  : {topic}")

    client = anthropic.Anthropic(api_key=api_key)
    print(f"  Generating {args.num_chapters} chapters with Claude...")
    chapters = _generate_chapters(client, scenes, args.num_chapters, topic)

    formatted = _format_chapters(chapters)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(formatted, encoding="utf-8")

    print(f"\n{'─'*50}")
    print(formatted)
    print(f"{'─'*50}")
    print(f"\n✓ Chapters saved → {out_path}")
    print("  Paste this block at the TOP of your YouTube description.")


if __name__ == "__main__":
    main()
