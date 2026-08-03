# Tasks — The Interested Indian Pipeline

Last updated: 2026-07-28

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

## 🧪 test_2min — Full Pipeline Validation Test (Complete)

Ran a real, fresh 2-minute episode end-to-end (script → DNA review → voice →
CUDA split → prompts → images → overlays → stitch) on a genuinely current
topic (the July 2026 NEET-UG paper leak / "Cockroach Janta Party" protests /
Dharmendra Pradhan's resignation) to validate the whole pipeline in one pass,
not just individual stages. Driven stage-by-stage manually rather than via
`run_episode_v2.py`'s automatic runner, to avoid an unattended real YouTube
upload attempt at the end.

- **DNA review loop worked for real**: 3 rounds, 6/10 → 6/10 (different
  issues each pass) → 8/10 PASS. Round 1 caught a genuine one-sidedness
  problem on the politically live topic — directly led to Check C below.
- **CUDA split confirmed on real fresh content**: ~40s for a 2.3-min clip,
  27 scenes, valid manifest.
- **Found and fixed a real bug**: `route_images.py`'s 3 `subprocess.run()`
  calls (map/chart/pexels) had no explicit encoding, crashing (non-fatally,
  in a background thread) on non-cp1252 bytes in `search_pexels.py`'s
  photographer-credit output. Fixed with `encoding="utf-8", errors="replace"`,
  verified by re-triggering the exact failing call directly. Pushed (`9cb571d`).
- **Found the caption pipeline was silently broken**: `stitch_video_longform.py`
  needs `stamp_manifest.py`/`generate_srt.py`, which only existed in the
  sibling `shorts_pipeline2` project — every video from this project was
  missing burned-in captions. User copied both files in; re-stitch confirmed
  it now produces a valid SRT + captioned MP4.
- **Caught a stale CTA by ear**: `common/cta/cta.mp3` was still using a
  pre-`gemini_cloudtts` voice. Regenerated with current settings — directly
  motivated Check B below (an automatic version of "caught by ear").
- **Real, if minor, transcription defect found**: WhisperX misheard "if there
  weren't already" as "if they want already" and "2024 leak" as "2024
  league" — confirmed the error flows all the way into burned-in captions.
  Directly motivated Check A below.
- Artifact: `test_2min/` (disposable test content — not a real episode).

## Video-Pipeline Checks — Transcription Accuracy, CTA Freshness, Evenhandedness (Complete)

Three checks added to the review agents, each grounded in something the
`test_2min` test actually surfaced (not hypothetical):

- **Check A** — `ReviewAgent._review_split` now diffs WhisperX's
  reconstructed transcript against the original script (`difflib`, stdlib —
  no fuzzy-match library exists in this repo) to catch ASR mishearings
  before they reach captions. Numeric tokens (digits + spelled-out numbers)
  are stripped before diffing, since WhisperX routinely normalizes those
  and a naive diff would flag it constantly — this channel's content is
  statistics-heavy. Single-word swaps with >0.8 character-similarity
  (e.g. "janta"/"janata") are also suppressed as transliteration variants,
  not real mishearings. Verified against real `test_2min` data: catches the
  exact "2024 league" mismatch, zero false positives on `test_2min` or a
  second, unrelated real project (`test_script`, 36 scenes, 0 flags).
- **Check B** — `generate_source_audio.py` now writes a
  `{output}.voice.json` sidecar recording which provider/voice/model/locale/
  style produced each audio file (there was previously no way to answer
  "what voice made this file" other than re-listening).
  `ReviewAgent._review_stitch` compares `common/cta/cta.mp3`'s sidecar
  against `channel_config.json`'s currently active voice config and flags
  drift. Verified with a real CTA regeneration (fresh, no flag), a
  simulated mismatch (hand-edited config, confirmed flagged, reverted
  cleanly), and — found via `/but-for-real` — an `edge`-provider resolution
  gap that would've always false-flagged if the channel ever switched to
  `edge`; fixed and verified in an isolated scratch environment.
- **Check C** — `review_script.py` gains a 9th scored dimension,
  `EVENHANDEDNESS_SCORE`, with its own independent gate in
  `_stage_review_script` (separate from `OVERALL_SCORE` — confirmed no
  sub-dimension gated anything before this). Verified end-to-end with a
  real Claude call against `test_2min`'s script: scored 8/10, consistent
  with the earlier manual fix that added audience-address balance to that
  same script.
- **Bonus fix found via `/but-for-real`**: `run_episode_v2.py`'s
  `show_status()` `labels` dict was missing `"review-script"`/`"overlays"`
  despite both being in `STAGE_ORDER` — confirmed this made `--status`
  raise `KeyError` for every project, always. Fixed and verified against
  a real project (`test_2min --status` now prints cleanly).
- A fourth originally-planned item, "wire `review_images.py` into the
  loop," turned out to already be built — `_stage_images` already runs it
  in a 5-round regen loop and `ReviewAgent._review_images` already gates
  on 0 FAILs; it only looked unwired because the `test_2min` test drove
  stages manually. Confirmed with user, dropped from scope.
- `.gitignore`: added `*.voice.json` (the new sidecars describe
  already-gitignored regenerable audio — tracking one without the other
  is a confusing orphaned-metadata state).
- Pushed (`55af19e`).

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

- [x] **#5** Install CUDA PyTorch in the WhisperX venv — done as part of relocating the venv
  (see below) to a shared, cross-project location. `torch==2.13.0+cu126` installed, confirmed
  `torch.cuda.is_available() == True` on the RTX 4050 Laptop (6GB VRAM). `pipeline_agents.py`
  `_stage_split` now passes `--device cuda --compute-type int8_float16 --batch-size 8` (safer
  than WhisperX's own float16/batch-16 defaults, given large-v2's own README says it needs
  "under 8GB" VRAM for transcription alone — tight against 6GB once alignment is added). Real
  speedup + a valid manifest confirmed via an actual transcription run (see verification below),
  not just a clean exit code. Fallback documented if `large-v2` still OOMs: drop to `--model medium`.

- [x] **Relocate shared WhisperX venv out of `Aeonium_Glow`** — previously lived at
  `Aeonium_Glow\transcription-tools\.venv` even though both `interested_indian_pipeline`
  (hardcoded `WHISPERX_PYTHON` in `pipeline_agents.py`) and `Aeonium_Glow\shorts_pipeline2`
  (`transcription_venv_python` in its own `pipeline_config.json`) depend on it — structurally
  confusing to have a shared dependency living inside one specific project's folder. Moved to
  `C:\Bakcup_Asus\shared-tools\transcription-tools\.venv`, a project-neutral location. Both
  projects' references updated and verified with real transcription runs (`interested_indian_pipeline`:
  ~39s on a 2.6-min clip; `shorts_pipeline2`: ~29s on a Shorts-length clip, no VRAM OOM). Old venv
  at the Aeonium_Glow path deleted after both projects confirmed working against the new path.

