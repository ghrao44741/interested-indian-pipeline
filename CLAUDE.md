# The Interested Indian — Claude Code Context

Faceless YouTube channel on Indian history, geography, geopolitics, administrative dynamics, society, film
industry politics, religious-institution administration, and city history.
Flat digital cartoon style, 12–21 min landscape video essays.

Full session history: `interesting_indian_session.md`
Task list: `TASKS.md`

---

## IMMEDIATE NEXT STEPS (not yet run)

These commands need to be run now, in this order:

```powershell
cd C:\Bakcup_Asus\interested_indian_pipeline

# 1. Git push (90+ unstaged files including code fixes from last session)
Remove-Item '.git\index.lock' -Force -ErrorAction SilentlyContinue
git add -A
git commit -m "fix: route_images keyword-search PROMPT-only, MAP no-args fallback, pipeline_agents speaking rate propagation"
git push

# 2. Validate pipeline with test_script
#    Option B — fast (~15 min, reuses existing WhisperX manifest, just re-runs prompts/images/stitch):
python run_episode_v2.py --project test_script --from-stage prompts
#    Option A — full (~50 min, regenerates narration at rate 0.85 + everything):
python run_episode_v2.py --project test_script --from-stage voice

# 3. Review test_script output
python local_mp4_analyzer.py test_script/output/test_script_final.mp4

# 4. If test_script looks good, run EP01 full pipeline
python run_episode_v2.py --project ep01 --from-stage voice
```

**Speaking rate**: `channel_config.json` has `gemini_speaking_rate: 0.85`. Preview files at
`test_script/source_audio/preview_Charon_rate80/85/90.mp3` — listen before running voice stage and
update config if needed.

---

## PROJECT STRUCTURE

```
interested_indian_pipeline/
├── CLAUDE.md                          ← you are here
├── channel_config.json                ← voice, mascot, thumbnail settings
├── brand.json                         ← pad_color: #1A2B4C (deep navy)
├── pipeline_agents.py                 ← main orchestrator (OrchestratorAgent, ReviewAgent, ResearchAgent)
├── run_episode_v2.py                  ← entry point: python run_episode_v2.py --project ep01
├── generate_source_audio.py           ← TTS: Gemini/ElevenLabs/Edge, SSML speaking rate
├── auto_split_scenes_v1_stage3_export.py  ← WhisperX transcription → manifest.json
├── generate_image_prompts.py          ← Claude API → image_prompts_one_line_per_prompt.md (TYPE+MAP_ARGS)
├── route_images.py                    ← dispatcher: MAP→GeoJSON, CARTOON→xAI, PHOTO→Pexels
├── generate_images_flux.py            ← xAI Grok image generation (CARTOON/CHART/PHOTO fallback)
├── generate_india_map.py              ← GeoJSON map renderer (accurate geography, never AI)
├── generate_chart.py                  ← matplotlib charts (bar, timeline, stat card, pie)
├── search_pexels.py                   ← Pexels API photo fetcher
├── add_text_overlays.py               ← PIL: burns OVERLAY text onto images post-generation
├── stitch_video_longform.py           ← FFmpeg stitch: 1920×1080, Ken Burns, SRT captions, BGM, CTA
├── review_images.py                   ← Claude Haiku vision QA (8-check rubric)
├── review_script.py                   ← Channel DNA & wittiness reviewer (quick/default/deep)
├── generate_thumbnail.py              ← dark/light/auto theme thumbnails
├── generate_chapters.py               ← Whisper → Claude → chapter timestamps
├── upload_youtube.py                  ← YouTube Data API v3
├── local_mp4_analyzer.py             ← audio analysis + Whisper transcription on final MP4
├── common/
│   ├── cta/
│   │   ├── cta_script.txt             ← active CTA text (swap per episode before stitch)
│   │   ├── cta_options.txt            ← A/B/C/D rotation options
│   │   ├── cta.mp3                    ← 27,885 bytes ✓ (generated)
│   │   └── cta.png                    ← CTA card image ✓
│   ├── bgm/default.mp3                ← background music fallback
│   └── thumbnails/
│       ├── base_dark.png              ← odd episodes (navy bg, mascot left)
│       └── base_light.png             ← even episodes (cream bg, mascot right)
├── ep01/                              ← Article 356 / President's Rule episode
├── test_script/                       ← Union Territories episode (pipeline validation)
└── ep01_v1/                           ← old fiscal federalism episode (archived)
```

