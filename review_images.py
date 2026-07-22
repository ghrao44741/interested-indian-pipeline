"""
review_images.py — AI-powered image QA for The Interested Indian pipeline.

Reads image_prompts_one_line_per_prompt.md, loads each generated image from
{project}/images/, sends it to Claude with a style/content rubric, and writes
a review report to {project}/review_report.md.

Usage:
    python review_images.py --project ep01
    python review_images.py --project ep01 --shot 07        # single shot
    python review_images.py --project ep01 --fail-only      # issues only
    python review_images.py --project ep01 --model sonnet   # sonnet for deeper review

Requirements:
    pip install anthropic
    ANTHROPIC_API_KEY set in environment (or .env file in this folder)

Cost estimate (Haiku): ~$0.01–0.02 per image → ~$1–2 for all 90 images
Cost estimate (Sonnet): ~$0.05–0.10 per image → ~$4–9 for all 90 images
"""

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("❌ anthropic package not found. Run: pip install anthropic")
    sys.exit(1)

# ── constants ──────────────────────────────────────────────────────────────────
PROMPTS_FILE = "image_prompts_one_line_per_prompt.md"

MODELS = {
    "haiku":  "claude-haiku-4-5-20251001",   # fast, cheap — default
    "sonnet": "claude-sonnet-5",              # deeper review
}
DEFAULT_MODEL = "haiku"

DELAY_BETWEEN_CALLS = 0.5   # seconds — avoids rate-limit bursts

STYLE_GUIDE = """
CHANNEL STYLE GUIDE (The Interested Indian):
- Minimalist 2D doodle / vector illustration
- Background: white or warm cream (#FAF7F2) — never dark, never gradient
- Line art: black ink only, hand-drawn feel, slight sketch texture
- Colour accents ONLY:
    • Warm orange → South Indian states (Karnataka, TN, Kerala, Telangana, AP)
    • Muted grey → Union government / Parliament elements
    • Neutral blue → data, charts, numbers
    • Muted green → positive / compliant states
    • Red → losses, penalties, negative outcomes
- Stick figures for people (simple, no detailed faces)
- No photorealism, no 3D renders, no gradients, no complex shading
- Aspect ratio: 16:9 landscape (1920×1080)
- Text in images: legible, hand-lettered or clean block style
"""

REVIEW_PROMPT = """\
You are a quality reviewer for an animated video essay about Indian fiscal federalism.
Review the attached image against the specifications below and respond ONLY with valid JSON.

SHOT: {shot}
FILENAME: {filename}
NARRATION: "{narration}"
INTENDED VISUAL: {prompt}
OVERLAY TEXT REQUIRED: {overlay}
ANIMATION CUE: {cue}

{style_guide}

KEY TERMS that must be spelled correctly if they appear in the image:
  Numbers: 42%, 41%, 4.713%, 3.647%, 4.131%, 15 paise, ₹80,000 crore, 12.5%, 45%, 15%, 10%, 2.5%
  States: Karnataka, Tamil Nadu, Kerala, Telangana, Andhra Pradesh, Uttar Pradesh, Bihar
  Institutions: Finance Commission, GST Council, Article 280
  People: Siddaramaiah, Arvind Panagariya, Thomas Isaac
  Concepts: Income Distance, Demographic Performance, Divisible Pool, Finance Commission
  Finance Commissions: 14th FC, 15th FC, 16th FC
  Years: 1971, 2011, 2015, 2017, 2021, 2022, 2023, 2026

THIS VIDEO'S TOPIC: Indian fiscal federalism — Finance Commission devolution formula,
South Indian states (especially Karnataka) losing tax share due to population-based formula
changes (1971 → 2011 census), and the 15th/16th Finance Commission arithmetic.
Any image showing content from a completely different topic (recipes, sports, foreign countries,
unrelated people, product advertisements, etc.) is off-topic.

RUBRIC — check each dimension:
1. style_ok       : Does it match the style guide? (doodle, white bg, correct palette)
2. content_match  : Does the image clearly depict what INTENDED VISUAL describes?
3. overlay_ok     : Is the OVERLAY TEXT visible, legible, and present in the image?
                    (Mark true if overlay is absent but not required to be in the image itself)
4. ratio_ok       : Is the image landscape / 16:9? (mark true if unsure)
5. no_artifacts   : Is the image free of AI generation errors (mangled text, extra limbs,
                    distorted shapes, blurry regions)?
6. no_typos       : Read ALL visible text in the image carefully. Is it free of spelling mistakes,
                    wrong numbers, garbled words, or incorrect proper nouns?
                    Compare against KEY TERMS above. Mark false if ANY text is wrong.
7. on_topic       : Does the image relate to THIS VIDEO'S TOPIC? Mark false if the image
                    appears to belong to a completely different video or subject matter.
8. no_watermark   : Is the image free of watermarks, AI generator logos, stock photo badges,
                    or platform UI elements (e.g. Midjourney grid lines, DALL-E watermarks)?

TYPO REPORTING: If no_typos is false, list the exact wrong text found and what it should be.

VERDICT rules:
- "PASS"  : All 8 dimensions OK
- "WARN"  : 1–2 minor issues — style slightly off, text small but readable, content mostly right,
            minor spelling variant (e.g. "Siddaramaiah" vs "Siddaramaiah" — acceptable)
- "FAIL"  : Any of: wrong style entirely, content unrecognisable, required text missing/unreadable,
            obvious AI artifacts, confirmed typo in a key number or proper noun,
            image is off-topic, watermark present

Respond with ONLY this JSON (no markdown, no explanation):
{{
  "verdict": "PASS" | "WARN" | "FAIL",
  "style_ok": true | false,
  "content_match": true | false,
  "overlay_ok": true | false,
  "ratio_ok": true | false,
  "no_artifacts": true | false,
  "no_typos": true | false,
  "on_topic": true | false,
  "no_watermark": true | false,
  "typos_found": ["list of wrong text → correct text, or empty list"],
  "notes": "one concise sentence describing all issues found, or 'Looks good.' if none"
}}
"""


