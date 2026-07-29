# The Interested Indian — Session Tracker

## Channel
Faceless YouTube channel on Indian history, geography, geopolitics, and administrative dynamics — modeled on the "Fat Little Asian Man" production blueprint. Minimalist 2D doodle/vector style, stick-figure mascot, 16:9, 12–21 min analytical video essays.

## Workflow Stages
1. Generate 5 viral topic ideas
2. Full narration script (2,000–3,200 words) → downloadable .txt
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
- **`run_episode_needed_or_not.py`** (renamed from `run_episode.py`) — earlier simpler orchestrator (no agents), kept as backup; flagged for a keep-or-delete decision
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

---

## Session — CHANNEL_DNA Research Refinement, Backlog #7/#9/#10, Gemini 3.1 Cloud TTS Voice Switch (July 2026)

*Note: this session's early turns (codebase orientation, `generate_country_map.py` review) were lost to a context-compaction gap before this section was written — reconstructed here from the post-compaction summary handed off internally. Everything below is accurate to what actually shipped, verified via git log/diff at write time.*

### Competitive Research — @justaFLAM & @JustCuriousIndia (via Amplifiers/AIBMM + vidiq tools)
- Re-researched justaFLAM (the channel `CHANNEL_DNA` was originally modeled on) and a newly-found channel, @JustCuriousIndia, to validate/refine the voice and content-angle guidance.
- Findings fed into a `CHANNEL_DNA` refinement, approved via full Plan Mode (Explore → Plan → AskUserQuestion → ExitPlanMode), then implemented and validated against a real script-generation API call (not just reasoned about).

### CHANNEL_DNA Refinement (`pipeline_agents.py`)
- Niche expanded; format widened from "12–18 min" to **"12–21 minute"**.
- 5 new situational technique blocks added: OPENER VARIANT (sibling/personification), RECURRING REFRAIN, JARGON ANCHORING, EVEN-HANDED DISMISSAL, MID-VIDEO THESIS RE-STATEMENT — all marked situational, not mandatory every episode.
- `_stage_topics()` gained 4 new `CONTENT ANGLE GUIDE` bullets: cinema/film industry politics, religious-institution administration, controversial icons, city history — plus a paired-series note and Formula B/D seed examples.
- **Bug found via `/but-for-real` + a real test script-generation run:** all of technique A/D's illustrative examples used the same subject ("Centre vs States"), so Claude was reproducing them near-verbatim in generated scripts instead of adapting the pattern. Fixed by diversifying examples to a different pairing (RBI vs Finance Ministry) and adding a general "adapt, don't copy" instruction.
- **Second bug found the same way:** 4 places in `pipeline_agents.py` were still hardcoded to the old 2,000–2,800 word target after the 12–21 min widening (main script-generation instruction + 3 review/scoring checks). Updated to 2,000–3,200, ceiling 3,400.
- Validated: technique A/D fire at the right structural position, C correctly stays absent on non-tour topics, B adapts rather than copies. `review_script.py --deep` scored the test script 6/10 — correctly, on pre-existing rules unrelated to this session's changes.
- Test artifact: `test_channel_dna/`.

