"""
review_script.py — Channel DNA & Wittiness Reviewer for The Interested Indian

Reads a narration script and grades it against CHANNEL_DNA:
  • Hook quality — specific number/fact, absurdity-first, within 4 lines
  • Humor density — every 2-3 paragraphs must have a joke/analogy/poke
  • Jargon audit — every policy term must be immediately translated
  • Banned words — "genuinely", "honestly", "straightforward", clichés
  • Audience address — anticipating viewer confusion/skepticism
  • Sentence rhythm — short/long variation, ends with question or punchline
  • Narrative arc — hook → explain → mechanism → consequence → close
  • Wittiness score — overall entertainment value

Output:
  • Terminal: colour-coded pass/warn/fail per paragraph
  • File: {project}/script_review.md — full report with rewrite suggestions

Usage:
    python review_script.py --project ep01
    python review_script.py --script ep01/script_*.txt
    python review_script.py --project ep01 --deep    # asks Claude to rewrite boring sections
    python review_script.py --project ep01 --quick   # flags only, no rewrites
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent

# ── Auto-load .env ─────────────────────────────────────────────────────────────
_env_path = PIPELINE_DIR / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── Banned words & clichés ─────────────────────────────────────────────────────
BANNED_WORDS = [
    "genuinely", "honestly", "straightforward",
    "unleash", "unlock", "dive into", "tapestry", "game-changer",
    "delve", "testament", "it's important to note", "in conclusion",
    "fascinating", "crucial", "pivotal", "seamless",
]

HUMOR_SIGNALS = [
    "sort of like", "kind of like", "imagine if", "think of it as",
    "which is basically", "which is bureaucrat for", "which is fancy for",
    "I know exactly what", "I know what you", "before you say",
    "stay with me", "hear me out", "wait,", "okay, wait",
    "I'm going to be honest", "I spent", "I still don't",
    "— right?", "— okay?", "right?", "(yes, really)", "(seriously)",
    "cricket", "food delivery", "gaming", "zomato", "swiggy",
    "whatsapp", "ola", "uber", "ipl", "bcci",
]

JARGON_WORDS = [
    "devolution", "divisible pool", "vertical share", "horizontal share",
    "inter-se", "fiscal federalism", "revenue deficit grant",
    "tax devolution", "non-plan", "plan expenditure",
    "article 356", "president's rule", "constitutional machinery",
    "floor test", "anti-defection", "whip", "prorogation",
    "constituency", "lok sabha", "rajya sabha", "vidhan sabha",
    "finance commission", "planning commission", "niti aayog",
    "gst council", "concurrent list", "state list", "union list",
    "directive principles", "fundamental rights", "preamble",
]

CLICHE_PHRASES = [
    "over the years", "throughout history", "since time immemorial",
    "it is worth noting", "needless to say", "as we all know",
    "in the annals", "the powers that be", "at the end of the day",
    "on the other hand", "in other words", "to put it simply",
]

CHANNEL_DNA_BRIEF = """
You are a script quality reviewer for "The Interested Indian" — a viral Indian history/policy YouTube channel.

CHANNEL DNA (summary):
- Voice: First-person ("I"), conversational, occasionally self-deprecating. Narrator learns alongside viewer.
- Hook: Opens with an absurd-sounding fact. Specific number within first 4 lines. Makes viewer feel slightly betrayed.
- Humor mandate: Every 2-3 paragraphs must have ONE of: modern analogy (gaming/cricket/food delivery), self-aware observation, deadpan understatement, or audience poke ("I know exactly what you're thinking").
- Jargon rule: Every policy term immediately translated in plain language on the SAME sentence.
- Audience address: Anticipate viewer confusion. Name it directly. "Before you say..."
- Rhythm: Short sentence. Fact. Longer connective sentence. Short sentence. End every 5-6 sentences with question or punchline.
- BANNED: "genuinely", "honestly", "straightforward", "unleash", "unlock", "dive into", "tapestry", "game-changer", "delve", "fascinating", "crucial", "pivotal", any corporate cliché.
- Narrative arc: Hook → Explain the rule → How the machine works → Unintended consequence → Who wins/loses → Close that echoes the hook.

