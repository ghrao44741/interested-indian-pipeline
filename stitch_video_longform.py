"""
stitch_video_longform.py — Long-Form Video Pipeline (The Interested Indian)
Landscape 1920×1080, Ken Burns, optional mascot overlay, SRT captions.

Differences from stitch_video_complete.py (Shorts):
  • Resolution  : 1920×1080 (landscape) vs 1080×1920 (vertical)
  • Ken Burns   : upscales to 3840×2160 for headroom, zoompan outputs 1920×1080
  • Mascot layer: optional per-scene PNG pop-in (reads mascot_config.json)
  • CTA card    : auto-appended from {project}/../common/cta/ (static, no Ken Burns)
                  Skip with --no-cta
  • Captions    : SRT burn (standard subtitle at bottom-third, not karaoke ASS)
                  Upload the .srt to YouTube as a subtitle track too

Usage:
    python stitch_video_longform.py --project ep01
    python stitch_video_longform.py --project ..\\..\\interested_indian_pipeline\\ep01
    python stitch_video_longform.py --project C:\\abs\\path\\to\\ep01
    python stitch_video_longform.py --project ep01 --skip-captions
    python stitch_video_longform.py --project ep01 --no-cta

CTA SCENE (auto-detected, shared across all episodes):
    Place these two files once in the channel repo:
      interested_indian_pipeline/common/cta/cta.mp3   ← generated via generate_source_audio.py
      interested_indian_pipeline/common/cta/cta.png   ← generated from the image prompt

    The script looks for them at {project}/../common/cta/.
    CTA renders as a static slide (Ken Burns off) so the subscribe card reads cleanly.
    To skip for a specific render: --no-cta

Requirements:
    ffmpeg on PATH
    pip install mutagen
    stamp_manifest.py, generate_srt.py in same folder as this script

──────────────────────────────────────────────────────────
MASCOT OVERLAY (optional)
──────────────────────────────────────────────────────────
Place mascot_config.json inside the project folder:

{
  "mascot_dir": "mascots",
  "scenes": {
    "SCENE-045": {
      "png": "mascot_pointing.png",
      "position": "bottom-right",
      "start_offset": 0.5,
      "duration": 3.0
    },
    "SCENE-001": {
      "png": "mascot_waving.png",
      "position": "bottom-left",
      "start_offset": 0.0,
      "duration": 0
    }
  }
}

  mascot_dir   : subfolder inside project containing PNG files (transparent bg)
  position     : "bottom-right" | "bottom-left" | "top-right" | "top-left"
  start_offset : seconds after scene start to pop the mascot in
  duration     : seconds to show (0 = hold for full scene)

Mascot PNGs must have transparent backgrounds. Rendered at 180px height.
──────────────────────────────────────────────────────────
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

from mutagen.mp3 import MP3

try:
    from stamp_manifest import stamp_manifest
    from generate_srt import generate_srt
    _CAPTION_PIPELINE_AVAILABLE = True
except ImportError as e:
    _CAPTION_PIPELINE_AVAILABLE = False
    _CAPTION_IMPORT_ERROR = str(e)

# ── constants ─────────────────────────────────────────────────────────────────
FPS               = 30
BGM_VOLUME        = 0.08          # slightly quieter than Shorts (0.10) — more room for narration
RESOLUTION        = "1920x1080"   # landscape long-form
KEN_BURNS_ENABLED = True
KEN_BURNS_ZOOM_RATIO = 1.08       # 8% zoom — subtle, same as Shorts

MASCOT_HEIGHT_PX  = 180           # mascot scaled to this height at 1080p
MASCOT_PADDING_PX = 30            # padding from edge

# ffmpeg overlay x:y expressions for each position (evaluated at render time)
_MASCOT_POSITIONS = {
    "bottom-right": f"W-w-{MASCOT_PADDING_PX}:H-h-{MASCOT_PADDING_PX}",
    "bottom-left":  f"{MASCOT_PADDING_PX}:H-h-{MASCOT_PADDING_PX}",
    "top-right":    f"W-w-{MASCOT_PADDING_PX}:{MASCOT_PADDING_PX}",
    "top-left":     f"{MASCOT_PADDING_PX}:{MASCOT_PADDING_PX}",
}


# ═══════════════════════════════════════════════════════════════════════════════
# KEN BURNS — landscape variant
# ═══════════════════════════════════════════════════════════════════════════════

def ken_burns_filter(scene_index: int, duration_seconds: float, fps: int) -> str:
    """
    Slow Ken Burns pan/zoom for 1920×1080 landscape output.
    4 alternating variants so consecutive scenes move differently.
    Entirely self-contained within each clip — cannot cause sync drift.
    """
    total_frames = max(1, int(duration_seconds * fps))
    variant = scene_index % 4

    if variant == 0:                             # slow zoom-in, drift top-left
        zoom_expr = f"min(zoom+0.0015,{KEN_BURNS_ZOOM_RATIO})"
        x_expr = "iw/2-(iw/zoom/2)-ow*0.02"
        y_expr = "ih/2-(ih/zoom/2)-oh*0.02"
    elif variant == 1:                           # slow zoom-in, drift bottom-right
        zoom_expr = f"min(zoom+0.0015,{KEN_BURNS_ZOOM_RATIO})"
        x_expr = "iw/2-(iw/zoom/2)+ow*0.02"
        y_expr = "ih/2-(ih/zoom/2)+oh*0.02"
    elif variant == 2:                           # slow zoom-out, center
        zoom_expr = f"if(eq(on,0),{KEN_BURNS_ZOOM_RATIO},max(zoom-0.0015,1.0))"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    else:                                        # very slow zoom-in, center
        zoom_expr = f"min(zoom+0.0012,{KEN_BURNS_ZOOM_RATIO})"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"

    return (
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={total_frames}:s=1920x1080:fps={fps}"   # ← landscape output
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE LOOKUP
# ═══════════════════════════════════════════════════════════════════════════════

def get_audio_duration(path: str) -> float:
    return MP3(path).info.length


def find_video_source(project_dir: str, scene_id: str,
                      visual_group_id: str) -> tuple:
    """
    Returns (source_path, source_type).
    Priority: scene video > scene image > group video > group image.
    No CTA card logic (long-form doesn't use it).
    """
    video_path = os.path.join(project_dir, "videos", f"{scene_id}.mp4")
    if os.path.exists(video_path):
        return video_path, "video"

    for ext in [".png", ".jpg", ".jpeg"]:
        image_path = os.path.join(project_dir, "images", f"{scene_id}{ext}")
        if os.path.exists(image_path):
            return image_path, "image"

    if visual_group_id:
        group_video = os.path.join(project_dir, "videos", f"{visual_group_id}.mp4")
        if os.path.exists(group_video):
            return group_video, "video"
        for ext in [".png", ".jpg", ".jpeg"]:
            group_image = os.path.join(project_dir, "images", f"{visual_group_id}{ext}")
            if os.path.exists(group_image):
                return group_image, "image"

    return None, None


def find_cta(project_dir: str) -> tuple:
    """
    Look for shared CTA assets at {project}/../common/cta/.
    Returns (mp3_path, image_path) if both exist, else (None, None).
    """
    common_cta = os.path.normpath(os.path.join(project_dir, "..", "common", "cta"))
    mp3   = os.path.join(common_cta, "cta.mp3")
    image = None
    for ext in [".png", ".jpg", ".jpeg"]:
        candidate = os.path.join(common_cta, f"cta{ext}")
        if os.path.exists(candidate):
            image = candidate
            break
    if os.path.exists(mp3) and image:
        return mp3, image
    return None, None


def load_mascot_config(project_dir: str) -> dict:
    """Load mascot_config.json if present; return empty dict otherwise."""
    config_path = os.path.join(project_dir, "mascot_config.json")
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# CLIP BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def build_clip_from_video(scene_id: str, video_path: str, audio_path: str,
                           audio_duration: float, tmp_dir: str) -> str:
    """Render a scene clip from an animated .mp4 source (loops if shorter)."""
    clip_path = os.path.join(tmp_dir, f"{scene_id}.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-vf", (
            "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0xFAF7F2,setsar=1"
        ),
        "-t", str(audio_duration + 0.5),
        "-r", str(FPS), clip_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ ffmpeg error ({scene_id} video):\n{result.stderr[-500:]}")
        raise RuntimeError(f"ffmpeg failed: {scene_id}")
    return clip_path


def build_clip_from_image(scene_id: str, image_path: str, audio_path: str,
                           audio_duration: float, tmp_dir: str,
                           scene_index: int = 0,
                           force_static: bool = False) -> str:
    """
    Render a scene clip from a still image.
    With KEN_BURNS_ENABLED: upscales to 3840×2160 (2× headroom for landscape)
    then applies zoompan → 1920×1080 output.
    force_static=True skips Ken Burns (for title cards etc.).
    """
    clip_path = os.path.join(tmp_dir, f"{scene_id}.mp4")
    total_duration = audio_duration + 0.5

    if KEN_BURNS_ENABLED and not force_static:
        vf_chain = (
            # 2× upscale for landscape headroom (3840×2160)
            "scale=3840:2160:force_original_aspect_ratio=increase,"
            "crop=3840:2160,"
            f"{ken_burns_filter(scene_index, total_duration, FPS)}"
        )
    else:
        vf_chain = (
            "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0xFAF7F2,setsar=1"
        )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-vf", vf_chain,
        "-t", str(total_duration),
        "-r", str(FPS), clip_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ ffmpeg error ({scene_id} image):\n{result.stderr[-500:]}")
        raise RuntimeError(f"ffmpeg failed: {scene_id}")
    return clip_path


# ═══════════════════════════════════════════════════════════════════════════════
# MASCOT OVERLAY
# ═══════════════════════════════════════════════════════════════════════════════

def apply_mascot_overlay(clip_path: str, mascot_png: str, position: str,
                          start_offset: float, duration: float,
                          clip_duration: float,
                          tmp_dir: str, scene_id: str) -> str:
    """
    Overlay a transparent-background mascot PNG onto an already-rendered clip.
    The mascot pops in at start_offset seconds and stays for duration seconds
    (or the rest of the clip if duration=0).
    Returns the new clip path (or original if overlay fails).
    """
    output_path = os.path.join(tmp_dir, f"{scene_id}_m.mp4")
    xy = _MASCOT_POSITIONS.get(position, _MASCOT_POSITIONS["bottom-right"])
    end_time = (start_offset + duration) if duration > 0 else clip_duration
    end_time = min(end_time, clip_duration)
    enable_expr = f"between(t,{start_offset},{end_time})"

    cmd = [
        "ffmpeg", "-y",
        "-i", clip_path,
        "-i", mascot_png,
        "-filter_complex", (
            f"[1:v]scale=-1:{MASCOT_HEIGHT_PX}[m];"
            f"[0:v][m]overlay={xy}:enable='{enable_expr}'[v]"
        ),
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ⚠️  Mascot overlay failed for {scene_id} — using clip without mascot")
        return clip_path
    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
# CONCAT + BGM
# ═══════════════════════════════════════════════════════════════════════════════

def concatenate_clips(clip_paths: list, output_path: str, tmp_dir: str):
    list_path = os.path.join(tmp_dir, "concat_list.txt")
    with open(list_path, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p.replace(os.sep, '/')}'\n")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ ffmpeg concat error:\n{result.stderr[-500:]}")
        raise RuntimeError("ffmpeg concat failed")


def mix_background_music(video_path: str, bgm_path: str,
                          output_path: str, volume: float):
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex", (
            f"[1:a]volume={volume}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        ),
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ BGM mix error:\n{result.stderr[-500:]}")
        raise RuntimeError("BGM mix failed")


# ═══════════════════════════════════════════════════════════════════════════════
# CAPTION BURN (SRT — landscape-appropriate, bottom-third)
# ═══════════════════════════════════════════════════════════════════════════════

def burn_srt_captions(video_path: str, srt_path: str, output_path: str):
    """
    Burn SRT captions into the video using ffmpeg subtitles filter.
    Uses bottom-third positioning (Alignment=2, MarginV=40) and a clean
    white font with black outline — appropriate for 1920×1080 landscape.
    """
    # ffmpeg needs forward slashes and escaped colons in Windows paths
    srt_ff = srt_path.replace("\\", "/").replace(":", "\\:")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", (
            f"subtitles='{srt_ff}':force_style='"
            "FontName=Arial,FontSize=28,Bold=1,"
            "PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,"
            "Outline=2,Shadow=0,"
            "Alignment=2,MarginV=40'"
        ),
        "-c:v", "libx264", "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ Caption burn error:\n{result.stderr[-500:]}")
        raise RuntimeError("Caption burn failed")


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — STITCH
# ═══════════════════════════════════════════════════════════════════════════════

def run_stitch(project_dir: str, include_cta: bool = True) -> str:
    """Render all scenes → {project}/output/{episode}_final.mp4. Returns output path."""
    manifest_path = os.path.join(project_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    episode = manifest["episode"]
    scenes  = manifest["scenes"]
    output_dir   = os.path.join(project_dir, "output")
    output_file  = os.path.join(output_dir, f"{episode}_final.mp4")
    bgm_path     = os.path.join(project_dir, "bgm.mp3")

    os.makedirs(output_dir, exist_ok=True)

    # Mascot config (optional)
    mascot_config = load_mascot_config(project_dir)
    mascot_dir    = os.path.join(project_dir, mascot_config.get("mascot_dir", "mascots"))
    mascot_scenes = mascot_config.get("scenes", {})

    print(f"Project   : {project_dir}")
    print(f"Title     : {manifest.get('title', '—')}")
    print(f"Scenes    : {len(scenes)}")
    print(f"Resolution: {RESOLUTION}")
    print(f"Ken Burns : {'on' if KEN_BURNS_ENABLED else 'off'}  ({KEN_BURNS_ZOOM_RATIO}× zoom)")
    print(f"BGM       : {'✓ ' + bgm_path if os.path.exists(bgm_path) else '✗ none'}")
    if mascot_scenes:
        print(f"Mascot    : ✓ {len(mascot_scenes)} scene(s) configured")
    else:
        print(f"Mascot    : — (no mascot_config.json — add one when mascot PNGs are ready)")

    # CTA
    cta_mp3, cta_image = (None, None)
    if include_cta:
        cta_mp3, cta_image = find_cta(project_dir)
        print(f"CTA       : {'✓ ' + cta_mp3 if cta_mp3 else '— not found (run generate_source_audio.py + generate cta.png)'}")
    else:
        print(f"CTA       : — skipped (--no-cta)")
    print(f"{'─' * 55}")

    # ── Plan sources ────────────────────────────────────────────────────────
    print("\nPlanning sources...")
    scene_plan    = []
    missing_audio = []
    video_count   = 0
    image_count   = 0

    for scene in scenes:
        scene_id       = scene["id"]
        aud_path       = os.path.join(project_dir, scene["audio"])
        visual_group   = scene.get("visual_group_id")
        source_path, source_type = find_video_source(project_dir, scene_id, visual_group)

        if not os.path.exists(aud_path):
            missing_audio.append(aud_path)

        scene_plan.append({
            "id":          scene_id,
            "source_path": source_path,
            "source_type": source_type,
            "audio_path":  aud_path,
            "mascot":      mascot_scenes.get(scene_id),
        })

        if   source_type == "video": video_count += 1
        elif source_type == "image": image_count += 1

        label   = f"[{source_type.upper()}]" if source_type else "[MISSING]"
        mascot_mark = " 🎭" if scene_id in mascot_scenes else ""
        print(
            f"  {scene_id}  {label:<9}"
            f"  vis {'✓' if source_path else '✗'}"
            f"  aud {'✓' if os.path.exists(aud_path) else '✗'}"
            f"{mascot_mark}"
        )

    print(f"\n  Video clips : {video_count}")
    print(f"  Still images: {image_count}")

    # Append CTA as a final pseudo-scene (static, no Ken Burns)
    if cta_mp3 and cta_image:
        scene_plan.append({
            "id":          "SCENE-CTA",
            "source_path": cta_image,
            "source_type": "image",
            "audio_path":  cta_mp3,
            "mascot":      None,
            "force_static": True,   # no Ken Burns — CTA card must be readable
        })
        print(f"  CTA slide   : appended")

    missing_sources = [s for s in scene_plan if not s["source_path"]]
    if missing_sources or missing_audio:
        print("\n✗ Cannot stitch — missing files:")
        for s in missing_sources:
            print(f"  No image/video for {s['id']}")
        for a in missing_audio:
            print(f"  Missing audio: {a}")
        sys.exit(1)

    print(f"\n  ✓ All sources resolved. Starting render...")

    # ── Render ──────────────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_paths     = []
        total_duration = 0.0

        for i, plan in enumerate(scene_plan, 1):
            aud_duration    = get_audio_duration(plan["audio_path"])
            total_duration += aud_duration + 0.5
            label           = f"[{plan['source_type'].upper()}]"
            print(f"\n[{i:03d}/{len(scenes)}] {plan['id']}  {label}  ({aud_duration:.1f}s)")

            if plan["source_type"] == "video":
                clip = build_clip_from_video(
                    plan["id"], plan["source_path"],
                    plan["audio_path"], aud_duration, tmp_dir
                )
            else:
                clip = build_clip_from_image(
                    plan["id"], plan["source_path"],
                    plan["audio_path"], aud_duration, tmp_dir,
                    scene_index=i,
                    force_static=plan.get("force_static", False)
                )

            # Optional mascot overlay
            if plan["mascot"]:
                m          = plan["mascot"]
                mascot_png = os.path.join(mascot_dir, m["png"])
                if os.path.exists(mascot_png):
                    pos = m.get("position", "bottom-right")
                    print(f"  🎭 Mascot: {m['png']} @ {pos}")
                    clip = apply_mascot_overlay(
                        clip, mascot_png, pos,
                        m.get("start_offset", 0.5),
                        m.get("duration", 3.0),
                        aud_duration + 0.5,
                        tmp_dir, plan["id"]
                    )
                else:
                    print(f"  ⚠️  Mascot PNG not found: {mascot_png}")

            clip_paths.append(clip)
            print(f"  ✓ rendered")

        # ── Concat + optional BGM ────────────────────────────────────────────
        print(f"\n{'─' * 55}")
        m_min, m_sec = divmod(int(total_duration), 60)
        print(f"Concatenating {len(clip_paths)} clips  (~{m_min}m {m_sec}s)...")

        if os.path.exists(bgm_path):
            concat_tmp = os.path.join(tmp_dir, "concat_no_bgm.mp4")
            concatenate_clips(clip_paths, concat_tmp, tmp_dir)
            print(f"Mixing BGM at {int(BGM_VOLUME * 100)}%...")
            mix_background_music(concat_tmp, bgm_path, output_file, BGM_VOLUME)
        else:
            concatenate_clips(clip_paths, output_file, tmp_dir)

    print(f"\n✓ Stitch done: {output_file}  (~{m_min}m {m_sec}s)")
    return output_file


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Long-form stitch: 1920×1080 landscape, Ken Burns, mascot overlay, SRT captions"
    )
    parser.add_argument(
        "--project", required=True,
        help="Project folder — relative to this script, or absolute path. "
             "Example: ..\\..\\interested_indian_pipeline\\ep01"
    )
    parser.add_argument(
        "--skip-captions", action="store_true",
        help="Stop after stitch — skip stamp, SRT generation, and caption burn"
    )
    parser.add_argument(
        "--no-cta", action="store_true",
        help="Skip the shared CTA scene even if common/cta/ assets are present"
    )
    args = parser.parse_args()

    script_dir  = os.path.dirname(os.path.abspath(__file__))
    project_dir = (
        args.project if os.path.isabs(args.project)
        else os.path.normpath(os.path.join(script_dir, args.project))
    )
    if not os.path.isdir(project_dir):
        print(f"❌ Project folder not found: {project_dir}")
        sys.exit(1)

    # ── Step 1: Stitch ────────────────────────────────────────────────────
    print(f"\n{'═' * 55}")
    print("STEP 1/4 — STITCH  (1920×1080 landscape)")
    print(f"{'═' * 55}\n")
    final_video = run_stitch(project_dir, include_cta=not args.no_cta)

    if args.skip_captions:
        print("\n--skip-captions set. Done.")
        return

    if not _CAPTION_PIPELINE_AVAILABLE:
        print(f"\n⚠️  Caption pipeline unavailable: {_CAPTION_IMPORT_ERROR}")
        print("   stamp_manifest.py and generate_srt.py must be in the same folder.")
        sys.exit(1)

    # ── Step 2: Stamp manifest ────────────────────────────────────────────
    print(f"\n{'═' * 55}")
    print("STEP 2/4 — STAMP MANIFEST")
    print(f"{'═' * 55}\n")
    stamp_manifest(project_dir)

    # ── Step 3: Generate SRT ──────────────────────────────────────────────
    print(f"\n{'═' * 55}")
    print("STEP 3/4 — GENERATE SRT")
    print(f"{'═' * 55}\n")
    generate_srt(project_dir)

    # ── Step 4: Burn SRT captions ─────────────────────────────────────────
    print(f"\n{'═' * 55}")
    print("STEP 4/4 — BURN CAPTIONS (SRT, bottom-third)")
    print(f"{'═' * 55}\n")

    with open(os.path.join(project_dir, "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    episode       = manifest["episode"]
    output_dir    = os.path.join(project_dir, "output")
    srt_path      = os.path.join(output_dir, f"{episode}_captions.srt")
    captioned_out = os.path.join(output_dir, f"{episode}_captioned.mp4")

    if os.path.exists(srt_path):
        print(f"Burning captions from {srt_path}...")
        burn_srt_captions(final_video, srt_path, captioned_out)
        print(f"✓ Captioned: {captioned_out}")
    else:
        print(f"⚠️  SRT not found at {srt_path} — skipping burn")

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'═' * 55}")
    print(f"✅ ALL DONE — {manifest.get('title', episode)}")
    print(f"{'═' * 55}")
    print(f"  {episode}_final.mp4      ← stitched, no captions")
    print(f"  {episode}_captions.srt   ← upload to YouTube as subtitle track")
    print(f"  {episode}_captioned.mp4  ← burned-in captions, ready to post")
    print(f"\n  All files in: {output_dir}")
    print(f"\nTo re-run this episode:")
    print(f'  python stitch_video_longform.py --project "{args.project}"')


if __name__ == "__main__":
    main()
