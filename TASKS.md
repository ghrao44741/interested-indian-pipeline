# Tasks — The Interested Indian Pipeline

Last updated: 2026-07-26

## Immediate

- [x] **CTA audio generated** — `common/cta/cta.mp3` — 27,885 bytes ✓

- [ ] **Listen to speaking rate previews** — `test_script\source_audio\preview_Charon_rate80/85/90.mp3`
  - `channel_config.json` already has `gemini_speaking_rate: 0.85` (recommended starting point)
  - If happy with 0.85: proceed as-is. If not: update `channel_config.json` `gemini_speaking_rate` before running voice stage.

- [ ] **Git commit + push** — 90+ unstaged files (including this session's code fixes)
  ```powershell
  cd C:\Bakcup_Asus\interested_indian_pipeline
  Remove-Item '.git\index.lock' -Force -ErrorAction SilentlyContinue
  git add -A
  git commit -m "fix: route_images keyword-search PROMPT-only, MAP no-args fallback, pipeline_agents speaking rate propagation"
  git push
  ```

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

- [ ] **#6** Fix banned word false positives in `review_script.py`
  - "genuinely"/"honestly" flagged in conversational context where they're fine
  - Add context check — only flag in formal/corporate phrasing

- [ ] **#7** Tune question ratio threshold in `review_script.py`
  - 5–6 questions in 1800 words should not fail (~1 per 400 words, not 250)

- [ ] **#8** Add `--topic` override flag to `run_episode_v2.py`
  - Skip ResearchAgent topics stage, inject user-supplied topic directly into script stage

- [ ] **#9** Wire `generate_chart.py` as CHART route in `route_images.py`
  - Currently CHART falls back to xAI Grok; should use matplotlib for accurate data visuals

- [ ] **#10** Update `generate_thumbnail.py` to composite onto base images
  - Use `common/thumbnails/base_light.png` (even episodes) / `base_dark.png` (odd episodes)
  - PIL text composite for title + episode number

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
