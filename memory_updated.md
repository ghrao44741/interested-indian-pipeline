**Purpose & context**

Giri is building semi-automated faceless video production pipelines for three YouTube channels:

- **@TheInterestedIndian** (`C:\Bakcup_Asus\interested_indian_pipeline\`) — landscape long-form (10-15 min) Indian history & policy explainer videos. Primary active development channel. Pipeline is fully end-to-end, battle-tested with test_script run (170s output ✓). EP01 pending re-publish.
- **@ThatsWhy** — landscape health/body science explainers ("What happens to your body when..."); targets higher-RPM monetization via AdSense + supplement/app/wearable affiliates.
- **@AeoniumGlow** (`C:\Bakcup_Asus\Aeonium_Glow\shorts_pipeline2\`) — vertical Shorts focused on succulent care.

Goal: repeatable, low-touch pipeline producing consistent, monetizable content. The two projects (`interested_indian_pipeline` and `Aeonium_Glow/shorts_pipeline2`) are **fully independent** — no cross-references in code. Only shared external dependency is WhisperX venv.

---

**The Interested Indian — Pipeline architecture**

Folder: `C:\Bakcup_Asus\interested_indian_pipeline\`

**Stage order (managed by `pipeline_agents.py` → `OrchestratorAgent`):**

| Stage | Script | Notes |
|---|---|---|
| topics | Claude API (inline) | 5 topic ideas, human picks one |
| script | Claude API (inline) | ~2000-word narration, justaFLAM voice |
| review-script | `review_script.py` | Channel DNA + wittiness check (quick/default/deep) |
| voice | `generate_source_audio.py` | Gemini TTS (Charon), SSML prosody for speaking rate |
| split | `auto_split_scenes_v1_stage3_export.py` | WhisperX word-align → scene manifest |
| prompts | `generate_image_prompts.py` | TYPE-tagged prompts (CARTOON/MAP/PHOTO/CHART) |
| images | `route_images.py` → type dispatchers | xAI Grok (CARTOON), geopandas (MAP), Pexels (PHOTO), matplotlib (CHART) |
| overlays | `add_text_overlays.py` | PIL burns OVERLAY text onto all images |
| stitch | `stitch_video_longform.py` (local copy) | ffmpeg Ken Burns + BGM + CTA card |
| metadata | Claude API (inline) | YouTube title / description / tags |
| thumbnail | `generate_thumbnail.py` | PIL composite onto base_light/dark.png |
| chapters | `generate_chapters.py` | Timestamp block for description |
| upload | `upload_youtube.py` | OAuth2 YouTube Data API v3 |

Run a single stage: `python pipeline_agents.py --project ep01 --only stitch`
Run from a stage: `python pipeline_agents.py --project ep01 --start-from images`

**Episode state** tracked in `{project}/episode_state.json` — completed stages skipped on resume.

---

**EP01 status — "The Bizarre Way India's Prime Minister Fires State Governments"**

- Video live (private): https://www.youtube.com/watch?v=pqHPKA92xuk
- Duration: ~13.5 min, 147 scenes
- **To fix before re-publish:** regen all 147 images (correct mascot + scene type routing), re-stitch, re-upload
- CTA: Option C — *"India is running a system most people don't know exists..."* (set in `common/cta/cta_script.txt`)

---

**Key scripts in `interested_indian_pipeline/`**

| Script | Purpose |
|---|---|
| `pipeline_agents.py` | Main orchestrator — all stages, CHANNEL_DNA, OrchestratorAgent |
| `channel_config.json` | Voice, speaking rate, mascot ref, thumbnail bases, stitch settings |
| `generate_source_audio.py` | Gemini TTS (Charon), SSML speaking rate, rate-tagged previews, standalone `--out` mode |
| `route_images.py` | Scene type dispatcher → correct generator per TYPE |
| `generate_images_flux.py` | xAI Grok default backend; `ensure_png()` resizes to 1280×720 |
| `generate_india_map.py` | geopandas map renderer; all states labeled; enforces 1280×720 via PIL |
| `generate_country_map.py` | Generalised map renderer (any country); auto-detects name field |
| `generate_chart.py` | matplotlib chart renderer (CHART type — not yet wired to router) |
| `search_pexels.py` | Pexels API stock photo fetcher (PHOTO type) |
| `add_text_overlays.py` | PIL text burn stage — processes all PNGs in images/ folder |
| `review_images.py` | Claude Haiku vision QA — 8-check rubric, PASS/WARN/FAIL |
| `review_script.py` | Channel DNA & wittiness reviewer (quick/default/deep modes) |
| `generate_thumbnail.py` | Composites title onto base_light/dark.png; `--theme auto/dark/light` |
| `generate_chapters.py` | Timestamp-based chapter list |
| `generate_image_prompts.py` | TYPE-tagged image prompts with CARTOON guardrail (no maps for geographic mascot scenes) |
| `upload_youtube.py` | YouTube OAuth2 upload |

**Security:** `Youtube_Interested_Indian_Upload.json` (OAuth2 client secret) and `token.json` are `.gitignore`-d. Git operations done in PowerShell (Linux VM can't clear Windows `.git/index.lock`).

---

**Voice / TTS**

- **Current:** Gemini TTS, voice **Charon** (`gemini-2.5-flash-preview-tts`) — deep, authoritative
- **Speaking rate:** Controlled via SSML `<prosody rate="85%">` — SDK has no native `speaking_rate` field on SpeechConfig or GenerateContentConfig. Config key: `gemini_speaking_rate` in `channel_config.json` (default 0.85)
- **Preview previews:** `python generate_source_audio.py --project X --script X.txt --preview 3 --speaking-rate 0.80/0.85/0.90` → writes `preview_Charon_rate80.mp3` etc. to `{project}/source_audio/`
- **Standalone mode:** `--project` is optional — pass `--out` as direct path for CTA/ad-hoc files
- Returns raw PCM → wrapped in WAV via Python `wave` module (24kHz, 16-bit, mono)

---

**Image pipeline — 4-type scene architecture**

| TYPE | Generator | Notes |
|---|---|---|
| CARTOON | `generate_images_flux.py` (xAI Grok) | Mascot scenes; explicit guardrail prevents geography→map |
| MAP | `generate_india_map.py` | Only when map IS the primary visual; all states labeled; 1280×720 enforced |
| PHOTO | `search_pexels.py` | Real-world subjects |
| CHART | `generate_chart.py` | Data visualisations (not yet wired to router — falls back to xAI) |

All images normalised to **1280×720** — maps via PIL resize after savefig (removed `bbox_inches="tight"`), AI images via `ensure_png()` resize.

**Mascot:** Chubby round Indian cartoon boy, amber/gold round glasses, spiky black hair, warm tan brown skin (#D4A85C), cream kurta, off-white baggy pajama trousers, brown leather sandals. Reference: `mascot_reference.png`, session `d9d31dea-1095-4a70-b57e-9c0de7eaca7b`. NOT a generic Asian character.

---

**CTA rotation system**

CTA is swapped per episode before stitching. Options in `common/cta/cta_options.txt`:
- **Option A** — deep research: *"I spent three days reading things most people wouldn't touch..."*
- **Option B** — warm/audience poke: *"If you made it this far, you're exactly the kind of person..."*
- **Option C** — serious/systemic (EP01): *"India is running a system most people don't know exists..."*
- **Option D** — lighter/absurd: *"Okay. That's the one. Subscribe if it broke your brain a little..."*

Edit `common/cta/cta_script.txt`, then:
```powershell
python generate_source_audio.py --script common\cta\cta_script.txt --out common\cta\cta.mp3
```

---

**Stitch settings (`interested_indian_pipeline/stitch_video_longform.py`)**

- Ken Burns zoom: `1.05` (config: `channel_config.json` → `stitch.ken_burns_zoom`)
- BGM: `common/bgm/default.mp3` at 0.08 volume
- CTA card: auto-appended from `common/cta/cta.png` + `cta.mp3`; skipped if `cta.mp3` < 1024 bytes (size guard added to `find_cta()`)
- Output: `{project}/output/{episode}_final.mp4`
- BGM found via `pipeline_dir = os.path.dirname(os.path.abspath(__file__))` — correct since stitch is local copy in `interested_indian_pipeline/`

---

**WhisperX (only legitimate cross-project reference)**

`WHISPERX_PYTHON = Path(r"C:\Bakcup_Asus\shared-tools\transcription-tools\.venv\Scripts\python.exe")`
Relocated out of `Aeonium_Glow` to a project-neutral shared location (also used by
`shorts_pipeline2`). CUDA-enabled (#5 in TASKS.md, done) — `--device cuda --compute-type
int8_float16 --batch-size 8`.

---

**Pending tasks — see TASKS.md for full list**

Immediate:
1. Generate CTA audio: `python generate_source_audio.py --script common\cta\cta_script.txt --out common\cta\cta.mp3`
2. Listen to 3 speaking rate previews in `test_script\source_audio\` → lock rate in `channel_config.json`
3. Git commit + push (85+ unstaged files)
4. EP01 image regen → re-stitch → re-upload → set public

---

**Key learnings & principles**

- **Pipeline isolation:** two projects must be fully independent; no path cross-references
- **`bbox_inches="tight"` in matplotlib:** destroys canvas size — never use it for fixed-dimension output
- **SSML for TTS rate control:** Gemini SDK has no `speaking_rate` field — use `<prosody rate="85%">` wrapper
- **WhisperX is environment-sensitive** — requires torch 2.5.1+cu121 or later
- **Windows/ffmpeg traps:** double-extension, hex color escaping, `os.path.join` dropping first arg when second is absolute
- **amix halves volume** — always use `loudnorm` on voice before mixing
- **YouTube API tags:** total chars < 500; apostrophes cause `invalidTags`; plain-word tags only
- **xAI Grok returns variable image sizes** — always run `ensure_png()` resize to 1280×720 after download

---

**Tools & environment**

- Windows, PowerShell, Python 3.11, NVIDIA RTX 4050 Laptop GPU
- ffmpeg 8.1.1, WhisperX (`large-v2`), Gemini TTS (`gemini-2.5-flash-preview-tts`)
- xAI Grok (image gen), Pexels API, geopandas, matplotlib, PIL, pydub, mutagen
- YouTube Data API v3 (OAuth2, scopes: `youtube.upload`)

---

**@ThatsWhy brand identity**

Warm white `#FAF7F2`, deep brown text `#2C1A0E`, dusty coral `#E8714A`, sage `#7A9E7E`, amber `#F0A500`. Fonts: Playfair Display + DM Sans. Tagline: "Your body, finally explained."

**@AeoniumGlow brand colors**

`#1C1C1A` background, `#C4713A` terracotta, `#7A8C6E` sage, `#F5EFE0` cream. Fonts: Gelasio-Bold and DMSans-Variable (SIL OFL licensed).
