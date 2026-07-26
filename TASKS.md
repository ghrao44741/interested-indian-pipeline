# Tasks — The Interested Indian Pipeline

## Active

### 🚨 #25 — EP01 Publish Gate (DO NOT start EP02 until done)
- [ ] **Generate CTA audio** — PowerShell: `python -m edge_tts --voice en-IN-PrabhatNeural --text "If this made you think, subscribe. New episode every week. The Interested Indian — Indian history and policy, explained clearly." --write-media common\cta\cta.mp3`
- [ ] **Regen all 147 images** — `python pipeline_agents.py --project ep01 --only images`
- [ ] **Re-stitch** — `python pipeline_agents.py --project ep01 --only stitch` (writes to ep01/output/ep01_final.mp4)
- [ ] **Re-run metadata** — `python pipeline_agents.py --project ep01 --only metadata` (clean tags this time)
- [ ] **Re-upload** — `python upload_youtube.py --project ep01`
- [ ] **Thumbnail** — upload manually in YouTube Studio (API thumbnail upload needs 1000+ subs)
- [ ] **Tags** — add manually in YouTube Studio
- [ ] **Final review** — watch end-to-end, then set public

### #24 — Finalise Narration Voice
- [ ] **Evaluate Indian voices** — ElevenLabs Indian accent voices + Google/Gemini TTS
  - Current: ANX - Deep & Friendly (gYQ0co3BoppQZ8BDM3lj), quiet, needs loudnorm
  - Use `preview_elevenlabs_voices.py` to A/B test candidates
  - Once locked, regenerate CTA audio with same voice (currently using Edge TTS en-IN-PrabhatNeural as placeholder)

### #21 — Add `--topic` override flag to pipeline
- [ ] In `pipeline_agents.py`, let user pass `--topic "Article 356"` to skip the topics stage and inject directly into script stage

### #22 — Add `--script-file` override flag to pipeline
- [ ] In `pipeline_agents.py`, `--script-file path/to/script.txt` skips topics + script stages, starts from review-script

## Waiting On

### shorts_pipeline2 Git Setup
- [ ] **Initialize and push** — waiting on user to do this locally:
  ```powershell
  cd C:\Bakcup_Asus\Aeonium_Glow\shorts_pipeline2
  git init
  git config user.name "Giri"
  git config user.email "ghrao4474@gmail.com"
  git add -A
  git commit -m "Initial commit: shared pipeline scripts"
  git remote add origin https://github.com/ghrao44741/shorts-pipeline2.git
  git branch -M main
  git push -u origin main
  ```

### brand.json placement
- [ ] Drop `{"pad_color": "#1A2B4C"}` into `interested_indian_pipeline/brand.json` (channel root) — stitch script picks it up via parent-fallback. Currently not committed.

## Someday

### #11 — CUDA PyTorch in transcription-tools venv
- [ ] Install CUDA PyTorch so WhisperX runs on GPU (40 min → ~3 min per episode)
  - `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118`
  - Test: `python -c "import torch; print(torch.cuda.is_available())"`

### #12 — Fix banned word false positives in script reviewer
- [ ] "genuinely" / "honestly" flagged even in legitimate conversational uses
  - Approach: add context window check — only flag if not followed by a comma + subordinate clause

### #13 — Tune question ratio threshold in script reviewer
- [ ] 5–6 questions in 1800 words currently fails the check — threshold is too tight
  - Adjust to ~1 question per 400 words (was every 250)

### #15 — Build notification agent
- [ ] Email or Telegram alert at human checkpoints (topic pick, script approve, upload complete)
- [ ] Telegram bot is simplest: single HTTP POST to `api.telegram.org`

### #16 — Build analytics feedback loop
- [ ] 7-day post-publish YouTube Analytics pull → fed back into ResearchAgent competitive brief
- [ ] Metrics: CTR, AVD, retention curve, top traffic sources
- [ ] Store in `ep01/analytics_7day.json`

## Done

- [x] ~~#26 — Build review_script.py~~ (July 2026)
  - Standalone Channel DNA + wittiness reviewer, three modes (quick/default/deep)
  - Integrated as pipeline stage between script and voice
- [x] ~~CHANNEL_DNA v2 — FLAM analysis~~ (July 2026)
  - Opener template, 30-45s/5-10s/30s rhythm, meme framework technique
  - One-liner region summaries, dark social commentary layer, audience invitation CTA
- [x] ~~generate_image_prompts.py meme beat system~~ (July 2026)
  - Meme reaction beat every 2-3 shots, photo cutout collage, no copyrighted names rule
- [x] ~~pipeline_agents.py topics formula guide~~ (July 2026)
  - 5 proven title formulas + content angle guide from FLAM research
- [x] ~~Git push — FLAM session changes~~ (July 2026)
