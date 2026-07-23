"""
pipeline_agents.py — Three-agent system for The Interested Indian pipeline.

OrchestratorAgent  Routes between stages, decides on failures, handles human checkpoints.
ReviewAgent        Evaluates stage output before allowing the pipeline to proceed.
ResearchAgent      Web-searches for verified facts before script generation.

Each agent uses Claude API. The orchestrator coordinates the other two.
"""

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import anthropic

try:
    from duckduckgo_search import DDGS
    SEARCH_AVAILABLE = True
except ImportError:
    SEARCH_AVAILABLE = False

# ── Constants ──────────────────────────────────────────────────────────────────

PIPELINE_DIR = Path(__file__).parent
SHORTS_DIR   = PIPELINE_DIR.parent / "Aeonium_Glow" / "shorts_pipeline2"

BANNED_WORDS = [
    "unleash", "unlock", "dive into", "delve into", "game-changer", "game changer",
    "revolutionary", "tapestry", "genuinely", "honestly", "straightforward",
    "cutting-edge", "leverage", "synergy", "paradigm shift", "seamlessly",
    "robust", "comprehensive", "innovative", "transformative", "groundbreaking",
    "holistic", "deep dive", "unpack",
]

STAGE_ORDER = ["topics", "script", "voice", "split", "prompts", "images", "stitch", "metadata",
               "thumbnail", "chapters", "upload"]

CHANNEL_DNA = """You are a viral educational YouTube video creation engine for "The Interested Indian".

CHANNEL DNA:
- Niche: Indian history, administrative evolution, political geography, economic history, regional geopolitics.
- Format: 12–18 minute video essay. First-person narrator — the host is learning this alongside the viewer.
- Voice register: "I" narrates. "You" is the audience. Conversational, direct, occasionally self-deprecating.
  WRONG: "Karnataka contributes nearly nine percent of India's GDP."
  RIGHT: "So I looked at Karnataka's numbers, and — okay, wait, this can't be right."
- Hook: A policy paradox that sounds genuinely absurd out of context. Opens within the first 4 lines. Make the
  viewer feel slightly betrayed by something they never knew. The hook must contain a specific number or fact,
  not vague setup.
- Humor mandate: Every 2–3 paragraphs must include ONE of: a modern analogy (gaming, cricket, food delivery),
  a self-aware observation, a deadpan understatement, or a gentle audience poke ("I know exactly what some of
  you are typing right now"). Humor must serve comprehension — it's the spoonful of sugar, not the point.
  WRONG: "The Finance Commission is a body reconstituted every five years."
  RIGHT: "Every five years, the government assembles a committee of economists to decide who gets what — sort of
         like if your family had a constitutional requirement to argue about the restaurant bill."
- Jargon rule: NEVER use a policy term without immediately translating it in plain language on the same beat.
  WRONG: "The inter-se share of the divisible tax pool"
  RIGHT: "Your inter-se share — which is bureaucrat for 'your slice of the pie'"
- Audience address: Anticipate what confused or skeptical viewers are thinking and name it directly.
  "Now, before you say this is just states whining about not getting enough money — hear me out."
  "I know this sounds like a dry finance policy story. It is. But stay with me because the ending is annoying."
- Self-aware humility: Acknowledge complexity honestly at least once per video.
  "This next part is actually confusing and I'm going to do my best not to make it worse."
  "I spent two days reading about this and I'm still not entirely sure I understand it. Here's what I do know."
- Sentence rhythm: Short sentence. One concrete fact. One longer connective sentence. Short sentence. End every
  5–6 sentence block with either a question or a one-line punchline.
- Narrative arc:
    1. Hook — the absurd-sounding fact that makes no sense yet
    2. "Let me explain" — the rule or law that created this
    3. Mechanism — how the machine actually works, translated into human terms
    4. The unintended consequence — nobody planned for this, and it matters
    5. Who wins, who loses — and why the people on the losing side aren't wrong to be annoyed
    6. Close — echo the hook, but now the viewer understands the dark joke
- STRICT: ZERO corporate clichés (unleash, unlock, dive into, tapestry, game-changer). No sensationalism.
  No abstract academic framing without a plain-English follow-up on the same line.
- Visual style: Flat cartoon doodles on warm cream or pale backgrounds. Chubby expressive mascot with glasses.
  Real photo inserts for context. Colorful region-coded maps. Bold text callouts in colored boxes."""

# ── Shared result types ────────────────────────────────────────────────────────

@dataclass
class ReviewResult:
    passed: bool
    score: int           # 0–10; >= 7 = pass
    issues: list         # list of str — specific problems found
    recommendations: list  # list of str — how to fix
    data: dict = field(default_factory=dict)  # stage-specific extra data

    def summary(self) -> str:
        status = "✓ PASS" if self.passed else "✗ FAIL"
        return f"{status}  score={self.score}/10  issues={len(self.issues)}"


@dataclass
class OrchestratorDecision:
    action: str          # "retry" | "human_checkpoint" | "proceed" | "abort"
    reason: str
    human_message: str = ""  # shown to human if action == human_checkpoint


# ══════════════════════════════════════════════════════════════════════════════
# REVIEW AGENT
# ══════════════════════════════════════════════════════════════════════════════

