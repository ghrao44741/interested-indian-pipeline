"""
review_narration_audio.py
The Interested Indian — Narration Audio Review

Catches issues in a generated narration.mp3 before it feeds the rest of the
pipeline:
  1. Known chunk-concatenation seams — the exact timestamp where a multi-chunk
     TTS generation was stitched together (read from the voice sidecar written
     by generate_source_audio.py). This is the single most likely place for an
     audible tone/pace/pitch shift, so it's reported first and always.
  2. Transcript-vs-script mismatches — Whisper's transcript of the actual audio
     diffed against the original script text, catching skipped, repeated, or
     badly mispronounced words.
  3. Silence-gap anomalies — unusually long near-silent stretches that can
     indicate a dropped word or a splice glitch.

Honest scope: this does NOT judge subjective voice/tone quality (e.g. "does the
narrator suddenly sound like a different person") — no audio-understanding model
is wired up for that, and pretending otherwise would be dishonest. What this DOES
do is tell you exactly where to listen instead of scanning 10+ minutes blind, and
catch the objective, checkable errors a human ear can miss on a long file.

USAGE:
    python review_narration_audio.py --project pilot_neet_scandal
    python review_narration_audio.py --audio path/to/narration.mp3 --script path/to/script.txt
    python review_narration_audio.py --project ep01 --whisper-model small
"""

import argparse
import difflib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import whisper
    from pydub import AudioSegment
except ImportError as e:
    raise ImportError(
        "Missing dependencies. Run: pip install openai-whisper pydub\n"
        "Also requires ffmpeg installed on your system."
    ) from e