## Future

- [ ] **#11** Build notification agent — Telegram or email at checkpoints and on completion

- [ ] **#12** Build analytics feedback loop
  - 7-day YouTube Analytics pull → feed CTR/retention into ResearchAgent for next topic
  - Store in `ep##/analytics_7day.json`

- [ ] **#13** Grade-match Pexels PHOTO shots to the flat-cartoon brand style — `search_pexels.py`
  currently searches/picks/resizes with zero color treatment. A real photo dropped between two
  flat-illustration scenes looks visually jarring. Needs a lightweight filter pass (desaturate,
  color-match toward the `#FAF7F2`/navy palette) — will need visual iteration against real
  generated frames, not a one-shot fix. From the VideoClaude/Arcads workflow-doc review
  (2026-07-28): "grade-match every clip."

- [ ] **#14** Code-rendered graphics for text-heavy elements (stat/title/quote cards, numbered
  step lists) — a new subsystem, not a patch: needs a template design (what does a stat card
  look like in this brand?), a new TYPE in `generate_image_prompts.py`/`route_images.py`
  routing, and new ffmpeg compositing logic. Directly attacks the stray-title-hallucination
  failure mode (AI image generation is bad at rendering clean text; code-rendered text sidesteps
  it entirely) for the specific scenes where the image *is* text/data. Deserves its own planning
  pass before implementation. From the VideoClaude/Arcads workflow-doc review (2026-07-28).
  **Tool + concrete starting scope (from a second workflow-doc review, Sandy Lee AI
  auto-editor, 2026-07-28): Remotion (React-based programmatic video rendering) is the
  purpose-built tool for exactly this — not template image-filling, actual code-rendered
  frames.** Overlay types worth building first, prioritized by fit with this channel's
  content: `StepProgress` / `AnimatedList` (sequential-process explanations — "steps of
  Article 356," "how the NEET-UG scandal unfolded," a lot of this channel's actual subject
  matter), `LowerThird` (name/title callouts), `TypographyReveal` (stat/quote emphasis),
  `FlowchartGlitch` (institutional-relationship diagrams). Full reference list from that
  doc also included `WordPop`, `EmphasisAlert`, `BrowserMockup`, `AIChatBubble`,
  `CardFlip3D`, `CursorSelect`, `NewsFlash`, `GoogleSearch`, `YouTubeGrowth`, `PIPLayout` —
  mostly creator/influencer-content conventions (screen-recording mockups, growth-chart
  flexes) that don't fit this channel's tone, listed here for completeness but not a
  starting priority.
  **Third real trigger case (pilot_neet_scandal production, 2026-07-29):** 3 PHOTO-type
  shots (Sonam Wangchuk's hunger strike, a cockroach-sign protest at Jantar Mantar, a
  police lathi-charge) had no matching real Pexels photo — these are recent, specific,
  real 2026 news events that generic stock libraries simply don't carry. Worked around
  this time by converting them to CARTOON illustrations, but an HTML/CSS-rendered
  "info card" or "quote card" (activist name, dates, a stat) would handle this class of
  problem more precisely than either forcing a photo search that can't succeed or an
  illustration that has to guess at specifics — a second concrete use case for this
  backlog item beyond the original text-hallucination motivation.