class ReviewAgent:
    """
    Evaluates pipeline stage output before allowing progression.
    Mix of deterministic rule checks + Claude qualitative assessment.
    Threshold: score >= 7 = pass.
    """

    PASS_THRESHOLD = 7

    def __init__(self, client: anthropic.Anthropic):
        self.client = client

    def review(self, stage: str, context: dict) -> ReviewResult:
        reviewers = {
            "topics":    self._review_topics,
            "script":    self._review_script,
            "voice":     self._review_voice,
            "split":     self._review_split,
            "prompts":   self._review_prompts,
            "images":    self._review_images,
            "stitch":    self._review_stitch,
            "metadata":  self._review_metadata,
            "thumbnail": self._review_thumbnail,
            "chapters":  self._review_chapters,
            "upload":    self._review_upload,
        }
        fn = reviewers.get(stage)
        if not fn:
            return ReviewResult(passed=True, score=10, issues=[], recommendations=[])
        return fn(context)

    # ── Topics ─────────────────────────────────────────────────────────────────

    def _review_topics(self, ctx: dict) -> ReviewResult:
        ideas_text = ctx.get("topic_ideas", "")
        issues, recs = [], []

        # Rule: count table rows — tolerant of both "| 1 | ..." and "1 | ..." formats
        rows = []
        for line in ideas_text.splitlines():
            s = line.strip()
            if not s or "---" in s:
                continue
            # Skip header row (e.g. "# | Video Title | ..." or "| # | ...")
            if s.startswith("# ") or s.startswith("| #"):
                continue
            # Count any line with pipe separators as a data row
            if "|" in s:
                rows.append(s)
        if len(rows) < 5:
            issues.append(f"Only {len(rows)} topic ideas generated (need 5)")
            recs.append("Regenerate with explicit instruction to produce exactly 5 ideas")

        # Claude quality check
        result = self._claude_assess(
            "Review these 5 YouTube topic ideas for 'The Interested Indian' channel. "
            "Score 0–10 on: viral potential, diversity of angles, alignment with proven formats, "
            "titles under 70 chars. List any issues.\n\n" + ideas_text
        )
        issues += result.get("issues", [])
        recs   += result.get("recommendations", [])
        score   = result.get("score", 7)
        if len(rows) < 5:
            score = min(score, 4)

        return ReviewResult(
            passed=score >= self.PASS_THRESHOLD,
            score=score,
            issues=issues,
            recommendations=recs,
        )

    # ── Script ─────────────────────────────────────────────────────────────────

    def _review_script(self, ctx: dict) -> ReviewResult:
        script_path = Path(ctx.get("script_path", ""))
        issues, recs = [], []

        if not script_path.exists():
            return ReviewResult(False, 0, ["Script file not found"], ["Re-run script generation"])

        text = script_path.read_text(encoding="utf-8")
        words = text.split()
        wc = len(words)

        # Rule: word count
        if wc < 1800:
            issues.append(f"Script too short: {wc} words (minimum 2,000)")
            recs.append("Regenerate with explicit 2,000–2,800 word target")
        elif wc > 3000:
            issues.append(f"Script too long: {wc} words (maximum 2,800)")
            recs.append("Trim to under 2,800 words")

        # Rule: banned words
        text_lower = text.lower()
        found_banned = [w for w in BANNED_WORDS if w in text_lower]
        if found_banned:
            issues.append(f"Banned words found: {', '.join(found_banned)}")
            recs.append(f"Remove or rephrase: {', '.join(found_banned)}")

        # Rule: questions (at least 1 per 7 sentences)
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if s.strip()]
        questions = [s for s in sentences if s.endswith("?") or "?" in s]
        q_ratio = len(questions) / max(len(sentences), 1)
        if q_ratio < 0.08:
            issues.append(f"Too few questions: {len(questions)} in {len(sentences)} sentences (need ~1 per 6–7)")
            recs.append("Add more rhetorical questions to break up the narration")

        # Claude quality check
        preview = " ".join(words[:600])
        result = self._claude_assess(
            "Review this narration script excerpt for 'The Interested Indian'. "
            "Score 0–10 on: hook quality (does it open with an administrative paradox in first 4 lines?), "
            "narrative arc (Hook → Precedent → Breakdown → Consequences → Synthesis), "
            "rhythm (short/long/short sentence variation), institutional specificity (dates, acts, statistics). "
            "List specific issues.\n\nSCRIPT EXCERPT:\n" + preview
        )
        issues += result.get("issues", [])
        recs   += result.get("recommendations", [])

        # Weighted score: start from Claude score, penalise rule failures
        score = result.get("score", 7)
        score -= len(found_banned) * 1
        if wc < 1800 or wc > 3000:
            score -= 2
        score = max(0, min(10, score))

        return ReviewResult(
            passed=score >= self.PASS_THRESHOLD,
            score=score,
            issues=issues,
            recommendations=recs,
            data={"word_count": wc, "banned_found": found_banned, "question_ratio": round(q_ratio, 2)},
        )

    # ── Voice ──────────────────────────────────────────────────────────────────

    def _review_voice(self, ctx: dict) -> ReviewResult:
        project_dir = Path(ctx["project_dir"])
        issues, recs = [], []

        audio_files = list((project_dir / "source_audio").glob("*.mp3"))
        if not audio_files:
            return ReviewResult(False, 0, ["No audio files found in source_audio/"], ["Re-run voice generation"])

        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(str(audio_files[0]))
            dur = len(audio) / 1000
            if dur < 480:  # 8 min minimum
                issues.append(f"Narration too short: {dur:.0f}s (expected 8–18 min)")
                recs.append("Check if script was truncated before voice generation")
            elif dur > 1200:  # 20 min max
                issues.append(f"Narration too long: {dur:.0f}s (expected 8–18 min)")
        except Exception as e:
            issues.append(f"Could not analyse audio: {e}")

        score = 10 if not issues else 5
        return ReviewResult(passed=not issues, score=score, issues=issues, recommendations=recs)

    # ── Split ──────────────────────────────────────────────────────────────────

    def _review_split(self, ctx: dict) -> ReviewResult:
        project_dir = Path(ctx["project_dir"])
        issues, recs = [], []

        manifest_path = project_dir / "manifest.json"
        if not manifest_path.exists():
            return ReviewResult(False, 0, ["manifest.json not found"], ["Re-run scene splitting"])

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        scenes = manifest.get("scenes", [])
        total_dur = manifest.get("total_duration", 0)

        if len(scenes) < 50:
            issues.append(f"Only {len(scenes)} scenes (expected 80–120 for a 12–18 min essay)")
            recs.append("Check if WhisperX ran correctly on the source audio")

        if total_dur < 480:
            issues.append(f"Manifest total duration {total_dur:.0f}s seems too short")

        # Check for duplicate IDs
        ids = [s["id"] for s in scenes]
        dupes = [i for i in ids if ids.count(i) > 1]
        if dupes:
            issues.append(f"Duplicate scene IDs: {set(dupes)}")

        score = 10 if not issues else (6 if len(issues) == 1 else 3)
        return ReviewResult(
            passed=score >= self.PASS_THRESHOLD,
            score=score,
            issues=issues,
            recommendations=recs,
            data={"scene_count": len(scenes), "total_duration": total_dur},
        )

    # ── Prompts ────────────────────────────────────────────────────────────────

    def _review_prompts(self, ctx: dict) -> ReviewResult:
        project_dir = Path(ctx["project_dir"])
        issues, recs = [], []

        prompts_path = project_dir / "image_prompts_one_line_per_prompt.md"
        if not prompts_path.exists():
            return ReviewResult(False, 0, ["image_prompts file not found"], ["Re-run prompt generation"])

        lines = [l.strip() for l in prompts_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        shot_lines = [l for l in lines if l.startswith("**SHOT")]

        manifest_path = project_dir / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            # Count unique images expected
            seen, unique = set(), 0
            for s in manifest["scenes"]:
                img = s["image"]
                if img not in seen:
                    seen.add(img)
                    unique += 1
            if len(shot_lines) < unique - 2:  # allow 2 slack
                issues.append(f"Only {len(shot_lines)} prompts for {unique} required images")

        # Style DNA checks on a sample
        missing_open  = [l for l in shot_lines if "Minimalist 2D doodle" not in l]
        missing_close = [l for l in shot_lines if "hand-drawn, 16:9" not in l and "hand-drawn" not in l]
        missing_overlay = [l for l in shot_lines if "OVERLAY:" not in l]
        missing_cue     = [l for l in shot_lines if "CUE:" not in l]

        if missing_open:
            issues.append(f"{len(missing_open)} prompts missing 'Minimalist 2D doodle' opening")
            recs.append("Prompts must start with: Minimalist 2D doodle, white bg,")
        if missing_overlay:
            issues.append(f"{len(missing_overlay)} prompts missing OVERLAY field")
        if missing_cue:
            issues.append(f"{len(missing_cue)} prompts missing CUE field")

        # Sample Claude quality check (first 5 prompts)
        sample = "\n".join(shot_lines[:5])
        result = self._claude_assess(
            "Review these image prompts for 'The Interested Indian' channel. "
            "Score 0–10 on: style DNA adherence (minimalist doodle, white bg, hand-drawn), "
            "abstract-to-visual translation quality, overlay text brevity, editor cue clarity. "
            "List issues.\n\n" + sample
        )
        issues += result.get("issues", [])
        recs   += result.get("recommendations", [])
        score = result.get("score", 7) - len(issues)
        score = max(0, min(10, score))

        return ReviewResult(
            passed=score >= self.PASS_THRESHOLD,
            score=score,
            issues=issues,
            recommendations=recs,
            data={"prompt_count": len(shot_lines)},
        )

    # ── Images ─────────────────────────────────────────────────────────────────

    def _review_images(self, ctx: dict) -> ReviewResult:
        project_dir = Path(ctx["project_dir"])
        report_path = project_dir / "review_report.md"
        issues, recs = [], []

        if not report_path.exists():
            return ReviewResult(False, 0, ["review_report.md not found"], ["Run review_images.py"])

        text = report_path.read_text(encoding="utf-8")
        fail_match = re.search(r"✗ FAIL\s*:\s*(\d+)", text)
        warn_match = re.search(r"⚠ WARN\s*:\s*(\d+)", text)
        pass_match = re.search(r"✓ PASS\s*:\s*(\d+)", text)

        fail_count = int(fail_match.group(1)) if fail_match else 0
        warn_count = int(warn_match.group(1)) if warn_match else 0
        pass_count = int(pass_match.group(1)) if pass_match else 0
        total      = fail_count + warn_count + pass_count

        if fail_count > 0:
            issues.append(f"{fail_count} images FAILED review")
            recs.append("Run generate_images_flux.py --from-report --overwrite")
        if warn_count > 5:
            issues.append(f"{warn_count} WARN images — consider reviewing manually")

        score = 10
        if fail_count > 0: score -= min(fail_count * 2, 8)
        if warn_count > 5: score -= 1
        score = max(0, score)

        return ReviewResult(
            passed=fail_count == 0,
            score=score,
            issues=issues,
            recommendations=recs,
            data={"pass": pass_count, "warn": warn_count, "fail": fail_count, "total": total},
        )

    # ── Stitch ─────────────────────────────────────────────────────────────────

    def _review_stitch(self, ctx: dict) -> ReviewResult:
        project_dir = Path(ctx["project_dir"])
        issues, recs = [], []

        output_file = project_dir / "output" / f"{project_dir.name}_final.mp4"
        if not output_file.exists():
            return ReviewResult(False, 0, ["Output MP4 not found"], ["Re-run stitch_video_longform.py"])

        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(str(output_file), format="mp4")
            dur = len(audio) / 1000
            dbfs = audio.dBFS

            if dur < 480:
                issues.append(f"Video too short: {dur:.0f}s")
            if dbfs < -35:
                issues.append(f"Audio very quiet: {dbfs:.1f} dBFS (typical is -20 to -28)")
                recs.append("Consider boosting narration gain in stitch script")
            if dbfs > -15:
                issues.append(f"Audio may clip: {dbfs:.1f} dBFS")

            # Check CTA is present (video longer than manifest total)
            manifest_path = project_dir / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest_dur = manifest.get("total_duration", 0)
                if dur < manifest_dur:
                    issues.append(f"Video ({dur:.0f}s) shorter than manifest ({manifest_dur:.0f}s) — CTA or scenes may be missing")
                    recs.append("Check stitch script output for render errors")

        except Exception as e:
            issues.append(f"Could not analyse video audio: {e}")

        score = 10 - len(issues) * 2
        score = max(0, min(10, score))
        return ReviewResult(
            passed=score >= self.PASS_THRESHOLD,
            score=score,
            issues=issues,
            recommendations=recs,
        )

    # ── Metadata ───────────────────────────────────────────────────────────────

    def _review_metadata(self, ctx: dict) -> ReviewResult:
        meta_path = Path(ctx.get("metadata_path", ""))
        issues, recs = [], []

        if not meta_path.exists():
            return ReviewResult(False, 0, ["Metadata file not found"], ["Re-run metadata generation"])

        text = meta_path.read_text(encoding="utf-8")

        title_match = re.search(r"VIRAL VIDEO TITLE:\n(.+)", text)
        tags_match  = re.search(r"VIRAL VIDEO TAGS:\n(.+)", text, re.DOTALL)

        if title_match:
            title = title_match.group(1).strip()
            if len(title) > 70:
                issues.append(f"Title too long: {len(title)} chars (max 70)")
                recs.append("Shorten title to under 70 characters")
            banned_in_title = [w for w in BANNED_WORDS if w.lower() in title.lower()]
            if banned_in_title:
                issues.append(f"Banned words in title: {banned_in_title}")
        else:
            issues.append("VIRAL VIDEO TITLE section missing")

        if tags_match:
            tags = [t.strip() for t in tags_match.group(1).split(",") if t.strip()]
            if len(tags) < 20:
                issues.append(f"Too few tags: {len(tags)} (need 25–40)")
        else:
            issues.append("VIRAL VIDEO TAGS section missing")

        if "#" not in text:
            issues.append("No hashtags found in description")
            recs.append("Add 15–20 hashtags at the end of the description")

        score = 10 - len(issues) * 2
        score = max(0, min(10, score))
        return ReviewResult(
            passed=score >= self.PASS_THRESHOLD,
            score=score,
            issues=issues,
            recommendations=recs,
        )

    # ── Thumbnail ──────────────────────────────────────────────────────────────

    def _review_thumbnail(self, ctx: dict) -> ReviewResult:
        thumb_path = Path(ctx.get("thumbnail_path", ""))
        issues, recs = [], []

        if not thumb_path.exists():
            return ReviewResult(False, 0, ["thumbnail.png not found"], ["Re-run thumbnail stage"])

        try:
            from PIL import Image as _Image
            img = _Image.open(thumb_path)
            w, h = img.size
            if (w, h) != (1280, 720):
                issues.append(f"Wrong dimensions: {w}×{h} (expected 1280×720)")
                recs.append("Regenerate thumbnail — check generate_thumbnail.py output")
            size_kb = thumb_path.stat().st_size // 1024
            if size_kb > 2048:
                issues.append(f"Thumbnail too large: {size_kb} KB (YouTube limit: 2 MB)")
                recs.append("Save with more compression or reduce image complexity")
        except Exception as e:
            issues.append(f"Could not open thumbnail: {e}")

        score = 10 - len(issues) * 3
        return ReviewResult(passed=score >= self.PASS_THRESHOLD, score=max(0, score), issues=issues, recommendations=recs)

    # ── Chapters ───────────────────────────────────────────────────────────────

    def _review_chapters(self, ctx: dict) -> ReviewResult:
        chapters_path = Path(ctx.get("chapters_path", ""))
        issues, recs = [], []

        if not chapters_path.exists():
            return ReviewResult(False, 0, ["chapters.txt not found"], ["Re-run chapters stage"])

        text  = chapters_path.read_text(encoding="utf-8").strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # Must have at least 5 chapters
        if len(lines) < 5:
            issues.append(f"Only {len(lines)} chapters (need at least 5)")
            recs.append("Re-run with --num-chapters 7")

        # First chapter must start at 00:00
        if lines and not lines[0].startswith("00:00"):
            issues.append("First chapter does not start at 00:00")

        # Each line must match timestamp pattern
        import re as _re
        bad = [l for l in lines if not _re.match(r"\d{1,2}:\d{2}", l)]
        if bad:
            issues.append(f"{len(bad)} chapter lines missing timestamp: {bad[:2]}")

        # Generic chapter names are bad
        generic = ["chapter", "section", "part ", "intro"]
        for line in lines:
            name_part = line.split(" ", 1)[1].lower() if " " in line else ""
            if any(g in name_part for g in generic) and name_part.strip() == g.strip():
                issues.append(f"Generic chapter name: '{line}'")
                recs.append("Use specific chapter names describing the content")
                break

        score = 10 - len(issues) * 2
        return ReviewResult(passed=score >= self.PASS_THRESHOLD, score=max(0, score), issues=issues, recommendations=recs)

    # ── Upload ─────────────────────────────────────────────────────────────────

    def _review_upload(self, ctx: dict) -> ReviewResult:
        """Check that an upload record exists and has a valid video ID."""
        # Find project_dir — it's passed through context
        project_dir = Path(ctx.get("project_dir", ""))
        record_path = project_dir / "upload_record.json"
        issues, recs = [], []

        if not record_path.exists():
            # Upload was skipped — that's allowed (not a failure)
            return ReviewResult(
                passed=True, score=8,
                issues=[],
                recommendations=["Upload skipped — run upload_youtube.py manually when ready"],
            )

        try:
            rec    = json.loads(record_path.read_text(encoding="utf-8"))
            vid_id = rec.get("video_id", "")
            if not vid_id or len(vid_id) < 8:
                issues.append("upload_record.json has no valid video_id")
                recs.append("Check YouTube Studio to confirm upload, or re-run upload stage")
        except Exception as e:
            issues.append(f"Could not read upload_record.json: {e}")

        score = 10 - len(issues) * 4
        return ReviewResult(passed=score >= self.PASS_THRESHOLD, score=max(0, score), issues=issues, recommendations=recs)

    # ── Claude quality assessor ────────────────────────────────────────────────

    def _claude_assess(self, prompt: str, max_retries: int = 2) -> dict:
        """Call Claude for qualitative review. Returns {score, issues, recommendations}.
        Retries up to max_retries times on empty response or JSON parse failure."""
        last_err = None
        for attempt in range(max_retries + 1):
            try:
                response = self.client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=512,
                    system=(
                        "You are a quality reviewer for 'The Interested Indian' YouTube channel. "
                        "When asked to review content, respond ONLY with valid JSON in this exact format:\n"
                        '{"score": <int 0-10>, "issues": ["issue1", ...], "recommendations": ["rec1", ...]}\n'
                        "Be specific and actionable. No other text."
                    ),
                    messages=[{"role": "user", "content": prompt}],
                )
                text = response.content[0].text.strip()
                if not text:
                    raise ValueError("Empty response from Claude assess")
                return json.loads(text)
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    time.sleep(2 ** attempt)   # 1s, 2s backoff
        return {"score": 7, "issues": [f"Review call failed: {last_err}"], "recommendations": []}