def format_timestamp(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_sidecar(audio_path: Path) -> dict:
    sidecar_path = Path(f"{audio_path}.voice.json")
    if sidecar_path.exists():
        try:
            return json.loads(sidecar_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def find_silence_gaps(audio: AudioSegment, min_gap_ms: int = 1200,
                       silence_thresh_db: float = -45.0) -> list[dict]:
    """Scan for unusually long quiet stretches (possible dropped word / splice glitch)."""
    window_ms = 100
    gaps = []
    gap_start = None
    for i in range(0, len(audio), window_ms):
        window = audio[i:i + window_ms]
        is_quiet = window.dBFS < silence_thresh_db or window.dBFS == float("-inf")
        if is_quiet:
            if gap_start is None:
                gap_start = i
        else:
            if gap_start is not None:
                gap_len = i - gap_start
                if gap_len >= min_gap_ms:
                    gaps.append({"start_s": round(gap_start / 1000, 2),
                                 "end_s": round(i / 1000, 2),
                                 "duration_s": round(gap_len / 1000, 2)})
                gap_start = None
    if gap_start is not None:
        gap_len = len(audio) - gap_start
        if gap_len >= min_gap_ms:
            gaps.append({"start_s": round(gap_start / 1000, 2),
                         "end_s": round(len(audio) / 1000, 2),
                         "duration_s": round(gap_len / 1000, 2)})
    return gaps


def dbfs_around(audio: AudioSegment, t_sec: float, window_s: float = 2.0) -> dict:
    """dBFS just before/after a timestamp — supplementary evidence for a known
    chunk seam, not proof of an audible issue by itself."""
    t_ms = int(t_sec * 1000)
    window_ms = int(window_s * 1000)
    before = audio[max(0, t_ms - window_ms):t_ms]
    after = audio[t_ms:t_ms + window_ms]
    return {
        "before_dBFS": round(before.dBFS, 2) if len(before) > 0 else None,
        "after_dBFS": round(after.dBFS, 2) if len(after) > 0 else None,
    }


_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_SEPARATORS = {"point", "lakh", "lac", "lack", "crore", "thousand", "hundred", "and"}
NUMBER_WORDS = _ONES.keys() | _TENS.keys() | _SEPARATORS


def _is_numeric_ish(words: list[str]) -> bool:
    """True if every word is a digit or a number-word — i.e. this diff LOOKS LIKE
    spoken-number formatting ("twenty two point seven lakh" vs Whisper's "22.7
    lakh"). This alone is NOT sufficient to suppress the diff — see
    _same_spoken_numbers, which checks the values actually match, so a genuine
    wrong-number narration error (e.g. TTS saying 32.7 instead of 22.7) doesn't
    get silently hidden just because both sides are numeric-shaped."""
    if not words:
        return True
    return all(w.isdigit() or w in NUMBER_WORDS for w in words)


def _words_to_number_groups(words: list[str]) -> list[int]:
    """Extract the sequence of numeric VALUES spoken, treating magnitude/connector
    words (hundred/thousand/lakh/crore/point/and) as group separators rather than
    multipliers — matches how these scripts phrase numbers as distinct groups
    ("twenty two point seven lakh" -> [22, 7], not a single combined value).
    'hundred' immediately after an accumulated value multiplies it (e.g. "one
    hundred" -> 100 within its group, but the far more common "a hundred and one"
    pattern doesn't parse 'a' as 1, so it intentionally falls through unconverted
    — see the fallback note where this is used."""
    groups: list[int] = []
    current = 0
    have_value = False
    for w in words:
        if w.isdigit():
            groups.append(int(w))
            continue
        if w == "hundred" and have_value:
            current *= 100
            continue
        if w in _SEPARATORS:
            if have_value:
                groups.append(current)
                current = 0
                have_value = False
            continue
        if w in _TENS:
            current += _TENS[w]
            have_value = True
        elif w in _ONES:
            current += _ONES[w]
            have_value = True
        else:
            # Unrecognized word (e.g. "a", or Whisper mishearing a proper noun as
            # a similar-sounding word) — bail out rather than guess; the caller
            # treats an inconclusive conversion as "don't suppress" (safer default).
            return []
    if have_value:
        groups.append(current)
    return groups


def _same_spoken_numbers(script_chunk: list[str], transcript_chunk: list[str]) -> bool:
    """True only if both sides convert to the same sequence of numeric values.
    An inconclusive conversion (empty result on either side) is treated as NOT
    matching — when in doubt, surface the diff instead of hiding it."""
    script_values = _words_to_number_groups(script_chunk)
    transcript_values = _words_to_number_groups(transcript_chunk)
    if not script_values or not transcript_values:
        return False
    return script_values == transcript_values


def _approx_time_for_word_offset(whisper_segments: list[dict], word_offset: int) -> float | None:
    """Approximate timestamp for a position in the word-tokenized, concatenated
    transcript — walks segments accumulating word counts until it passes the
    target offset. Good enough to point a human at the right few seconds."""
    cursor = 0
    for seg in whisper_segments:
        seg_word_count = len(normalize(seg["text"]).split())
        if cursor + seg_word_count > word_offset:
            return round(seg["start"], 2)
        cursor += seg_word_count
    return whisper_segments[-1]["start"] if whisper_segments else None


def diff_transcript_vs_script(script_text: str, whisper_segments: list[dict]) -> tuple[list[dict], int]:
    """Word-level diff of the transcript against the script (word-level, not
    character-level, so snippets stay whole-word and legible instead of
    fragmenting mid-word once the alignment drifts). Spoken-number formatting
    differences (script spells numbers out for TTS, Whisper transcribes them as
    digits) are real diffs but not narration errors — they're counted separately
    and excluded from the main issue list so they don't drown out real mismatches.
    Returns (issues, numeric_diffs_suppressed_count).
    """
    full_transcript = " ".join(seg["text"].strip() for seg in whisper_segments)
    script_words = normalize(script_text).split()
    transcript_words = normalize(full_transcript).split()

    sm = difflib.SequenceMatcher(None, script_words, transcript_words)
    issues = []
    numeric_diffs = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        script_chunk = script_words[i1:i2]
        transcript_chunk = transcript_words[j1:j2]
        # Skip tiny 1-2 word diffs — mostly filler-word noise, not real errors.
        if len(script_chunk) <= 2 and len(transcript_chunk) <= 2:
            continue
        if (_is_numeric_ish(script_chunk) and _is_numeric_ish(transcript_chunk)
                and _same_spoken_numbers(script_chunk, transcript_chunk)):
            numeric_diffs += 1
            continue
        approx_time = _approx_time_for_word_offset(whisper_segments, j1)
        issues.append({
            "type": tag,
            "script_said": " ".join(script_chunk)[:150],
            "audio_said": " ".join(transcript_chunk)[:150],
            "approx_time_s": approx_time,
        })
    return issues, numeric_diffs


def review(audio_path: Path, script_path: Path | None, whisper_model: str = "base") -> dict:
    print(f"Loading audio: {audio_path}")
    audio = AudioSegment.from_file(str(audio_path))
    duration_s = len(audio) / 1000
    print(f"  Duration: {duration_s:.1f}s ({format_timestamp(duration_s)})")

    sidecar = load_sidecar(audio_path)
    known_boundaries = sidecar.get("chunk_boundaries_sec", [])

    print(f"\nTranscribing with Whisper '{whisper_model}'...")
    model = whisper.load_model(whisper_model)
    transcription = model.transcribe(str(audio_path))
    segments = [{"start": s["start"], "end": s["end"], "text": s["text"]}
                for s in transcription["segments"]]
    print(f"  {len(segments)} segments")

    print("\nScanning for silence gaps...")
    silence_gaps = find_silence_gaps(audio)
    print(f"  {len(silence_gaps)} gap(s) >= 1.2s found")

    transcript_issues = []
    numeric_diffs = 0
    if script_path and script_path.exists():
        print("\nDiffing transcript against script...")
        script_text = script_path.read_text(encoding="utf-8")
        transcript_issues, numeric_diffs = diff_transcript_vs_script(script_text, segments)
        print(f"  {len(transcript_issues)} substantial mismatch(es) found "
              f"({numeric_diffs} numeric-formatting diff(s) suppressed as expected)")
    else:
        print("\n(No script provided — skipping transcript-vs-script diff)")

    seam_reports = []
    for b in known_boundaries:
        evidence = dbfs_around(audio, b)
        seam_reports.append({"time_s": b, **evidence})

    return {
        "audio_file": str(audio_path),
        "duration_s": round(duration_s, 1),
        "sidecar": sidecar,
        "known_seams": seam_reports,
        "silence_gaps": silence_gaps,
        "transcript_issues": transcript_issues,
        "numeric_diffs_suppressed": numeric_diffs,
        "full_transcript": transcription["text"].strip(),
    }


def build_report(data: dict) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("THE INTERESTED INDIAN — NARRATION AUDIO REVIEW")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"File     : {data['audio_file']}")
    lines.append(f"Duration : {data['duration_s']}s ({format_timestamp(data['duration_s'])})")
    sidecar = data.get("sidecar") or {}
    if sidecar:
        lines.append(f"Provider : {sidecar.get('provider')} / voice={sidecar.get('voice')} "
                      f"/ model={sidecar.get('model', '?')}")
    lines.append("")

    lines.append("KNOWN CHUNK SEAMS (exact — from generation-time chunk boundaries)")
    lines.append("-" * 70)
    if data["known_seams"]:
        lines.append("Mechanical splice points from multi-chunk TTS generation. Listen here")
        lines.append("first — this is the single most likely place for an audible tone/")
        lines.append("pace/pitch shift.")
        lines.append("")
        for seam in data["known_seams"]:
            lines.append(
                f"  [{format_timestamp(seam['time_s'])}]  before={seam['before_dBFS']} dBFS  "
                f"after={seam['after_dBFS']} dBFS"
            )
    else:
        lines.append("None recorded — either this audio was generated in a single request")
        lines.append("(no seam exists), or it predates chunk-boundary tracking (regenerate")
        lines.append("to get exact seam timestamps next time).")
    lines.append("")

    lines.append("SILENCE GAPS (>= 1.2s near-silence — possible dropped word/glitch)")
    lines.append("-" * 70)
    if data["silence_gaps"]:
        for g in data["silence_gaps"]:
            lines.append(f"  [{format_timestamp(g['start_s'])} -> {format_timestamp(g['end_s'])}]  "
                          f"{g['duration_s']}s")
    else:
        lines.append("None found.")
    lines.append("")

    lines.append("TRANSCRIPT VS SCRIPT MISMATCHES")
    lines.append("-" * 70)
    if data.get("numeric_diffs_suppressed"):
        lines.append(f"({data['numeric_diffs_suppressed']} additional numeric-formatting diff(s) "
                      f"suppressed — script spells numbers out for TTS, Whisper transcribes them as "
                      f"digits; expected, not a narration error.)")
        lines.append("")
    if data["transcript_issues"]:
        lines.append(f"{len(data['transcript_issues'])} substantial mismatch(es) — Whisper's transcript")
        lines.append("of the actual audio doesn't match the script at these points. Could be")
        lines.append("a mispronunciation, skipped/repeated text, or a Whisper mishearing —")
        lines.append("check the timestamp to tell which.")
        lines.append("")
        for issue in data["transcript_issues"]:
            t = format_timestamp(issue["approx_time_s"]) if issue["approx_time_s"] is not None else "?:??"
            lines.append(f"  [~{t}] ({issue['type']})")
            lines.append(f"    script said : {issue['script_said']}")
            lines.append(f"    audio said  : {issue['audio_said']}")
            lines.append("")
    else:
        lines.append("None found — transcript matches script closely throughout.")
    lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", help="Episode folder (e.g. pilot_neet_scandal) — looks "
                                           "for source_audio/narration.mp3 and script_*.txt inside it")
    parser.add_argument("--audio", help="Explicit path to the narration audio file")
    parser.add_argument("--script", help="Explicit path to the script .txt file "
                                          "(enables the transcript-vs-script diff)")
    parser.add_argument("--whisper-model", default="base", help="Whisper model size (default: base)")
    args = parser.parse_args()

    if args.audio:
        audio_path = Path(args.audio)
    elif args.project:
        audio_path = Path(__file__).parent / args.project / "source_audio" / "narration.mp3"
    else:
        parser.error("Provide --project or --audio")
        return

    if not audio_path.exists():
        print(f"❌ Audio file not found: {audio_path}")
        sys.exit(1)

    script_path = None
    if args.script:
        script_path = Path(args.script)
    elif args.project:
        matches = list((Path(__file__).parent / args.project).glob("script_*.txt"))
        if matches:
            script_path = matches[0]

    data = review(audio_path, script_path, whisper_model=args.whisper_model)
    report = build_report(data)
    print("\n" + report)

    out_path = audio_path.parent / f"{audio_path.stem}_review.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n✓ Report saved to: {out_path}")


if __name__ == "__main__":
    main()