# ═══════════════════════════════════════════════════════════════════════════════
# PARSE PROMPTS FILE
# ═══════════════════════════════════════════════════════════════════════════════

def parse_prompts(md_path: str) -> list[dict]:
    """
    Parse image_prompts_one_line_per_prompt.md into a list of shot dicts.
    Each dict has: shot, filename, narration, prompt, overlay, cue
    """
    shots = []
    with open(md_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("**SHOT"):
                continue

            # SHOT number
            shot_m = re.search(r"\*\*SHOT (\d+)\*\*", line)
            if not shot_m:
                continue
            shot = f"SHOT {shot_m.group(1).zfill(2)}"

            # Filename inside backticks
            fname_m = re.search(r"`([^`]+\.png)`", line)
            filename = fname_m.group(1) if fname_m else ""

            # NARRATION (quoted)
            narr_m = re.search(r'NARRATION:\s*"([^"]+)"', line)
            narration = narr_m.group(1) if narr_m else ""

            # PROMPT (between PROMPT: and OVERLAY:)
            prompt_m = re.search(r"PROMPT:\s*(.+?)\s*OVERLAY:", line)
            prompt = prompt_m.group(1) if prompt_m else ""

            # OVERLAY (between OVERLAY: and CUE:)
            overlay_m = re.search(r"OVERLAY:\s*(.+?)\s*CUE:", line)
            overlay = overlay_m.group(1) if overlay_m else ""

            # CUE (rest of line after CUE:)
            cue_m = re.search(r"CUE:\s*(.+)$", line)
            cue = cue_m.group(1) if cue_m else ""

            shots.append({
                "shot":      shot,
                "filename":  filename,
                "narration": narration,
                "prompt":    prompt,
                "overlay":   overlay,
                "cue":       cue,
            })

    return shots


# ═══════════════════════════════════════════════════════════════════════════════
# IMAGE LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_image_b64(image_path: str) -> tuple[str, str]:
    """Returns (base64_data, media_type)."""
    ext = Path(image_path).suffix.lower()
    media_map = {
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    media_type = media_map.get(ext, "image/png")
    with open(image_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8"), media_type


# ═══════════════════════════════════════════════════════════════════════════════
# REVIEW ONE IMAGE
# ═══════════════════════════════════════════════════════════════════════════════

def review_image(client: anthropic.Anthropic, shot: dict,
                 image_path: str, model: str) -> dict:
    """Send one image to Claude and return the parsed review dict."""
    img_b64, media_type = load_image_b64(image_path)

    user_prompt = REVIEW_PROMPT.format(
        shot=shot["shot"],
        filename=shot["filename"],
        narration=shot["narration"],
        prompt=shot["prompt"],
        overlay=shot["overlay"],
        cue=shot["cue"],
        style_guide=STYLE_GUIDE,
    )

    response = client.messages.create(
        model=model,
        max_tokens=768,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": img_b64,
                    },
                },
                {"type": "text", "text": user_prompt},
            ],
        }],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fallback — extract verdict manually
        verdict = "WARN"
        if '"FAIL"' in raw or "'FAIL'" in raw:
            verdict = "FAIL"
        elif '"PASS"' in raw or "'PASS'" in raw:
            verdict = "PASS"
        return {
            "verdict":      verdict,
            "style_ok":     None,
            "content_match": None,
            "overlay_ok":   None,
            "ratio_ok":     None,
            "no_artifacts": None,
            "no_typos":     None,
            "on_topic":     None,
            "no_watermark": None,
            "typos_found":  [],
            "notes": f"[JSON parse error — raw: {raw[:120]}]",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT WRITER
# ═══════════════════════════════════════════════════════════════════════════════

VERDICT_ICON = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗", "SKIP": "—", "ERROR": "💥"}


def bool_icon(v) -> str:
    if v is True:  return "✓"
    if v is False: return "✗"
    return "?"


def write_report(results: list[dict], output_path: str, model_key: str,
                 total_shots: int, fail_only: bool):
    passes  = sum(1 for r in results if r["result"].get("verdict") == "PASS")
    warns   = sum(1 for r in results if r["result"].get("verdict") == "WARN")
    fails   = sum(1 for r in results if r["result"].get("verdict") == "FAIL")
    skips   = sum(1 for r in results if r["result"].get("verdict") == "SKIP")
    errors  = sum(1 for r in results if r["result"].get("verdict") == "ERROR")
    reviewed = len([r for r in results if r["result"].get("verdict") not in ("SKIP",)])

    lines = [
        f"# Image Review Report — ep01",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Model:** {MODELS.get(model_key, model_key)}",
        f"**Images reviewed:** {reviewed} / {total_shots}  "
        f"({'skipped ' + str(skips) + ' missing' if skips else 'all present'})",
        "",
        "## Summary",
        f"✓ PASS : {passes}",
        f"⚠ WARN : {warns}",
        f"✗ FAIL : {fails}",
        f"— SKIP : {skips}  ← image not found in images/",
        f"💥 ERROR: {errors}",
        "",
        "---",
        "",
        "## Results",
        "",
        "| Shot | File | Verdict | Style | Content | Overlay | Ratio | Artifacts | Typos | On-topic | Watermark | Notes |",
        "|------|------|---------|-------|---------|---------|-------|-----------|-------|----------|-----------|-------|",
    ]

    for r in results:
        res  = r["result"]
        v    = res.get("verdict", "?")
        icon = VERDICT_ICON.get(v, v)
        if fail_only and v == "PASS":
            continue
        row = (
            f"| {r['shot']} "
            f"| `{r['filename']}` "
            f"| {icon} {v} "
            f"| {bool_icon(res.get('style_ok'))} "
            f"| {bool_icon(res.get('content_match'))} "
            f"| {bool_icon(res.get('overlay_ok'))} "
            f"| {bool_icon(res.get('ratio_ok'))} "
            f"| {bool_icon(res.get('no_artifacts'))} "
            f"| {bool_icon(res.get('no_typos'))} "
            f"| {bool_icon(res.get('on_topic'))} "
            f"| {bool_icon(res.get('no_watermark'))} "
            f"| {res.get('notes', '')} |"
        )
        lines.append(row)

    # Issues section
    issues = [r for r in results if r["result"].get("verdict") in ("WARN", "FAIL", "ERROR")]
    if issues:
        lines += ["", "---", "", "## Issues requiring attention", ""]
        for r in issues:
            res  = r["result"]
            v    = res.get("verdict", "?")
            icon = VERDICT_ICON.get(v, v)
            lines.append(f"### {icon} {r['shot']} · `{r['filename']}` · {v}")
            lines.append(f"**Notes:** {res.get('notes', '—')}")
            checks = []
            for key, label in [
                ("style_ok",      "Style"),
                ("content_match", "Content"),
                ("overlay_ok",    "Overlay"),
                ("ratio_ok",      "Ratio"),
                ("no_artifacts",  "No artifacts"),
                ("no_typos",      "No typos"),
                ("on_topic",      "On-topic"),
                ("no_watermark",  "No watermark"),
            ]:
                checks.append(f"{bool_icon(res.get(key))} {label}")
            lines.append("  " + "  ·  ".join(checks))
            typos = res.get("typos_found", [])
            if typos:
                lines.append(f"**Typos found:** {' | '.join(typos)}")
            lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="AI image review for The Interested Indian pipeline"
    )
    parser.add_argument("--project", required=True,
                        help="Project folder (e.g. ep01), relative or absolute")
    parser.add_argument("--shot", type=int, default=None,
                        help="Review a single shot number (e.g. --shot 7)")
    parser.add_argument("--fail-only", action="store_true",
                        help="Only show WARN/FAIL entries in the report table")
    parser.add_argument("--model", choices=["haiku", "sonnet"], default=DEFAULT_MODEL,
                        help=f"Claude model to use (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    # ── resolve paths ──────────────────────────────────────────────────────────
    script_dir  = Path(__file__).parent
    project_dir = (
        Path(args.project) if Path(args.project).is_absolute()
        else (script_dir / args.project).resolve()
    )
    if not project_dir.is_dir():
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    prompts_path = project_dir / PROMPTS_FILE
    if not prompts_path.exists():
        print(f"❌ Prompts file not found: {prompts_path}")
        print(f"   Expected: {PROMPTS_FILE} inside the project folder")
        sys.exit(1)

    images_dir   = project_dir / "images"
    report_path  = project_dir / "review_report.md"
    model_name   = MODELS[args.model]

    # ── load prompts ───────────────────────────────────────────────────────────
    shots = parse_prompts(str(prompts_path))
    if not shots:
        print("❌ No shots parsed from prompts file — check the format.")
        sys.exit(1)

    # Filter to single shot if requested
    if args.shot is not None:
        target = f"SHOT {str(args.shot).zfill(2)}"
        shots = [s for s in shots if s["shot"] == target]
        if not shots:
            print(f"❌ {target} not found in prompts file.")
            sys.exit(1)

    # ── init Anthropic client ──────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Try .env in script folder
        env_file = script_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set.")
        print("   Set it as an environment variable or add it to a .env file:")
        print("   ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # ── review loop ────────────────────────────────────────────────────────────
    print(f"\n{'═' * 55}")
    print(f"Image Review — {project_dir.name}")
    print(f"Model     : {model_name}")
    print(f"Shots     : {len(shots)}")
    print(f"Images dir: {images_dir}")
    print(f"{'═' * 55}\n")

    results = []

    for i, shot in enumerate(shots, 1):
        filename = shot["filename"]

        # Find image file (try exact name, then case-insensitive)
        image_path = images_dir / filename
        if not image_path.exists():
            # Try other extensions
            stem = Path(filename).stem
            found = None
            for ext in [".png", ".jpg", ".jpeg", ".webp"]:
                candidate = images_dir / f"{stem}{ext}"
                if candidate.exists():
                    found = candidate
                    break
            image_path = found

        if not image_path:
            print(f"[{i:02d}/{len(shots)}] {shot['shot']} · {filename}  — SKIP (not found)")
            results.append({
                "shot":     shot["shot"],
                "filename": filename,
                "result": {
                    "verdict":       "SKIP",
                    "style_ok":      None,
                    "content_match": None,
                    "overlay_ok":    None,
                    "ratio_ok":      None,
                    "no_artifacts":  None,
                    "no_typos":      None,
                    "on_topic":      None,
                    "no_watermark":  None,
                    "typos_found":   [],
                    "notes":         "Image file not found in images/",
                },
            })
            continue

        print(f"[{i:02d}/{len(shots)}] {shot['shot']} · {filename}", end="  ", flush=True)

        try:
            review = review_image(client, shot, str(image_path), model_name)
            verdict = review.get("verdict", "?")
            icon    = VERDICT_ICON.get(verdict, verdict)
            print(f"{icon} {verdict}  — {review.get('notes', '')}")
            results.append({
                "shot":     shot["shot"],
                "filename": filename,
                "result":   review,
            })
        except Exception as e:
            print(f"💥 ERROR — {e}")
            results.append({
                "shot":     shot["shot"],
                "filename": filename,
                "result": {
                    "verdict":       "ERROR",
                    "style_ok":      None,
                    "content_match": None,
                    "overlay_ok":    None,
                    "ratio_ok":      None,
                    "no_artifacts":  None,
                    "no_typos":      None,
                    "on_topic":      None,
                    "no_watermark":  None,
                    "typos_found":   [],
                    "notes":         str(e)[:120],
                },
            })

        if i < len(shots):
            time.sleep(DELAY_BETWEEN_CALLS)

    # ── write report ───────────────────────────────────────────────────────────
    write_report(results, str(report_path), args.model,
                 total_shots=len(shots), fail_only=args.fail_only)

    passes = sum(1 for r in results if r["result"].get("verdict") == "PASS")
    warns  = sum(1 for r in results if r["result"].get("verdict") == "WARN")
    fails  = sum(1 for r in results if r["result"].get("verdict") == "FAIL")
    skips  = sum(1 for r in results if r["result"].get("verdict") == "SKIP")

    print(f"\n{'═' * 55}")
    print(f"Review complete")
    print(f"  ✓ PASS : {passes}")
    print(f"  ⚠ WARN : {warns}")
    print(f"  ✗ FAIL : {fails}")
    print(f"  — SKIP : {skips}")
    print(f"\nReport: {report_path}")
    print(f"{'═' * 55}\n")

    if warns + fails > 0:
        sys.exit(1)   # non-zero exit so CI pipelines can catch issues


if __name__ == "__main__":
    main()