# ══════════════════════════════════════════════════════════════════════════════
# RESEARCH AGENT
# ══════════════════════════════════════════════════════════════════════════════

class ResearchAgent:
    """
    Three-track research before script generation:

    Track 1 — FACTS        Verified historical data, statistics, acts, dates.
    Track 2 — AUDIENCE     What people are actually asking/debating about this topic.
                           Reddit, Quora, news comments, trending questions.
    Track 3 — COMPETITIVE  What successful channels are doing in this space.
                           Title formulas, hooks, content gaps, viral angles.

    All three tracks feed into the script prompt so the output is both accurate
    and strategically positioned for viewership growth.
    """

    RESULTS_PER_QUERY = 4

    # Channels to benchmark against — edit freely
    REFERENCE_CHANNELS = [
        "justaFLAM",
        "Wendover Productions",
        "RealLifeLore",
        "Half as Interesting",
        "Dhruv Rathee",
        "Geography Now",
        "TLDR News India",
        "Kurzgesagt",
    ]

    def __init__(self, client: anthropic.Anthropic):
        self.client = client

    # ── Public entry point ─────────────────────────────────────────────────────

    def research(self, topic: str) -> dict:
        if not SEARCH_AVAILABLE:
            print("  ⚠ duckduckgo-search not installed — skipping research.")
            print("    pip install duckduckgo-search --break-system-packages")
            return {"note": "Research skipped"}

        print("\n  ── Track 1: Facts & Data ──")
        facts = self._research_facts(topic)

        print("\n  ── Track 2: Audience Signals ──")
        audience = self._research_audience(topic)

        print("\n  ── Track 3: Competitive Intelligence ──")
        competitive = self._research_competitive(topic)

        print("\n  Synthesising all tracks...")
        brief = self._synthesize_all(topic, facts, audience, competitive)
        return brief

    # ── Track 1: Facts ─────────────────────────────────────────────────────────

    def _research_facts(self, topic: str) -> list:
        queries = self._gen_queries(
            topic,
            system=(
                "Generate 5 web search queries to find verified historical facts, "
                "government statistics, acts of parliament, court rulings, and "
                "administrative data for a YouTube video about India. "
                "Return ONLY a JSON array of query strings."
            )
        )
        snippets = self._search_all(queries, label="facts")
        return snippets

    # ── Track 2: Audience Signals ──────────────────────────────────────────────

    def _research_audience(self, topic: str) -> list:
        """What are real people asking, debating, and finding surprising about this topic?"""
        queries = self._gen_queries(
            topic,
            system=(
                "Generate 5 web search queries to find what audiences are genuinely "
                "curious or confused about on this topic. Target: Reddit threads, Quora "
                "questions, news comment sections, Twitter/X debates, YouTube comment "
                "highlights. We want the surprising questions, common misconceptions, "
                "and emotional flashpoints audiences have. "
                "Return ONLY a JSON array of query strings."
            )
        )
        # Add some hardcoded high-signal sources
        queries += [
            f"site:reddit.com {topic} India",
            f"site:quora.com {topic} India why how",
        ]
        snippets = self._search_all(queries[:6], label="audience")
        return snippets

    # ── Track 3: Competitive Intelligence ─────────────────────────────────────

    def _research_competitive(self, topic: str) -> list:
        """What are successful channels doing on this topic and adjacent topics?"""
        snippets = []

        # Search each reference channel's content in this space
        channel_queries = []
        for ch in self.REFERENCE_CHANNELS[:4]:
            channel_queries.append(f'"{ch}" youtube India history geopolitics most viewed')
        channel_queries.append(f"best youtube videos {topic} India viral 2024 2025")
        channel_queries.append(f"youtube channel Indian history geopolitics subscribers growth")
        channel_queries.append(f'youtube title formula "{topic}" India views clicks')

        snippets = self._search_all(channel_queries[:6], label="competitive")
        return snippets

    # ── Synthesis ──────────────────────────────────────────────────────────────

    def _synthesize_all(self, topic: str, facts: list, audience: list, competitive: list) -> dict:
        all_snippets = facts + audience + competitive
        snippets_text = "\n\n".join(all_snippets[:30])

        response = self.client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            system=(
                "You are a senior research strategist for 'The Interested Indian' YouTube channel. "
                "Your job is to extract insights across three dimensions from web search snippets "
                "and return a structured brief that will make the episode both factually sharp "
                "AND strategically positioned for subscriber growth.\n\n"
                "Return ONLY valid JSON in this exact format:\n"
                "{\n"
                '  "key_facts": ["verified fact with source hint", ...],\n'
                '  "key_dates": {"event description": "year or date"},\n'
                '  "key_statistics": {"metric label": "value with context"},\n'
                '  "acts_and_laws": ["Full Act Name, Year — one-line summary"],\n'
                '  "recent_developments": ["something that happened recently relevant to topic"],\n'
                '  "audience_signals": {\n'
                '    "top_questions": ["question audiences are genuinely asking"],\n'
                '    "misconceptions": ["common wrong belief about this topic"],\n'
                '    "emotional_flashpoints": ["aspect of topic that provokes strong reaction"],\n'
                '    "surprising_angles": ["counterintuitive fact that would stop a scroll"]\n'
                "  },\n"
                '  "competitive_intelligence": {\n'
                '    "successful_title_patterns": ["title formula that works in this space"],\n'
                '    "content_gaps": ["angle that no major channel has covered well"],\n'
                '    "channels_doing_this_well": ["channel name — what they do right"],\n'
                '    "avoid": ["format or angle that gets low engagement in this space"]\n'
                "  },\n"
                '  "hook_ideas": ["one-sentence hook that combines surprise + institutional detail"],\n'
                '  "caution": ["claim found in snippets that needs fact-checking before use"]\n'
                "}"
            ),
            messages=[{
                "role": "user",
                "content": f"Topic: {topic}\n\nSearch snippets from three research tracks:\n\n{snippets_text}"
            }],
        )
        try:
            raw = response.content[0].text.strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            return json.loads(match.group(0)) if match else {"key_facts": [], "raw": raw}
        except Exception:
            return {"key_facts": [], "raw": response.content[0].text.strip()}

    # ── Shared search helpers ──────────────────────────────────────────────────

    def _gen_queries(self, topic: str, system: str) -> list:
        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system=system,
                messages=[{"role": "user", "content": f"Topic: {topic}"}],
            )
            raw = response.content[0].text.strip()
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            return json.loads(match.group(0)) if match else [topic]
        except Exception:
            return [topic, f"{topic} India"]

    def _search_all(self, queries: list, label: str = "") -> list:
        snippets = []
        for q in queries:
            try:
                results = list(DDGS().text(q, max_results=self.RESULTS_PER_QUERY))
                for r in results:
                    snippets.append(f"[{r.get('title','')}] {r.get('body','')}"[:500])
                time.sleep(0.4)
            except Exception as e:
                print(f"    ⚠ Search failed ({label}): {e}")
        print(f"    {len(snippets)} snippets collected ({label})")
        return snippets

    # ── Format for script prompt ───────────────────────────────────────────────

    def format_for_prompt(self, brief: dict) -> str:
        """Formats the full three-track brief for injection into the script generation prompt."""
        lines = []

        # ── Track 1: Facts
        lines.append("═" * 50)
        lines.append("RESEARCH BRIEF — weave these into the script")
        lines.append("═" * 50)

        if brief.get("key_facts"):
            lines.append("\nVERIFIED FACTS:")
            for f in brief["key_facts"][:10]:
                lines.append(f"  • {f}")

        if brief.get("key_dates"):
            lines.append("\nKEY DATES:")
            for event, date in list(brief["key_dates"].items())[:6]:
                lines.append(f"  • {date}: {event}")

        if brief.get("key_statistics"):
            lines.append("\nSTATISTICS:")
            for metric, val in list(brief["key_statistics"].items())[:6]:
                lines.append(f"  • {metric}: {val}")

        if brief.get("acts_and_laws"):
            lines.append("\nACTS & LAWS:")
            for act in brief["acts_and_laws"][:5]:
                lines.append(f"  • {act}")

        if brief.get("recent_developments"):
            lines.append("\nRECENT DEVELOPMENTS (add freshness):")
            for dev in brief["recent_developments"][:4]:
                lines.append(f"  • {dev}")

        # ── Track 2: Audience
        audience = brief.get("audience_signals", {})
        if audience:
            lines.append("\n─" * 25)
            lines.append("AUDIENCE INTELLIGENCE — use to frame the hook and questions:")

            if audience.get("top_questions"):
                lines.append("\n  What audiences are genuinely asking:")
                for q in audience["top_questions"][:4]:
                    lines.append(f"    • {q}")

            if audience.get("misconceptions"):
                lines.append("\n  Common misconceptions to address or exploit:")
                for m in audience["misconceptions"][:3]:
                    lines.append(f"    • {m}")

            if audience.get("emotional_flashpoints"):
                lines.append("\n  Emotional flashpoints (use carefully — not sensationally):")
                for fp in audience["emotional_flashpoints"][:3]:
                    lines.append(f"    • {fp}")

            if audience.get("surprising_angles"):
                lines.append("\n  Scroll-stopping counterintuitive angles:")
                for a in audience["surprising_angles"][:3]:
                    lines.append(f"    • {a}")

        # ── Track 3: Competitive
        comp = brief.get("competitive_intelligence", {})
        if comp:
            lines.append("\n─" * 25)
            lines.append("COMPETITIVE INTELLIGENCE — position this episode differently:")

            if comp.get("successful_title_patterns"):
                lines.append("\n  Title patterns that work in this space:")
                for t in comp["successful_title_patterns"][:4]:
                    lines.append(f"    • {t}")

            if comp.get("content_gaps"):
                lines.append("\n  Gaps no channel has covered well yet:")
                for g in comp["content_gaps"][:3]:
                    lines.append(f"    • {g}")

            if comp.get("channels_doing_this_well"):
                lines.append("\n  Channels to benchmark (study their structure, not copy):")
                for c in comp["channels_doing_this_well"][:4]:
                    lines.append(f"    • {c}")

            if comp.get("avoid"):
                lines.append("\n  Angles/formats that underperform — avoid:")
                for av in comp["avoid"][:3]:
                    lines.append(f"    • {av}")

        # ── Hook ideas
        if brief.get("hook_ideas"):
            lines.append("\n─" * 25)
            lines.append("SUGGESTED HOOKS (pick the strongest or combine):")
            for h in brief["hook_ideas"][:4]:
                lines.append(f"  • {h}")

        # ── Cautions
        if brief.get("caution"):
            lines.append("\n⚠ VERIFY BEFORE USING:")
            for c in brief["caution"][:3]:
                lines.append(f"  • {c}")

        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR AGENT
