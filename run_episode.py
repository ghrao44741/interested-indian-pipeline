"""
run_episode.py — Orchestrator for The Interested Indian episode pipeline.

Implements the full 4-stage Mega Prompt pipeline with human checkpoints.
State is saved to {project}/episode_state.json so the run can be interrupted
and resumed at any stage.

Usage:
    python run_episode.py --project ep02
    python run_episode.py --project ep02 --from-stage voice   # resume from a stage
    python run_episode.py --project ep02 --status             # show current state

Stages (in order):
    topics    → Claude generates 5 viral topic ideas      [CHECKPOINT: you pick one]
    script    → Claude writes the full narration script   [CHECKPOINT: you approve]
    voice     → Edge TTS generates narration.mp3          [AUTO]
    split     → WhisperX splits into scene audio files    [AUTO]
    prompts   → Claude generates image prompts            [AUTO]
    images    → Flux/Grok generates images (review loop)  [AUTO]
    stitch    → ffmpeg stitches the final video           [AUTO]
    metadata  → Claude generates YouTube metadata         [AUTO]
    upload    → [future] YouTube Data API upload          [CHECKPOINT: you approve]

Requires:
    ANTHROPIC_API_KEY  env var
    REPLICATE_API_TOKEN or XAI_API_KEY  env var (for image generation)
    ffmpeg  on PATH
    pip install anthropic edge-tts
"""

import argparse
import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("❌ anthropic not found. Run: pip install anthropic --break-system-packages")
    sys.exit(1)

# ── Paths ──────────────────────────────────────────────────────────────────────

PIPELINE_DIR   = Path(__file__).parent           # interested_indian_pipeline/
SHORTS_DIR     = PIPELINE_DIR.parent / "Aeonium_Glow" / "shorts_pipeline2"
COMMON_DIR     = PIPELINE_DIR / "common"

STAGE_ORDER = ["topics", "script", "voice", "split", "prompts", "images", "stitch", "metadata"]

# ── Stage display names ────────────────────────────────────────────────────────

STAGE_LABELS = {
    "topics":   "Stage 1 — Topic Ideas",
    "script":   "Stage 2 — Narration Script",
    "voice":    "Stage 3a — Voice Generation",
    "split":    "Stage 3b — Scene Splitting",
    "prompts":  "Stage 3c — Image Prompts",
    "images":   "Stage 3d — Image Generation",
    "stitch":   "Stage 4 — Video Stitch",
    "metadata": "Stage 5 — YouTube Metadata",
}

# ── Mega Prompt system context ─────────────────────────────────────────────────

CHANNEL_DNA = """You are a viral educational YouTube video creation engine for "The Interested Indian".

CHANNEL KNOWLEDGE BASE:
- Niche: Indian history, administrative evolution, political geography, economic history, regional geopolitics.
- Format: 12–18 minute analytical video essay. Calm, objective, grounded voice.
- Hook Formula: Opens with counter-intuitive administrative anomaly or policy paradox → reframes through historical/institutional mechanics → high-density deep dive.
- Script Rhythm: Short sentence. Direct factual observation. One longer sentence explaining structural cause-and-effect. Short sentence. Question every 4–6 sentences.
- Narrative Arc: Hook → Legislative/Historical Precedent → Geographic/Administrative Breakdown → Unintended Policy Consequences → Modern Economic Reality → Final Synthesis echoing the opening paradox.
- STRICT NEGATIVE RULES: ZERO corporate clichés (unleash, unlock, dive into, delve, game-changer, tapestry). No emotional sensationalism. Calm matter-of-fact analysis only.

PROVEN VIRAL TOPIC ANGLES:
1. "Every [Region/State] Explained" — breaks down macro-regions into administrative engines.
2. "How One [Policy/Law] Accidentally Stunted/Built ___" — traces macro-outcomes to a single legislative moment.
3. "The Historical Divisions of ___ That Still Dictate Its Politics" — state borders, colonial partitions, regional dynamics.
4. "Why North and South/East and West ___ Operate Completely Differently" — comparative federalist analysis.
5. "The Mechanics of ___ Explained" — procedural breakdown of major historical shifts."""

# ── State management ───────────────────────────────────────────────────────────

def load_state(project_dir: Path) -> dict:
    state_path = project_dir / "episode_state.json"
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {"stage": "topics", "completed": [], "data": {}}


def save_state(project_dir: Path, state: dict):
    state_path = project_dir / "episode_state.json"
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def mark_complete(project_dir: Path, state: dict, stage: str):
    if stage not in state["completed"]:
        state["completed"].append(stage)
    idx = STAGE_ORDER.index(stage)
    if idx + 1 < len(STAGE_ORDER):
        state["stage"] = STAGE_ORDER[idx + 1]
    save_state(project_dir, state)


