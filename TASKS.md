# Tasks — The Interested Indian Pipeline

Last updated: 2026-07-26

## Immediate

- [x] **Narration voice decided: `gemini_cloudtts` (Charon, en-IN, styled)** — plain
  `gemini` provider Charon sounded generically American, not Indian, since it never
  set a locale at all. A/B'd via a real Cloud TTS call (`test_gemini31_en_in_voice.py`)
  against Gemini 3.1 + `en-IN` locale + a style prompt ("warm, witty, conversational
  Indian English") — confirmed as the pick, wired in as the new default provider.
  Cloud TTS rejects plain API keys (confirmed via a real 401), so this goes through
  gcloud OAuth2 instead (`gcloud auth login` once) — `generate_source_audio.py`
  auto-falls-back to the plain `gemini` provider if gcloud isn't available, verified
  by simulating that failure directly (not just reading the code). Real end-to-end
  preview generation via the actual pipeline entry point confirmed a valid 31.5s MP3.
  Fixed `CLOUDTTS_CHUNK_LIMIT` (4500→3500 chars) to match Google's documented
  4,000-byte-per-field limit, found while researching pricing — verified against
  real scripts that byte overhead from em-dashes/curly quotes is only ~0.24%, so
  3500 chars stays safely under the byte limit.

- [ ] **Confirm `gemini_cloudtts` actual billing** — checked the GCP billing console
  (`unseen-lever-auto-uploader` project) after testing: $9.82 total this month, 73%
  of which is unrelated "Imagen 4 Generation" from a different project ("Rewired").
  No distinct Cloud Text-to-Speech or Gemini 3.1 audio SKU appeared — only line was
  "Gemini 2.0 Flash TTS" audio tokens at $0.09 (likely leftover from earlier
  `gemini`/`test_gemini_tts.py` runs, not today's Cloud TTS calls). Likely means
  today's Cloud TTS testing fell inside the free quota or hadn't posted yet.
  Revisit in a couple of weeks once real episode-scale usage has accumulated and
  billing has had time to post, to get an actual confirmed cost-per-episode figure.

- [x] **CTA audio generated** — `common/cta/cta.mp3` — 27,885 bytes ✓

- [x] **Git commit + push** — route_images.py / pipeline_agents.py fixes pushed (`7dc9abf`)

- [x] **CHANNEL_DNA refined + niche expanded** (`03bc2da`) — 5 new FLAM-derived techniques
  (sibling opener, recurring refrain, jargon anchor, even-handed dismissal, mid-video
  restatement, all situational), niche expanded to cinema/religious institutions/
  controversial icons/city history, format widened 12–18 → 12–21 min. Verified against
  real FLAM + @JustCuriousIndia data.

- [x] **CHANNEL_DNA validated against a real script-generation run** — generated a real
  script on a comparative topic (Centre vs. States) via the actual `_stage_script` API
  call. Confirmed techniques A/D fire correctly (including at the right structural
  position), C correctly stays absent on non-tour topics, B adapts rather than copies.
  Found and fixed: (1) my own A/D examples were being reproduced near-verbatim because
  they all used the same illustrative topic — diversified to a different pairing (RBI /
  Finance Ministry) and added a general "adapt, don't copy" instruction to CHANNEL_DNA;
  (2) `pipeline_agents.py` had 4 places still hard-coded to the old 2,000–2,800 word
  target after last session's 12–21 min widening (the actual script-generation
  instruction plus 3 review/scoring checks) — all updated to 2,000–3,200 / ceiling 3,400.
  `review_script.py --deep` scored the test script 6/10 ("needs work") — correctly, on
  pre-existing rules (hook number, humor density), not on anything added this session.
  Artifact: `test_channel_dna/`.

- [x] **`generate_country_map.py` implemented + tested** (`774a196`) — name-field
  auto-detect + latitude aspect correction verified against India (regression-checked)
  and a real UK GeoJSON (non-equatorial). `--india-regions` stays opt-in by design
  (documented in the script's own `--help`). Not yet wired into any pipeline stage —
  still a standalone tool for future comparative episodes.

- [ ] **Decide: keep or delete `run_episode_needed_or_not.py`** — renamed from
  `run_episode.py` this session; it's a legacy single-agent orchestrator with its own
  stale, unsynced `CHANNEL_DNA` copy, superseded by `run_episode_v2.py` +
  `pipeline_agents.py`. Not referenced by any current documented workflow.

- [ ] **Listen to speaking rate previews** — `test_script\source_audio\preview_Charon_rate80/85/90.mp3`
  - `channel_config.json` already has `gemini_speaking_rate: 0.85` (recommended starting point)
  - If happy with 0.85: proceed as-is. If not: update `channel_config.json` `gemini_speaking_rate` before running voice stage.

## 🧪 test_script — Validate Pipeline (then sign off)

The test_script existing output has image issues (15P/5W/7F). Three code bugs fixed this session:
1. `route_images.py` — keyword classification now searches PROMPT only (not NARRATION)
2. `route_images.py` — MAP with no MAP_ARGS falls back to CARTOON (xAI) instead of blank map
3. `pipeline_agents.py` — `_stage_voice` now passes `--speaking-rate` from `channel_config.json`

**Option A — Full re-run (tests narration + all fixes, ~50 min total):**
```powershell
python run_episode_v2.py --project test_script --from-stage voice
python local_mp4_analyzer.py test_script/output/test_script_final.mp4
```

**Option B — Image+stitch only (fast, ~15 min, skips narration re-gen):**
```powershell
python run_episode_v2.py --project test_script --from-stage prompts
python local_mp4_analyzer.py test_script/output/test_script_final.mp4
```

## 🚨 EP01 — Full Pipeline Run

After test_script validates, run EP01 full pipeline:
```powershell
python run_episode_v2.py --project ep01 --from-stage voice
```
This generates: Gemini TTS Charon at rate 0.85 → WhisperX split → prompts with TYPE+MAP_ARGS → images with fixed routing → overlays → stitch → metadata. Human checkpoint: watch video → set public.

Post-upload (manual in YouTube Studio):
- [ ] Set thumbnail
- [ ] Add tags
- [ ] Watch end-to-end → set public

## Pipeline Improvements

(none currently open — #7, #9, #10 closed below)

## Infrastructure

- [ ] **#5** Install CUDA PyTorch in `transcription-tools` venv
  - WhisperX on CPU: ~40 min/episode. On GPU: ~3 min
  ```powershell
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
  python -c "import torch; print(torch.cuda.is_available())"
  ```

## Future

- [ ] **#11** Build notification agent — Telegram or email at checkpoints and on completion

- [ ] **#12** Build analytics feedback loop
  - 7-day YouTube Analytics pull → feed CTR/retention into ResearchAgent for next topic
  - Store in `ep##/analytics_7day.json`

## Done

- [x] Build `review_images.py` — AI image QA, 8-check rubric, Claude Haiku vision
- [x] Build `generate_images_flux.py` — Replicate/xAI batch image generator
- [x] Fix security — `Youtube_Interested_Indian_Upload.json` in `.gitignore`
- [x] Build `generate_thumbnail.py`, `generate_chapters.py`, `upload_youtube.py`
- [x] Integrate post-video stages into `pipeline_agents.py`
- [x] Update CHANNEL_DNA to justaFLAM voice/style
- [x] Build `generate_india_map.py` — geopandas GeoJSON renderer
- [x] Build `search_pexels.py` — Pexels API photo fetcher
- [x] Build `generate_chart.py` — matplotlib chart renderer
- [x] Build `generate_country_map.py` — generalised map renderer (any country)
- [x] Add `--theme dark|light|auto` to `generate_thumbnail.py`
- [x] Find and finalise narration voice — Gemini TTS, Charon ✓
- [x] Build `review_script.py` — Channel DNA & wittiness reviewer (quick/default/deep modes)
- [x] Add `--script-file` override flag to pipeline
- [x] Add TYPE + MAP_ARGS fields to `generate_image_prompts.py` output format
- [x] Build `route_images.py` — scene type dispatcher (MAP/PHOTO/CARTOON/CHART)
- [x] Wire `route_images.py` into `pipeline_agents.py`
- [x] Switch default image backend from Replicate → xAI Grok
- [x] Lock Indian mascot design in `STYLE_PREFIX` and `SYSTEM_PROMPT`
- [x] Add CARTOON prompt guardrail — geography scenes don't generate maps
- [x] Build PIL text overlay stage (`add_text_overlays.py`)
- [x] Pipeline isolation — removed all Aeonium Glow folder references from `pipeline_agents.py`
- [x] Fix map state labels — all states labelled by default
- [x] Fix map output size — enforced 1280×720 (removed `bbox_inches="tight"`)
- [x] Fix AI image size — `ensure_png()` resizes xAI output to 1280×720
- [x] Add `--speaking-rate` to `generate_source_audio.py` + `channel_config.json`
- [x] CTA audio guard in `stitch_video_longform.py` — skips gracefully if file empty
- [x] Full pipeline test run — test_script episode end-to-end ✓
- [x] **#6** Fix banned word false positives in `review_script.py` — "genuinely"/"honestly"
  no longer flagged in first-person conversational use or sentence-opening interjections
  ("Honestly, ..."), only in formal/corporate-sounding phrasing. Verified against 7 test
  sentences + no regression on `test_script`'s baseline.
- [x] **#8** Add `--topic` override flag to `run_episode_v2.py` — skips only the topics
  stage (`OrchestratorAgent.inject_topic`), script generation still runs normally with
  the supplied topic. Mutually exclusive with `--script-file`. Verified state transitions
  in isolation (stage→script, topics marked complete) without an API call.
- [x] **#7** Question-ratio threshold recalibrated to 0.04 (was 0.08) in `pipeline_agents.py`
  (`ReviewAgent._review_script` + `_print_script_preview` — NOT `review_script.py`, which
  has no such check; TASKS.md previously named the wrong file). Calibrated against 4 real
  generated scripts, not an estimate — `ep01`'s real 5-questions/1885-words case now
  passes (0.0427 ≥ 0.04), `ep01_v1`/`test_channel_dna` (weak scripts) still correctly fail.
  Confirmed the check is non-blocking either way (`passed` is always score-based, and this
  check never touches score) — a diagnostic-accuracy fix, not a gate fix. Separately
  confirmed `review_script.py`'s actual Claude-based `OVERALL_SCORE` gate doesn't
  over-penalize question density (read a real `--deep` review's RHYTHM_SCORE rationale —
  it's about sentence-length monotony, never mentions question marks) — no fix needed there.
- [x] **#9** Wired `generate_chart.py` as the real CHART route. Bigger than the backlog
  description implied — required a new `CHART_ARGS` schema in `generate_image_prompts.py`
  (mirrors `MAP_ARGS`) plus parsing/validation/dispatch in `route_images.py`
  (`_validate_chart_args`, `run_chart`, downgrade-to-CARTOON on missing/invalid args,
  mirroring the existing MAP_ARGS-missing fallback). Verified end-to-end: hand-authored
  a test prompts file with valid/missing/invalid CHART_ARGS, ran a real (non-dry-run)
  generation, and visually confirmed the resulting chart PNG rendered correctly
  (brand-colored bar chart, correct labels/values) — not just that the subprocess exited 0.
- [x] **#10** `generate_thumbnail.py` now composites onto `common/thumbnails/base_light.png`
  / `base_dark.png` (cover-fit-cropped to 1280×720) instead of a solid-colour canvas,
  with a PIL-drawn title in the natural gap between the mascot and map/silhouette art
  (scrim behind it for legibility) and an "EPxx" badge in the top corner opposite the
  mascot. The base art already has "THE INTERESTED INDIAN" baked into its own footer —
  deliberately does NOT redraw a competing footer. Original solid-canvas path preserved
  untouched as the fallback when base art is missing. Iterated visually against the
  actual PNGs (not just a clean exit code) — first pass had the text scrim overlapping
  the mascot's face on a long test title; narrowed the text zone and made badge placement
  theme-aware to fix it, then re-verified on both a long and a realistic-length title.