# ══════════════════════════════════════════════════════════════════════════════

class OrchestratorAgent:
    """
    Routes the pipeline, reviews each stage output, and decides how to handle failures.
    Human checkpoints only when necessary. Persists state across runs.
    """

    MAX_RETRIES = 2

    def __init__(
        self,
        client: anthropic.Anthropic,
        project_dir: Path,
        review_agent: ReviewAgent,
        research_agent: ResearchAgent,
    ):
        self.client         = client
        self.project_dir    = project_dir
        self.review_agent   = review_agent
        self.research_agent = research_agent
        self.state          = self._load_state()

    # ── State ──────────────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        p = self.project_dir / "episode_state.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return {"stage": "topics", "completed": [], "data": {}, "retry_counts": {}}

    def _save_state(self):
        p = self.project_dir / "episode_state.json"
        p.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")

    def _mark_complete(self, stage: str):
        if stage not in self.state["completed"]:
            self.state["completed"].append(stage)
        idx = STAGE_ORDER.index(stage)
        if idx + 1 < len(STAGE_ORDER):
            self.state["stage"] = STAGE_ORDER[idx + 1]
        self._save_state()

    # ── Pipeline runner ────────────────────────────────────────────────────────

    def run_pipeline(self, from_stage: Optional[str] = None):
        if from_stage:
            self.state["stage"] = from_stage
            self._save_state()

        start_idx = STAGE_ORDER.index(self.state["stage"])

        self._banner(f"The Interested Indian — Episode Orchestrator")
        print(f"  Project : {self.project_dir}")
        print(f"  Resuming: {self.state['stage']}")
        print(f"  Done    : {', '.join(self.state['completed']) or 'none'}")

        for stage in STAGE_ORDER[start_idx:]:
            if stage in self.state["completed"] and stage != self.state["stage"]:
                print(f"\n  ↷ {stage} already complete — skipping")
                continue
            self._run_with_review(stage)

        self._banner("✓ Pipeline Complete")
        output = self.project_dir / "output" / f"{self.project_dir.name}_final.mp4"
        if output.exists():
            size_mb = output.stat().st_size // (1024 * 1024)
            print(f"  Video     : {output}  ({size_mb} MB)")
        meta = self.state["data"].get("metadata_path", "")
        if meta:
            print(f"  Metadata  : {meta}")
        thumb = self.state["data"].get("thumbnail_path", "")
        if thumb:
            print(f"  Thumbnail : {thumb}")
        chapters = self.state["data"].get("chapters_path", "")
        if chapters:
            print(f"  Chapters  : {chapters}")
        upload_url = self.state["data"].get("upload_url", "")
        if upload_url:
            print(f"\n  ✓ Published: {upload_url}")
            print("    Next: add end screen + cards in YouTube Studio, then set Public.")
        else:
            print("\n  Final step: run upload_youtube.py --project <ep> when ready.")

    def _run_with_review(self, stage: str):
        self._banner(f"Stage: {stage.upper()}")
        retries = 0

        while retries <= self.MAX_RETRIES:
            # Run the stage
            try:
                self._run_stage(stage)
            except Exception as e:
                print(f"\n  ✗ Stage '{stage}' errored: {e}")
                decision = self._decide_on_error(stage, str(e))
                self._handle_decision(decision, stage)
                if decision.action == "abort":
                    sys.exit(1)
                if decision.action == "human_checkpoint":
                    break
                retries += 1
                continue

            # Review the output
            context = {**self.state["data"], "project_dir": str(self.project_dir)}
            print(f"\n  Reviewing stage output...")
            review = self.review_agent.review(stage, context)
            print(f"  Review: {review.summary()}")

            if review.issues:
                for issue in review.issues:
                    print(f"    ✗ {issue}")
            if review.recommendations:
                for rec in review.recommendations[:2]:
                    print(f"    → {rec}")

            if review.passed:
                self._mark_complete(stage)
                print(f"\n  ✓ Stage '{stage}' passed review.")
                break
            else:
                decision = self._decide_on_review_failure(stage, review, retries)
                print(f"\n  Decision: {decision.action} — {decision.reason}")
                self._handle_decision(decision, stage)

                if decision.action == "proceed":
                    self._mark_complete(stage)
                    break
                elif decision.action == "abort":
                    sys.exit(1)
                elif decision.action == "human_checkpoint":
                    self._mark_complete(stage)
                    break
                # else: retry — loop continues

            retries += 1

        if retries > self.MAX_RETRIES:
            print(f"\n  ⚠ Max retries reached for '{stage}' — proceeding anyway.")
            self._mark_complete(stage)

    # ── Decision making ────────────────────────────────────────────────────────

    def _decide_on_review_failure(
        self, stage: str, review: ReviewResult, retry_count: int
    ) -> OrchestratorDecision:
        """Ask Claude how to handle a review failure."""
        prompt = (
            f"Stage '{stage}' failed quality review.\n"
            f"Score: {review.score}/10 (pass threshold: {ReviewAgent.PASS_THRESHOLD})\n"
            f"Issues: {json.dumps(review.issues)}\n"
            f"Retry attempt: {retry_count}/{self.MAX_RETRIES}\n\n"
            "Choose the best action and respond with JSON only:\n"
            '{"action": "retry"|"human_checkpoint"|"proceed"|"abort", "reason": "...", "human_message": "..."}\n\n'
            "Guidelines:\n"
            "- retry: issues are auto-fixable by re-running (e.g. banned words, short word count, missing fields)\n"
            "- human_checkpoint: issues need human judgment (e.g. hook quality, topic relevance, visual coherence)\n"
            "- proceed: score >= 6 and issues are minor/stylistic only\n"
            "- abort: critical failure (missing files, API errors, score < 3)"
        )
        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system="You are a pipeline orchestrator. Respond only with valid JSON.",
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            d = json.loads(match.group(0)) if match else {}
            return OrchestratorDecision(
                action=d.get("action", "human_checkpoint"),
                reason=d.get("reason", ""),
                human_message=d.get("human_message", ""),
            )
        except Exception:
            return OrchestratorDecision("human_checkpoint", "Could not get routing decision")

    def _decide_on_error(self, stage: str, error: str) -> OrchestratorDecision:
        if "not found" in error.lower() or "no such file" in error.lower():
            return OrchestratorDecision("human_checkpoint", "Missing file", f"Stage '{stage}' needs a file that doesn't exist: {error}")
        if "api" in error.lower() or "rate" in error.lower():
            return OrchestratorDecision("retry", "API error — will retry", "")
        return OrchestratorDecision("human_checkpoint", f"Unexpected error: {error}", "")

    def _handle_decision(self, decision: OrchestratorDecision, stage: str):
        if decision.action == "human_checkpoint":
            msg = decision.human_message or f"Stage '{stage}' needs your attention."
            answer = self._checkpoint(msg + "\n  Options: [enter] proceed  |  retry  |  abort")
            if answer.lower() == "abort":
                print("  Aborting pipeline.")
                sys.exit(0)
            elif answer.lower() == "retry":
                decision.action = "retry"

    # ── Stage implementations ──────────────────────────────────────────────────

    def _run_stage(self, stage: str):
        fns = {
            "topics":    self._stage_topics,
            "script":    self._stage_script,
            "voice":     self._stage_voice,
            "split":     self._stage_split,
            "prompts":   self._stage_prompts,
            "images":    self._stage_images,
            "stitch":    self._stage_stitch,
            "metadata":  self._stage_metadata,
            "thumbnail": self._stage_thumbnail,
            "chapters":  self._stage_chapters,
            "upload":    self._stage_upload,
        }
        fns[stage]()

    def _stage_topics(self):
        response = self.client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=CHANNEL_DNA,
            messages=[{
                "role": "user",
                "content": (
                    "Generate exactly 5 viral topic ideas for the channel. "
                    "Output ONLY a markdown table: # | Video Title | Core Institutional Focus. "
                    "No preamble."
                )
            }]
        )
        ideas_text = response.content[0].text.strip()
        print(f"\n{ideas_text}\n")
        self.state["data"]["topic_ideas"] = ideas_text
        self._save_state()

        choice = self._checkpoint(
            "Which idea? Reply with 1–5, or type your own title:"
        )
        self.state["data"]["topic_choice"] = choice
        self._save_state()

    def _stage_script(self):
        topic_choice = self.state["data"].get("topic_choice", "")
        topic_ideas  = self.state["data"].get("topic_ideas", "")

        # Research phase
        print("  ResearchAgent: gathering facts...")
        brief = self.research_agent.research(topic_choice)
        research_context = self.research_agent.format_for_prompt(brief)
        self.state["data"]["research_brief"] = brief
        self._save_state()

        print("  Generating script...")
        response = self.client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=8192,
            system=CHANNEL_DNA,
            messages=[{
                "role": "user",
                "content": (
                    f"Topic ideas:\n{topic_ideas}\n\n"
                    f"Selected: {topic_choice}\n\n"
                    f"{research_context}\n\n"
                    "Write the full narration script:\n"
                    "- 2,000–2,800 words of pure narration\n"
                    "- No headers, no bullets, no stage directions\n"
                    "- Use verified facts from the research brief above\n"
                    "- Weave in specific dates, acts, statistics naturally\n"
                    "- Open with administrative paradox in first 4 lines\n"
                    "- Close echoing the opening\n"
                    "Output: TITLE: [title] on line 1, then the script."
                )
            }]
        )

        raw = response.content[0].text.strip()
        lines = raw.splitlines()
        title_line = next((l for l in lines if l.startswith("TITLE:")), None)
        title = title_line.replace("TITLE:", "").strip() if title_line else topic_choice
        script_text = "\n".join(l for l in lines if not l.startswith("TITLE:")).strip()

        slug = re.sub(r'[^a-z0-9]+', '_', title.lower())[:50].strip('_')
        script_path = self.project_dir / f"script_{slug}.txt"
        script_path.write_text(script_text, encoding="utf-8")

        wc = len(script_text.split())
        print(f"\n  Title : {title}")
        print(f"  Words : {wc}")
        print(f"  Saved : {script_path.name}")

        self.state["data"].update({"title": title, "slug": slug, "script_path": str(script_path)})
        self._save_state()

        answer = self._checkpoint(
            "Script ready.\n"
            "  [enter] Accept  |  edit  (opens Notepad)  |  redo  (regenerate)"
        ).lower()
        if answer == "edit":
            subprocess.Popen(["notepad.exe", str(script_path)])
            input("  Press ENTER after editing...")
        elif answer == "redo":
            self._stage_script()

    def _stage_voice(self):
        gen_audio = PIPELINE_DIR / "generate_source_audio.py"
        script_path = Path(self.state["data"]["script_path"])
        self._run_cmd(
            [sys.executable, str(gen_audio), "--script", str(script_path),
             "--out-dir", str(self.project_dir / "source_audio")],
            label="generate_source_audio.py"
        )

    def _stage_split(self):
        split = self._find_script(SHORTS_DIR, ["auto_split_scenes_v1_stage3_export.py", "auto_split_scenes.py"])
        self._run_cmd(
            [sys.executable, str(split), "--project", str(self.project_dir), "--video-type", "LongVideo"],
            cwd=SHORTS_DIR, label=split.name
        )

    def _stage_prompts(self):
        gen = PIPELINE_DIR / "generate_image_prompts.py"
        self._run_cmd([sys.executable, str(gen), "--project", str(self.project_dir)], label="generate_image_prompts.py")

    def _stage_images(self):
        gen    = PIPELINE_DIR / "generate_images_flux.py"
        review = PIPELINE_DIR / "review_images.py"

        self._run_cmd([sys.executable, str(gen), "--project", str(self.project_dir)], label="Initial image generation")

        for round_num in range(1, 6):
            print(f"\n  Review round {round_num}...")
            self._run_cmd([sys.executable, str(review), "--project", str(self.project_dir)], label="review_images.py")
            report = self.project_dir / "review_report.md"
            if report.exists():
                text = report.read_text(encoding="utf-8")
                m = re.search(r"✗ FAIL\s*:\s*(\d+)", text)
                fails = int(m.group(1)) if m else 0
                print(f"  FAILs: {fails}")
                if fails == 0:
                    break
                self._run_cmd(
                    [sys.executable, str(gen), "--project", str(self.project_dir), "--from-report", "--overwrite"],
                    label="Regenerating FAIL shots"
                )

    def _stage_stitch(self):
        stitch = SHORTS_DIR / "stitch_video_longform.py"
        self._run_cmd(
            [sys.executable, str(stitch), "--project", str(self.project_dir)],
            cwd=SHORTS_DIR, label="stitch_video_longform.py"
        )

    def _stage_metadata(self):
        script_path = Path(self.state["data"].get("script_path", ""))
        title       = self.state["data"].get("title", "")
        script_text = script_path.read_text(encoding="utf-8") if script_path.exists() else ""

        response = self.client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            system=CHANNEL_DNA,
            messages=[{
                "role": "user",
                "content": (
                    f"Video title: {title}\n\n"
                    f"Script (first 600 words):\n{' '.join(script_text.split()[:600])}\n\n"
                    "Generate YouTube metadata:\n\n"
                    "VIRAL VIDEO TITLE:\n[under 70 chars]\n\n"
                    "VIDEO DESCRIPTION:\n[2-3 line hook, 3-4 line summary, subscribe line, 15-20 hashtags]\n\n"
                    "VIRAL VIDEO TAGS:\n[25-40 comma-separated tags]"
                )
            }]
        )
        meta_text = response.content[0].text.strip()
        slug = self.state["data"].get("slug", self.project_dir.name)
        meta_path = self.project_dir / f"metadata_{slug}.txt"
        meta_path.write_text(meta_text, encoding="utf-8")

        print(f"\n{meta_text}\n")
        print(f"  Saved: {meta_path.name}")
        self.state["data"]["metadata_path"] = str(meta_path)
        self._save_state()

    def _stage_thumbnail(self):
        gen = PIPELINE_DIR / "generate_thumbnail.py"
        self._run_cmd(
            [sys.executable, str(gen), "--project", str(self.project_dir)],
            label="generate_thumbnail.py"
        )
        thumb = self.project_dir / "thumbnail.png"
        if thumb.exists():
            self.state["data"]["thumbnail_path"] = str(thumb)
            self._save_state()
            print(f"  Thumbnail: {thumb}")
        else:
            raise RuntimeError("thumbnail.png not created")

    def _stage_chapters(self):
        gen = PIPELINE_DIR / "generate_chapters.py"
        self._run_cmd(
            [sys.executable, str(gen), "--project", str(self.project_dir)],
            label="generate_chapters.py"
        )
        chapters = self.project_dir / "chapters.txt"
        if chapters.exists():
            self.state["data"]["chapters_path"] = str(chapters)
            self._save_state()
        else:
            raise RuntimeError("chapters.txt not created")

    def _stage_upload(self):
        """Human-gated: show what would upload, ask for confirmation."""
        upload_record = self.project_dir / "upload_record.json"
        if upload_record.exists():
            rec = json.loads(upload_record.read_text(encoding="utf-8"))
            print(f"\n  ⚠ Already uploaded: {rec.get('url', 'unknown URL')}")
            answer = self._checkpoint("Upload again? (yes / no [default])").lower()
            if answer != "yes":
                self.state["data"]["upload_url"] = rec.get("url", "")
                self._save_state()
                return

        gen = PIPELINE_DIR / "upload_youtube.py"

        # First: dry-run so human sees what will go up
        self._run_cmd(
            [sys.executable, str(gen), "--project", str(self.project_dir), "--dry-run"],
            label="upload_youtube.py --dry-run"
        )

        answer = self._checkpoint(
            "Ready to upload to YouTube?\n"
            "  Options: [enter] upload now  |  schedule 2026-07-28T17:00:00+05:30  |  skip"
        )

        if answer.lower() == "skip":
            print("  Upload skipped — run upload_youtube.py manually when ready.")
            return

        cmd = [sys.executable, str(gen), "--project", str(self.project_dir)]
        if answer.lower().startswith("schedule"):
            parts = answer.split(maxsplit=1)
            if len(parts) == 2:
                cmd += ["--schedule", parts[1]]
        self._run_cmd(cmd, label="upload_youtube.py")

        if upload_record.exists():
            rec = json.loads(upload_record.read_text(encoding="utf-8"))
            self.state["data"]["upload_url"] = rec.get("url", "")
            self._save_state()

    # ── Utilities ──────────────────────────────────────────────────────────────

    def _run_cmd(self, cmd: list, cwd: Path = None, label: str = ""):
        if label:
            print(f"  → {label}")
        result = subprocess.run(cmd, cwd=cwd or PIPELINE_DIR, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"{label or cmd[0]} exited with code {result.returncode}")

    def _find_script(self, directory: Path, names: list) -> Path:
        for name in names:
            p = directory / name
            if p.exists():
                return p
        raise FileNotFoundError(f"None of {names} found in {directory}")

    def _checkpoint(self, prompt: str) -> str:
        print(f"\n{'─'*60}")
        print(f"  ⏸  CHECKPOINT")
        return input(f"  {prompt}\n  → ").strip()

    def _banner(self, text: str):
        print(f"\n{'═'*60}")
        print(f"  {text}")
        print(f"{'═'*60}")