REVIEWER MINDSET:
You are a harsh but constructive editor. Your job is to find every sentence that a viewer might skip past or zone out on.
Boring = anything that reads like a Wikipedia article, a news report, or a textbook.
Interesting = specific, personal, surprising, slightly unfair to someone powerful.
"""


def _load_script(project: Path | None, script_arg: str | None) -> tuple[str, Path]:
    """Returns (script_text, script_path)."""
    if script_arg:
        p = Path(script_arg)
        if not p.is_absolute():
            p = PIPELINE_DIR / p
        if not p.exists():
            matches = list(PIPELINE_DIR.glob(script_arg))
            if matches:
                p = matches[0]
        if not p.exists():
            print(f"❌ Script not found: {script_arg}")
            sys.exit(1)
        return p.read_text(encoding="utf-8"), p

    if project:
        matches = sorted(project.glob("script_*.txt"))
        if matches:
            p = matches[-1]
            return p.read_text(encoding="utf-8"), p

    print("❌ No script found. Pass --project or --script.")
    sys.exit(1)


def _split_paragraphs(text: str) -> list[str]:
    """Split into non-empty paragraphs."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


# ── Deterministic checks ───────────────────────────────────────────────────────

def check_banned_words(text: str) -> list[dict]:
    issues = []
    for word in BANNED_WORDS:
        pattern = re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
        for m in pattern.finditer(text):
            line_no = text[:m.start()].count('\n') + 1
            issues.append({"word": word, "line": line_no, "context": _snippet(text, m.start())})
    return issues


def check_humor_density(paragraphs: list[str]) -> list[dict]:
    """Flag runs of 3+ consecutive paragraphs with no humor signals."""
    issues = []
    no_humor_run = []
    for i, para in enumerate(paragraphs):
        has_humor = any(sig.lower() in para.lower() for sig in HUMOR_SIGNALS)
        if has_humor:
            if len(no_humor_run) >= 3:
                issues.append({
                    "paragraphs": list(no_humor_run),
                    "count": len(no_humor_run),
                    "msg": f"Paragraphs {no_humor_run[0]+1}–{no_humor_run[-1]+1}: {len(no_humor_run)} consecutive paragraphs with no humor/analogy"
                })
            no_humor_run = []
        else:
            no_humor_run.append(i)

    if len(no_humor_run) >= 3:
        issues.append({
            "paragraphs": list(no_humor_run),
            "count": len(no_humor_run),
            "msg": f"Paragraphs {no_humor_run[0]+1}–{no_humor_run[-1]+1}: {len(no_humor_run)} consecutive paragraphs with no humor"
        })
    return issues


def check_jargon(paragraphs: list[str]) -> list[dict]:
    """Flag jargon words not followed by a translation within the same sentence."""
    issues = []
    translation_signals = ["which is", "meaning", "basically", "in plain", "in other words",
                           "that is", "i.e.", "—", "or ", "also known as", "bureaucrat for",
                           "fancy for", "a fancy way", "which means", "what that means"]
    for i, para in enumerate(paragraphs):
        sentences = re.split(r'(?<=[.!?])\s+', para)
        for sent in sentences:
            sent_lower = sent.lower()
            for jargon in JARGON_WORDS:
                if jargon.lower() in sent_lower:
                    has_translation = any(sig in sent_lower for sig in translation_signals)
                    if not has_translation:
                        issues.append({
                            "paragraph": i + 1,
                            "jargon": jargon,
                            "sentence": sent[:120] + ("..." if len(sent) > 120 else ""),
                        })
    return issues


def check_hook(paragraphs: list[str]) -> dict:
    """Quick checks on the opening paragraph."""
    if not paragraphs:
        return {"passed": False, "issues": ["Script is empty"]}

    hook = paragraphs[0]
    issues = []
    # Must have a number
    if not re.search(r'\d+', hook):
        issues.append("Hook has no specific number or statistic")
    # Should be short (setup, not explanation)
    words = hook.split()
    if len(words) > 120:
        issues.append(f"Hook is long ({len(words)} words) — should be punchy and under ~80 words")
    # Should create a puzzle / question
    if "?" not in hook and not any(x in hook.lower() for x in ["here's the thing", "turns out", "wait", "actually", "but here"]):
        issues.append("Hook may not create enough curiosity — try ending with a question or 'turns out'")

    return {"passed": len(issues) == 0, "issues": issues, "text": hook[:200]}


def check_cliches(text: str) -> list[dict]:
    issues = []
    for phrase in CLICHE_PHRASES:
        if phrase.lower() in text.lower():
            issues.append({"phrase": phrase, "context": _snippet(text, text.lower().index(phrase.lower()))})
    return issues


