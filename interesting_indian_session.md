# The Interested Indian — Session Tracker

## Channel
Faceless YouTube channel on Indian history, geography, geopolitics, and administrative dynamics — modeled on the "Fat Little Asian Man" production blueprint. Minimalist 2D doodle/vector style, stick-figure mascot, 16:9, 12–18 min analytical video essays.

## Workflow Stages
1. Generate 5 viral topic ideas
2. Full narration script (2,000–2,800 words) → downloadable .txt
3. User pastes timestamped script (post-voiceover) → image prompts + editing cues, batches of 20
4. Final viral metadata (title, description, tags)

## Status Log

### Stage 1 — Topic Ideas (Complete)
Five ideas generated:
1. How One 1952 Freight Policy Stunted Eastern India for 40 Years
2. Why the Northeast Was Cut Off From the Rest of India
3. The Reorganization That Redrew India Overnight
4. **Why South Indian States Collect More Tax Than They Get Back** ← SELECTED
5. The Administrative Line That Still Splits Kashmir's Economy

### Stage 2 — Script Generation (Complete)
- Selected topic: #4 — fiscal federalism / Finance Commission devolution formulas / interstate transfer dispute
- Output file: `script_south_india_tax_devolution.txt` (~2,300 words)
- Key facts verified via search: 14th FC (2015–20, 42% vertical share) vs 15th FC (2021–26, 41%); Karnataka's share fell 4.713%→3.647% (~23% drop, ~₹80,000 cr cumulative loss); "15 paise per rupee" figure; 2011 vs 1971 census population-data shift as core mechanism; demographic performance/income distance/area/forest cover weighting; 2024 multi-state CM protest in Delhi; GST compensation expiry 2022; 16th FC (Panagariya, constituted Nov 2023, effective FY2027–31) early estimates showing Karnataka's share recovering to ~4.131%
- Waiting on: user to record voiceover (Edge TTS / ElevenLabs) and paste back a timestamped transcript to begin Stage 3

### Stage 3 — Image Prompts + Editing Cues
- Not started. Waiting on user's timestamped script (post voiceover + transcription).

### Stage 4 — Final Metadata
- Not started.

