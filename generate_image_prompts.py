"""
generate_image_prompts.py — Auto-generate image prompts from manifest.json using Claude API.

Implements Stage 3 of the Interested Indian Mega Prompt. Reads the manifest,
deduplicates scenes by visual_group_id, and calls Claude in batches to produce
image prompts matching the channel's visual style DNA.

Output: ep01/image_prompts_one_line_per_prompt.md  (same format consumed by
        review_images.py and generate_images_flux.py)

Usage:
    python generate_image_prompts.py --project ep01
    python generate_image_prompts.py --project ep01 --batch-size 10
    python generate_image_prompts.py --project ep01 --overwrite
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("❌ anthropic not found. Run: pip install anthropic --break-system-packages")
    sys.exit(1)

# ── Style DNA (from Interested Indian Mega Prompt) ─────────────────────────────

SYSTEM_PROMPT = """You are the image prompt writer for "The Interested Indian" YouTube channel.

CHANNEL VISUAL STYLE DNA:
- Art style: Minimalist 2D doodle/vector — bold black outlines, hand-drawn stick figures, high-contrast maps, infographic charts.
- Background: Warm cream/off-white (#FAF7F2) or stark white. NEVER dark backgrounds. No gradients, no drop shadows, no textures.
- Color palette for highlights: warm orange (#E8763A), crimson (#8B0000), navy blue (#1A2B4C), forest green (#1E4D2B), gold (#D4AF37), teal (#3D9C9C).
- Mascot: Minimalist stick figure with round glasses, dot eyes, expressive eyebrows. Gestures: pointing at maps, thinking, confused/skeptical, holding documents.
- Maps: Clean, high-contrast vector maps of India or specific states. Bold color blocks for administrative zones.
- On-screen text: Added in post-production by editor — do NOT include text in the image prompt itself (except for short labels like "15p ← ₹1" that are part of the visual diagram).
- Labels & arrows: Bright yellow or red arrows pointing at specific regions, with short ALL CAPS labels.
- Aspect ratio: Always 16:9.

IMAGE PROMPT RULES:
1. Every prompt MUST open with: "Minimalist 2D doodle, white bg,"
2. Every prompt MUST end with: "hand-drawn, 16:9"
3. Generate backgrounds text-free — no narration text, no subtitles, no paragraph text in the image.
4. Short labels on diagrams (e.g. "CENSUS 1971", "₹1 → 15p", "Article 280") are fine and encouraged.
5. Translate abstract policy concepts into concrete doodle visuals:
   - "fiscal devolution" → split diagram: central treasury vault, arrows distributing coins to states
   - "border demarcation" → map with dashed red line, skeptical mascot pointing at disputed zone
   - "population formula" → bar chart with stick figures stacked by state, rupee stacks beside each
6. Hold scenes across consecutive timestamps — if 3 lines describe the same policy, describe a consistent base image and only note what changes (mascot pose, new arrow, new label).
7. Each OVERLAY is the editor's text overlay — a short punchy phrase, 4–8 words, TITLE CASE. Not a full sentence.
8. Each CUE is the editor's motion/animation directive — one sentence describing Ken Burns zoom, mascot pop-in, arrow animation, or hold duration.

OUTPUT FORMAT — output exactly this structure for each scene, nothing else:
SHOT: [shot number, 2 digits e.g. 01]
FILE: [image filename e.g. SCENE-001.png or group-01.png]
NARRATION: [exact narration text provided]
PROMPT: [image generation prompt]
OVERLAY: [editor text overlay, 4-8 words]
CUE: [editor motion/animation directive]
---"""

USER_TEMPLATE = """Generate image prompts for these scenes. Follow the output format exactly.
Each scene is: SCENE_ID [MM:SS] "narration text" → IMAGE_FILE

{scenes}"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def fmt_ts(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def build_shots(manifest: dict) -> list[dict]:
    """
    Deduplicate scenes by visual_group_id and return ordered shot list.
    Each shot = one image file.
    Returns list of dicts: {shot_num, file, scene_ids, timestamp, narration, group_id}
    """
    scenes = manifest["scenes"]
    shots = []
    seen_groups = {}
    shot_num = 0

    for scene in scenes:
        gid = scene.get("visual_group_id")
        image_file = Path(scene["image"]).name

        if gid and gid in seen_groups:
            # Append narration to the existing group shot
            seen_groups[gid]["narration"] += " " + scene["script"]
            seen_groups[gid]["scene_ids"].append(scene["id"])
        else:
            shot_num += 1
            shot = {
                "shot_num": shot_num,
                "file": image_file,
                "scene_ids": [scene["id"]],
                "timestamp": scene.get("whisperx_start", scene.get("start", 0)),
                "narration": scene["script"],
                "group_id": gid,
            }
            shots.append(shot)
            if gid:
                seen_groups[gid] = shot

    return shots


def format_shot_for_prompt(shot: dict) -> str:
    ts = fmt_ts(shot["timestamp"])
    scene_label = shot["scene_ids"][0] if len(shot["scene_ids"]) == 1 else shot["scene_ids"][0] + "…"
    return f'{scene_label} [{ts}] "{shot["narration"]}" → {shot["file"]}'


def parse_claude_output(text: str, shots: list[dict]) -> list[dict]:
    """Parse Claude's structured output into a list of shot dicts with prompt/overlay/cue."""
    results = []
    blocks = [b.strip() for b in text.split("---") if b.strip()]

    for block in blocks:
        lines = {k.strip(): v.strip()
                 for line in block.splitlines()
                 if ":" in line
                 for k, v in [line.split(":", 1)]}
        if "PROMPT" not in lines:
            continue
        results.append({
            "shot_num": lines.get("SHOT", "??"),
            "file":     lines.get("FILE", ""),
            "narration": lines.get("NARRATION", ""),
            "prompt":   lines.get("PROMPT", ""),
            "overlay":  lines.get("OVERLAY", ""),
            "cue":      lines.get("CUE", ""),
        })

    return results


def build_output_line(shot: dict, result: dict) -> str:
    """Build a single-line entry matching image_prompts_one_line_per_prompt.md format."""
    shot_num = int(shot["shot_num"]) if str(shot["shot_num"]).isdigit() else shot["shot_num"]
    shot_label = f"{shot_num:02d}" if isinstance(shot_num, int) else shot_num

    if shot["group_id"]:
        scene_type = f"group-{shot['file'].replace('group-','').replace('.png','')}"
        type_str = f"group-{scene_type.split('-')[-1]}"
    else:
        scene_type = "standalone"
        type_str = "standalone"

    primary_id = shot["scene_ids"][0]

    return (
        f"**SHOT {shot_label}** · {primary_id} · {type_str} → `{shot['file']}` "
        f"NARRATION: \"{shot['narration']}\" "
        f"PROMPT: {result['prompt']} "
        f"OVERLAY: {result['overlay']} "
        f"CUE: {result['cue']}"
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def generate_prompts(project_dir: Path, batch_size: int, overwrite: bool):
    manifest_path = project_dir / "manifest.json"
    output_path   = project_dir / "image_prompts_one_line_per_prompt.md"

    if not manifest_path.exists():
        print(f"❌ manifest.json not found: {manifest_path}")
        sys.exit(1)

    if output_path.exists() and not overwrite:
        print(f"⚠  Output already exists: {output_path}")
        print("   Use --overwrite to regenerate.")
        sys.exit(0)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shots = build_shots(manifest)
    print(f"✓ Manifest loaded: {len(manifest['scenes'])} scenes → {len(shots)} shots (deduplicated)")

    client = anthropic.Anthropic(api_key=api_key)
    all_lines = []
    total_batches = (len(shots) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        batch = shots[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        batch_num = batch_idx + 1
        print(f"\nBatch {batch_num}/{total_batches} — shots {batch[0]['shot_num']}–{batch[-1]['shot_num']}...")

        scene_text = "\n".join(format_shot_for_prompt(s) for s in batch)
        user_msg   = USER_TEMPLATE.format(scenes=scene_text)

        for attempt in range(3):
            try:
                response = client.messages.create(
                    model="claude-sonnet-4-5",
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                )
                raw = response.content[0].text
                break
            except Exception as e:
                print(f"  ⚠ Attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    time.sleep(5)
                else:
                    raise

        results = parse_claude_output(raw, batch)

        if len(results) != len(batch):
            print(f"  ⚠ Expected {len(batch)} results, got {len(results)} — check output")

        for i, shot in enumerate(batch):
            if i < len(results):
                result = results[i]
            else:
                # Fallback: blank entry so the file is still complete
                result = {"shot_num": shot["shot_num"], "file": shot["file"],
                          "narration": shot["narration"], "prompt": "[NEEDS MANUAL PROMPT]",
                          "overlay": "[OVERLAY]", "cue": "[CUE]"}
            line = build_output_line(shot, result)
            all_lines.append(line)
            print(f"  ✓ SHOT {shot['shot_num']:02d}  {shot['file']}")

        if batch_num < total_batches:
            time.sleep(1)  # brief pause between batches

    # Write output
    output_path.write_text("\n\n".join(all_lines) + "\n", encoding="utf-8")
    print(f"\n✓ Written → {output_path}  ({len(all_lines)} shots)")
    print("  Ready for: review_images.py and generate_images_flux.py")


def main():
    parser = argparse.ArgumentParser(description="Generate image prompts from manifest.json via Claude API")
    parser.add_argument("--project",    required=True, help="Episode folder (e.g. ep01)")
    parser.add_argument("--batch-size", type=int, default=10, help="Scenes per Claude call (default: 10)")
    parser.add_argument("--overwrite",  action="store_true", help="Overwrite existing output")
    args = parser.parse_args()

    project_dir = Path(args.project)
    if not project_dir.is_absolute():
        project_dir = Path(__file__).parent / project_dir

    generate_prompts(project_dir, args.batch_size, args.overwrite)


if __name__ == "__main__":
    main()