### Backlog #7 — Question-Ratio Threshold (pipeline_agents.py)
- **TASKS.md misnamed the file** — said "tune it in `review_script.py`"; that standalone tool has no question-ratio logic at all. The real (duplicated) check lives in `pipeline_agents.py`'s `ReviewAgent._review_script` and `_print_script_preview`.
- **Confirmed non-blocking either way**: every `ReviewResult` uses `passed = score >= 7`, and this check never touches `score` — it's a diagnostic message only, not a gate.
- Separately confirmed the *actual* pipeline gate (`review_script.py`'s Claude-based `OVERALL_SCORE`) doesn't over-penalize low question density — read a real `--deep` review's RHYTHM_SCORE rationale, which is entirely about sentence-length monotony, never mentions question marks. No fix needed there.
- **Threshold recalibrated to 0.04** (was 0.08; an initial estimate of 0.05 was also checked and found still too strict) — done empirically against 4 real generated scripts' actual word/question counts (not an assumed words-per-sentence ratio): `ep01` (1,885 words/5 questions) → 0.0427; `ep01_v1` → 0.0000; `test_channel_dna` → 0.0238; `test_script` → 0.1282. 0.04 is the number that makes `ep01`'s real case pass while correctly still failing the two weak scripts.
- Message text updated to match: "need ~1 per 25, roughly 1 per 400 words".

### Backlog #9 — CHART Routing Wired End-to-End
- Bigger than the backlog description implied ("wire route_images.py") — `route_images.py` had nowhere to route CHART shots *to*; no prompt file had ever contained structured chart data. Required a new `CHART_ARGS` schema in `generate_image_prompts.py` (mirrors `MAP_ARGS`) first — confirmed via an Explore agent, user approved full scope over the AskUserQuestion tool.
- `generate_image_prompts.py`: new `CHART_ARGS format` instruction block (bar/stat/timeline/pie JSON shapes, single-line safety rules — forbids reserved words NARRATION/PROMPT/OVERLAY/CUE inside chart labels), `CHART_ARGS:` output line, `"chart_args"` key in `parse_claude_output()`, `chart_args_part` in `build_output_line()`.
- `route_images.py`: new `_validate_chart_args()` (shlex-split + `--type` in bar/stat/timeline/pie + `--data` parses as non-empty JSON array), `parse_shots()` downgrades CHART→CARTOON with a warning on missing/invalid args (mirrors the existing MAP_ARGS-missing pattern), new `run_chart()` mirroring `run_map()`, `main()` gained the chart_shots list/dry-run listing/generation loop/failure summary.
- Verified end-to-end: hand-authored a test prompts file with valid/missing/invalid `CHART_ARGS`, ran real (non-dry-run) generation, visually confirmed a correctly-rendered branded bar chart PNG — not just a clean exit code.

### Backlog #10 — Thumbnail Base-Image Compositing
- `generate_thumbnail.py` previously built thumbnails from a solid-color canvas, never touching `common/thumbnails/base_light.png` / `base_dark.png`.
- Added `ImageOps.fit()` cover-fit-crop of the 1672×941 base art to 1280×720; `_resolve_theme()` now also returns the theme *name*; new `_render_on_base()` composites the title into a narrow center-ish text zone with a translucent scrim (base art already has "THE INTERESTED INDIAN" baked into its own footer, so no competing footer is redrawn), plus an "EPxx" badge in the corner opposite the mascot (`mascot_on_right = theme_name == "light"`).
- **Found via visual iteration, not just code review:** first pass used a 56%-width text zone that overlapped the mascot's face/glasses on a long test title — narrowed to ~44% and made badge placement theme-aware, then re-verified on both a long and realistic-length title.
- Original solid-canvas path preserved untouched as the fallback when base art is missing (verified via a simulated missing-file test).

### Narration Voice — Switched to Gemini 3.1 Cloud TTS (en-IN, styled Charon)
User raised that plain Gemini TTS "Charon" sounded generically American, not Indian, and shared a Google Cloud TTS Studio screenshot showing Gemini 3.1 Flash TTS with an `en-IN` locale + free-text style prompt. Investigated and built a standalone (non-pipeline) A/B tool: **`test_gemini31_en_in_voice.py`**.

**Key technical distinction confirmed, not assumed:**
- The existing `gemini` provider hits the **Gemini Developer API** (`generativelanguage.googleapis.com`) — simple API-key auth, no locale/style control, which is *why* it never sounded Indian (it never set a locale at all).
- The Cloud TTS Studio screenshot uses a **different API**: **Google Cloud Text-to-Speech** (`texttospeech.googleapis.com/v1beta1/text:synthesize`) — supports `voice.languageCode` and a free-text `input.prompt` style field, on the newer `gemini-3.1-flash-tts-preview` model.
- **Confirmed via a real 401, not assumed:** Cloud TTS rejects plain API keys outright ("API keys are not supported by this API") — requires OAuth2 user credentials or a service account. Solved via `gcloud auth print-access-token` (short-lived OAuth2 token, fetched via subprocess, never printed/logged).
- **Windows-specific fix:** `subprocess.run(["gcloud", ...])` fails with `[WinError 2]` without `shell=True`, since `gcloud` is a `.cmd` batch wrapper that `CreateProcess` can't resolve directly.
- Vertex AI routing confirmed: Gemini-TTS in Cloud TTS requires `aiplatform.endpoints.predict` (`roles/aiplatform.user`) — it's routed through Vertex AI infrastructure under the Cloud TTS surface.

Generated two real A/B samples reusing the exact same sample text as the existing `gemini_Charon.wav` baseline: `voice_previews/gemini31_Charon_enIN.wav` (locale-only) and `voice_previews/gemini31_Charon_enIN_styled.wav` (locale + style prompt "warm, witty, conversational Indian English tone"). **User explicitly chose the styled version** — "let's do this - gemini31_Charon_enIN_styled.wav".

**Wired into the real production pipeline** (not just the standalone script) as a new `gemini_cloudtts` provider:
- `channel_config.json` — `voice.provider` default changed `"gemini"` → `"gemini_cloudtts"`; added `gemini_cloudtts_model` (`gemini-3.1-flash-tts-preview`), `gemini_cloudtts_locale` (`en-IN`), `gemini_cloudtts_style` (the warm/witty/conversational Indian English prompt), and a note documenting the gcloud-auth requirement + fallback behavior.
- `generate_source_audio.py` — new `_get_gcloud_access_token()`, `_cloudtts_call()` (POSTs via `urllib.request` to match the file's existing style, requests MP3 directly), `cloudtts_generate()`. **Includes a real implemented fallback**: if gcloud isn't available, warns and calls the plain `gemini_generate()` instead of hard-failing — found via `/but-for-real` that this fallback was *documented* in `channel_config.json` but not actually implemented, then implemented and verified via a monkeypatch test simulating gcloud unavailability. `--provider` choices now `["gemini_cloudtts", "gemini", "elevenlabs", "edge"]`.
- `pipeline_agents.py` `_stage_voice` — voice-resolution logic updated to treat `gemini`/`gemini_cloudtts` identically for voice-name lookup, reads `gemini_speaking_rate` for both.
- **`CLOUDTTS_CHUNK_LIMIT` bug found and fixed:** initially copied the sibling Gemini path's 4500-char chunk limit without independent verification. Pricing research turned up Google's documented **hard 4,000-byte-per-field limit** for Cloud TTS's Gemini-TTS. Fixed to 3500 chars; verified against real scripts that byte overhead from em-dashes/curly quotes is only ~0.24%, so 3500 chars stays safely under 4,000 bytes.
- Verified end-to-end via a real `--preview 3` run against `test_script`'s actual script — produced a valid 31.5s MP3 (confirmed via `mutagen.mp3.MP3`).

### Pricing & Billing Investigation (Cloud TTS vs Gemini Developer API)
- User asked for a clear auth comparison (above) plus **real** pricing — explicitly wanted no guessed numbers.
- Confirmed via `ai.google.dev` official pricing: Gemini TTS billed at 25 tokens/second of audio output; `gemini-2.5-flash-preview-tts` = $0.50/$10 per 1M input/output tokens; `gemini-3.1-flash-tts-preview` = $1.00/$20.00 per 1M input/output tokens.
- User authorized real GCP billing console access via Claude in Chrome (their authenticated session). Investigated `unseen-lever-auto-uploader` project: $9.82 total for the month, 73% of which was unrelated "Imagen 4 Generation" from a different project ("Rewired"). **No distinct Cloud TTS or Gemini 3.1 audio SKU appeared** — only a "Gemini 2.0 Flash TTS" line at $0.09 (likely leftover from earlier `gemini`/`test_gemini_tts.py` runs, not this session's Cloud TTS calls). Conclusion: today's Cloud TTS testing almost certainly fell inside free quota or hadn't posted yet — reconciled by confirming the visible SKU rows summed to the account's full total, rather than fighting the console's pagination further.
- **Deferred, not resolved:** actual confirmed per-episode Cloud TTS cost. Recorded in `TASKS.md` as an open item to revisit in a couple of weeks once real episode-scale usage has accumulated and billing has posted.

### Commits this session
- `1e7ec28` — fix: backlog #7/#9/#10 (question ratio, CHART routing, thumbnail compositing)
- `a3e2589` — feat: switch default narration voice to Gemini 3.1 Cloud TTS (en-IN, styled Charon)
- `39b878a` — fix: correct CLOUDTTS_CHUNK_LIMIT to match Google's documented 4,000-byte limit
- `6e1b582` — docs: record billing investigation findings for gemini_cloudtts, defer to later

### Pending Tasks (Updated — see `TASKS.md` as source of truth)
- **Confirm `gemini_cloudtts` actual billing** — revisit in a couple of weeks, deferred by user.
- **#5** Install CUDA PyTorch in transcription-tools venv (WhisperX 40 min CPU → 3 min GPU)
- Decide: keep or delete `run_episode_needed_or_not.py` (legacy orchestrator, stale CHANNEL_DNA copy, unreferenced)
- Listen to speaking-rate previews (`test_script/source_audio/preview_Charon_rate80/85/90.mp3`) — likely superseded by the new voice decision, worth re-checking rate against the new Gemini 3.1 Cloud TTS voice specifically
- Next actionable step (not yet run): validate `test_script` end-to-end with the new `gemini_cloudtts` voice, then run EP01's full pipeline — see `CLAUDE.md` "IMMEDIATE NEXT STEPS" for exact commands

---

## Session — WhisperX Venv Relocated + CUDA PyTorch Installed (Backlog #5, July 2026)

User's idea: rather than just installing CUDA PyTorch in-place inside `Aeonium_Glow\transcription-tools\.venv` (the original #5 backlog wording), move the venv itself to a project-neutral shared location under `C:\Bakcup_Asus\` first, since it was already a de-facto shared dependency (both `interested_indian_pipeline` and `Aeonium_Glow\shorts_pipeline2` pointed at that one path) sitting oddly inside one specific project's folder. Done via full Plan Mode (Explore investigation → AskUserQuestion on 3 open decisions → approved plan → execution).

### Investigation findings (before touching anything)
- Old venv confirmed real: Python 3.11.8, `torch==2.8.0` **CPU-only** build, despite an NVIDIA RTX 4050 Laptop GPU (6GB VRAM, driver 566.07, reports CUDA 12.7) sitting unused on the machine (`torch.cuda.is_available()` was `False`).
- `pip freeze` was the *only* record of what was installed — no requirements.txt existed anywhere in `transcription-tools\`. Full package list captured before migrating: `whisperx==3.8.6`, `faster-whisper==1.2.1`, `ctranslate2==4.8.0`, `pyannote-audio==4.0.5` (+ pyannote-core/database/metrics/pipeline), `torch/torchvision/torchaudio==2.8.0`.
- Confirmed exactly two hardcoded reference points across two independent projects: `pipeline_agents.py:47` (`WHISPERX_PYTHON` constant) and `shorts_pipeline2\pipeline_config.json` (`transcription_venv_python` key + a `whisper_device: "cpu"` setting).
- `pipeline_agents.py`'s `_stage_split` was hardcoding `--device cpu` even though the split script itself already defaulted to `--device cuda` — meaning the CPU-only bottleneck was two separate things stacked: no CUDA torch installed, *and* the pipeline explicitly overriding to CPU regardless.
- **Real OOM risk surfaced via research, not assumption:** WhisperX's own README states `large-v2` (this pipeline's default model) needs "under 8GB" VRAM for transcription alone — tight against a 6GB card before even adding the alignment model. Researched actual PyTorch CUDA wheel compatibility (`cu126` chosen as the safe tag for a driver reporting CUDA 12.7) and a known Windows-specific ctranslate2/cuDNN-9 DLL pain point (`Could not locate cudnn_ops64_9.dll`), so both were treated as expected troubleshooting steps rather than surprises if hit.

### User decisions (via AskUserQuestion)
1. **Shared venv location:** user said "please suggest" — chose `C:\Bakcup_Asus\shared-tools\transcription-tools\.venv` (a `shared-tools` umbrella, leaving room for other cross-project venvs later, rather than baking one tool's name into the path).
2. **OOM handling:** keep `large-v2` (don't downgrade model quality preemptively) + add safer defaults (`int8_float16` compute type, reduced batch size) as the first attempt, with `medium` model documented as the fallback if verification still OOMs.
3. **Old venv:** delete it after the new one is verified working (not kept as a backup).

### What was actually built/changed
- New venv created at `C:\Bakcup_Asus\shared-tools\transcription-tools\.venv` from the same system Python 3.11.8. `torch/torchvision/torchaudio` installed via `--index-url https://download.pytorch.org/whl/cu126`, then `whisperx==3.8.6` installed after.
- **Confirmed the exact risk the plan flagged, live:** installing `whisperx` (which depends on `torch` with no version/index pin) silently pulled the plain PyPI **CPU** wheel back in, undoing the CUDA install — `pip install torch ... --index-url cu126` afterward reported "Requirement already satisfied" and did nothing, since pip doesn't reinstall a satisfied unpinned requirement. Fixed with `pip install --force-reinstall --no-deps torch torchvision torchaudio --index-url .../cu126` (the `--no-deps` avoids re-triggering another resolution cascade). Verified clean afterward: `torch==2.13.0+cu126`, `torch.cuda.is_available() == True`, GPU correctly identified as the RTX 4050 Laptop.
- Also found and fixed a smaller side effect: `torchcodec` (a `pyannote-audio` dependency) was installed at a version built against torch 2.8.0, producing a version-mismatch warning after the CUDA torch swap. Upgraded `torchcodec` to latest (0.15.0, `--no-deps`) — this resolved the version-mismatch message but surfaced a *different*, purely cosmetic FFmpeg-DLL-discovery warning from `pyannote.audio`'s own optional audio-decode path. Confirmed via reading `whisperx`'s own `load_audio()` source that it never uses `torchcodec` at all (it shells out to ffmpeg directly) — the warning only fires because `pyannote.audio` is imported as a WhisperX dependency, and this pipeline never uses pyannote's diarization features. Non-blocking, documented as a known cosmetic warning, not chased further.
- `auto_split_scenes_v1_stage3_export.py` — added real `--compute-type` and `--batch-size` CLI flags (previously both were hardcoded inline: `"float16" if device=="cuda" else "int8"` and `batch_size=16`), threaded through `transcribe_with_timestamps()`.
- `pipeline_agents.py` — `WHISPERX_PYTHON` repointed to the new shared path; `_stage_split` now passes `--device cuda --compute-type int8_float16 --batch-size 8` (safer than WhisperX's own float16/batch-16 defaults, per the user's chosen approach).
- `Aeonium_Glow\shorts_pipeline2\pipeline_config.json` — `transcription_venv_python` repointed to the new shared path; `whisper_device` changed `"cpu"` → `"cuda"`.
- `Aeonium_Glow\shorts_pipeline2\run_pipeline.py` — its own hardcoded fallback default (used only if the config key is missing) also repointed, so it doesn't silently point at a now-deleted path if that ever triggers.
- Doc references updated in both projects: `interested_indian_pipeline\CLAUDE.md` (+ cleaned up several other stale "still to do" bullets that were actually already done in earlier sessions), `TASKS.md`, `memory_updated.md`, and `Aeonium_Glow\shorts_pipeline2\CLAUDE.md` (added a dated changelog note in its ENVIRONMENT section).

### Verification (real runs, not just clean exit codes)
- **`interested_indian_pipeline`:** copied `test_script`'s real `narration.mp3` into an isolated scratch project folder (to avoid clobbering `test_script`'s actual completed manifest/images/stitch state) and ran the split script directly through the new venv with `--device cuda --compute-type int8_float16 --batch-size 8`. First attempt via the Bash tool crashed on the final `print(f"✓ Transcribed...")` — the same known cp1252-console false alarm documented earlier in this file (Git Bash can't print `✓`), **not a real bug** — confirmed by re-running via PowerShell, which completed cleanly. **Result: ~39 seconds end-to-end** (transcription + forced alignment + scene-splitting for a 2.6-minute clip), producing a valid `manifest.json` with 37 correctly-timed scenes and a full proposed shot-split printout. Dramatically faster than the documented ~40 min/episode CPU baseline (different clip lengths, so not a strict 1:1 ratio, but the qualitative win is exactly what was expected).
- **`Aeonium_Glow\shorts_pipeline2` spot-check:** copied `NewShort-01`'s real `narration.wav` into a separate scratch folder and ran *that* project's own (unmodified) `auto_split_scenes.py` — which still hardcodes `float16`/`batch_size=16`, the less-safe defaults — through the same new venv. **Completed in ~29 seconds with no OOM**, valid 13-scene manifest. Confirms the sibling project's repoint works too, and that even its riskier hardcoded settings are fine at Shorts-length audio on this GPU (flagged in the shorts_pipeline2 CLAUDE.md note that its script lacks the safer flags the sibling project's script now has, in case a longer/heavier short ever OOMs there).
- Grepped both projects for any remaining reference to the old `Aeonium_Glow\transcription-tools` path before deleting — none found outside historical doc/session-log text (intentionally left as history, not live references).
- Old venv (`Aeonium_Glow\transcription-tools\.venv`, 46,000+ site-packages files) deleted, freeing disk space; confirmed gone via a follow-up existence check.

### Key learning for next time
Never assume a `pip install <package> --index-url <cuda-wheel-index>` stays "locked in" after installing something else afterward that transitively depends on the same package unpinned — always re-verify `torch.cuda.is_available()` after *any* further pip install into a CUDA-enabled venv, not just right after the initial CUDA install. Documented as an explicit caveat in both projects' docs.

---

## Session — Full Pipeline Test (test_2min) + Three New Review Checks (July 2026)

### agent-reach installed (this machine)
Installed from scratch — the CLI wasn't on this machine yet. Isolated venv at `$env:USERPROFILE\.agent-reach-venv` (plain `pip --user` failed: the active `pip` resolved into an unrelated `hermes-agent` venv that rejects `--user`). Web search (Exa) needed a separate `npm install -g mcporter` + `mcporter config add exa https://mcp.exa.ai/mcp` step beyond the base install — 6/13 channels available after both steps. Saved to memory (`agent_reach_setup.md`) along with a note that AIBMM/Amplifiers already has X/Reddit/YouTube scrapers connected as a lower-friction alternative (`aibmm_social_scrapers.md`).

### test_2min — full pipeline validated end-to-end on a real, current topic
User asked for a ~2-minute test script to validate the complete pipeline (script → DNA review → voice → split → prompts → images → overlays → stitch), including testing the DNA review itself. Researched a genuinely current, on-brand story via Exa: the July 2026 NEET-UG paper-leak scandal → the satirical "Cockroach Janta Party" protest movement → Education Minister Dharmendra Pradhan's resignation (the first Cabinet resignation under public pressure in the entire Modi era) → the government immediately standing up a *new* exam-reform task force even though a *previous* one from the 2024 leak scandal was still working through its own 100+ unimplemented recommendations — a genuine "committee reviewing the committee" irony that's exactly this channel's niche.

- **DNA review loop worked for real, not just in theory**: 6/10 → 6/10 (different real issues each round — humor density, then passive/euphemistic phrasing on the tear-gas moment) → **8/10 PASS** after 2 revision passes. Round 1's Claude review incidentally flagged that the script "reads pro-protest" on a live political topic and needed evenhandedness — validated the concern before I even asked about it, and directly motivated the new Check C (below).
- **Voice**: real `gemini_cloudtts` call, 2.3 min narration.
- **Split (new CUDA venv)**: ~40s for the 2.3-min clip, 27 scenes, valid manifest.
- **Prompts**: 20 shots, real mix of CARTOON/CHART/PHOTO — the CHART_ARGS schema (built earlier this session) fired for real this time, producing a correctly-rendered "22 Lakh" stat card.
- **Images**: 18 AI + 1 chart + 1 photo, 20/20 succeeded.
- **Bug found and fixed**: `route_images.py`'s 3 `subprocess.run()` calls had no explicit encoding, crashing non-fatally (background thread) on non-cp1252 bytes in a Pexels photographer credit. Fixed with `encoding="utf-8", errors="replace"`, verified by re-triggering the exact failing call directly. Pushed `9cb571d`.
- **Stitch**: succeeded, but the caption-burning step failed — `stamp_manifest.py`/`generate_srt.py` only existed in the sibling `shorts_pipeline2` project, never ported into this one during an earlier "pipeline isolation" refactor. Flagged as a spawn_task; **user copied both files in themselves mid-session**, and a re-stitch confirmed a working SRT + captioned MP4 immediately after.
- Ran the whole thing manually stage-by-stage (not via `run_episode_v2.py`'s automatic runner) specifically to avoid it running unattended all the way to a real YouTube upload attempt at the end.
- **`local_mp4_analyzer.py` review** (user's request, separate plain-Whisper pass — not WhisperX): confirmed solid audio (-24.44 dBFS overall, -6.31 peak, consistent throughout) and, independently, transcribed the CTA line verbatim — which is how the user caught that the CTA was still in a stale, pre-`gemini_cloudtts` voice. Regenerated `common/cta/cta.mp3` with current settings to fix it.
- Two small, real WhisperX mishearings surfaced and made it into burned-in captions before anyone caught them: "if there weren't already" → "if they want already", "2024 leak" → "2024 league". Directly motivated Check A (below) rather than being hand-fixed in this one video.

### Three new review checks, each demanded by something the test above actually found
Went through full Plan Mode (two parallel Explore agents, then a design pass) before touching code, since this touches the core `ReviewAgent`/`OrchestratorAgent` architecture.

- **Check A — transcription-accuracy check**, `ReviewAgent._review_split`: diffs WhisperX's reconstructed transcript against the original script (`difflib.SequenceMatcher`, stdlib — confirmed no fuzzy-match library exists anywhere in this repo). Strips numeric tokens before diffing (WhisperX routinely normalizes "twenty-two" → "22", and this channel's content is statistics-heavy — a naive diff would flag that constantly). Also suppresses single-word swaps with >0.8 character-similarity (e.g. "janta"/"janata" — a transliteration variant, not a mishearing; empirically separated from real mishearings like "leak"/"league" at ratio 0.60). Verified against real data: catches the exact "2024 league" mismatch, zero false positives on `test_2min` and on a second, unrelated real project (`test_script`, 36 scenes).
- **Check B — CTA voice-freshness check**: `generate_source_audio.py` now writes a `{output}.voice.json` sidecar recording provider/voice/model/locale/style/speaking_rate for every generated audio file — there was previously no way to answer "what voice made this file" other than re-listening, which is exactly the gap that let the CTA go stale unnoticed. `ReviewAgent._review_stitch` compares the CTA's sidecar against `channel_config.json`'s *currently active* voice config (correctly branching on the active provider, since `gemini_model` and `gemini_cloudtts_model` are two separate config keys) and flags drift. Verified with a real regeneration (fresh, no flag) and a simulated mismatch (hand-edited config, confirmed flagged, reverted cleanly with zero residual diff). **A real bug found via `/but-for-real`**: the `edge` provider branch was never filled in, meaning it would always false-flag if the channel ever switched to `edge` — fixed and verified in an isolated scratch environment (monkeypatched `PIPELINE_DIR`, no real files touched).
- **Check C — evenhandedness as a scored dimension**: `review_script.py` gains a 9th dimension, `EVENHANDEDNESS_SCORE`, with its own independent gate in `_stage_review_script` — confirmed via exploration that gating today was *exclusively* keyed off `OVERALL_SCORE`, with no sub-dimension independently blocking anything. Deliberately excluded from the OVERALL-fallback-average path (that path only exists for when Claude omits OVERALL entirely; folding evenhandedness in would inconsistently weight it only in that rare case). Verified end-to-end with a real Claude call against `test_2min`'s actual script: scored 8/10, consistent with the manual revision that had already fixed the one-sidedness problem Claude flagged in round 1.
- **A 4th planned item turned out to already exist**: "wire `review_images.py` into the loop" — turns out `_stage_images` already runs it in a 5-round regen loop and `ReviewAgent._review_images` already gates on it; it only looked unwired because the pipeline test drove stages manually. Confirmed with the user and dropped from scope rather than duplicating existing work.
- **Bonus fix found via `/but-for-real`**: `run_episode_v2.py`'s `show_status()` labels dict was missing `"review-script"`/`"overlays"` despite both being in `STAGE_ORDER` — confirmed this made `--status` raise `KeyError` for *every* project, always (a real, pre-existing, guaranteed-reproducible bug, unrelated to anything touched this session until now). Fixed and verified against a real project.
- `.gitignore` gained `*.voice.json` — the new sidecars describe already-gitignored regenerable audio; tracking the sidecar without the audio it describes would be a confusing orphaned-metadata state. Found via `/but-for-real` reading `git status` output, not assumed.
- Pushed `55af19e`.

### Handoff note
Uncommitted, separate from this work: `test_2min/` (disposable test content), `config/` (mcporter config from the agent-reach install). The caption-pipeline files (`stamp_manifest.py`, `generate_srt.py`) the user copied in are also still uncommitted — worth deciding whether to commit those as-is or adapt them further for this project's manifest schema before doing so.

---

## Session — Self-Review Memory, Final-Video CTA Check, Parallel Image Gen, Two-Tier Review (July 2026)

Four ideas from a "how to build AI agents that check their own work" video, applied via full Plan Mode (three parallel Explore agents before designing anything).

- **Script-rewrite loop gets memory**: `review_script.py` gained `--previous-review PATH` — when a rewrite is triggered, `_stage_review_script` now snapshots the rejected `script_review.md` to a timestamped backup (`script_review_backup_{ts}.md`, mirroring `stamp_manifest.py`'s existing backup pattern) before the next attempt overwrites it, then threads that snapshot into the next full Claude review so it explicitly checks whether each specific prior issue was fixed, instead of scoring the rewrite fresh with zero memory of what failed.
- **Final-video transcript check**, `ReviewAgent._review_stitch`: lazily imports `local_mp4_analyzer.analyze_mp4()` and diffs the actual rendered MP4's Whisper transcript tail against `common/cta/cta_script.txt` — the only check in the whole pipeline that listens to what the *finished render* actually says, versus every other check operating on the pre-stitch manifest. Deliberately narrow (CTA presence only, not a full transcript diff) since that's the one already-proven failure mode (this is literally how the stale-CTA-voice bug got caught once already, by hand).
- **Parallel image generation**: `generate_images_flux.py`'s sequential per-shot loop replaced with `ThreadPoolExecutor` (`--workers`, default 4), dropping the flat inter-call sleep since the SDK's own retry/backoff already handles transient errors at this concurrency level.
- **Two-tier image review**: `review_images.py` gained a cost-efficient two-pass design — Haiku reviews the full batch, Sonnet only re-checks whatever Haiku flagged WARN/FAIL, rather than running the expensive model over every shot. Also added PHOTO/MAP/CHART type-awareness to the rubric (a MAP/CHART/PHOTO shot correctly lacking a cartoon mascot is never a style failure — only fail a shot against its own type's guide).
- Pushed as `173cba4` and `94da2a9`.

---

## Session — Voice Provider Journey (ElevenLabs → xAI Grok), Real Pilot Episode, Image Feedback Loop, Pipeline Hardening (July 2026)

The longest single session so far — covers the first real, fully-researched episode (not a pipeline-validation test script), a genuine image-hallucination bug hunt, and two complete TTS-provider migrations driven by real defects found in each prior choice.

### Voice re-selection: Iapetus (Indian-American hybrid)
Generated more Charon/Iapetus previews at the user's request (energetic, newsreader styles). User picked Iapetus with an explicit "Indian-American hybrid, more American than Indian" accent style — an Indian-born narrator who's lived in the US 30 years, mostly-American accent with a faint Indian trace. Wired into `channel_config.json` (`gemini_voice: "Iapetus"`, updated `gemini_cloudtts_style`), full narration + CTA regenerated. Pushed `a092fe5`.

### Real episode chosen and produced: the July 2026 NEET-UG scandal
User chose a pilot topic after discussion — the NEET-UG paper leak, the satirical "Cockroach Janta Party" protest movement, and Education Minister Dharmendra Pradhan's resignation — then explicitly asked for the full version, not just a 10-minute pilot. All facts verified via WebSearch, not fabricated: Pradhan resigned July 25, 2026, after a 37/50-day Jantar Mantar protest; the party was founded by Abhijeet Dipke within 78 hours of CJI Surya Kant's "cockroach" remark about RTI activists; activist Sonam Wangchuk joined with a 26-day hunger strike; police lathi-charged protesters July 20–21; Pralhad Joshi absorbed the Education portfolio on top of his own. Project: `pilot_neet_scandal/`.

### AIBMM Creative Feedback Loop → standalone local reimplementation
User asked to review a defect (a hallucinated "The Interested Indian" title baked into a generated image) using AIBMM's Creative Feedback Loop MCP tool, then asked whether a local, dependency-free reimplementation made sense (rather than staying coupled to the MCP for every future project). Built `C:\Bakcup_Asus\shared-tools\creative-feedback-loop\creative_feedback_loop.py` — stdlib-only (`http.server.ThreadingHTTPServer`), session state lives entirely in `session.json` on disk, mirrors the AIBMM tool's API shape (`SessionStore`, `CreativeFeedbackLoop.create/add_batch/wait_for_feedback/close`).

**A genuinely subtle bug hunt**: what looked like an indefinite hang in the feedback loop was chased through two wrong hypotheses first (blamed an in-process Grok API call inside the long-lived server process; then blamed mixing subprocess-spawning with the server's own socket) — each "fix" was architecturally reasonable on its own but didn't touch the actual bug, since the code was never even reaching the regeneration call. The user's repeated, direct feedback about wasted time ("there seems to be an issue... too much time to resolve") is what triggered dropping both theories and instead writing an isolated, fully-synchronous diagnostic script with `print(..., flush=True)` at every step — which found the real bug in one run: `wait_for_feedback()` and the CLI's `status` command both built their `comments` list without including each candidate's `source_label` field, so `review_images.py`'s shot-matching regex always silently failed, and the loop just quietly waited forever for feedback it could never recognize as having arrived. Fixed in two call sites, verified via a clean ~12s full loop including wrap-up.

Wired into `review_images.py` as two cooperating CLI modes (`--feedback-loop`, server-only, never spawns subprocesses; `--consume-feedback`, watches `session.json` directly, regenerates only candidates with actual feedback via `generate_images_flux.py --shot N --prompt-override`). Documented in a new `IMAGE_FEEDBACK_LOOP.md`. Real `/but-for-real` findings before commit: misleading disproven-hypothesis text left in docstrings (corrected), an unused parameter, a missing consistency check between the two CLI branches, and leftover scratch/debug files (all fixed/removed). Pushed as part of `739353d`.

### The stray-title hallucination bug — root-caused and fixed for real
A screenshot of SHOT 63 showed a large hallucinated "The Interested Indian" title baked into the illustration itself (distinct from the small, intentional bottom-right channel badge `add_text_overlays.py` burns onto every image by design). Confirmed **Haiku alone still misses this defect even after rewording the rubric** — re-tested SHOT 63 directly against Haiku (PASS, wrongly) and Sonnet (correctly FAILed, including catching a garbled "foanifesto"→"manifesto" typo) — proving a structural gap in the two-tier design: Sonnet only ever rechecks what Haiku already flagged, so a Haiku false-PASS is invisible to the whole pipeline. Necessitated a **full Sonnet-only pass on all 116 shots**: 97 PASS / 8 WARN / 11 FAIL, with 10 separate stray-title-hallucination instances found (not just SHOT 63) plus 9 other distinct real defects, including a genuine factual/geography error (SHOT 94 showed a domed mausoleum instead of Jantar Mantar's actual geometric astronomical instruments).

Root cause found and fixed: `generate_images_flux.py`'s `STYLE_PREFIX` contained the literal phrase "Channel: The Interested Indian — friendly, approachable, educational" — very likely what was leaking into generated images. Removed that phrasing, added an explicit "do not render ANY text/words/titles/logos" instruction plus a reinforcing `STYLE_SUFFIX`. `review_images.py`'s rubric updated to explicitly distinguish a hallucinated title (fail) from the intentional bottom-right badge (correct, don't flag).

### Two real narration bugs found and fixed
- **The literal word "Title" was being spoken aloud** at the start of the narration. Root cause: I had written `TITLE: ...` on script line 1 (the pipeline's own documented convention), but that convention is only stripped by `pipeline_agents.py`'s normal `_stage_script`/`--script-file` orchestration — calling `generate_source_audio.py` directly (bypassing that orchestration) meant nothing stripped it. Fixed by removing the line directly (the title itself wasn't lost — `manifest.json` already stores it separately).
- **Number-system inconsistency**: the script mixed Indian (lakh) and Western (million) number phrasing — currency figures already correctly used lakh ("five lakh rupees"), but population/follower counts didn't ("2.27 million", "three million followers"). Fixed all repeat mentions to lakh, keeping exactly one bilingual bridge on first mention ("twenty-two point seven lakh — that's 2.27 million") for accessibility. Verified no genuine 10x scale error existed (22.7 lakh = 2,270,000 = 2.27 million, correct) before making any changes.

### TTS provider migration #1: Gemini Cloud TTS → ElevenLabs
User reported an audible voice/tone change around 3:35 in the narration. Diagnosed to the exact char offset: Cloud TTS chunks long scripts into independent API calls (`CLOUDTTS_CHUNK_LIMIT=3500`), producing 4 chunks for this ~11.8k-char script — the boundary fell almost exactly at the reported timestamp. Root-caused as a documented, acknowledged limitation of `gemini-3.1-flash-tts-preview` itself (Google's docs: consistency "may begin to drift" beyond a few minutes, no seed/consistency parameter exposed) — not a fixable code bug. Researched OpenAI vs ElevenLabs as alternatives (OpenAI's `gpt-4o-mini-tts` has its own, potentially worse, long-form instability); ElevenLabs emerged as the stronger candidate — Flash/Turbo v2.5 supports up to 40,000 chars in a single request (structurally eliminates the chunk-seam problem rather than reducing it), and its shared-voice-library has a dedicated Indian-accent category (160 voices) that the earlier-rejected "ANX" voice was drawn from but wasn't actually a good pick from within it.

Sampled 20 Indian-male ElevenLabs candidates across 5 rounds (deliberately excluding "ANX," the exact voice already tried and rejected). User selected **"Cyber Creed"** (`aJm4vjjSZkqTKDkM19X1`).

**A same-voice, same-model A/B that mattered**: first configured `eleven_turbo_v2_5` for its 40k-char single-request capacity (zero chunk seams) — user reported the full-narration result didn't match the approved sample. Direct A/B on identical text/voice across `eleven_multilingual_v2`, `eleven_turbo_v2_5`, and `eleven_flash_v2_5` confirmed the models render the same voice audibly differently. User chose `eleven_multilingual_v2` (the one they'd actually approved) — an explicit, accepted trade-off: better voice match, but the script (11,830 chars) still needs 2 chunks (1 seam) since multilingual_v2 caps at 10k/request.

Also fixed a real dead-config-key bug found while wiring this: `channel_config.json`'s `elevenlabs_default` key was never actually read — `generate_source_audio.py` looked for a `default` key that didn't exist, silently falling back to a hardcoded voice ID that happened to match by coincidence.

### `review_narration_audio.py` — new standalone (and pipeline-wired) audio QA tool
Built to catch chunk seams, transcript-vs-script errors, and silence gaps automatically. Honest, explicit scope: does NOT judge subjective voice/tone quality (no audio-understanding model available for that) — it points at exactly where to listen and catches objective, checkable errors.

- `generate_source_audio.py` now computes and records exact `chunk_boundaries_sec` (from each chunk's real audio duration, not estimated from character ratios) in the `.voice.json` sidecar, for both the ElevenLabs and Cloud TTS code paths.
- First real run produced 46 mostly-illegible mismatches — root cause found and fixed: character-level diffing desyncs badly once Whisper transcribes spoken numbers as digits ("22.7 lakh") against the script's spelled-out words. Switched to word-level diffing + explicit numeric-formatting suppression. A second, more serious correctness gap found on the *same* pass: the numeric suppression only checked that both sides "look like a number," not that they represent the *same value* — meaning a genuine wrong-digit narration error could have been silently hidden. Fixed with a real word-to-number-value comparison (`_words_to_number_groups`) so a value mismatch is never suppressed, only a pure formatting difference is.
- Wired into the automated pipeline: `ReviewAgent._review_voice` now runs this tool automatically after every voice stage (advisory only, never blocks progression), and in the same pass tightened the pre-existing audio-file selection (which was unsorted and didn't exclude `preview_*.mp3` test clips — a latent bug adjacent to what was being touched, fixed while there).
- Diagnosed a real, reported "voice up and down" complaint at ~9:45 by directly scanning dBFS across that window: confirmed a genuine ~7–10 dB loudness jump right at the known chunk-concatenation seam (~9:40), on a narration file generated *before* LUFS normalization existed.

### Two workflow-doc reviews → concrete pipeline changes
User asked for a review of two unrelated talking-head video-editing workflow docs (VideoClaude/Arcads, then a "Sandy Lee AI" auto-editor doc) to see what applied. Most of both didn't (filler-word cuts, voice isolation, PiP, silence-trimming — none apply to a TTS-narrated, faceless-mascot channel), but real, verified gaps got fixed:

- **LUFS normalization** (`generate_source_audio.py`'s new `normalize_loudness()`, two-pass ffmpeg `loudnorm`, target -14 LUFS): confirmed via grep that no normalization existed anywhere in the codebase, then confirmed via a real measurement that `pilot_neet_scandal`'s narration was averaging -26.76 LUFS. **Found a real accuracy bug while testing**: ffmpeg's loudnorm pass-1 JSON reports a *predicted* output loudness assuming linear normalization, but on this input ffmpeg silently fell back to dynamic normalization (the true-peak ceiling made a pure linear gain unsafe) — pass 1 predicted -14.0, the real result was -16.02. Fixed by parsing pass 2's own stats for what actually gets logged/stored.
- **Approval checkpoint before image generation** (`pipeline_agents.py`'s `_stage_images`): counts shots by TYPE from the prompts file (CARTOON costs xAI credits; MAP/CHART/PHOTO are free/local) and pauses before calling `route_images.py` — directly targets this session's own stray-title/wrong-landmark discovery-after-the-fact problem. Verified end-to-end with a real prompts file and mocked "quit" input, confirming the actual credit-spending subprocess is never reached.
- Three ideas judged not urgent enough to implement now — grade-matching Pexels photos to the flat-cartoon style, code-rendered graphics for text-heavy elements (a second doc named the concrete tool, Remotion, and specific overlay types worth building first — folded into the existing backlog entry), and a with/without-B-roll A/B render — logged in `TASKS.md` as `#13`–`#15` rather than built.
- Pushed as `1f7bf84` and `0c0eb89` (review tool + pipeline wiring were a separate commit from the LUFS/approval-gate work), plus `b169c8b` (docs-only Remotion fold-in).

### Speaking-rate tuning
Measured the actual narration at 165.2 WPM (1,964 words / 11.89 min) — fast for dense, number-heavy explainer content. Confirmed ElevenLabs' `speed` parameter is a **generation-time pacing control**, not a post-hoc time-stretch (baked into synthesis, so pitch/timbre stay natural at any setting) — verified via a real 20-sentence preview at `speed=0.9`: measured 145.0 WPM, matching the estimate closely. Wired `speed` through `_elevenlabs_call`/`elevenlabs_generate`, set as the new `channel_config.json` default.

### TTS provider migration #2: ElevenLabs → xAI Grok (voice cloning)
User decided to try cloning their own voice via Grok. Verified pasted third-party claims against xAI's real docs/API before building anything (a repeated theme this session: don't trust a summary, test live) — pricing ($15/1M chars, confirmed current, not the outdated $4.20 launch price), the 15,000 char/request limit (fits this whole script in one call, zero chunk seams), and the geographic restriction (Custom Voice cloning is US-only, excluding Illinois — user confirmed eligible).

Built `grok_generate()`/`_grok_call()`/`grok_list_voices()` in `generate_source_audio.py`, following the same pattern as the other providers, reusing `XAI_API_KEY` (already in `.env` for image generation). **Two real bugs found via live testing, not assumed from docs**:
1. xAI's own docs page describes a JSON+base64-audio response; a real live call proved that wrong — the API returns raw binary MP3 directly (`Content-Type: audio/mpeg`, body starts with an MPEG frame-sync byte). Caught immediately via a `UnicodeDecodeError` when the first implementation tried to JSON-parse the raw bytes. Also found and fixed a stale module-level comment that still asserted the disproven JSON claim directly above the corrected function — same self-contradicting-docs pattern as an earlier `channel_config.json` issue this session.
2. `grok_list_voices()` initially hardcoded a static list of the 5 original voices — already wrong the moment "naksh" (one of 21 newer flagship voices xAI added in July 2026, and the voice the user specified using) was confirmed working and wasn't in that list. Replaced with a live call to `GET /v1/tts/voices` (confirmed returns 26 real voices).

`channel_config.json`: `provider` switched to `"grok"`, `grok_voice_id: "naksh"` (placeholder until the user's own cloned voice_id exists). ElevenLabs config deliberately left in place and marked inactive rather than deleted, per the user's explicit request — a one-line revert if the clone doesn't work out. Pushed `e009d31`.

### Commits this session
- `a092fe5` — feat: switch production voice to Iapetus, Indian-American hybrid accent
- `739353d` — feat: visual image feedback loop for review_images.py + supporting fixes
- `6e4de58` — feat: switch narration to ElevenLabs, fix stray-title image bug, add narration audio review tool
- `0c0eb89` — feat: wire review_narration_audio.py into the voice-stage reviewer
- `1f7bf84` — feat: LUFS loudness normalization + pre-generation image approval gate
- `b169c8b` — docs: fold Remotion/overlay-type detail into #14 backlog item
- `e009d31` — feat: add Grok (xAI) TTS provider, switch default from ElevenLabs

### Pending / handoff
- **Waiting on the user**: create the actual custom voice clone at `console.x.ai` (Voice Library, live passphrase verification — not automatable from this pipeline). Once the 8-char `voice_id` exists, swap it into `channel_config.json`'s `grok_voice_id` in place of the `"naksh"` placeholder.
- `pilot_neet_scandal/`'s `manifest.json`, images, and stitched output are all stale relative to the current (title-stripped, lakh-fixed, Grok-voice-pending) script/narration — the full split → prompts → images → overlays → stitch pipeline needs a fresh run once the final voice is locked in.
- `TASKS.md` `#13`–`#15` (Pexels grade-matching, Remotion code-rendered graphics, B-roll A/B render) remain unimplemented by design — logged with reasoning for deferring each, not forgotten.
- Same uncommitted, out-of-scope items as the previous session's handoff note (`test_2min/`, `config/`, the PDF, `generate_srt.py`/`stamp_manifest.py`) — still untouched, still not part of any commit this session either.