---

## PIPELINE STAGE ORDER

```
topics → script → review-script → voice → split → prompts → images → overlays → stitch → metadata → thumbnail → chapters → upload
```

Run from any stage:
```powershell
python run_episode_v2.py --project ep01 --from-stage voice
python run_episode_v2.py --project ep01 --only images
python run_episode_v2.py --project ep01 --status
```

Each episode has `episode_state.json` tracking current stage and completed stages.

---

## ENVIRONMENT & API KEYS

All keys in `.env` (not committed):
```
GEMINI_API_KEY=...       # TTS + image prompts + script review
XAI_API_KEY=...          # xAI Grok image generation (default backend)
OPENAI_API_KEY=...       # GPT Image 2 for mascot scenes (generate_images_aibmm.py)
PEXELS_API_KEY=...       # free photo API
ANTHROPIC_API_KEY=...    # review_images.py (Claude Haiku vision), review_script.py
ELEVENLABS_API_KEY=...   # legacy, kept for reference — now using Gemini TTS
```

YouTube OAuth: `Youtube_Interested_Indian_Upload.json` (in .gitignore)

WhisperX venv (shared across projects, CUDA-enabled): `C:\Bakcup_Asus\shared-tools\transcription-tools\.venv\Scripts\python.exe`
(pipeline reads `WHISPERX_PYTHON` constant in `pipeline_agents.py`; `_stage_split` runs `--device cuda --compute-type int8_float16 --batch-size 8`)

---

## VOICE CONFIG

Provider: **Gemini TTS** — voice: **Charon**, model: `gemini-2.5-flash-preview-tts`

Speaking rate set via SSML `<prosody rate="85%">` — configured in `channel_config.json`:
```json
"voice": {
    "provider": "gemini",
    "gemini_voice": "Charon",
    "gemini_model": "gemini-2.5-flash-preview-tts",
    "gemini_speaking_rate": 0.85
}
```

Rate previews for test_script at: `test_script/source_audio/preview_Charon_rate80/85/90.mp3`

---

## IMAGE PIPELINE (4-type routing)

| TYPE     | Generator              | When                                             |
|----------|------------------------|--------------------------------------------------|
| MAP      | `generate_india_map.py`| Scene literally shows a map (primary visual)     |
| CARTOON  | `generate_images_flux.py` (xAI Grok) | Mascot + concept illustrations  |
| CHART    | `generate_images_flux.py` (fallback) | Data visuals (wire generate_chart.py eventually) |
| PHOTO    | `search_pexels.py`     | Real-world context shots                         |

`generate_image_prompts.py` outputs `TYPE` and `MAP_ARGS` fields per scene.
`route_images.py` reads those and dispatches. Legacy prompts (no TYPE field) use keyword fallback
that searches PROMPT text only (not NARRATION — bug fixed 2026-07-26).

**MAP scenes need `--highlight` state names** in MAP_ARGS. GeoJSON uses pre-2019 names:
"Jammu & Kashmir" (undivided), "Uttaranchal", "Orissa" — check with `python generate_india_map.py --list-states`

---

## MASCOT

