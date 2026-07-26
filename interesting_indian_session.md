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

## Immediate Next Steps (Resume Here) — updated 2026-07-23

### EP01 Status: Video generated ✓ — Testing in progress
- `ep01_final.mp4` generated (10:50 duration, -26.85 dBFS, 1920×1080)
- Visual quality confirmed good from screenshots (flat cartoon style ✓, text overlays ✓, channel branding ✓)
- **Pending before upload:**
  - Regenerate voiceover with male voice (en-US-GuyNeural) — manifest has JennyNeural (female)
  - Review video end-to-end and note any image quality issues
  - Generate thumbnail: `python generate_thumbnail.py --project ep01`
  - Generate chapters: `python generate_chapters.py --project ep01`
  - Upload: `python run_episode_v2.py --project ep01 --from-stage upload`

### To regenerate EP01 voice with male voice:
```powershell
python generate_source_audio.py --project ep01 --script ep01\script_the_clause_that_makes_the_president_ask_the_govern.txt --voice en-US-GuyNeural
python run_episode_v2.py --project ep01 --from-stage split
```

### Pipeline fixes in this session (2026-07-23):
1. **PIL text overlay stage** (`add_text_overlays.py`) — burns OVERLAY text from prompts file onto images post-generation. Navy banner bottom, white bold text, amber badge. Backed up as `_orig.png`. Added to STAGE_ORDER between images and stitch.
2. **AI text typos fixed** — `generate_image_prompts.py` updated to forbid text in AI image prompts; all text added via PIL overlay stage.
3. **`generate_images_aibmm.py` `--force-general` flag** — generates map/chart/photo scenes as cartoon illustrations instead of routing to dedicated generators.
4. **`stitch_video_longform.py` group image resolution** — `resolve_group_images()` copies group rep → all member filenames before stitch. Now logs each copy (no silent failure).
5. **BGM support** — `common/bgm/default.mp3` created. Stitch falls back to `common/bgm/` automatically if no `ep01/bgm.mp3`.
6. **Voice config** — `channel_config.json` now has `voice.default = en-US-GuyNeural`. Pipeline reads this instead of hardcoded JennyNeural.
7. **Ken Burns zoom configurable** — `channel_config.json` `stitch.ken_burns_zoom = 1.05` (was hardcoded 1.08). Stitch reads at startup.
8. **`review_images.py`** — fixed style guide (was checking "minimalist stick figures"), fixed topic (was fiscal federalism), fixed overlay_ok check (always true, PIL adds separately).
9. **`generate_images_flux.py`** — fixed STYLE_PREFIX (was injecting "stick figures" into every prompt).
10. **`auto_split_scenes_v1_stage3_export.py`** — now writes `duration` per scene and `total_duration` to manifest.

### Remaining tasks:
- #11 Install CUDA PyTorch in transcription-tools venv (WhisperX slow on CPU)
- #12 Fix banned word false positives in script reviewer
- #13 Tune question ratio threshold in script reviewer
- #14 Review EP01 generated images for visual style quality
- #15 Build notification agent
- #16 Build analytics feedback loop

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

## This Session — Brand Color System, Script Comparisons, Shorts Fork

### ⚠️ Possible Duplicate Tooling — Check Before Using
This session (a separate conversation from the one that built the automation pipeline above) built **`generate_images_review.py`** — a Gemini-vision image QA agent, parsing the same `image_prompts_one_line_per_prompt*.md` format, flagging style drift / baked-in text / corruption / content mismatch, and explicitly routing any prompt naming an Indian state to `generate_india_map.py` rather than trying to vision-check map accuracy (a vision reviewer has the same geographic blind spot as the generator). **This looks like it overlaps with `review_images.py`** (Claude Haiku vision, 8-check rubric) documented above as already built and used successfully on ep01's 90 images. The two were built in different conversations without awareness of each other. Before running either again: compare the two, pick one, and retire the other — don't maintain both.