def _snippet(text: str, pos: int, radius: int = 60) -> str:
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    return ("..." if start > 0 else "") + text[start:end].strip() + ("..." if end < len(text) else "")


# ── Claude qualitative review ──────────────────────────────────────────────────

def claude_review(client, script_text: str, boring_runs: list[dict], deep: bool) -> dict:
    """Ask Claude to score and flag boring sections, optionally rewrite them."""

    boring_context = ""
    if boring_runs:
        boring_context = "\n\nThe following paragraph ranges were flagged as likely lacking humor:\n"
        for run in boring_runs[:5]:  # limit to top 5
            boring_context += f"  - {run['msg']}\n"

    mode_instruction = ""
    if deep:
        mode_instruction = """
For the 3 most boring sections you identify, provide a REWRITE that:
- Keeps all the facts intact
- Adds one humor/analogy element (modern reference, self-aware observation, or audience poke)
- Maintains the same sentence rhythm
Format rewrites as:
REWRITE [paragraph N]:
[original first sentence...]
→ [your improved version]
"""
    else:
        mode_instruction = "Flag specific sentences that are boring. Do NOT rewrite — just quote them and explain why they're flat."

    prompt = f"""{CHANNEL_DNA_BRIEF}

---

SCRIPT TO REVIEW ({len(script_text.split())} words):

{script_text}

{boring_context}

---

YOUR TASK:

Score this script on each dimension (1–10, be harsh):
1. HOOK_SCORE: Does the opening hook work? Specific number, creates puzzle, absurdity-first?
2. HUMOR_SCORE: Is humor present every 2-3 paragraphs? Is it natural or forced?
3. JARGON_SCORE: Is every policy term immediately explained?
4. RHYTHM_SCORE: Sentence variety — short/long alternation, punchline endings?
5. AUDIENCE_SCORE: Does it anticipate viewer skepticism and name it?
6. WITTINESS_SCORE: Overall entertainment. Would you watch this at 1x speed?
7. ARC_SCORE: Hook → explain → mechanism → consequence → close. Does the arc land?
8. OVERALL_SCORE: Overall quality.

Then:
BEST_MOMENTS: Quote 2-3 sentences that are working brilliantly.
BORING_SECTIONS: Quote 3-5 specific sentences that are the most sleep-inducing and explain exactly why.
{mode_instruction}

FORMAT (use these exact headers):
HOOK_SCORE: N/10
HUMOR_SCORE: N/10
JARGON_SCORE: N/10
RHYTHM_SCORE: N/10
AUDIENCE_SCORE: N/10
WITTINESS_SCORE: N/10
ARC_SCORE: N/10
OVERALL_SCORE: N/10

BEST_MOMENTS:
[quotes]

BORING_SECTIONS:
[quotes + why]

RECOMMENDATIONS:
[3-5 specific, actionable fixes]
"""

    try:
        import anthropic
    except ImportError:
        print("❌ anthropic not installed. Run: pip install anthropic --break-system-packages")
        sys.exit(1)

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return {"raw": response.content[0].text}


def _parse_scores(raw: str) -> dict:
    scores = {}
    for key in ["HOOK", "HUMOR", "JARGON", "RHYTHM", "AUDIENCE", "WITTINESS", "ARC", "OVERALL"]:
        m = re.search(rf"{key}_SCORE:\s*(\d+)/10", raw)
        if m:
            scores[key] = int(m.group(1))

    # If Claude skipped OVERALL or returned a non-integer (e.g. "?"),
    # compute it as the average of the other seven dimensions.
    if "OVERALL" not in scores:
        sub_keys = ["HOOK", "HUMOR", "JARGON", "RHYTHM", "AUDIENCE", "WITTINESS", "ARC"]
        sub_vals = [scores[k] for k in sub_keys if k in scores]
        if sub_vals:
            scores["OVERALL"] = round(sum(sub_vals) / len(sub_vals))

    return scores


def _color(score: int) -> str:
    if score >= 8: return "\033[92m"   # green
    if score >= 6: return "\033[93m"   # yellow
    return "\033[91m"                  # red