- [ ] **#15** With/without B-roll A/B render for PHOTO-heavy episodes — render once with all
  PHOTO-type real-world shots, once with them swapped for CARTOON, compare which flows better.
  Low urgency; more a habit to try on a photo-heavy episode than a code change (maybe a
  `--skip-photos` flag at most). From the VideoClaude/Arcads workflow-doc review (2026-07-28).

- [ ] **#18** (Task 2B) Evaluation defaults should come from the Channel Pack, not module
  `DEFAULT_*` constants — `generate_source_audio.py`'s evaluation path (an omitted
  `--provider`/`--voice`) still falls back to `DEFAULT_PROVIDER`/`DEFAULT_VOICE_*`, which are
  read from the root `channel_config.json` at import time — the generated legacy adapter for
  `interested_indian` specifically, not the loaded Channel Pack's own `voice.working_default`.
  A second channel with no legacy adapter would get Interested Indian's defaults instead of its
  own, silently. Flagged during the second Task 2A follow-up review (2026-08-02) alongside the
  `--preview`/full-profile-forwarding/cloudtts-fallback/split-containment fixes in `e8744f7` and
  `5fdc828` — explicitly scoped OUT of that micro-fix and deferred to Task 2B, which already
  owns migrating evaluation-path behavior onto the Channel Pack.

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
- [x] **#16** LUFS loudness normalization — `generate_source_audio.py`'s new
  `normalize_loudness()` (two-pass ffmpeg `loudnorm`, target -14 LUFS, configurable via
  `channel_config.json` `voice.target_lufs`) runs once on the master narration file right
  after generation, before the split stage cuts per-scene clips — every clip inherits
  consistent loudness for free rather than each tiny clip being normalized independently
  (unreliable on short audio). Skipped for `--preview` (short A/B clips, same reason).
  From the VideoClaude/Arcads workflow-doc review (2026-07-28). Real gap: grepped the
  codebase first and confirmed no normalization existed anywhere — verified against the
  real `pilot_neet_scandal` narration, which measured -26.76 LUFS input (far below any
  reasonable target, and with no consistency guaranteed across the mid-session TTS
  provider switch). Found and fixed a real accuracy bug during testing: ffmpeg's loudnorm
  pass-1 JSON reports a *predicted* output loudness assuming linear normalization, but on
  this input ffmpeg silently fell back to dynamic normalization (the linear gain needed
  would have clipped past the true-peak ceiling) — pass 1 predicted -14.0 LUFS, the real
  result was -16.02. Fixed by parsing pass 2's own stats for the logged/stored value
  instead of trusting pass 1's prediction. Chunk-boundary timestamps (added for the
  narration-review tool) are unaffected — loudnorm is a pure gain filter, sample count
  and duration are unchanged either way.
- [x] **#17** Approval checkpoint before image generation spends AI credits —
  `OrchestratorAgent._stage_images()` now counts shots by TYPE from
  `image_prompts_one_line_per_prompt.md` (CARTOON = costs xAI Grok credits; MAP/CHART/PHOTO
  = free/local) and pauses with `[enter] Generate | edit | quit` before calling
  `route_images.py`. From the VideoClaude/Arcads workflow-doc review (2026-07-28) —
  directly targets this session's own pain point, where stray-title hallucinations and a
  wrong landmark were only caught *after* a full ~100-shot paid batch, requiring a costly
  Sonnet re-audit. Verified end-to-end against the real `pilot_neet_scandal` prompts file
  (102 CARTOON / 7 CHART / 1 MAP / 6 PHOTO counted correctly) with input mocked to "quit" —
  confirmed the real `route_images.py` subprocess call (the actual credit-spending step)
  is never reached.

## Backlog from VideoClaude/Arcads workflow-doc review (2026-07-28)

Reviewed a talking-head video-editing workflow doc (Claude Edits VideoClaude + Arcads MCP).
Most of it doesn't apply — assumes real camera footage and a recorded voice (filler-word
cuts, voice isolation, PiP face-shrink), none of which exist in a TTS-narrated, AI-illustrated
pipeline. Five ideas were judged worth taking; #1 (LUFS) and #3 (approval gate) implemented
immediately above as **#16**/**#17**. The other three are **#13**/**#14**/**#15** in Future.