# ── UI helpers ─────────────────────────────────────────────────────────────────

def header(text: str):
    print(f"\n{'═'*60}")
    print(f"  {text}")
    print(f"{'═'*60}")


def checkpoint(prompt: str) -> str:
    print(f"\n{'─'*60}")
    print(f"  ⏸  CHECKPOINT")
    print(f"{'─'*60}")
    return input(f"  {prompt}\n  → ").strip()


def run_cmd(cmd: list, cwd: Path = None, label: str = ""):
    if label:
        print(f"  Running: {label}")
    result = subprocess.run(cmd, cwd=cwd or PIPELINE_DIR, capture_output=False, text=True)
    if result.returncode != 0:
        print(f"  ✗ Command failed (exit {result.returncode})")
        raise RuntimeError(f"Command failed: {' '.join(str(c) for c in cmd)}")
    return result


# ── Stage 1: Topics ────────────────────────────────────────────────────────────

def stage_topics(project_dir: Path, state: dict, client: anthropic.Anthropic):
    header(STAGE_LABELS["topics"])

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=CHANNEL_DNA,
        messages=[{
            "role": "user",
            "content": (
                "Generate exactly 5 viral topic ideas for the channel. "
                "Output ONLY a markdown table with columns: # | Video Title | Core Institutional Focus. "
                "No preamble, no explanation, just the table."
            )
        }]
    )
    ideas_text = response.content[0].text.strip()
    print(f"\n{ideas_text}\n")

    state["data"]["topic_ideas"] = ideas_text
    save_state(project_dir, state)

    choice = checkpoint("Which idea do you want to develop? Reply with a number (1–5), or type your own title:")

    state["data"]["topic_choice"] = choice
    mark_complete(project_dir, state, "topics")
    print(f"\n  ✓ Topic selected: {choice}")


# ── Stage 2: Script ────────────────────────────────────────────────────────────

def stage_script(project_dir: Path, state: dict, client: anthropic.Anthropic):
    header(STAGE_LABELS["script"])

    topic_choice = state["data"].get("topic_choice", "")
    topic_ideas  = state["data"].get("topic_ideas", "")

    print("  Generating script (2,000–2,800 words)...")

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=8192,
        system=CHANNEL_DNA,
        messages=[{
            "role": "user",
            "content": (
                f"Topic ideas presented:\n{topic_ideas}\n\n"
                f"User selected: {topic_choice}\n\n"
                "Write the full narration script. Rules:\n"
                "- 2,000–2,800 words\n"
                "- Pure narration only — no headers, no bullet points, no stage directions\n"
                "- Voice: calm, objective, analytical\n"
                "- Rhythm: short sentence. Short sentence. One longer sentence. Short sentence. Question every 4–6 sentences.\n"
                "- Include specific dates, acts of parliament, district boundaries woven naturally\n"
                "- Open with an administrative paradox or geographic anomaly\n"
                "- Close with a line that directly echoes the opening concept\n"
                "Output the full video title on line 1 (prefixed with TITLE:), then the script."
            )
        }]
    )

    raw = response.content[0].text.strip()
    lines = raw.splitlines()
    title_line = next((l for l in lines if l.startswith("TITLE:")), None)
    if title_line:
        title = title_line.replace("TITLE:", "").strip()
        script_text = "\n".join(l for l in lines if not l.startswith("TITLE:")).strip()
    else:
        title = topic_choice
        script_text = raw

    # Derive slug
    slug = title.lower()
    for ch in " -–—/\\,:;!?()[]{}\"'":
        slug = slug.replace(ch, "_")
    slug = "_".join(w for w in slug.split("_") if w)[:50]

    script_path = project_dir / f"script_{slug}.txt"
    script_path.write_text(script_text, encoding="utf-8")

    word_count = len(script_text.split())
    print(f"\n  VIDEO TITLE: {title}")
    print(f"  Words: {word_count}")
    print(f"  Script saved: {script_path.name}")
    print(f"\n  Preview (first 3 lines):")
    for line in script_text.splitlines()[:3]:
        if line.strip():
            print(f"    {line.strip()}")

    state["data"]["title"]       = title
    state["data"]["slug"]        = slug
    state["data"]["script_path"] = str(script_path)
    save_state(project_dir, state)

    answer = checkpoint(
        "Script ready. Options:\n"
        "  [enter]  Accept and continue to voice generation\n"
        "  edit     Open script in notepad for manual edits, then press enter\n"
        "  redo     Regenerate script"
    ).lower()

    if answer == "edit":
        subprocess.Popen(["notepad.exe", str(script_path)])
        input("  Press ENTER when you have finished editing the script...")
    elif answer == "redo":
        print("  Regenerating...")
        return stage_script(project_dir, state, client)

    mark_complete(project_dir, state, "script")
    print(f"\n  ✓ Script approved.")