RESET = "\033[0m"
BOLD  = "\033[1m"


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=None, help="Episode folder (e.g. ep01)")
    parser.add_argument("--script",  default=None, help="Direct path to script .txt file")
    parser.add_argument("--deep",    action="store_true",
                        help="Ask Claude to rewrite the 3 most boring sections (slower, costs more)")
    parser.add_argument("--quick",   action="store_true",
                        help="Deterministic checks only — no Claude call (free, instant)")
    parser.add_argument("--out",     default=None,
                        help="Output path for report (default: {project}/script_review.md)")
    args = parser.parse_args()

    project_dir = None
    if args.project:
        project_dir = Path(args.project)
        if not project_dir.is_absolute():
            project_dir = PIPELINE_DIR / args.project

    script_text, script_path = _load_script(project_dir, args.script)
    paragraphs = _split_paragraphs(script_text)
    word_count = len(script_text.split())

    print(f"\n{'═'*60}")
    print(f"  Script Review — {script_path.name}")
    print(f"  {word_count} words · {len(paragraphs)} paragraphs")
    print(f"{'═'*60}\n")

    # ── Deterministic checks ──
    banned     = check_banned_words(script_text)
    humor_gaps = check_humor_density(paragraphs)
    jargon     = check_jargon(paragraphs)
    cliches    = check_cliches(script_text)
    hook       = check_hook(paragraphs)

    print(f"{BOLD}── DETERMINISTIC CHECKS ──{RESET}")

    # Hook
    status = "✓" if hook["passed"] else "✗"
    color  = "\033[92m" if hook["passed"] else "\033[91m"
    print(f"\n{color}{status} HOOK{RESET}")
    if hook["issues"]:
        for iss in hook["issues"]:
            print(f"   ⚠ {iss}")
    else:
        print(f"   ✓ Hook looks strong")

    # Banned words
    print(f"\n{'✓' if not banned else '✗'} BANNED WORDS  ({len(banned)} found)")
    for b in banned[:8]:
        print(f"   ⚠ '{b['word']}' (line {b['line']}): ...{b['context']}...")

    # Clichés
    print(f"\n{'✓' if not cliches else '⚠'} CLICHÉS  ({len(cliches)} found)")
    for c in cliches[:5]:
        print(f"   ⚠ '{c['phrase']}'")

    # Humor gaps
    print(f"\n{'✓' if not humor_gaps else '✗'} HUMOR DENSITY  ({len(humor_gaps)} dry run(s))")
    for gap in humor_gaps:
        print(f"   ⚠ {gap['msg']}")

    # Jargon
    print(f"\n{'✓' if not jargon else '✗'} JARGON TRANSLATION  ({len(jargon)} untranslated)")
    for j in jargon[:6]:
        print(f"   ⚠ Para {j['paragraph']}: '{j['jargon']}' — {j['sentence'][:80]}...")

    # Summary of deterministic
    det_issues = len(banned) + len(humor_gaps) + len(jargon) + len(cliches) + len(hook.get("issues", []))
    det_color  = "\033[92m" if det_issues == 0 else ("\033[93m" if det_issues < 5 else "\033[91m")
    print(f"\n{det_color}Deterministic total: {det_issues} issue(s){RESET}")

    if args.quick:
        print("\n[--quick mode: skipping Claude review]\n")
        _write_report(project_dir, args.out, script_path, paragraphs,
                      banned, humor_gaps, jargon, cliches, hook, None, None)
        return

    # ── Claude qualitative review ──
    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception as e:
        print(f"\n⚠ Claude unavailable ({e}). Run with --quick for deterministic-only review.")
        _write_report(project_dir, args.out, script_path, paragraphs,
                      banned, humor_gaps, jargon, cliches, hook, None, None)
        return

    print(f"\n{BOLD}── CLAUDE REVIEW {'(deep — rewrites included)' if args.deep else '(standard)'} ──{RESET}")
    print("  Analysing...")

    result = claude_review(client, script_text, humor_gaps, deep=args.deep)
    raw    = result["raw"]
    scores = _parse_scores(raw)

    print()
    for dim, label in [
        ("HOOK",      "Hook quality   "),
        ("HUMOR",     "Humor density  "),
        ("JARGON",    "Jargon clarity "),
        ("RHYTHM",    "Sentence rhythm"),
        ("AUDIENCE",  "Audience addr. "),
        ("WITTINESS", "Wittiness      "),
        ("ARC",       "Narrative arc  "),
        ("OVERALL",   "OVERALL        "),
    ]:
        score = scores.get(dim, "?")
        if isinstance(score, int):
            bar   = "█" * score + "░" * (10 - score)
            color = _color(score)
            print(f"  {label} {color}{bar}{RESET} {score}/10")
        else:
            print(f"  {label} ?/10")

    overall = scores.get("OVERALL", 0)
    overall_label = "✓ PASS" if overall >= 7 else ("⚠ NEEDS WORK" if overall >= 5 else "✗ REWRITE NEEDED")
    overall_color = "\033[92m" if overall >= 7 else ("\033[93m" if overall >= 5 else "\033[91m")
    print(f"\n  {overall_color}{BOLD}{overall_label} — {overall}/10{RESET}")

    # Extract and print key sections
    for section in ["BEST_MOMENTS", "BORING_SECTIONS", "RECOMMENDATIONS"]:
        m = re.search(rf"{section}:\s*\n(.*?)(?=\n[A-Z_]+:|\Z)", raw, re.DOTALL)
        if m:
            content = m.group(1).strip()
            label   = section.replace("_", " ").title()
            print(f"\n{BOLD}{label}:{RESET}")
            for line in content.split("\n")[:12]:
                print(f"  {line}")

    if args.deep:
        # Print rewrites
        for m in re.finditer(r"REWRITE \[paragraph (\d+)\]:\s*\n(.*?)(?=REWRITE|\Z)", raw, re.DOTALL):
            print(f"\n{BOLD}REWRITE [paragraph {m.group(1)}]:{RESET}")
            for line in m.group(2).strip().split("\n")[:8]:
                print(f"  {line}")

    _write_report(project_dir, args.out, script_path, paragraphs,
                  banned, humor_gaps, jargon, cliches, hook, result, scores)
    print()


