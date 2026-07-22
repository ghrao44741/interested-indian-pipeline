import os
import sys
from datetime import datetime

try:
    import whisper
    from pydub import AudioSegment
except ImportError as e:
    raise ImportError(
        "Missing dependencies. Run: pip install openai-whisper pydub\n"
        "Also requires ffmpeg installed on your system."
    ) from e


def format_timestamp(seconds: float) -> str:
    """Convert seconds to MM:SS format."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


def analyze_mp4(file_path: str, whisper_model: str = "base") -> dict:
    """
    Analyzes a local mp4 file for audio loudness and transcription.
    Returns structured data for use in downstream pipeline agents.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Video file not found: {file_path}")

    result = {
        "file": os.path.basename(file_path),
        "duration_seconds": 0,
        "overall_dBFS": None,
        "max_dBFS": None,
        "loudness_timeline": [],
        "transcript_segments": [],
        "full_text": "",
        "transcript_available": False
    }

    # ==========================================
    # 1. AUDIO VOLUME & ENERGY ANALYSIS
    # ==========================================
    print(f"🔊 Extracting audio from: {result['file']}")
    try:
        audio = AudioSegment.from_file(file_path, format="mp4")
        result["duration_seconds"] = len(audio) / 1000
        result["overall_dBFS"] = round(audio.dBFS, 2)
        result["max_dBFS"] = round(audio.max_dBFS, 2)

        chunk_ms = 10_000
        chunks = len(audio) // chunk_ms
        timeline = []

        for i in range(chunks):
            chunk = audio[i * chunk_ms: (i + 1) * chunk_ms]
            timeline.append({
                "start_s": i * 10,
                "end_s": (i + 1) * 10,
                "dBFS": round(chunk.dBFS, 2)
            })

        result["loudness_timeline"] = timeline

        print(f"  Duration     : {result['duration_seconds']:.1f}s  ({format_timestamp(result['duration_seconds'])})")
        print(f"  Overall dBFS : {result['overall_dBFS']}")
        print(f"  Peak dBFS    : {result['max_dBFS']}")

    except Exception as e:
        print(f"⚠️ Audio analysis failed: {e}")

    # ==========================================
    # 2. WHISPER TRANSCRIPTION
    # ==========================================
    print(f"\n📝 Transcribing with Whisper '{whisper_model}' model...")
    try:
        model = whisper.load_model(whisper_model)
        transcription = model.transcribe(file_path)

        result["transcript_segments"] = [
            {
                "start": round(seg["start"], 2),
                "end": round(seg["end"], 2),
                "text": seg["text"].strip()
            }
            for seg in transcription["segments"]
        ]
        result["full_text"] = transcription["text"].strip()
        result["transcript_available"] = True

        print(f"  Segments extracted: {len(result['transcript_segments'])}")

    except Exception as e:
        print(f"⚠️ Transcription failed: {e}")

    return result


def build_report(data: dict) -> str:
    """Build a human-readable text report from analysis data."""
    lines = []
    lines.append("=" * 60)
    lines.append("THE INTERESTED INDIAN — VIDEO ANALYSIS REPORT")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)
    lines.append("")

    # --- Summary ---
    lines.append("SUMMARY")
    lines.append("-" * 40)
    lines.append(f"File         : {data['file']}")
    dur = data['duration_seconds']
    lines.append(f"Duration     : {dur:.1f}s  ({format_timestamp(dur)})")
    lines.append(f"Overall dBFS : {data['overall_dBFS']}")
    lines.append(f"Peak dBFS    : {data['max_dBFS']}")
    lines.append(f"Transcript   : {'✅ Available' if data['transcript_available'] else '❌ Not available'}")
    lines.append("")

    # --- Loudness timeline ---
    lines.append("LOUDNESS TIMELINE (10s chunks)")
    lines.append("-" * 40)
    lines.append("Use drops in dBFS to identify quiet sections (logo cards, transitions)")
    lines.append("")

    avg_dbfs = 0
    if data["loudness_timeline"]:
        avg_dbfs = sum(c["dBFS"] for c in data["loudness_timeline"]) / len(data["loudness_timeline"])
        for chunk in data["loudness_timeline"]:
            meter = "█" * max(0, int((chunk["dBFS"] + 60) / 2))
            flag = "  ◀ QUIET" if chunk["dBFS"] < avg_dbfs - 3 else ""
            lines.append(
                f"  [{format_timestamp(chunk['start_s'])}-{format_timestamp(chunk['end_s'])}] "
                f"{chunk['dBFS']:>7.2f} dBFS | {meter}{flag}"
            )
    lines.append("")

    # --- NotebookLM logo detection hint ---
    lines.append("NOTEBOOKLM LOGO DETECTION HINT")
    lines.append("-" * 40)
    if data["loudness_timeline"]:
        quiet_chunks = [
            c for c in data["loudness_timeline"]
            if c["dBFS"] < avg_dbfs - 3
        ]
        if quiet_chunks:
            first_quiet = quiet_chunks[0]
            last_quiet = quiet_chunks[-1]
            lines.append(
                f"Quiet section starts around: {format_timestamp(first_quiet['start_s'])} "
                f"(dBFS: {first_quiet['dBFS']})"
            )
            lines.append(
                f"Quiet section ends around  : {format_timestamp(last_quiet['end_s'])} "
                f"(dBFS: {last_quiet['dBFS']})"
            )
            lines.append(f"→ Check this window visually for the NotebookLM logo card.")
            lines.append(f"→ Suggested brand_video.py overlay window: between(t,0,{first_quiet['start_s']})")
        else:
            lines.append("No significant quiet sections detected — audio is consistent throughout.")
    lines.append("")

    # --- Transcript ---
    lines.append("TRANSCRIPT SEGMENTS")
    lines.append("-" * 40)
    if data["transcript_available"]:
        for seg in data["transcript_segments"]:
            lines.append(
                f"  [{format_timestamp(seg['start'])} -> {format_timestamp(seg['end'])}]  {seg['text']}"
            )
        lines.append("")
        lines.append("FULL TEXT")
        lines.append("-" * 40)
        lines.append(data["full_text"])
    else:
        lines.append("Transcript not available. Whisper model could not be loaded.")
        lines.append("Ensure you have internet access for first-time model download,")
        lines.append("or pre-download the model: python -c \"import whisper; whisper.load_model('base')\"")
    lines.append("")

    # --- Chapter suggestion prompt ---
    lines.append("CHAPTER SUGGESTION PROMPT")
    lines.append("-" * 40)
    lines.append("Once transcript is available, paste segments into Claude with:")
    lines.append("")
    lines.append('  "Based on this timestamped transcript, suggest YouTube chapter')
    lines.append('   markers in 0:00 / MM:SS format, grouped by topic.')
    lines.append('   First chapter must always be 0:00."')
    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python local_mp4_analyzer.py <path_to_video.mp4> [whisper_model]")
        print("Models: tiny, base, small, medium, large  (default: base)")
        sys.exit(1)

    model_name = sys.argv[2] if len(sys.argv) > 2 else "base"
    data = analyze_mp4(sys.argv[1], whisper_model=model_name)

    # Build report
    report = build_report(data)

    # Print to console
    print(report)

    # Save .txt file alongside the input video
    base = os.path.splitext(sys.argv[1])[0]
    output_path = f"{base}_analysis.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n✅ Report saved to: {output_path}")