# ── Stage 3a: Voice ────────────────────────────────────────────────────────────

def stage_voice(project_dir: Path, state: dict, client: anthropic.Anthropic):
    header(STAGE_LABELS["voice"])

    script_path = Path(state["data"]["script_path"])
    if not script_path.exists():
        print(f"  ❌ Script not found: {script_path}")
        sys.exit(1)

    gen_audio = PIPELINE_DIR / "generate_source_audio.py"
    if not gen_audio.exists():
        print(f"  ❌ generate_source_audio.py not found in {PIPELINE_DIR}")
        sys.exit(1)

    print("  Generating narration via Edge TTS...")
    run_cmd(
        [sys.executable, str(gen_audio), "--script", str(script_path),
         "--out-dir", str(project_dir / "source_audio")],
        label="generate_source_audio.py"
    )

    mark_complete(project_dir, state, "voice")
    print("  ✓ Narration generated.")


# ── Stage 3b: Split ────────────────────────────────────────────────────────────

def stage_split(project_dir: Path, state: dict, client: anthropic.Anthropic):
    header(STAGE_LABELS["split"])

    split_script = SHORTS_DIR / "auto_split_scenes_v1_stage3_export.py"
    if not split_script.exists():
        # Fallback to any available split script
        for name in ["auto_split_scenes.py", "auto_split_scenes_v1_stage3_export.py"]:
            candidate = SHORTS_DIR / name
            if candidate.exists():
                split_script = candidate
                break
    if not split_script.exists():
        print(f"  ❌ auto_split_scenes script not found in {SHORTS_DIR}")
        sys.exit(1)

    print("  Running WhisperX scene splitter...")
    run_cmd(
        [sys.executable, str(split_script),
         "--project", str(project_dir),
         "--video-type", "LongVideo"],
        cwd=SHORTS_DIR,
        label=split_script.name
    )

    mark_complete(project_dir, state, "split")
    print("  ✓ Scenes split, manifest.json generated.")


# ── Stage 3c: Image Prompts ────────────────────────────────────────────────────

def stage_prompts(project_dir: Path, state: dict, client: anthropic.Anthropic):
    header(STAGE_LABELS["prompts"])

    gen_prompts = PIPELINE_DIR / "generate_image_prompts.py"
    if not gen_prompts.exists():
        print(f"  ❌ generate_image_prompts.py not found")
        sys.exit(1)

    print("  Generating image prompts via Claude API...")
    run_cmd(
        [sys.executable, str(gen_prompts), "--project", str(project_dir)],
        label="generate_image_prompts.py"
    )

    mark_complete(project_dir, state, "prompts")
    print("  ✓ Image prompts generated.")


# ── Stage 3d: Images ──────────────────────────────────────────────────────────

def stage_images(project_dir: Path, state: dict, client: anthropic.Anthropic):
    header(STAGE_LABELS["images"])

    gen_images  = PIPELINE_DIR / "generate_images_flux.py"
    review      = PIPELINE_DIR / "review_images.py"

    if not gen_images.exists() or not review.exists():
        print("  ❌ generate_images_flux.py or review_images.py not found")
        sys.exit(1)

    # Initial generation
    print("  Generating images...")
    run_cmd([sys.executable, str(gen_images), "--project", str(project_dir)],
            label="generate_images_flux.py")

    # Review loop — keep going until 0 FAILs
    max_rounds = 5
    for round_num in range(1, max_rounds + 1):
        print(f"\n  Review round {round_num}...")
        run_cmd([sys.executable, str(review), "--project", str(project_dir)],
                label="review_images.py")

        report = project_dir / "review_report.md"
        if report.exists():
            text = report.read_text(encoding="utf-8")
            fail_line = next((l for l in text.splitlines() if "FAIL" in l and ":" in l), "")
            fail_count = int(fail_line.split(":")[1].strip()) if fail_line else -1
            print(f"  FAILs this round: {fail_count}")
            if fail_count == 0:
                print("  ✓ 0 FAILs — image review passed.")
                break
            print(f"  Regenerating {fail_count} FAIL shots...")
            run_cmd([sys.executable, str(gen_images), "--project", str(project_dir),
                     "--from-report", "--overwrite"],
                    label="generate_images_flux.py --from-report")
        else:
            print("  ⚠ No review report found.")
            break
    else:
        print(f"  ⚠ Still FAILs after {max_rounds} rounds — proceeding anyway.")

    mark_complete(project_dir, state, "images")


# ── Stage 4: Stitch ────────────────────────────────────────────────────────────