## Pipeline Tooling
- `auto_split_scenes_v1_stage3_export.py` created as a versioned snapshot of `shorts_pipeline2/auto_split_scenes.py` (baseline untouched, per existing vN convention)
- Addition: after writing `manifest.json`, also writes `{project}/timestamped_script.txt` — one `[MM:SS]` line per scene, with scenes sharing a `visual_group_id` merged into a single line (matches Stage 3's "hold the same background across consecutive lines" rule)
- Use `--video-type LongVideo` for this project so timing-based scene grouping runs before export
- `--fragment-max-seconds` CLI flag added (default 2.5s, same as before — no behavior change unless you pass it). Verified: passing a value that's higher than *every* scene's duration collapses the whole episode into one group, so pick a threshold that sits between your typical short/transitional sentences and typical full-thought sentences — not just "as high as possible."
- `generate_source_audio.py` created — takes the raw narration .txt directly (no manifest.json needed, since none exists yet at this point) and calls `edge_tts.Communicate()` (Python function, not CLI) to produce one continuous source audio file. Avoids CLI argument-length/quoting issues with long scripts; the library chunks internally regardless of call method.
- End-to-end flow for this project: `script_*.txt` (Stage 2 output) → `generate_source_audio.py` → `{project}/source_audio/narration.mp3` → `auto_split_scenes_v1_stage3_export.py --video-type LongVideo` → `manifest.json` + `timestamped_script.txt` → paste into Stage 3
- `generate_source_audio.py` extended with two more modes:
  - `--list-voices [--locale en-US]` — prints available Edge TTS voices for a locale, no audio generated
  - `--preview N` — synthesizes only the first N sentences (tested: correctly extracts "Karnataka contributes nearly nine percent..." + next sentence from the actual episode script), writes to `preview_{voice}.mp3` so multiple candidates don't overwrite each other, so voices can be A/B'd before committing to a full ~15min generation
- Standalone `manifest_to_timestamped_script.py` also exists (converts any existing manifest.json → same format, no merging) — useful for one-off conversions without rerunning WhisperX

## Project Structure Decision (Option B)
- Restructured as a sibling project: `interested_indian_pipeline/` sits alongside `shorts_pipeline2/`, does NOT duplicate pipeline scripts
- Episode folder renamed `interested-indian-ep01` → `ep01` (matches existing `ep02`-style convention)
- Pipeline scripts (`auto_split_scenes_v1_stage3_export.py`, `generate_source_audio.py`, `stitch_video.py`) stay solely in `shorts_pipeline2/` as shared source of truth
- Commands invoked from `shorts_pipeline2\` with `--project ..\..\interested_indian_pipeline\ep01` (confirmed actual path: `C:\Bakcup_Asus\Aeonium_Glow\shorts_pipeline2` — so `interested_indian_pipeline` recommended as sibling to `Aeonium_Glow` itself, i.e. `C:\Bakcup_Asus\interested_indian_pipeline`, making it two levels up from shorts_pipeline2, not one)
- Git initialized: `.gitignore` excludes generated media (`audio/`, `source_audio/`, `images/`, `videos/`, `output/` contents — keeps `.gitkeep`), tracks scripts/manifests/READMEs. Initial commit made with placeholder git identity (`Giri <giri@example.com>`) — needs real `git config user.name`/`user.email` before next commit
- Delivered as `interested_indian_pipeline.zip` (includes `.git/` history)

## shorts_pipeline2 Git Setup
- Not initialized by Claude directly — the actual `shorts_pipeline2` folder (containing `stitch_video.py`, `generate_cta_card.py`, `debug_whisperx.py`, and other files never uploaded to this chat) isn't available here, so committing a partial picture would misrepresent the real repo history
- Delivered instead: `shorts_pipeline2.gitignore` (covers generated media across every project subfolder — ep01/ep02/short-04/etc. — plus Python/model caches) and `shorts_pipeline2_README.md` (documents all known scripts, the versioning convention, and that Interested Indian's project folders live in the separate sibling repo rather than inside this one)
- **To do locally, in the real folder:**
  ```
  cd C:\Bakcup_Asus\Aeonium_Glow\shorts_pipeline2
  # copy in shorts_pipeline2.gitignore as .gitignore, and shorts_pipeline2_README.md as README.md
  git init
  git config user.name "your actual name"
  git config user.email "your actual email"
  git add -A
  git commit -m "Initial commit: shared pipeline scripts"
  ```
- Once created, connect to GitHub the same way as `interested_indian_pipeline` (separate repo — e.g. `shorts-pipeline2` — not nested inside it, since it's shared across three channels):
  ```
  git remote add origin https://github.com/ghrao44741/shorts-pipeline2.git
  git branch -M main
  git push -u origin main
  ```

## Image Generation Issue — Gemini Getting Map Geography Wrong
- Gemini/Imagen repeatedly misplaced Karnataka's location/shape on generated India maps — a fundamental limitation (diffusion models lack reliable spatial/geographic reasoning), not a prompt-wording fix
- Solution built: `generate_india_map.py` — renders India state-highlight maps from real GeoJSON boundary data (geohacker/india, GADM-derived) instead of AI generation. 100% geographically accurate by construction, matches channel's flat high-contrast vector map style, text-free (labels added in post per Stage 3 convention)
- Supports single-state highlight, multi-state same-color group, and two-group comparison maps (e.g. Karnataka/TN/Kerala in crimson vs UP/Bihar in green) — verified visually correct on both single and comparison renders
- `--list-states` validates names before wasting a render; dataset uses pre-rename names (Orissa, Uttaranchal, undivided Jammu and Kashmir) — check spelling per episode
- Geojson source (22MB, not delivered as chat output — too large, one-time download): `curl -o india_states.geojson https://raw.githubusercontent.com/geohacker/india/master/state/india_state.geojson`, save once in shorts_pipeline2 (reusable across all episodes, not per-episode content)
- Recommendation: use this script for any scene where a specific state/region *is* the point of the shot; keep Gemini for non-geographic illustrative scenes (mascot poses, abstract concept visuals, document/vault imagery, etc.)

## Image Generation In Progress (generate_images.py)
- Reviewed `generate_images.py` (Gemini/Imagen-based, reads image_prompts_one_line_per_prompt_fixed.md, outputs to ep01/images/)
- **Time-sensitive:** Imagen models (imagen-4.0-generate-001) deprecated, shutting down August 17, 2026 per Google's docs — fine to finish current batch, but plan migration to gemini-2.5-flash-image ("Nano Banana", different response shape — generate_content + inline_data parts, not generate_images) for episode 2 onward
- Other review notes (not yet acted on, batch was in progress): no retry/backoff on API errors, no rate-limit delay between calls, skip-if-exists doesn't validate file integrity, no persistent log file — offered to build `generate_images_v1_retry_logging.py` once batch completes, following existing versioning convention
## Session Status (Updated — ep01 COMPLETE)
Where things stand:
- Stages 1–4 done. ep01 fully produced and ready for upload.
- All 90 images (79 SCENE + 11 group) reviewed, 8 manually flagged images regenerated (group-01, SCENE-018, SCENE-026, SCENE-061, SCENE-062, SCENE-076, SCENE-078, SCENE-083)
- Final review: 85 PASS, 5 WARN (overlay-text-only, acceptable), 0 FAIL
- BGM added: `ep01/bgm.mp3` — behavioral_finance_bed track at 8% volume
- Video stitched: `ep01/output/ep01_final.mp4` — 9:51 runtime, CTA confirmed good
- Audio analysis: -25.89 dBFS overall, flat/consistent throughout, peak -8.1 dBFS
- Note: 9:51 video vs 10:05 source narration — difference is 57s of inter-sentence silence trimmed by WhisperX across 107 scenes; no words missing
- `local_mp4_analyzer.py` added to `interested_indian_pipeline/` (updated header from Aeonium Glow → The Interested Indian)
- GitHub push still pending
- `shorts_pipeline2` git setup files delivered but not yet applied locally

## Longform Pipeline Tooling (This Session)

### New Scripts Built
- **`stitch_video_longform.py`** (in `shorts_pipeline2/`) — longform stitch for The Interested Indian. 1920×1080 landscape, Ken Burns upscales to 3840×2160 then crops back to 1080p, mascot overlay via `mascot_config.json`, SRT caption burn (not karaoke), BGM at 0.08 volume, auto-detects CTA from `{project}/../common/cta/cta.mp3 + cta.png`. Flags: `--project`, `--skip-captions`, `--no-cta`. Run from `shorts_pipeline2/`: `python stitch_video_longform.py --project ..\..\interested_indian_pipeline\ep01`
- **`review_images.py`** (in `interested_indian_pipeline/`) — AI image QA agent using Claude Haiku vision. Reads `image_prompts_one_line_per_prompt.md`, sends each image with 8-check rubric (style, content, overlay text, ratio, artifacts, typos, on-topic, watermark). Outputs `ep01/review_report.md` with PASS/WARN/FAIL verdicts. ~$1–2 for 90 images. Flags: `--project`, `--shot N`, `--fail-only`, `--model haiku|sonnet`
- **`generate_images_flux.py`** (in `interested_indian_pipeline/`) — batch image generator via Replicate (Flux schnell/dev/pro) or xAI Grok API. Reads same prompts file, skips existing images by default. `--from-report` regenerates only WARN/FAIL shots from review_report.md. `--overwrite` forces regeneration. Default: Flux dev at $0.025/image
- **`match_images.py`** (in `interested_indian_pipeline/`) — matches unnamed/wrongly-named images to correct SCENE-XXX.png or group-XX.png filenames using Claude Haiku vision. Dry-run by default; `--apply` renames files. `--confidence` threshold (high/medium/low). Essential for images from Flo that arrive without proper names

### CTA
- Script: *"If this changed how you think about India — not the textbook version, but how it actually works — subscribe. More essays on the way. I'll see you in the next one."*
- Stored at: `interested_indian_pipeline/common/cta/cta_script.txt`, `cta.mp3`, and `cta.png`
- CTA confirmed good in final video ✓

### Image Prompts File
- `ep01/image_prompts_one_line_per_prompt.md` — all 90 shots reformatted to one line each. Used by review_images.py, generate_images_flux.py, and match_images.py
- Typos fixed from original: `Siddharma Yaa` → `Siddaramaiah`, `waiting` → `weighting`

### ep01 Image Status
- 91 files dropped into `ep01/images/` (mix of Flo-generated with inconsistent naming)
- `match_images.py --apply` run → 87 files renamed to correct SCENE-XXX.png / group-XX.png names
- 3 images generated via `generate_images_flux.py` for missing shots: SCENE-069, SCENE-083, SCENE-091
- **Issue found:** all renamed files were JPEGs with .png extension → Claude API rejected them during review. Fixed with PIL batch conversion: `Image.open(p).save(p, format='PNG')` for all files
- `SCENE-099.jpg` duplicate removed; `cta.jpg` moved to `common/cta/cta.png`
- Current state: 90 images (79 SCENE + 11 group), all proper PNG format, ready for review

### Group Image Approach (ep01 Decision)
- Stitch script processes 107 scenes, not 90 shots. For scenes sharing a `visual_group_id`, `find_video_source()` falls back to `group-XX.png` automatically — no need to duplicate images
- Decided on **Option A**: name shared images as `group-XX.png` and let the stitch script handle fallback natively
- Same approach should eventually be ported to `shorts_pipeline2/stitch_video_complete.py`

## Immediate Next Steps (Resume Here)
1. **Upload ep01 to YouTube** — `ep01/output/ep01_final.mp4` ready; metadata in `ep01/metadata_*.txt`
2. **Thumbnail** — still needed for ep01 before upload; will be automated for ep02+
3. **Chapter markers** — run Whisper on final MP4, Claude groups into chapters for description
4. **YouTube upload script** — `upload_youtube.py` (YouTube Data API v3); covers upload + thumbnail + chapters + scheduling
5. **Notification agent** — email/Telegram alert when pipeline completes or checkpoint fires
6. **Analytics feedback loop** — after 7 days live, pull YouTube Analytics into Research Agent's competitive intelligence
7. **GitHub:** push `interested_indian_pipeline` repo (blocked on repo-naming mismatch)
8. **Port group fallback to shorts_pipeline2:** add `visual_group_id` → `group-XX.png` lookup to `stitch_video_complete.py`

## Automation Pipeline (Multi-Agent System)

### Scripts Built This Session
- **`generate_image_prompts.py`** — Stage 3 automation. Reads `manifest.json`, deduplicates by `visual_group_id`, calls Claude API in batches of 10 to generate image prompts matching channel visual style DNA. Output: `image_prompts_one_line_per_prompt.md`. Usage: `python generate_image_prompts.py --project ep01`
- **`pipeline_agents.py`** — Three-agent system:
  - **OrchestratorAgent**: routes pipeline stages, calls ReviewAgent after each stage, uses Claude to decide retry/human_checkpoint/proceed/abort on failures. Human checkpoints only when genuinely needed.
  - **ReviewAgent**: mix of rule-based + Claude qualitative scoring (pass threshold 7/10) for every stage — topics, script, voice, split, prompts, images, stitch, metadata.
  - **ResearchAgent**: three research tracks before script generation:
    - Track 1 (Facts): verified dates, statistics, acts, laws, recent developments
    - Track 2 (Audience): Reddit/Quora/news — what people are asking, misconceptions, emotional flashpoints, scroll-stopping angles
    - Track 3 (Competitive): what justaFLAM, Wendover, RealLifeLore, Dhruv Rathee etc. are doing — title formulas, content gaps, what to avoid
    - Synthesises all three into hook ideas + formatted brief injected into script prompt
  - `REFERENCE_CHANNELS` list in ResearchAgent is configurable
- **`run_episode_v2.py`** — entry point using the three agents. Usage:
  ```
  python run_episode_v2.py --project ep02
  python run_episode_v2.py --project ep02 --from-stage script
  python run_episode_v2.py --project ep02 --status
  ```
- **`run_episode.py`** — earlier simpler orchestrator (no agents), kept as backup
- **`local_mp4_analyzer.py`** — audio analysis + Whisper transcription for final MP4. Updated header to "THE INTERESTED INDIAN". Usage: `python local_mp4_analyzer.py ep01/output/ep01_final.mp4`

### Human Checkpoints (3 total per episode)
1. Pick topic from 5 ideas
2. Approve/edit/redo script
3. Watch final video, approve upload

### Post-Video Automation (To Build)
- `generate_thumbnail.py` — Flux/Grok base image + Pillow text overlay, 1280×720px
- `upload_youtube.py` — YouTube Data API v3; upload + thumbnail + chapters + schedule
- Chapter generation — Whisper on final MP4 → Claude groups into 6–8 chapters → injected into description
- Notification agent — email or Telegram alert at checkpoints and on completion
- Analytics feedback loop — 7-day post-publish YouTube Analytics → fed back into ResearchAgent competitive intelligence

## Notes / Decisions
- (add anything that changes scope, tone, or topic here as we go)
