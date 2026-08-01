# Pipeline Stage Contract

Why this exists: **every defect that reached a finished video was found by a human
watching it, or by transcribing the output and comparing it to the script.** Not one
was caught by the pipeline, even though checks for several of them already existed.

This document is the catalogue of what actually went wrong, why each escaped, and the
postconditions each stage must satisfy before the next one is allowed to run.

---

## 1. The mishap catalogue

| # | Mishap | How it was found | Why it escaped |
|---|---|---|---|
| 1 | Last word of a scene chopped — "June 21st" → "21st" (heard as "May"); "he wasn't." dropped entirely | User's ear, then A/B transcription | **No check existed.** Present in every episode ever produced (22–30% of scenes, 3 TTS providers) |
| 2 | `NEET-UG` spoken as "NETUG"/"an ETUG"/"Neat Uji" — the episode's own subject, 3/3 times | Manual watch-through | Check existed (`review_narration_audio` transcript diff) but never ran |
| 3 | Sentence-initial "Pass" spoken as "Ask" | Manual watch-through | Same as #2 |
| 4 | The literal word "Title" narrated aloud | Listening | Direct script call bypassed the `TITLE:` stripping that only `_stage_script` does |
| 5 | ~70s A/V desync (`-t audio+0.5` with no `apad`) | User reported caption drift | No duration invariant asserted after stitch |
| 6 | Captions contain ASR mishearings — "neat" for "Neet", "mempage" for "meme page" | User reading captions | Check existed (`_find_transcription_mismatches`) but never ran |
| 7 | Reviewers diffed against `*_PREVIOUS.txt` | Running the reviewer by hand | `sorted(glob("script_*.txt"))[-1]` sorts drafts *after* the real script |
| 8 | Split would transcribe a 30s voice-test clip | Code reading, before it fired | `sorted(source_audio/*.mp3)[0]`; never exercised because the orchestrator was never used |
| 9 | Configured voice silently replaced by `en-US-JennyNeural` | Code reading, before it fired | Split's argparse default written into the manifest, then read back as an "override" |
| 10 | A phrase rendered **twice** ("but it wasn't that. But it wasn't that.") | `local_mp4_analyzer.py` on the output | Self-inflicted while fixing #1; only caught because the output was transcribed |
| 11 | Stale CTA in a previous voice | Sidecar freshness check | Caught — this one worked |

### The three root patterns

1. **Checks that never ran.** #2, #3, #6 all had working detectors. Review agents fire *only*
   inside `_run_with_review()`, which only the orchestrator calls. Every episode here was built
   with direct script calls, so **zero reviews ran**. `episode_state.json` is the tell: no state
   file means the project has never been through the orchestrator.
2. **Advisory instead of blocking.** `_review_voice` returns `passed=True` unconditionally and
   floors its score at 7. Even had it run, it would have printed the NEET-UG defect and continued.
3. **Trusting an upstream artifact instead of ground truth.** Captions were trusted to match the
   audio; the manifest was trusted to record the voice; Whisper's timestamps were trusted to mark
   the end of speech. Each was wrong. The script and the rendered audio are the only ground truth.

---

## 2. The contract

Each stage declares postconditions that are **blocking**, checked against **ground truth**, and
runnable **standalone** — the last point is essential, because stages here are routinely run by
hand, and a contract that only the orchestrator enforces is a contract that does not exist.

    python verify_stage.py --project <p> --stage <s>     # exit 0 = contract met

| Stage | Must be true before the next stage runs |
|---|---|
| `script` | No `TITLE:` line survives into what TTS receives. Word count 1,800–3,200. No banned words. |
| `voice` | `narration.mp3` exists; duration 8–20 min; `.voice.json` **matches `channel_config.json`'s active voice** (closes #9). Whisper transcript vs script: no substantive mismatch (closes #2, #3). |
| `split` | Scene count ≥ 50; no duplicate IDs; every scene has an audio clip. **Every clip contains its script line's last word** (closes #1) and **does not contain the next scene's first word** (closes #10). Manifest text vs script diff clean (closes #6). |
| `prompts` | One prompt per scene/group; every `TYPE` valid; `MAP_ARGS` present for MAP. |
| `images` | Every scene resolves to an image ≥1280×720. No scene silently falls back. |
| `stitch` | `abs(video_duration − audio_duration) < 0.5s` (closes #5). Final render's Whisper transcript vs script: no substantive mismatch — **run on the finished MP4, not the raw narration** (closes #1, #2, #10). |

### Rules the contract encodes

- **Verify the artifact you shipped, not the one you meant to ship.** Transcribe the final MP4.
  Every defect above would have been caught by that single check.
- **Ground truth is the script and the rendered audio.** Never assert one derived artifact against
  another derived artifact.
- **Prefer explicit over discovered.** No `glob(...)[-1]` or `sorted(...)[0]` to choose an input
  file — name it, or take it from `episode_state.json` (#7, #8).
- **A silent fallback is a defect.** If a check degrades (no silences detected, whisper missing),
  say so loudly. Falling back quietly to known-broken behaviour is worse than failing.
- **Blocking by default.** "Advisory" checks are checks nobody reads.

---

## 3. Status

Implemented: `voice` sidecar-vs-config check is *specified but not yet wired* (the CTA equivalent
exists — mirror it). `split` truncation/bleed validation exists as a working script from the
2026-07-31 session and should be promoted into `_review_split`. Stitch duration invariant is
checked by hand today.

Not yet built: `verify_stage.py` as a single standalone entry point. That is the piece that makes
this a contract rather than a wish, because it works whether or not the orchestrator is used.
