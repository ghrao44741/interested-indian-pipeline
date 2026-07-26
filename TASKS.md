# Tasks — The Interested Indian Pipeline

Last updated: 2026-07-26

## Immediate

- [ ] **Generate CTA audio** — `common/cta/cta.mp3` is empty (0 bytes)
  ```powershell
  python generate_voice.py --text-file common\cta\cta_script.txt --out common\cta\cta.mp3
  ```

- [ ] **Git commit + push** — 85+ unstaged files from this session
  ```powershell
  Remove-Item '.git\index.lock' -Force -ErrorAction SilentlyContinue
  git add -A
  git commit -m "feat: xAI default, mascot fix, route_images, PIL overlay, type routing, map fixes, pipeline isolation, speaking rate"
  git push
  ```

- [ ] **Test and tune Charon speaking rate** — preview at different rates, then lock in `channel_config.json`
  ```powershell
  python generate_source_audio.py --project test_script --preview 3 --speaking-rate 0.80
  python generate_source_audio.py --project test_script --preview 3 --speaking-rate 0.85
  python generate_source_audio.py --project test_script --preview 3 --speaking-rate 0.90
  ```

## 🚨 EP01 — Publish Gate (do not start EP02 until done)

- [ ] **Pick CTA for this episode** — edit `common\cta\cta_script.txt`, then regenerate audio
  - EP01 (serious/systemic) → Option C: *"India is running a system most people don't know exists..."*
  - Lighter/absurd topics → Option D: *"Okay. That's the one..."*
  - Deep research ep → Option A: *"I spent three days reading things..."*
  - All options saved in `common/cta/cta_options.txt`
  ```powershell
  python generate_source_audio.py --script common\cta\cta_script.txt --out common\cta\cta.mp3
  ```
- [ ] Regen all 147 images with correct mascot + routing: `python run_episode_v2.py --project ep01 --from-stage images`
- [ ] Re-stitch: `python run_episode_v2.py --project ep01 --from-stage stitch`
- [ ] Re-run metadata: `python run_episode_v2.py --project ep01 --from-stage metadata`
- [ ] Re-upload: `python upload_youtube.py --project ep01`
- [ ] Set thumbnail manually in YouTube Studio (API needs 1000+ subs)
- [ ] Add tags manually in YouTube Studio
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