def stage_stitch(project_dir: Path, state: dict, client: anthropic.Anthropic):
    header(STAGE_LABELS["stitch"])

    stitch = SHORTS_DIR / "stitch_video_longform.py"
    if not stitch.exists():
        print(f"  ❌ stitch_video_longform.py not found in {SHORTS_DIR}")
        sys.exit(1)

    print("  Stitching video (this takes a few minutes)...")
    run_cmd(
        [sys.executable, str(stitch), "--project", str(project_dir)],
        cwd=SHORTS_DIR,
        label="stitch_video_longform.py"
    )

    output_file = project_dir / "output" / f"{project_dir.name}_final.mp4"
    if output_file.exists():
        size_mb = output_file.stat().st_size // (1024*1024)
        print(f"  ✓ Video ready: {output_file.name}  ({size_mb} MB)")
    else:
        print(f"  ⚠ Expected output not found: {output_file}")

    mark_complete(project_dir, state, "stitch")


# ── Stage 5: Metadata ──────────────────────────────────────────────────────────

def stage_metadata(project_dir: Path, state: dict, client: anthropic.Anthropic):
    header(STAGE_LABELS["metadata"])

    script_path = Path(state["data"].get("script_path", ""))
    title       = state["data"].get("title", "")
    script_text = script_path.read_text(encoding="utf-8") if script_path.exists() else ""

    print("  Generating YouTube metadata...")

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        system=CHANNEL_DNA,
        messages=[{
            "role": "user",
            "content": (
                f"Video title: {title}\n\n"
                f"Script excerpt (first 800 words):\n{' '.join(script_text.split()[:800])}\n\n"
                "Generate YouTube metadata. Output exactly three sections:\n\n"
                "VIRAL VIDEO TITLE:\n[one title under 70 chars]\n\n"
                "VIDEO DESCRIPTION:\n[2-3 sentence hook, 3-4 sentence summary, subscribe line, 15-20 hashtags]\n\n"
                "VIRAL VIDEO TAGS:\n[25-40 comma-separated SEO tags]"
            )
        }]
    )

    metadata_text = response.content[0].text.strip()
    slug = state["data"].get("slug", project_dir.name)
    meta_path = project_dir / f"metadata_{slug}.txt"
    meta_path.write_text(metadata_text, encoding="utf-8")

    print(f"\n{metadata_text}\n")
    print(f"  ✓ Metadata saved: {meta_path.name}")

    state["data"]["metadata_path"] = str(meta_path)
    mark_complete(project_dir, state, "metadata")


# ── Status display ─────────────────────────────────────────────────────────────

def show_status(project_dir: Path, state: dict):
    header(f"Episode Status — {project_dir.name}")
    completed = state.get("completed", [])
    current   = state.get("stage", "topics")

    for stage in STAGE_ORDER:
        if stage in completed:
            status = "✓ done"
        elif stage == current:
            status = "▶ next"
        else:
            status = "  —"
        print(f"  {status}  {STAGE_LABELS[stage]}")

    print(f"\n  Data keys: {list(state.get('data', {}).keys())}")


# ── Entry point ────────────────────────────────────────────────────────────────

STAGE_FNS = {
    "topics":   stage_topics,
    "script":   stage_script,
    "voice":    stage_voice,
    "split":    stage_split,
    "prompts":  stage_prompts,
    "images":   stage_images,
    "stitch":   stage_stitch,
    "metadata": stage_metadata,
}


def main():
    parser = argparse.ArgumentParser(description="The Interested Indian — Episode Orchestrator")
    parser.add_argument("--project",    required=True, help="Episode folder (e.g. ep02)")
    parser.add_argument("--from-stage", default=None,  help="Force-start from a specific stage",
                        choices=STAGE_ORDER)
    parser.add_argument("--status",     action="store_true", help="Show current state and exit")
    args = parser.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = PIPELINE_DIR / project_dir
    project_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(project_dir)

    if args.status:
        show_status(project_dir, state)
        return

    if args.from_stage:
        state["stage"] = args.from_stage
        save_state(project_dir, state)
        print(f"  Resuming from: {args.from_stage}")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print(f"\n  The Interested Indian — Episode Orchestrator")
    print(f"  Project: {project_dir}")
    print(f"  Starting from stage: {state['stage']}")

    start_idx = STAGE_ORDER.index(state["stage"])

    for stage in STAGE_ORDER[start_idx:]:
        if stage in state.get("completed", []) and stage != state["stage"]:
            print(f"\n  ↷ Skipping {stage} (already complete)")
            continue
        STAGE_FNS[stage](project_dir, state, client)

    header("✓ Pipeline Complete")
    print(f"  Project: {project_dir}")
    output = project_dir / "output" / f"{project_dir.name}_final.mp4"
    if output.exists():
        print(f"  Video:   {output}")
    meta = state["data"].get("metadata_path", "")
    if meta:
        print(f"  Metadata: {meta}")
    print(f"\n  Review the video, then upload to YouTube.")


if __name__ == "__main__":
    main()
