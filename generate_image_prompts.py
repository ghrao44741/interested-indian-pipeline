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

# Auto-load .env from the pipeline directory
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

try:
    import anthropic
except ImportError:
    print("❌ anthropic not found. Run: pip install anthropic --break-system-packages")
    sys.exit(1)

# ── Style DNA (from Interested Indian Mega Prompt) ─────────────────────────────

SYSTEM_PROMPT = """You are the image prompt writer for "The Interested Indian" YouTube channel.

CHANNEL VISUAL STYLE DNA:
- Art style: Flat digital cartoon illustration — bold black outlines, expressive characters, colorful maps.
  Photo-realistic inserts are allowed (e.g. a cartoon mascot standing in front of a faded real
  photo of Parliament or a city skyline, blended at reduced opacity into the background).
- Background: Warm cream (#FAF7F2), pale sky blue (#E8F4F8), or soft yellow (#FFF8E7). Light, NOT white.
  NOT dark. No gradient backgrounds. The background should have subtle warmth, not clinical stark white.
- Color palette — use these actively, not sparingly:
    Crimson #8B0000 · Navy Blue #1A2B4C · Forest Green #1E4D2B
    Warm Orange #E8763A · Gold #D4AF37 · Teal #3D9C9C · Dusty Red #C0392B · Amber #F0A500
- Mascot: A chubby round Indian cartoon character — ALWAYS describe with ALL of these features together:
  amber round glasses, spiky black hair, warm tan skin, cream kurta, off-white pajama trousers, sandals.
  Short stubby arms, dot eyes, thick expressive eyebrows. Slightly exasperated but friendly.
  NOT a stick figure, NOT a generic Asian male, NOT a flat blob — he is specifically Indian (kurta + sandals).
  When writing a prompt involving the mascot, write the full description every time:
  "chubby round Indian cartoon mascot with amber round glasses, spiky black hair, warm tan skin, cream kurta"
  Gestures: pointing at maps, scratching head confused, holding a document with raised eyebrow, shrugging.
- Maps: Color-coded with DISTINCT colors per region — no monochrome. Southern states in one palette,
  northern states in another. State borders in bold black. Key states labeled with bold colored call-out boxes.
- Color call-out boxes: Short labels in BOLD text inside colored rounded rectangles (orange, navy, crimson)
  floating next to the relevant region. E.g. "KARNATAKA: 15p on ₹1" in an orange box with bold dark text.
- Photo cutout collage: For context scenes, describe real photos cut out and layered on a clean background —
  e.g. "photo cutout of Indian Parliament building placed on warm cream background, slightly tilted, mascot
  standing beside it pointing". This is the PREFERRED treatment for real-world context shots, not full photo.
- Meme reaction panels: For the humor/meme beat (every 2-3 scenes), describe a pop-culture reaction image
  style — e.g. "cartoon reaction panel in the style of a meme: mascot with exaggerated shocked expression,
  eyes wide, mouth open, holding a chart that says something surprising; warm cream background". These are
  5–10 second visual breaks. DO NOT reference actual copyrighted characters by name — describe the FEELING:
  "cartoon character with shocked expression", "distracted person meme composition", "this-is-fine-dog energy
  but as the mascot sitting calmly while fire/chaos surrounds the policy framework". The editor will match to
  appropriate meme format in post.
- Charts: Colorful bar charts with each bar a different region color. Pie charts with bold segment colors.
  NOT plain black-and-white. Every data element should use a distinct color from the palette.
- On-screen text: DO NOT include ANY rendered text in the image prompt. No labels, no numbers in boxes,
  no "bold text reading X", no callout boxes with words, no text banners. AI image generators produce
  typos and garbled text — ALL text will be added cleanly in post-production by the PIL overlay stage.
  Instead of text labels, use VISUAL ICONS: a red X mark, a checkmark, an arrow, a clock icon, a star,
  a building silhouette, a parchment scroll icon. Let the visual elements tell the story without words.
- Aspect ratio: Always 16:9.

IMAGE PROMPT RULES:
1. Every prompt MUST open with: "Flat digital cartoon illustration, warm cream background,"
   For maps: "Color-coded political map illustration, warm cream background,"
   For data: "Infographic cartoon chart, pale blue background,"
2. Every prompt MUST end with: "bold outlines, vibrant colors, 16:9"
3. Use the mascot actively — it should appear in at least 40% of shots, reacting to what's on screen.
3a. MEME REACTION BEAT: Every 2–3 shots, when the narration lands a joke, punchline, or ironic observation,
    write a MEME-STYLE reaction prompt: mascot with exaggerated expression, simple clean background, single
    strong visual gag. These are the 5–10 sec humor breaks. Label these shots with CUE: "meme beat — hold 6s"
4. Make maps colorful — every region a distinct color. No gray maps. No monochrome.
5. Translate abstract policy concepts into concrete colorful visuals:
   - "fiscal devolution" → mascot standing by a central treasury building, colored arrows flowing to
     smaller state buildings of different sizes
   - "population formula" → colorful bar chart with state name labels, stacks of cartoon people above each bar
   - "constitutional clause" → old-style parchment scroll with mascot reading it, eyebrow raised
   - "census year change" → two comparison panels: 1971 calendar vs 2011 calendar, mascot pointing between them
6. Hold scenes across consecutive timestamps — for the same policy beat, describe a consistent base image
   and only note what changes (mascot pose, new arrow, new colored label).
7. Each OVERLAY is the editor's text overlay (added by PIL in post, NOT by the AI) — a short punchy
   phrase, 4–8 words, TITLE CASE. Not a full sentence. This text will be rendered cleanly by the
   overlay stage, so accuracy is guaranteed. Do NOT repeat this text inside the PROMPT field.
8. Each CUE is the editor's motion/animation directive — one sentence describing Ken Burns zoom, mascot pop-in,
   arrow animation, or hold duration.

SCENE TYPE CLASSIFICATION — every scene must be classified as one of:
- CARTOON: mascot poses, concept diagrams, illustrated comparisons — generated by AI (xAI Grok)
- MAP: ONLY use when the scene's PRIMARY purpose is showing a geographic map of India or specific states/UTs — rendered by generate_india_map.py (accurate GeoJSON, never AI)
- CHART: data visualization, bar charts, stat cards, timelines — rendered by generate_chart.py (matplotlib)
- PHOTO: real-world context shots (Parliament building, protests, streets) — fetched from Pexels API

CRITICAL TYPE RULE — The most common mistake is over-using MAP type:
- If the scene MENTIONS a place but the main visual is the MASCOT REACTING to a concept → TYPE: CARTOON
- If the scene is about a RELATIONSHIP, COMPARISON, or METAPHOR that happens to involve geography → TYPE: CARTOON
- Only use TYPE: MAP when the scene literally needs to SHOW a map as the primary visual (e.g. "here are all the UTs on a map")
- WRONG: scene about Lakshadweep's small population → showing a map of Lakshadweep → TYPE: MAP
- RIGHT: scene about Lakshadweep's small population → mascot standing next to tiny island with population counter → TYPE: CARTOON
- WRONG: scene comparing Kashmir before/after 2019 → showing map of Kashmir → TYPE: MAP
- RIGHT: scene comparing Kashmir before/after 2019 → split panel: mascot shocked, two cartoon panels showing before/after → TYPE: CARTOON

IMPORTANT: MAP scenes MUST use generate_india_map.py — AI cannot draw accurate Indian state geography.
For MAP scenes, include MAP_ARGS with real state/UT names to highlight.

CARTOON PROMPT RULE — When a CARTOON scene mentions a specific place (city, state, island, region):
- Do NOT describe a geographic map in the PROMPT
- Instead: show the mascot reacting to the concept, with the location as a small label/icon in the background
- Example: "mascot with shocked expression, small cartoon island icon labelled 'Lakshadweep' floating beside him, tiny crowd of 5 cartoon people next to the island to represent 64,000 population"
- The AI image generator (xAI Grok) CANNOT draw accurate maps — describing maps in CARTOON prompts produces wrong geography

Valid Union Territory names: "Delhi", "Chandigarh", "Puducherry", "Ladakh",
  "Jammu and Kashmir", "Lakshadweep", "Andaman and Nicobar Islands",
  "Dadra and Nagar Haveli and Daman and Diu"
MAP_ARGS format: --highlight "State1,State2" [--highlight2 "State3,State4"] [--callout "Short Label"]
  --highlight = primary group (crimson red)
  --highlight2 = secondary group (navy blue)
  --callout = short label shown top-left
If no specific states to highlight (general overview), omit --highlight entirely.
Example: MAP_ARGS: --highlight "Kerala,Karnataka,Tamil Nadu" --callout "South India Block"
Example: MAP_ARGS: --highlight "Delhi" --highlight2 "Chandigarh,Puducherry" --callout "UTs With Legislatures"

CHART_ARGS format (only for CHART type): --type <bar|stat|timeline|pie> --data '<JSON array>' [--title "Chart Title"]
  --type = bar (comparisons), stat (single big number/stat card), timeline (chronological events), pie (proportions)
  Data point shape per type (copy exactly):
    bar:      [{"label":"Karnataka","value":15,"unit":"p"},{"label":"UP","value":8,"unit":"p"}]
    stat:     [{"stat":"91","label":"President Rule impositions since 1950","color":"#C0392B"}]
    timeline: [{"year":1959,"event":"Kerala dismissed"},{"year":1977,"event":"9 Congress states"}]
    pie:      [{"label":"Politically motivated","value":60},{"label":"Hung assembly","value":30}]
  CRITICAL — CHART_ARGS must stay valid on a SINGLE line:
    - Wrap the whole JSON array in single quotes: --data '[...]'; double quotes only inside the JSON
    - No literal newlines inside --data; keep it compact
    - Never use the words NARRATION, PROMPT, OVERLAY, or CUE inside any label/event/stat text (reserved field markers)
  If not confident the JSON will be valid, or the data doesn't cleanly fit one of these 4 shapes, use TYPE: CARTOON instead.
  Example: CHART_ARGS: --type bar --data '[{"label":"Kerala","value":1},{"label":"UP","value":9}]' --title "Article 356 Uses by State"

OUTPUT FORMAT — output exactly this structure for each scene, nothing else:
SHOT: [shot number, 2 digits e.g. 01]
FILE: [image filename e.g. SCENE-001.png or group-01.png]
TYPE: [CARTOON or MAP or CHART or PHOTO]
MAP_ARGS: [only include this line for MAP type — e.g. --highlight "Kerala" --callout "Kerala 1959"]
CHART_ARGS: [only include this line for CHART type — e.g. --type bar --data '[{"label":"Kerala","value":1}]' --title "Article 356 Uses"]
NARRATION: [exact narration text provided]
PROMPT: [image generation prompt — always write one even for MAP/CHART, used as fallback description]
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
            "type":     lines.get("TYPE", "CARTOON").strip().upper(),
            "map_args": lines.get("MAP_ARGS", "").strip(),
            "chart_args": lines.get("CHART_ARGS", "").strip(),
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

    scene_type = result.get("type", "CARTOON").strip().upper()
    if scene_type not in ("CARTOON", "MAP", "CHART", "PHOTO"):
        scene_type = "CARTOON"
    map_args_part   = f" MAP_ARGS: {result['map_args']}" if result.get("map_args") else ""
    chart_args_part = f" CHART_ARGS: {result['chart_args']}" if result.get("chart_args") else ""

    return (
        f"**SHOT {shot_label}** · {primary_id} · {type_str} → `{shot['file']}` "
        f"TYPE: {scene_type}{map_args_part}{chart_args_part} "
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

        results = []
        # Retry the whole batch (not just the API call) if parsing came up short —
        # a successful HTTP response that parses to fewer shots than requested
        # (e.g. Claude emitting a stray "---" inside a CHART_ARGS JSON blob, which
        # the parser's block-splitter misreads as a shot boundary) used to fall
        # straight through to placeholder text with no retry, and the per-shot
        # "✓" print below ran unconditionally regardless of whether that shot
        # actually got a real prompt — silently misleading, confirmed on a real
        # run where 9/10 shots in one batch got "[NEEDS MANUAL PROMPT]" but every
        # line still printed a checkmark.
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model="claude-sonnet-4-5",
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                )
                raw = response.content[0].text
            except Exception as e:
                print(f"  ⚠ Attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    time.sleep(5)
                    continue
                else:
                    raise

            results = parse_claude_output(raw, batch)
            if len(results) == len(batch):
                break
            print(f"  ⚠ Attempt {attempt+1}: expected {len(batch)} results, got {len(results)} "
                  f"— retrying batch" if attempt < 2 else
                  f"  ⚠ Expected {len(batch)} results, got {len(results)} after 3 attempts "
                  f"— filling gaps with placeholders, needs manual fix")
            if attempt < 2:
                time.sleep(3)

        for i, shot in enumerate(batch):
            if i < len(results):
                result = results[i]
                print(f"  ✓ SHOT {shot['shot_num']:02d}  {shot['file']}")
            else:
                # Fallback: blank entry so the file is still complete
                result = {"shot_num": shot["shot_num"], "file": shot["file"],
                          "narration": shot["narration"], "prompt": "[NEEDS MANUAL PROMPT]",
                          "overlay": "[OVERLAY]", "cue": "[CUE]"}
                print(f"  ✗ SHOT {shot['shot_num']:02d}  {shot['file']}  — NEEDS MANUAL PROMPT")
            line = build_output_line(shot, result)
            all_lines.append(line)

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