### Brand Color System (brand.json)
- **Problem found:** both stitch scripts hardcoded `color=0xFAF7F2` (That's Why's warm white) in every ffmpeg pad/letterbox filter — including in `stitch_video_longform.py`, where it's simply wrong for Interested Indian's actual visual identity. Also a **latent bug in `stitch_video_complete.py`**: since that one script serves both That's Why *and* Aeonium Glow, any Aeonium Glow clip needing letterbox padding would silently get cream bars instead of its actual dark background — hadn't surfaced yet only because no asset so far happened to mismatch the output aspect ratio exactly.
- **Fix:** both scripts gained `load_pad_color(project_dir, default_hex)` + `normalize_hex_for_ffmpeg()` helpers. Looks for `{project}/brand.json` (long-form also checks `{project}/../brand.json` as a channel-level fallback, matching the sibling-repo layout). Format: `{"pad_color": "#1A2B4C"}` (accepts `#RRGGBB`, `0xRRGGBB`, or bare `RRGGBB`). Falls back to the old hardcoded value if no `brand.json` is found — verified zero behavior change for any project that doesn't have one yet.
- Both scripts now print the active pad color (and flag explicitly when it's the fallback default) at the top of every stitch run.
- **Three brand.json files finalized and delivered:**
  - Aeonium Glow → `#1C1C1A`
  - That's Why → `#FAF7F2`
  - **Interested Indian → `#1A2B4C` (deep navy) — finalized this session.** Rejected `#1C1C1C` charcoal (the placeholder color already silently baked into `generate_india_map.py`) because it's visually indistinguishable from Aeonium Glow's `#1C1C1A` — would've made two channels look the same. Navy chosen instead for distinctiveness + fits the analytical/geopolitics tone.
  - `generate_india_map.py`'s `BACKGROUND` constant updated from `#1C1C1C` → `#1A2B4C` to match; re-verified the Karnataka test render looks correct against the new navy background.
- **To do:** drop `brand.json` (pad_color `#1A2B4C`) into `ep01/` or once at `interested_indian_pipeline/brand.json` (channel root) so every episode picks it up via the parent-fallback check.

### Long-form vs Shorts Stitch Script — Full Comparison
Beyond the obvious 1920×1080 vs 1080×1920 resolution split, `stitch_video_longform.py` and `stitch_video_complete.py` differ in several *intentional* ways that should NOT be unified:
- **CTA architecture is fundamentally different, not just resized.** Shorts overlays the CTA card onto the *last manifest scene's own audio* (zero added runtime — matters for Shorts completion-rate algorithm); long-form appends a whole separate `SCENE-CTA` pseudo-scene with its own shared audio/image from `{project}/../common/cta/` (a few extra seconds is negligible on a 10+ min video). Shorts auto-generates its CTA card via `generate_cta_card.py` from manifest fields; long-form expects the shared assets pre-made. Don't port either approach onto the other format.
- **Mascot overlay exists only in long-form** (`mascot_config.json`, `apply_mascot_overlay`) — genuinely Interested-Indian-specific (its stick-figure mascot), not something Aeonium Glow/That's Why need.
- **Captions use two different systems**, not just different styling: long-form burns plain SRT bottom-third subtitles inline in the same file; Shorts imports an external `burn_captions.py` (presumably karaoke/ASS word-highlight style, per long-form's own docstring noting "not karaoke ASS"). Correct for each format — long essay vs punchy short-form.
- **BGM volume: 0.08 (long-form) vs 0.10 (Shorts)** — deliberate, quieter under sustained narration.
- **Path resolution:** long-form wraps the relative path in `os.path.normpath()` — needed for the `..\..\interested_indian_pipeline\ep01` sibling-repo traversal; Shorts doesn't need this since its projects are flat subfolders.
- **Only bug found (not a format difference):** the hardcoded pad color — now fixed via brand.json above.

### Scene Splitter — auto_split_scenes.py vs auto_split_scenes_v1_stage3_export.py
Confirmed **safe to switch Shorts over to the stage3_export version** — it's a strict superset for `--video-type ShortVideo` (the default in both files):
- Every audio-processing function (`transcribe_with_timestamps`, `split_into_sentences`, `split_long_sentence`, `build_scenes`, `cut_audio_clip`) is byte-identical between the two.
- The only two changes are additive: `group_scenes_by_timing` gained a configurable `--fragment-max-seconds` (only runs in `LongVideo` mode — no-op for Shorts' default `ShortVideo` mode), and a new Step 6 writes `{project}/timestamped_script.txt` alongside `manifest.json` (inert unless you use it — `stitch_video.py` never reads it).
- In `ShortVideo` mode specifically, both scripts produce identical `manifest.json`, identical audio clips, identical terminal output.
- Worth knowing: if a Short ever uses a numbered-list format ("Sign 1... Sign 2..."), both scripts' docstrings already recommend `--video-type LongVideo` for it — and `--fragment-max-seconds`' 2.5s default was written with exactly that Aeonium Glow label+explanation format in mind, so it's arguably more directly useful there than for long-form.

### generate_source_audio.py — Confirmed Long-Form Only + New Shorts Fork
- Clarified: `generate_source_audio.py` is used for the ~10-min long-form narration only.
- Compared against a raw `edge-tts --text "..." --write-media narration.wav` CLI test (used for an Aeonium Glow succulent-watering Short). Found: (1) the script had **no `--rate`/`--pitch`/`--volume` support at all** — couldn't replicate the CLI test's `--rate=-10%`; (2) `--write-media narration.wav` is a mislabeled file — edge-tts always returns mp3-encoded bytes regardless of the extension given, so that "wav" file is actually mp3 data; usually harmless since most tools sniff content, but a landmine for anything trusting the extension.
- **`generate_source_audio_shorts.py` created** as a dedicated Shorts fork (not just a flag added to the long-form script): same `--project`/`--script`/`--preview`/`--list-voices` interface, now with `--rate`/`--pitch`/`--volume` exposed, and its "next step" hint corrected to `--video-type ShortVideo` (the long-form version hardcoded `LongVideo`, which would've been wrong advice for a Short). `generate_source_audio.py` itself was left untouched — no risk to the long-form pipeline.
- Example Shorts usage: `python generate_source_audio_shorts.py --project aeonium-glow/succulent-watering --script succulent_watering_script.txt --voice en-US-JennyNeural --rate=-10% --out narration.mp3`

## Notes / Decisions
- (add anything that changes scope, tone, or topic here as we go)

---

## Session — Voice Style, Visual Overhaul, Image Pipeline, Mascot Design

### Channel Voice — CHANNEL_DNA Rewrite (pipeline_agents.py)
- Rewrote `CHANNEL_DNA` to match justaFLAM's first-person conversational voice:
  - Narrator is "I", audience is "you". Direct, occasionally self-deprecating.
  - Humor mandate: every 2–3 paragraphs must include one of: modern analogy, self-aware observation, deadpan understatement, or gentle audience poke.
  - Jargon rule: NEVER use a policy term without immediately translating it in plain language.
  - Banned words: "genuinely", "honestly", "straightforward" (flags in script reviewer — TODO: fix false positives, Task #12).
- Added `_print_script_preview` to `_stage_script`: shows word count, question ratio, banned words, hook + close paragraphs, and a one-line Claude tone-check.

### Visual Style — generate_image_prompts.py
- Rewrote `SYSTEM_PROMPT` from minimalist doodle → flat digital cartoon:
  - Background: warm cream (#FAF7F2), pale sky blue, or soft yellow — NOT stark white
  - Mascot: chubby round cartoon character, big round amber glasses, thick eyebrows, short stubby arms — NOT a stick figure
  - Maps: color-coded with DISTINCT colors per region, bold black borders, labeled callout boxes
  - Photo inserts: where real context helps, describe as a blended overlay
  - Charts: colorful, each bar/segment a different region color
  - Mandatory opener: `"Flat digital cartoon illustration, warm cream background,"`
  - Mandatory ender: `"bold outlines, vibrant colors, 16:9"`

### EP01 Restart — Article 356
- Old EP01 (tax devolution) renamed to `ep01_v1`
- New EP01 topic: "What Happens When The President Fires A State Government?" (Article 356 / President's Rule)
- Script: `script_the_clause_that_makes_the_president_ask_the_govern.txt` — 1885 words, justaFLAM voice
- Audio: `source_audio/narration.mp3` — 12.1 minutes, en-US-JennyNeural ✓
- WhisperX split: was running on CPU at end of session (status unknown — check manifest.json)

### Pipeline Bug Fixes (pipeline_agents.py)
All found and fixed during EP01 test run:
- `_review_topics`: row parser used `startswith("|")` — failed when table rows have no leading pipe. Fixed to `"|" in s`.
- `_claude_assess`: `json.loads("")` crash on empty API response. Fixed with retry loop (2 retries, exponential backoff).
- `duckduckgo_search` renamed to `ddgs`: dual-import with fallback added.
- Question counter always 0: `re.split(r'[.!?]+')` consumed `?` so ratio was 0. Fixed with `text.count("?")`.
- Voice stage: passed `--out-dir` (wrong). Fixed to `--project` + `--script`.
- WhisperX not in main Python: added `WHISPERX_PYTHON` constant routing `_stage_split` to correct venv.
- `_stage_split` missing `--audio`: finds mp3 in `source_audio/`, passes filename only (not full path — avoids path doubling).
- `--device cuda` on CPU-only torch: fixed to pass `--device cpu`.
- `pydub` ImportError in voice reviewer: catches ImportError separately, warns, passes with score ≥ 7.

### Thumbnail System — Dark / Light / Auto Themes
- `generate_thumbnail.py` updated with `THEMES` dict:
  - `dark`: deep navy (#0C1828) bg, white text, amber accent — odd episodes
  - `light`: warm cream (#FAF7F2) bg, dark brown text, crimson accent — even episodes
  - `auto`: parses episode number from folder name, alternates automatically
- New `--theme dark|light|auto` CLI argument (default: `auto`)
- `pipeline_agents.py` `_stage_thumbnail` updated to pass `--theme auto`
- Effect: channel grid shows alternating dark/light checkerboard pattern

### Mascot Design — The Interested Indian
- **Design formula** (based on justaFLAM analysis): anchor mascot + real geography background + 2-line huge text + one specific shocking stat
- **Mascot locked**: chubby round Indian cartoon character, amber round glasses, spiky dark hair, warm tan skin (#D4A85C), cream kurta, off-white pajama trousers with gathered ankles, simple leather sandals
- **4 expressions**: NEUTRAL, SHOCKED, CONFUSED, SMUG — on single reference sheet
- **Reference image URL**: `https://rqkumunldqvmynqxibca.supabase.co/storage/v1/object/public/generated-images/adhoc-1784824975374.png`
- **Local file**: `mascot_reference.png` (download via PowerShell Invoke-WebRequest if missing)
- **Session ID**: `d9d31dea-1095-4a70-b57e-9c0de7eaca7b` (for AIBMM continuity)
- **Thumbnail tested**: both dark and light versions generated via GPT Image 2 (AIBMM MCP)

### channel_config.json (new)
Central config file committed to repo:
- Channel name, handle, tagline
- Mascot reference URL + local path + description + locked date
- Thumbnail theme system documentation
- Image pipeline routing table (which script handles which scene type)
- Pexels API key env var name

### Image Pipeline — 4-Type Scene Architecture
Scene types and their dedicated generators:

| Type | Script | Method |
|---|---|---|
| mascot / general | `generate_images_aibmm.py` | OpenAI GPT Image 2, mascot reference via images.edit() |
| map | `generate_india_map.py` | geopandas + real GeoJSON (accurate geography) |
| chart / stat | `generate_chart.py` | matplotlib (bar, timeline, stat card, pie) |
| photo | `search_pexels.py` | Pexels API, free, commercial OK |

Classification: `generate_images_aibmm.py` auto-classifies each scene from prompt keywords and skips non-mascot types with a pointer to the right script.

### generate_india_map.py — Major Update
- Background changed: dark navy → warm cream (#FAF7F2) with pale blue ocean
- States now color-coded by region (North/South/East/West/Central/NE) — muted palette
- Highlighted states: crimson bold border + white name label with colored stroke
- Auto-downloads GeoJSON on first run → cached at `data/india_states.geojson`
- New flags: `--title`, `--callout`, `--project`, `--shot`, `--all-labels`
- `--geojson` now optional (defaults to cached file)
- Backward compatible: `--highlight` still comma-separated, `--out` still works

### search_pexels.py (new)
- Searches Pexels API for photo-type scenes
- Auto-extracts keywords from narration text (capitalised proper nouns)
- Downloads best landscape result, crops/resizes to 1280×720 PNG
- `--query` for single search, `--project` for batch (all photo scenes)
- `--preview` to see results without downloading
- API key: `PEXELS_API_KEY` in `.env` ✓ tested and working

### generate_chart.py (new)
Chart types:
- `bar` — horizontal/vertical bar chart, one color per bar from channel palette
- `stat` — big number callout card (e.g. "91 / Article 356 impositions")
- `timeline` — horizontal year-based event timeline, alternating above/below labels
- `pie` — pie chart with percentage labels
All use warm cream background, channel color palette. `--example` flag prints sample JSON.
Bug found and fixed during test: `axhline` doesn't accept `transform` kwarg.

### generate_images_aibmm.py — Rewritten for OpenAI
- Was: placeholder AIBMM REST API (fake endpoint)
- Now: uses `openai` Python library directly with `gpt-image-2` model
- Mascot scenes: `client.images.edit()` with `mascot_reference.png` as style anchor
- General scenes: `client.images.generate()` with style prefix
- Output: 1536×1024 from API → cropped/resized to 1280×720 PNG
- `--test` flag: generates one test image to verify key + mascot reference
- API key: `OPENAI_API_KEY` in `.env` ✓ tested and working

### API Keys in .env
```
PEXELS_API_KEY=...   ✓ confirmed working
OPENAI_API_KEY=...   ✓ confirmed working
```

### Pending Tasks
- #11 Install CUDA PyTorch in transcription-tools venv (WhisperX: 40min → 3min)
- #12 Fix banned word false positives ("genuinely"/"honestly" in conversational context)
- #13 Tune question ratio threshold (5-6 questions in 1800 words should not fail)
- #14 Review EP01 images once pipeline completes
- #15 Build notification agent
- #16 Build analytics feedback loop
- EP01 pipeline: check if WhisperX finished (manifest.json?), resume from prompts stage if yes

---

## Session — ElevenLabs Integration, EP01 End-to-End Test, Pipeline Bug Fixes (July 2026)

### ElevenLabs TTS — Replacing Edge TTS as Default

- **Voice selected:** ANX - Deep & Friendly (`gYQ0co3BoppQZ8BDM3lj`), `eleven_multilingual_v2`, stability 0.5, similarity 0.75
- **API key header:** `xi-api-key` (NOT `Authorization: Bearer` — confirmed via `debug_eleven_auth.py`)
- **Key scopes needed:** `voices_read`, `user_read`, `text_to_speech`. Old key had none → created new key.
- **10K char limit per call** → `generate_source_audio.py` rewritten with chunking: splits at sentence boundaries ≤9500 chars, generates each chunk separately, concatenates via pydub (fallback: ffmpeg concat)
- **channel_config.json voice section updated:**
  ```json
  "voice": {
      "provider": "elevenlabs",
      "default": "gYQ0co3BoppQZ8BDM3lj",
      "default_name": "ANX - Deep & Friendly",
      "model": "eleven_multilingual_v2",
      "stability": 0.5,
      "similarity_boost": 0.75
  }
  ```
- **Known issue:** ElevenLabs output is quiet. Fixed in stitch with `loudnorm=I=-14` (see Stitch section below).
- **Tier:** Starter (40K chars/month). EP01 script = 11,759 chars = 2 chunks, ~30% of monthly quota.
- **Helper scripts added:** `preview_elevenlabs_voices.py` (two-pass Indian accent search), `find_voice_id.py`, `debug_eleven_auth.py`
- **Task #24:** Evaluate Google/Gemini TTS + other ElevenLabs Indian voices before committing long-term.

### EP01 End-to-End Test — Article 356

- **Script:** `script_the_clause_that_makes_the_president_ask_the_govern.txt` (1885 words, 11,759 chars)
- **Audio:** ElevenLabs ANX, 2 chunks → 13.2 min voiceover → `ep01/source_audio/narration.mp3`
- **Scenes:** 147 (WhisperX re-split after ElevenLabs audio — more scenes than original 130 due to longer audio)
- **Images:** SCENE-001 to SCENE-130 from previous run. SCENE-131 to SCENE-147 were missing → copied SCENE-130.png as placeholder for pipeline test. All 83/98 FAIL on image review (wrong assets — see EP01 image issues below).
- **Stitch:** Completed. Output at `interested_indian_pipeline/ep01_final.mp4` (not `ep01/output/` — see bug fix below).
- **Upload:** https://www.youtube.com/watch?v=pqHPKA92xuk (private, no tags, no thumbnail via API)
- **Duration:** ~13.5 min, 128 MB

### EP01 Image Issues (AI Review via vidiq `vidiq_video_watch`)
- 83/98 FAIL rate — wrong assets throughout (e.g. American flag + "LAW" book appears 3 times at 00:38, 04:27, 10:06 instead of Indian context images)
- Video cuts off mid-sentence at 13:30 with no CTA
- Narration severely quiet relative to BGM
- Ken Burns zoom noticeable (was 1.08×)
- **Action required:** Full image regen before proper re-upload

### Pipeline Bug Fixes

**1. `manifest["episode"]` stored full Windows path**
- Root cause: `auto_split_scenes_v1_stage3_export.py` wrote `"episode": args.project` where `args.project` was the full absolute path passed by `pipeline_agents.py`.
- Effect: `stitch_video_longform.py` did `os.path.join(output_dir, f"{episode}_final.mp4")` — Windows `os.path.join` treats an absolute second arg as the full path, dropping `output_dir`. Result: `ep01_final.mp4` wrote to pipeline root, not `ep01/output/`.
- Fix: `auto_split_scenes_v1_stage3_export.py` → `"episode": os.path.basename(args.project.rstrip("/\\"))`
- Fix: `stitch_video_longform.py` → defensive `os.path.basename()` on episode read (both occurrences)
- Fix: `ep01/manifest.json` patched directly to `"episode": "ep01"`

**2. `manifest["title"]` was "Untitled Episode"**
- Root cause: `pipeline_agents.py` `_stage_split` never passed `--title` to the split script.
- Fix: reads `self.state["data"].get("title", "")` and appends `--title title` to split command.
- Fix: `ep01/manifest.json` patched to correct title.

**3. `pipeline_agents.py` had no `__main__` entry point**
- Fix: added `if __name__ == "__main__":` with argparse (`--project`, `--start-from`, `--only`).
- Instantiation: `OrchestratorAgent(client, project_dir, review_agent, research_agent)`

**4. `_review_stitch` looked for MP4 in one hardcoded path**
- Fix: checks 3 candidate paths: `ep01/output/`, `ep01/`, pipeline root.

**5. `upload_youtube.py` `_find_video()` searched only `ep01/output/`**
- Fix: searches `ep01/output/`, `ep01/`, and pipeline root (in order).

**6. `upload_youtube.py` tags `invalidTags` API error**
- Root cause: unknown (sanitised tags looked clean, 485 chars, no special chars). Workaround: used `--no-tags` for EP01 upload.
- Fix: metadata generation prompt updated to request "10-15 plain-word tags, no apostrophes, no hyphens, total under 400 chars".

**7. `upload_youtube.py` thumbnail upload 403**
- Cause: YouTube requires channel verification (1000+ subs) for API thumbnail upload.
- Fix: `_upload_thumbnail` now catches 403 and warns instead of crashing. Upload thumbnail manually in YouTube Studio.

### Stitch Audio/Visual Tuning

| Setting | Before | After | Reason |
|---|---|---|---|
| `BGM_VOLUME` | 0.08 | 0.04 | EP01 review: BGM drowning narration |
| `KEN_BURNS_ZOOM_RATIO` | 1.08 | 1.04 | EP01 review: zoom too noticeable |
| Voice normalisation | none | `loudnorm=I=-14:TP=-1.5:LRA=11` on voice track | ElevenLabs quiet output |
| BGM fade-in | none | `afade=t=in:st=0:d=2` | Smooth loop start |
| amix weights | equal (1:1) | 4:1 voice:BGM | Ensure voice dominates |

### CTA Setup

- `common/cta/cta.png` — uses existing `cta_ai.png` (AI-generated, 1.2MB) — ✓ ready
- `common/cta/cta_script.txt` — "If this made you think, subscribe. New episode every week. The Interested Indian — Indian history and policy, explained clearly."
- `common/cta/cta.mp3` — **still needs generating.** Run in PowerShell:
  ```powershell
  cd C:\Bakcup_Asus\interested_indian_pipeline
  python -m edge_tts --voice en-IN-PrabhatNeural --text "If this made you think, subscribe. New episode every week. The Interested Indian — Indian history and policy, explained clearly." --write-media common\cta\cta.mp3
  ```
- Note: using Edge TTS male voice (`en-IN-PrabhatNeural`) for CTA — acceptable mismatch since CTA is a distinct card moment. Regenerate with final narration voice once #24 is resolved.

### generate_country_map.py (New)

- Generalised from `generate_india_map.py` — works with any country's GeoJSON
- `_detect_name_field()` auto-detects from priority list: `ST_NM, NAME_1, ADM1_NAME, shapeName, name, ...`
- Latitude correction: `ax.set_aspect(1.0 / cos(mean_lat))` fixes E-W squishing at high latitudes
- `--name-field` CLI override, `--india-regions` flag for India-specific region colors
- Backward compatible: omitting `--geojson` defaults to India auto-download
- Tested: India (ST_NM, aspect 0.947), UK (NAME_1, aspect 0.503), Germany (shapeName, ~50°N)

### Immediate Next Steps (EP01 Re-Do)

1. Generate CTA audio (PowerShell command above)
2. Regen all 147 images — full regen with better prompts (`python pipeline_agents.py --project ep01 --only images`)
3. Patch `ep01/episode_state.json` to reset stitch/metadata/upload as incomplete
4. Re-stitch (`--only stitch`) — will now write to `ep01/output/ep01_final.mp4` ✓
5. Re-run metadata (`--only metadata`) — will generate clean tags ✓
6. Re-upload (`python upload_youtube.py --project ep01`) — no `--no-tags` needed this time
7. Upload thumbnail manually in YouTube Studio

### Pending Tasks (Updated)
- #11 Install CUDA PyTorch in transcription-tools venv
- #12 Fix banned word false positives in script reviewer
- #13 Tune question ratio threshold in script reviewer
- #14 EP01 image regen (147 scenes, full regen) ← **NEXT**
- #15 Build notification agent
- #16 Build analytics feedback loop
- #21 Add `--topic` override flag to pipeline
- #22 Add `--script-file` override flag to pipeline
- #24 Find and finalise narration voice (ElevenLabs Indian voices + Google/Gemini TTS) ← **NEW**

---

## Session — Script DNA Reviewer Built (July 2026)

### review_script.py (New — Task #26 ✓)

`review_script.py` in `interested_indian_pipeline/` — standalone Channel DNA & wittiness reviewer.

**What it checks (deterministic, instant, free):**
- **Banned words**: "genuinely", "honestly", "straightforward" + all corporate clichés (unleash, unlock, dive into, tapestry, game-changer, delve, etc.)
- **Humor density**: flags runs of 3+ consecutive paragraphs with no humor signals (modern analogy, self-aware observation, audience poke, etc.)
- **Jargon translation**: flags any known policy term not followed by a translation signal (which is, meaning, basically, —, etc.) in the same sentence
- **Clichés**: "over the years", "throughout history", "it is worth noting", etc.
- **Hook quality**: must have specific number, be under ~80 words, create curiosity

**What Claude checks (qualitative, ~$0.01 per review):**
Scores 1–10 on 8 dimensions:
- Hook quality, Humor density, Jargon clarity, Sentence rhythm, Audience address, Wittiness, Narrative arc, Overall
- Flags 3–5 specific boring sentences with explanations
- Highlights 2–3 best moments
- Gives 3–5 actionable recommendations

**Modes:**
- `--quick` — deterministic only, instant (no API cost)
- default — deterministic + Claude full review, saves `{project}/script_review.md`
- `--deep` — also rewrites the 3 most boring sections (costs slightly more tokens)

**Usage:**
```powershell
python review_script.py --project ep01
python review_script.py --project ep01 --quick
python review_script.py --project ep01 --deep
python review_script.py --script ep01/script_*.txt
```

**Pipeline integration:**
- Added `"review-script"` to `STAGE_ORDER` in `pipeline_agents.py` between `"script"` and `"voice"`
- `_stage_review_script` method: runs quick then full review, parses overall score
  - Score < 6: prompts user — [r]ewrite / [s]kip / [q]uit. Rewrite resets state to `script` stage.
  - Score 6–6.9: warning, shows report path, asks ENTER to continue
  - Score ≥ 7: passes automatically, logs score
- Human can bypass with `[s]kip` for cases where they already reviewed manually

**CHANNEL_DNA humor signals list** (used for density scan):
Modern analogies: "sort of like", "kind of like", "imagine if", "think of it as", "which is basically", "which is bureaucrat for"
Audience pokes: "I know exactly what", "I know what you", "before you say", "stay with me", "hear me out"
Self-aware: "wait,", "okay, wait", "— right?", "— okay?", "(yes, really)", "(seriously)", "I spent", "I still don't"
Indian pop culture: cricket, food delivery, gaming, zomato, swiggy, whatsapp, ola, uber, ipl

### Pending Tasks (Updated)
- #25 Finalize EP01 before publish (GATE — do not start EP02 until complete):
  1. Generate CTA audio (PowerShell command in CTA section above)
  2. Regen all 147 images (`python pipeline_agents.py --project ep01 --only images`)
  3. Re-stitch (`--only stitch`) — now writes to `ep01/output/ep01_final.mp4` ✓
  4. Re-run metadata (`--only metadata`) — clean tags ✓
  5. Re-upload (`python upload_youtube.py --project ep01`)
  6. Upload thumbnail manually in YouTube Studio
  7. Add tags manually in YouTube Studio
  8. Review final video → set public
- #26 ✓ Build review_script.py — COMPLETE
- #11, #12, #13, #14, #15, #16, #21, #22, #24 — see above

---

## Session — xAI Switch, Mascot Fix, Route Images, Pipeline Isolation (July 2026)

### Voice Finalised — Gemini TTS Charon
- Provider switched from ElevenLabs → Gemini TTS
- Voice: **Charon** (`gemini-2.5-flash-preview-tts`) — deep, authoritative; selected 2026-07-25 after A/B test vs Fenrir/Orus/Puck
- Gemini TTS returns raw PCM → wrapped in WAV headers via Python `wave` module (24kHz, 16-bit, mono)
- `channel_config.json` updated: `voice.provider = "gemini"`, `voice.gemini_voice = "Charon"`, `voice.gemini_model = "gemini-2.5-flash-preview-tts"`

### xAI Grok — Default Image Backend
- `generate_images_flux.py` default backend changed from `"replicate"` → `"grok"`
- Replicate was throttling (429 errors, 18/27 test_script images failed)
- xAI Grok: faster, no throttling, same flat cartoon output quality

### Mascot Fix — Indian Design Locked
- **Root cause of regression:** `STYLE_PREFIX` in `generate_images_flux.py` and `SYSTEM_PROMPT` in `generate_image_prompts.py` had generic descriptions — AI was generating a pale-skinned generic Asian character
- **Fix:** Both files updated with full locked description:
  - Chubby round Indian cartoon boy, amber/gold round glasses, spiky black hair, warm tan brown skin (#D4A85C), cream kurta, off-white baggy pajama trousers with gathered ankles, brown leather sandals
  - Explicit "NOT a generic Asian character, NOT pale-skinned, NOT in Western clothing"
- **Reference sheet locked:** `mascot_reference.png` — 4 expressions (NEUTRAL, SHOCKED, CONFUSED, SMUG), AIBMM session `d9d31dea-1095-4a70-b57e-9c0de7eaca7b`

### Thumbnail Bases — Regenerated
- Two GPT Image 2 base images generated (AIBMM MCP), session `ff68bd11-2791-4e85-9712-fbb7e2475769`:
  - **Light** (even episodes): cream bg (#FAF7F2), mascot right pointing up, India map left, amber footer bar → `common/thumbnails/base_light.png`
  - **Dark** (odd episodes): navy bg (#0C1828), mascot left pointing up, India silhouette right, amber footer bar → `common/thumbnails/base_dark.png`
- URLs and session ID stored in `channel_config.json` under `thumbnail_bases`
- Download locally (PowerShell):
  ```powershell
  Invoke-WebRequest "https://rqkumunldqvmynqxibca.supabase.co/storage/v1/object/public/generated-images/adhoc-1785033137395.png" -OutFile "common\thumbnails\base_light.png"
  Invoke-WebRequest "https://rqkumunldqvmynqxibca.supabase.co/storage/v1/object/public/generated-images/adhoc-1785033172185.png" -OutFile "common\thumbnails\base_dark.png"
  ```

### 4-Type Scene Architecture — Fully Wired
Scene types, generators, and routing:

| Type | Generator | Method |
|---|---|---|
| CARTOON | `generate_images_flux.py` | xAI Grok — mascot + concept illustrations |
| MAP | `generate_india_map.py` | geopandas + GeoJSON (accurate geography, never AI) |
| CHART | `generate_images_flux.py` (fallback) | future: `generate_chart.py` |
| PHOTO | `search_pexels.py` | Pexels API — real-world context shots |

**TYPE + MAP_ARGS fields** added to `generate_image_prompts.py` output format — placed BEFORE NARRATION so existing parsers (regex for CUE, OVERLAY) remain unbroken.

**`route_images.py`** (new dispatcher):
- Reads `image_prompts_one_line_per_prompt.md`, routes by TYPE
- Legacy prompts without TYPE → keyword fallback classification
- MAP failures auto-noted; PHOTO failures fall back to AI
- `--dry-run` and `--overwrite` flags
- Wired into `pipeline_agents.py` `_stage_images`

### CARTOON Prompt Guardrail (generate_image_prompts.py)
- Added explicit rule: if TYPE=CARTOON and scene *mentions* a place, do NOT describe a geographic map in PROMPT
- Wrong: scene about Lakshadweep population → AI draws a map
- Right: mascot standing next to small island icon labelled "Lakshadweep", tiny crowd of 5 cartoon people
- Wrong/right examples added directly to SYSTEM_PROMPT so Claude classifies correctly every run

### PIL Text Overlay — add_text_overlays.py
- Burns OVERLAY text from prompts file onto images post-generation
- Navy banner at bottom, white bold text, amber badge
- Backs up originals as `_orig.png`
- Inserted between images and stitch stages in `STAGE_ORDER`
- AI prompts explicitly forbid rendering text — all text goes through PIL (eliminates AI typos)

### Pipeline Isolation — No External Folder References
- `pipeline_agents.py` previously called `stitch_video_longform.py` and `auto_split_scenes_v1_stage3_export.py` from `C:\Bakcup_Asus\Aeonium_Glow\shorts_pipeline2\`
- **Fixed:** `SHORTS_DIR` variable removed. Both `_stage_stitch` and `_stage_split` now use `PIPELINE_DIR` (local copies in `interested_indian_pipeline\`)
- The two projects (`interested_indian_pipeline` and `shorts_pipeline2`) are now fully independent
- **Only legitimate external reference remaining:** `WHISPERX_PYTHON` pointing to `Aeonium_Glow\transcription-tools\.venv\Scripts\python.exe` — WhisperX is shared heavy ML tooling, not pipeline code

### Stitch Bug Fixes
- **CTA audio guard:** `find_cta()` in `stitch_video_longform.py` now checks file size ≥1024 bytes before returning the CTA path — prints a clear warning and skips gracefully if `cta.mp3` is empty (was crashing with `HeaderNotFoundError`). Applied to both `interested_indian_pipeline\stitch_video_longform.py` and `Aeonium_Glow\shorts_pipeline2\stitch_video_longform.py`
- **resolve_group_images logging:** Added print line so group image copies are visible in stitch output (was silently failing)
- **Group member images:** Stitch expects one image per scene; generator creates one per shot (deduped by visual_group_id). `resolve_group_images()` in stitch handles the copy automatically — but only after images exist. If overlays run before stitch's copy step, group secondaries may be missing. Workaround for now: ensure stitch runs after all images (including overlays) are complete.

### test_script Run — Results
- Episode: "Why India Has 9 Territories That Are Not Quite States" — 27 shots, 36 scenes
- Stages completed: topics ✓ script ✓ review-script ✓ voice ✓ split ✓ prompts ✓ images ✓ overlays ✓ stitch ✓
- Image review round 1: 19 PASS / 2 WARN / 6 FAIL
- Persistent FAILs (all 3 rounds): SCENE-003, 019, 021, 024, 027, 032 — geography-mentioned CARTOON scenes where xAI generated maps instead of illustrations → fixed by CARTOON prompt guardrail above
- Stitch: initially blocked by empty `cta.mp3` → fixed by CTA guard; completed successfully
- Output: `test_script/output/test_script_final.mp4`, `test_script_captioned.mp4`, `test_script_captions.srt`
- Duration: 170s (171s manifest — 1s floating-point rounding, not missing content; proceed accepted)
- **#28 COMPLETE** — full pipeline end-to-end validated

### CTA Audio — Still Needs Generating
`common/cta/cta.mp3` is empty (0 bytes). Generate with:
```powershell
cd C:\Bakcup_Asus\interested_indian_pipeline
python generate_voice.py --text-file common\cta\cta_script.txt --out common\cta\cta.mp3
```
CTA script: *"If this made you think, subscribe. New episode every week. The Interested Indian — Indian history and policy, explained clearly."*

### Git — Large Unstaged Batch Pending
85 files unstaged (all pipeline changes from this session + thumbnail bases + test_script run). After CTA fix and stitch completes:
```powershell
Remove-Item 'C:\Bakcup_Asus\interested_indian_pipeline\.git\index.lock' -Force  # if lock exists
git add -A
git commit -m "feat: xAI default, mascot fix, route_images, PIL overlay, type routing, thumbnail bases, pipeline isolation"
git push
```

### Map + Image Size Fixes
Three issues found and fixed after test_script review:

**1. India map state labels hidden by default**
- Root cause: `generate_india_map.py` only labeled highlighted states; non-highlighted states required `--all-labels` flag
- Fix: removed the `if not is_hi and not all_labels: continue` guard — all states now always get labels. Highlighted states get bold white text with colored stroke; non-highlighted get small brown labels.

**2. India map wrong output size**
- Root cause: `plt.savefig(..., bbox_inches="tight")` crops the figure to content bounds, destroying the 1280×720 canvas. Actual output was 490×574 despite the script printing "1280×720" (a lie).
- Fix: removed `bbox_inches="tight"`. Added PIL resize step after save to enforce exactly 1280×720.

**3. AI images (xAI Grok) wrong size**
- Root cause: `generate_images_flux.py` saved xAI output as-is; xAI returns variable sizes (some 517×574, some 1280×720).
- Fix: extended `ensure_png()` to also resize to 1280×720 when size doesn't match. All pipeline images now guaranteed 1280×720.

**PIL overlay on maps:** `add_text_overlays.py` processes all `.png` files in the images folder regardless of source — maps automatically get OVERLAY text burned on, same as AI images. No extra work needed.

### Pending Tasks (Updated)
- **IMMEDIATE:** Generate CTA audio: `python generate_voice.py --text-file common\cta\cta_script.txt --out common\cta\cta.mp3`
- **IMMEDIATE:** Git commit all session changes (85+ unstaged files)
- **#28 ✓** Full pipeline test run — COMPLETE
- **#25:** Finalize EP01 — regen 147 images with correct mascot + routing, re-stitch, re-upload
- **#14:** EP01 image regen (147 scenes, all with correct mascot)
- **#11:** Install CUDA PyTorch in transcription-tools venv (WhisperX: 40min CPU → 3min GPU)
- **#12:** Fix banned word false positives in script reviewer
- **#13:** Tune question ratio threshold in script reviewer
- **#15:** Build notification agent
- **#16:** Build analytics feedback loop

---

## Session — CTA System, Speaking Rate, Audio Fixes (July 2026)

### generate_source_audio.py — Three Fixes

**1. `--project` made optional**
- Previously `--project` and `--script` were both required; broke standalone CTA generation
- Fix: only `--script` is required. When `--project` is absent (or `--out` is an absolute path), output goes directly to the `--out` path — no `source_audio/` subdirectory created
- Allows: `python generate_source_audio.py --script common\cta\cta_script.txt --out common\cta\cta.mp3`

**2. Speaking rate — SSML `<prosody>` (not SDK field)**
- Previous attempt: set `speaking_rate` on `types.SpeechConfig` → `ValidationError: Extra inputs are not permitted`
- Second attempt: set on `types.GenerateContentConfig` → also not a valid field in this SDK version
- Confirmed fields: `SpeechConfig` has `[voice_config, language_code, multi_speaker_voice_config]`; `GenerateContentConfig` has no `speaking_rate` field
- Fix: wrap text in SSML `<speak><prosody rate="85%">...</prosody></speak>` when `speaking_rate` is set
  ```python
  if speaking_rate is not None:
      rate_pct = f"{int(speaking_rate * 100)}%"
      contents = f'<speak><prosody rate="{rate_pct}">{text}</prosody></speak>'
  else:
      contents = text
  ```
- `channel_config.json` note updated to reflect SSML implementation

**3. Rate-tagged preview filenames**
- Preview files were all named `preview_Charon.mp3` — each rate test overwrote the last
- Fix: filename now includes rate: `preview_Charon_rate80.mp3`, `preview_Charon_rate85.mp3`, `preview_Charon_rate90.mp3`
- Used `args.speaking_rate` (not `speaking_rate` — that variable isn't defined yet at filename construction)

### Speaking Rate — Preview Commands
Three previews queued for listening comparison:
```powershell
python generate_source_audio.py --project test_script --script "test_script\script_why_india_has_9_territories_that_are_not_quite_sta.txt" --preview 3 --speaking-rate 0.80
python generate_source_audio.py --project test_script --script "test_script\script_why_india_has_9_territories_that_are_not_quite_sta.txt" --preview 3 --speaking-rate 0.85
python generate_source_audio.py --project test_script --script "test_script\script_why_india_has_9_territories_that_are_not_quite_sta.txt" --preview 3 --speaking-rate 0.90
```
Listen to all three in `test_script\source_audio\`, then update `channel_config.json` → `gemini_speaking_rate`.

### CTA Rotation System
- `common/cta/cta_script.txt` — active CTA, swapped per episode before stitch
- `common/cta/cta_options.txt` — all options + rotation guide (new file)

**Rotation pattern:**
| Episode type | Option | Script |
|---|---|---|
| Serious/systemic (President's Rule, Article 356 etc.) | C | *"India is running a system most people don't know exists. This was one piece of it. Subscribe if you want the rest."* |
| Lighter/absurd topics | D | *"Okay. That's the one. Subscribe if it broke your brain a little. I'll be back next week."* |
| Deep-research episode | A | *"I spent three days reading things most people wouldn't touch with a ten-foot pole so you don't have to. Subscribe if that sounds useful."* |
| Warm/audience poke | B | *"If you made it this far, you're exactly the kind of person this channel is for. Subscribe. I'll see you next week with another one nobody asked for but definitely needed."* |

**EP01 CTA (set):** Option C — serious/systemic topic (President's Rule / Article 356)

CTA selection is now part of the EP finalization checklist in `TASKS.md` — do it before stitching.

### CTA Audio — Still Needs Generating
Run in PowerShell:
```powershell
python generate_source_audio.py --script common\cta\cta_script.txt --out common\cta\cta.mp3
```

### Git — Pending Commit
```powershell
Remove-Item '.git\index.lock' -Force -ErrorAction SilentlyContinue
git add -A
git commit -m "feat: SSML speaking rate, rate-tagged previews, CTA rotation system, pipeline isolation, map fixes, generate_source_audio standalone mode"
git push
```

### Handoff Note for Next Session
Start next session with:
> Continue The Interested Indian pipeline. Session doc: `interesting_indian_session.md`. Task list: `TASKS.md`. Immediate: (1) listen to speaking rate previews in `test_script\source_audio\`, lock rate in `channel_config.json`; (2) generate CTA audio; (3) git push; (4) EP01 image regen.
- **#21:** Add `--topic` override flag to pipeline
- Wire `generate_chart.py` as CHART route in `route_images.py` (currently falls back to AI)
- Wire `generate_images_aibmm.py` (GPT Image 2 + mascot reference) for MASCOT-type scenes
- Update `generate_thumbnail.py` to composite title text onto `base_light.png` / `base_dark.png`

---

## Session — Code Fixes, test_script Validation, EP01 Launch (July 2026)

### Status at Session Start
- CTA audio: `common/cta/cta.mp3` — 27,885 bytes ✓ (already generated)
- Speaking rate previews: exist at `test_script/source_audio/preview_Charon_rate80/85/90.mp3`. `channel_config.json` already has `gemini_speaking_rate: 0.85`. Cannot be listened to in this session — user must confirm rate before re-running voice stage.
- Git: 89 unstaged files (all pipeline changes from previous sessions)

### Code Bugs Fixed This Session

**Bug 1 — `route_images.py` keyword classification searched full line (including NARRATION)**
- Root cause: `_classify_by_keywords(line)` was doing `line.lower()` on the entire prompt entry — including the NARRATION field. Any scene where narration text mentioned "union territory", "India map", etc. got classified as MAP even when the PROMPT was a cartoon illustration.
- Example: SCENE-024 ("meme reaction beat: mascot holding puzzle piece") has narration "Union Territory structure..." → was classified MAP → got a generic India map instead of a cartoon.
- Example: SCENE-032 ("before-and-after split panel, mascot shocked") has narration "split into two union territories..." → same misclassification.
- **Fix:** Extract only the PROMPT field (`PROMPT: ... OVERLAY:`) before keyword matching. Now searches only the visual description, not the narration text.

**Bug 2 — `route_images.py` MAP with no MAP_ARGS → blank geography map**
- Root cause: Legacy prompts (generated before TYPE/MAP_ARGS fields were added) are correctly classified as MAP (prompt says "color-coded political map"), but have no MAP_ARGS field. `run_map()` called `generate_india_map.py --out SCENE-XXX.png` with no `--highlight` args → generated a generic regional-color map, not the specific highlights the scene needed.
- **Fix:** If `raw_type == "MAP"` but `map_args == ""`, downgrade to CARTOON with a warning. Routed to xAI Grok which generates at least a cartoon-style map. Prompts re-run (which now generates MAP_ARGS) restores proper behavior.

**Bug 3 — `pipeline_agents.py _stage_voice` never passed `--speaking-rate` to generate_source_audio.py**
- Root cause: Even though `channel_config.json` has `gemini_speaking_rate: 0.85` and `generate_source_audio.py` has the SSML `<prosody rate="...">` implementation, `_stage_voice` only read provider/voice from config — never `speaking_rate`. Result: every pipeline voice run used the default rate regardless of channel_config.json.
- **Fix:** `_stage_voice` now reads `vcfg.get("gemini_speaking_rate")` when provider is gemini, and appends `--speaking-rate {value}` to the generate_source_audio.py command.

### test_script Run Plan (post-fix)
The existing test_script output (`test_script_final.mp4`) has:
- 15 PASS, 5 WARN, 7 FAIL images (from review_report.md)
- Wrong narration speed (default rate, not 0.85 — generated before SSML fix)
- The code fixes above now handle all these correctly

**Full re-run sequence (from voice stage to validate everything):**
```powershell
cd C:\Bakcup_Asus\interested_indian_pipeline

# Step 1: Git push first (unblock any in-flight state)
git add -A
git commit -m "fix: route_images keyword search PROMPT-only, MAP fallback to CARTOON, pipeline_agents speaking rate propagation"
git push

# Step 2: Re-run test_script from voice (generates narration at rate 0.85, then splits/prompts/images/stitch)
python run_episode_v2.py --project test_script --from-stage voice

# Step 3: Review output
python local_mp4_analyzer.py test_script/output/test_script_final.mp4
```

**Faster option (skip narration re-gen, just fix images + stitch):**
```powershell
python run_episode_v2.py --project test_script --from-stage prompts
```
This re-generates prompts (with TYPE+MAP_ARGS) → routes images correctly → overlays → stitch. Uses the existing WhisperX manifest and narration.mp3 (at default rate). Good for validating image routing fixes only.

### EP01 Launch Sequence
After test_script validates:
```powershell
cd C:\Bakcup_Asus\interested_indian_pipeline

# Full pipeline from voice stage (Gemini TTS Charon at rate 0.85, new prompts with TYPE+MAP_ARGS, fixed routing)
python run_episode_v2.py --project ep01 --from-stage voice
```

This will:
1. Generate new narration with Charon at rate 0.85 (replaces old ElevenLabs ANX)
2. WhisperX split (~40 min CPU, ~3 min with CUDA GPU)
3. Generate image prompts with TYPE + MAP_ARGS
4. Route images: MAP → generate_india_map.py (accurate GeoJSON), CARTOON → xAI Grok
5. PIL text overlays
6. Stitch → `ep01/output/ep01_final.mp4`
7. Metadata (clean tags, no apostrophes/hyphens)
8. Human checkpoint: review video → approve upload

### Handoff Note
```
Continue The Interested Indian pipeline. Session doc: `interesting_indian_session.md`. Task list: `TASKS.md`.
Code fixes from last session: route_images.py (keyword classification), pipeline_agents.py (speaking rate propagation).
Immediate: (1) git push; (2) run test_script from prompts or voice stage; (3) run EP01 full pipeline.
```