def _write_report(project_dir, out_arg, script_path, paragraphs,
                  banned, humor_gaps, jargon, cliches, hook, claude_result, scores):
    """Write full Markdown report to file."""
    if out_arg:
        out_path = Path(out_arg)
    elif project_dir:
        out_path = project_dir / "script_review.md"
    else:
        out_path = script_path.parent / "script_review.md"

    lines = [
        f"# Script Review — {script_path.name}",
        f"",
        f"**Words:** {len(' '.join(p for p in paragraphs).split())}  "
        f"**Paragraphs:** {len(paragraphs)}",
        f"",
    ]

    if scores:
        lines += ["## Scores", ""]
        for dim, label in [
            ("HOOK", "Hook quality"), ("HUMOR", "Humor density"),
            ("JARGON", "Jargon clarity"), ("RHYTHM", "Sentence rhythm"),
            ("AUDIENCE", "Audience address"), ("WITTINESS", "Wittiness"),
            ("ARC", "Narrative arc"), ("OVERALL", "**OVERALL**"),
        ]:
            s = scores.get(dim, "?")
            bar = ("█" * s + "░" * (10 - s)) if isinstance(s, int) else "?"
            lines.append(f"| {label:<20} | `{bar}` | {s}/10 |")
        lines += ["", ""]

    lines += ["## Deterministic Issues", ""]

    if not hook["passed"]:
        lines += ["### ⚠ Hook Issues", ""]
        for iss in hook["issues"]:
            lines.append(f"- {iss}")
        lines.append("")

    if banned:
        lines += [f"### ✗ Banned Words ({len(banned)} found)", ""]
        for b in banned:
            lines.append(f"- **`{b['word']}`** (line {b['line']}): ...{b['context']}...")
        lines.append("")

    if cliches:
        lines += [f"### ⚠ Clichés ({len(cliches)} found)", ""]
        for c in cliches:
            lines.append(f"- `{c['phrase']}`")
        lines.append("")

    if humor_gaps:
        lines += [f"### ✗ Humor Gaps ({len(humor_gaps)} dry run(s))", ""]
        for gap in humor_gaps:
            lines.append(f"- {gap['msg']}")
        lines.append("")

    if jargon:
        lines += [f"### ✗ Untranslated Jargon ({len(jargon)} found)", ""]
        for j in jargon[:20]:
            lines.append(f"- Para {j['paragraph']}: **`{j['jargon']}`** — _{j['sentence'][:100]}..._")
        lines.append("")

    if claude_result:
        lines += ["## Claude Analysis", "", claude_result["raw"], ""]

    # Machine-readable score line — parsed by pipeline_agents.py _stage_review_script
    if scores and "OVERALL" in scores:
        lines += ["", f"<!-- OVERALL_SCORE: {scores['OVERALL']}/10 -->"]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  Report saved → {out_path}")


if __name__ == "__main__":
    main()
