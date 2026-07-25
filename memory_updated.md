**Purpose & context**

Giri is building semi-automated faceless video production pipelines for three YouTube channels:

- **@TheInterestedIndian** (`C:\Bakcup_Asus\interested_indian_pipeline\`) — landscape long-form (10-15 min) Indian history & policy explainer videos. Primary active development channel. Pipeline is fully end-to-end and battle-tested with EP01.
- **@ThatsWhy** — landscape health/body science explainers ("What happens to your body when..."); targets higher-RPM monetization via AdSense + supplement/app/wearable affiliates. Uses same `shorts_pipeline2/` stitch infrastructure.
- **@AeoniumGlow** (`C:\Bakcup_Asus\Aeonium_Glow\shorts_pipeline2\`) — vertical Shorts focused on succulent care.

Goal: repeatable, low-touch pipeline producing consistent, monetizable content. The Interested Indian is the pilot for proving the full automated stack end-to-end before expanding to other channels.

---

**The Interested Indian — Pipeline architecture**

Folder: `C:\Bakcup_Asus\interested_indian_pipeline\`
Shared stitch engine: `C:\Bakcup_Asus\Aeonium_Glow\shorts_pipeline2\stitch_video_longform.py`

**Stage order (managed by `pipeline_agents.py` → `OrchestratorAgent`):**

| Stage | Script | Notes |
|---|---|---|
| topics | Claude API (inline) | Generates 5 topic ideas, human picks one |
| script | Claude API (inline) | ~2000-word narration script |
| voice | `generate_source_audio.py` | ElevenLabs (chunked, ≤9500 chars/call) or Edge TTS fallback |
| split | `auto_split_scenes_v1_stage3_export.py` | WhisperX word-align → scene manifest |
| prompts | `generate_image_prompts.py` | One image prompt per scene |
| images | `generate_images_flux.py` | Replicate FLUX.1-schnell batch generation |
| overlays | `add_text_overlays.py` | PIL burns OVERLAY text onto images |
| stitch | `stitch_video_longform.py` | ffmpeg Ken Burns + BGM mix + CTA card |
| metadata | Claude API (inline) | YouTube title / description / tags |
| thumbnail | `generate_thumbnail.py` | matplotlib branded card |
| chapters | `generate_chapters.py` | Timestamp block for description |
| upload | `upload_youtube.py` | OAuth2 YouTube Data API v3 |

Run a single stage: `python pipeline_agents.py --project ep01 --only stitch`
Run from a stage: `python pipeline_agents.py --project ep01 --start-from images`

**Episode state** is tracked in `ep01/episode_state.json` — stages in `completed[]` are skipped on resume.

---

**EP01 status — "The Clause That Makes the President Ask the Governor..."**

- Video live (private): https://www.youtube.com/watch?v=pqHPKA92xuk
- Title: *The Bizarre Way India's Prime Minister Fires State Governments*
- Duration: ~13.5 min, 147 scenes, ANX ElevenLabs voice
- **Known issues with EP01 (to fix before re-upload):**
  - Images: 83/98 FAIL on review (placeholders for scenes 131-147; wrong assets like American flag appearing)
  - Video uploaded without tags (invalidTags API error — now fixed in metadata prompt)
  - Thumbnail not uploaded via API (YouTube channel needs verification/1000 subs); upload manually in Studio
  - No CTA at end (now fixed — `common/cta/cta.png` ready, `cta.mp3` still needs generating)
  - Voice too quiet (now fixed — `loudnorm` added to stitch BGM mix)

---

**Key scripts in `interested_indian_pipeline/`**

| Script | Purpose |
|---|---|
| `pipeline_agents.py` | Main orchestrator — `OrchestratorAgent` class, all stages |
| `channel_config.json` | Voice provider, model, voice ID, BGM path |
| `generate_source_audio.py` | ElevenLabs TTS with chunking + Edge TTS fallback |
| `generate_images_flux.py` | Replicate FLUX.1-schnell batch image gen |
| `review_images.py` | AI image QA — checks relevance, text accuracy, style |
| `generate_thumbnail.py` | matplotlib thumbnail with `--theme auto/dark/light` |
| `generate_chapters.py` | Timestamp-based chapter list |
| `generate_country_map.py` | geopandas map renderer, auto-detects name field, latitude correction |
| `generate_chart.py` | matplotlib chart renderer |
| `search_pexels.py` | Pexels API stock photo fetcher |
| `upload_youtube.py` | YouTube OAuth2 upload with multi-location video search |
| `preview_elevenlabs_voices.py` | ElevenLabs voice browser with Indian accent filter |

**Security:** `Youtube_Interested_Indian_Upload.json` (OAuth2 client secret) and `token.json` are `.gitignore`-d. Git operations done in PowerShell (Linux VM can't clear Windows `.git/index.lock`).

---

**Voice / TTS**

- **Current:** ElevenLabs ANX - Deep & Friendly (`gYQ0co3BoppQZ8BDM3lj`), `eleven_multilingual_v2`, stability 0.5, similarity 0.75. Starter tier: 40K chars/month.
- **API key header:** `xi-api-key` (not Bearer). Key stored in `.env`.
- **Chunking:** text split at sentence boundaries ≤9500 chars; chunks concatenated via pydub or ffmpeg.
- **Known issue:** ElevenLabs output level is quiet — fixed by `loudnorm=I=-14` in stitch BGM mix.
- **CTA audio:** Edge TTS (`en-IN-PrabhatNeural` for male match) — short clip, different voice is acceptable for CTA card.
- **Pending (#24):** Evaluate Google/Gemini TTS and additional ElevenLabs Indian voices; update `channel_config.json` when settled.

---

**Stitch settings (`stitch_video_longform.py`)**

- `BGM_VOLUME = 0.04` (was 0.08 — lowered after EP01 review)
- `KEN_BURNS_ZOOM_RATIO = 1.04` (was 1.08 — more subtle)
- Audio mix: `loudnorm=I=-14` on voice, `afade=t=in:st=0:d=2` on BGM, `amix weights=4 1`
- CTA card: auto-appended from `common/cta/cta.png` + `cta.mp3` (static slide, no Ken Burns)
- Output goes to `{project}/output/{episode}_final.mp4` — fixed by normalising `manifest["episode"]` to basename

**Key bugs fixed (session July 2026):**
- `manifest["episode"]` was storing full Windows path → stitch wrote to pipeline root. Fixed in `auto_split_scenes_v1_stage3_export.py` (now uses `os.path.basename`) and defensive fix in stitch.
- `manifest["title"]` was "Untitled Episode" — fixed by passing `--title` from episode state in split stage.
- Tags `invalidTags` — metadata prompt now generates 10-15 plain-word tags under 400 chars total.
- Thumbnail 403 on upload — caught gracefully with warning (requires YouTube channel verification).
- `pipeline_agents.py` had no `__main__` entry point — added argparse with `--project`, `--start-from`, `--only`.

---

**Pending tasks (priority order)**

1. **Re-do EP01 images** — regen all 147 scenes with better prompts, then re-stitch and re-upload (#14)
2. **Generate CTA audio** — `python -m edge_tts --voice en-IN-PrabhatNeural --text "If this made you think, subscribe. New episode every week. The Interested Indian — Indian history and policy, explained clearly." --write-media common/cta/cta.mp3`
3. **Find final narration voice** — evaluate ElevenLabs Indian voices + Google/Gemini TTS (#24)
4. **Fix banned word false positives** in script reviewer (#12)
5. **Tune question ratio threshold** in script reviewer (#13)
6. **Install CUDA PyTorch** in transcription-tools venv (#11)
7. **Add topic override** (`--topic` flag) (#21)
8. **Add script override** (`--script-file` flag) (#22)
9. **Build notification agent** (#15)
10. **Build analytics feedback loop** (#16)

---

**@ThatsWhy brand identity**

Warm white `#FAF7F2`, deep brown text `#2C1A0E`, dusty coral `#E8714A`, sage `#7A9E7E`, amber `#F0A500`. Fonts: Playfair Display + DM Sans. Tagline: "Your body, finally explained."

**@AeoniumGlow brand colors**

`#1C1C1A` background, `#C4713A` terracotta, `#7A8C6E` sage, `#F5EFE0` cream. Fonts: Gelasio-Bold and DMSans-Variable (SIL OFL licensed).

---

**Key learnings & principles**

- **Text pattern matching for scene splitting is fragile** — duration-only thresholding is more robust
- **Crossfade filters cause compounding sync drift** — hard cuts are safer for narration pipelines
- **WhisperX is environment-sensitive** — required torch upgrade (2.5.1+cu121 → 2.8.0+cu128)
- **Windows/ffmpeg traps:** double-extension (`bgm.mp3.mp3`), hex color escaping (`0xFAF7F2`), `os.path.join` with absolute path as second arg drops the first arg on Windows
- **amix halves volume** — always use `loudnorm` on voice before mixing with BGM
- **YouTube API tags:** total chars must be under 500; apostrophes may cause `invalidTags`; generate plain-word tags only
- **ElevenLabs:** use `xi-api-key` header (not Bearer); 10K char limit per call requires chunking; quiet output needs post-normalisation

---

**Tools & environment**

- Windows, PowerShell, Python 3.11, NVIDIA RTX 4050 Laptop GPU
- ffmpeg 8.1.1, WhisperX (`large-v2`), ElevenLabs API, Replicate API (FLUX.1-schnell)
- Edge TTS (free fallback), geopandas, matplotlib, pydub, mutagen
- YouTube Data API v3 (OAuth2, scopes: `youtube.upload`)
- **Zapiwala method reference:** `youtube.com/watch?v=4itY-VI7QSE`, prompts at `zapiwala.ai`