Chubby round Indian cartoon boy, amber/gold round glasses, spiky black hair, warm tan skin (#D4A85C),
cream kurta, off-white baggy pajama trousers with gathered ankles, brown leather sandals.
4 expressions: NEUTRAL, SHOCKED, CONFUSED, SMUG.
Reference: `mascot_reference.png` | Session ID (AIBMM): `d9d31dea-1095-4a70-b57e-9c0de7eaca7b`

---

## CHANNEL DNA (for scripts)

- First-person conversational: "I" narrates, "you" is audience
- Hook in first 4 lines — specific number, sounds absurd out of context
- Humor every 2–3 paragraphs: modern analogy, self-aware observation, deadpan, or audience poke
- Jargon rule: never use policy term without immediately translating it
- Banned words: "genuinely", "honestly", "straightforward" + corporate clichés (unleash, unlock, delve, etc.)
- Length: 1,800–3,200 words for a 12–21 min episode

---

## CURRENT EPISODE STATUS

### test_script (Union Territories — pipeline validation)
- Topic: "Why India Has 9 Territories That Are Not Quite States"
- State: `stage: stitch` (images + overlays done, stitch completed but state not updated)
- Output: `test_script/output/test_script_final.mp4` (exists but uses pre-fix images + default rate narration)
- Image review: 15 PASS / 5 WARN / 7 FAIL — all failures from route_images.py classification bug (now fixed)
- **Next**: re-run from `prompts` (fast) or `voice` (full) — see IMMEDIATE NEXT STEPS above

### ep01 (Article 356 / President's Rule)
- Script: `ep01/script_the_clause_that_makes_the_president_ask_the_govern.txt` (1,885 words)
- State: `stage: upload` — but narration is old ElevenLabs ANX voice (wrong), images mostly wrong
- **Next**: `python run_episode_v2.py --project ep01 --from-stage voice` (full pipeline re-run)
- YouTube: uploaded once at https://www.youtube.com/watch?v=pqHPKA92xuk (private, not final)

---

## KEY DECISIONS & CONSTRAINTS

- **Brand color**: `#1A2B4C` (deep navy) — `brand.json` → stitch uses for letterbox padding
- **Thumbnail**: alternating dark (odd eps) / light (even eps) — ep01 is dark theme
- **CTA**: Option C for EP01 ("India is running a system most people don't know exists...")
  - Swap `common/cta/cta_script.txt` before each episode's stitch stage
  - `cta.mp3` is generated; `cta.png` is ready
- **Ken Burns zoom**: 1.05× (configured in `channel_config.json` `stitch.ken_burns_zoom`)
- **BGM volume**: 0.04 (stitch hardcoded after EP01 review showed 0.08 drowned narration)
- **Output size**: 1920×1080, all images 1280×720 PNG (enforced by `ensure_png()` in flux generator)
- **Group images**: scenes sharing `visual_group_id` in manifest → one image per group, named `group-XX.png`
  `stitch_video_longform.py` `resolve_group_images()` copies group rep to all member filenames before stitch
- **Pipeline isolation**: `pipeline_agents.py` uses local scripts only. Only external ref:
  `WHISPERX_PYTHON` = `C:\Bakcup_Asus\shared-tools\transcription-tools\.venv\Scripts\python.exe`
  (shared with `Aeonium_Glow\shorts_pipeline2`, which points at the same venv via its own
  `transcription_venv_python` config key — not duplicated per-project)

---

## KNOWN BUGS (fixed) & REMAINING TASKS

**Fixed (see TASKS.md for full history):**
- `route_images.py` — keyword classification searches PROMPT field only (not full line incl. NARRATION)
- `route_images.py` — MAP with no MAP_ARGS falls back to CARTOON (xAI) instead of blank GeoJSON map
- `pipeline_agents.py _stage_voice` — reads and passes `gemini_speaking_rate` from channel_config.json
- `route_images.py` / `generate_image_prompts.py` — CHART route wired via `CHART_ARGS` schema (#9)
- `generate_thumbnail.py` — composites onto `base_dark.png` / `base_light.png` (#10)
- `review_script.py` — banned word false positives fixed for first-person/interjection use (#6)
- `pipeline_agents.py` — question-ratio threshold recalibrated to 0.04 (#7)
- **CUDA PyTorch installed** in the shared WhisperX venv (#5) — `torch==2.13.0+cu126`, confirmed
  `torch.cuda.is_available() == True` on the RTX 4050 Laptop GPU. `_stage_split` now runs
  `--device cuda --compute-type int8_float16 --batch-size 8` (safer defaults than WhisperX's own
  float16/batch-16 given the 6GB VRAM budget — fall back to `--model medium` if `large-v2` still OOMs).

**Still to do (see TASKS.md for full list):**
- Confirm `gemini_cloudtts` actual per-episode billing (deferred a couple of weeks)
- Decide: keep or delete `run_episode_needed_or_not.py`
