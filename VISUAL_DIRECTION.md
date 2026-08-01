# Visual Direction — acting on the two reviews

## How to read two overlapping reviews

They look contradictory and are not.

- **Review 1** says the character dominates and should drop to ~25–30% of screen time.
- **Review 2** says the character is the channel's strongest asset and deserves heavy investment.

Both are right, and together they say one thing: **fewer character shots, each one perfectly
consistent.** Today it is the inverse — the character is in 89% of shots and changes appearance
between them, which is the worst of both. Cutting his share *and* locking his design are the same
project, not competing ones.

The sorting rule used below: **systemic** (fix once in the pipeline, every future episode benefits),
**per-episode** (content work, repeated each time), **strategy** (the user's call, no code).
Both reviews also agree on one thing explicitly — *do not rebuild the pilot*. Lock it and publish.

---

## Where we actually are (measured, `pilot_neet_scandal`, 116 shots)

| Visual type | Pilot | Review 1 target | Gap |
|---|---:|---:|---|
| CARTOON (character/reenactment) | **89%** (103) | 25–30% | 3× too high |
| PHOTO (Pexels) | 5% (6) | 20–25% | 4× too low |
| CHART (cards/timelines) | 6% (7) | 15% | 2× too low |
| MAP | **0%** (0) | 10% | unused |
| DOCUMENT (notices, headlines) | — | 15–20% | type does not exist |

**The important finding: this is not an infrastructure gap.** `route_images.py` already dispatches
four types, and `generate_india_map.py` (real GeoJSON, never AI), `generate_chart.py` (bar,
timeline, stat, pie) and `search_pexels.py` all work. `generate_image_prompts.py` simply chooses
CARTOON almost every time. Closing most of the gap is a prompt-and-quota change, not new tooling.

Zero maps is the sharpest miss: a story about a paper leaking across Rajasthan and Bihar, with
protests in Delhi, Pune, Bengaluru and Lucknow, and we own an accurate map renderer that went unused.

---

## A. Systemic — do once, every future episode benefits

| # | Change | Why | Cost |
|---|---|---|---|
| A1 | **Type quotas in `generate_image_prompts.py`** — target CARTOON ≤35%, PHOTO ≥20%, CHART ≥12%, MAP ≥8% | Closes most of the mix gap with existing generators | Low |
| A2 | **Contract check for the mix** — `_review_prompts` fails when CARTOON exceeds its share | Otherwise the quota silently drifts back, exactly like every other unenforced check this pipeline has had | Low |
| A3 | **No three consecutive CARTOON shots** — enforce in the prompt stage | Review 1's single most concrete anti-"AI feel" rule; mechanically checkable | Low |
| A4 | **Character pose library (Review 2, Level 1)** — ~25 approved transparent PNGs, composited over generated backgrounds | Removes per-scene character regeneration, which is the root of the drift | Medium, one-off |
| A5 | **Locked identity prompt block** — immutable spec + explicit allowed/forbidden changes, prepended to every character generation | We have `mascot_reference.png` and an AIBMM session id, but no immutable text spec | Low |
| A6 | **Character QC in `review_images.py`** — check glasses/hair/proportions against the canonical sheet, reject on 2+ identity mismatches | We already run vision QA; this is a rubric change | Low |
| A7 | **New DOCUMENT type** — render NTA notices, headlines, court lines as styled cards with source + date | The only genuinely missing generator. Buildable on PIL like `add_text_overlays.py` | Medium |
| A8 | **Per-type motion** — maps get a push-in to the named state, cards get sequential reveal, photos get a slow drift | Currently every shot gets the same 1.05 Ken Burns, which is a real part of the sameness | Medium |
| A9 | **Chapter markers every 60–90s** — `generate_chapters.py` already exists and has never been run | Directly addresses the retention note | Low |
| A10 | **Editorial guardrails in CHANNEL_DNA + `review_script.py`** — require "alleged"/attribution near charge/arrest/conviction words | Charges are not convictions; this is credibility and legal risk, not style | Low |
| A11 | **Target 9–11 min** in the script stage word-count band (currently 1,800–3,200 words) | Both reviews flag 14 min as ambitious for a new channel | Low |

**A4 is the highest-leverage item.** Roughly 25 poses generated once, reused across every episode —
after two or three videos the library covers most needs and character generation nearly stops.

---

## B. This episode — small, targeted, not a rebuild

Already fixed this session: last-word truncation, −21.2 → −14.3 LUFS, 483 → 914 kbps, caption size
28 → 52, caption mis-transcriptions.

| # | Change | Effort | Recommend |
|---|---|---|---|
| B1 | **Shorten the CTA** — 9.07s static subscribe → ~3s, preceded by a closing question | New `cta.mp3` + re-stitch, ~15 min | **Yes** |
| B2 | **Chapter markers** in the description | Minutes — `generate_chapters.py` | **Yes** |
| B3 | **Sources in the description** — NTA notice, CBI/Reuters reporting, plus the BGM attribution already owed | Minutes, no re-render | **Yes** |
| B4 | **Swap ~6–10 middle-section CARTOON shots for MAP/CHART/PHOTO** | Regenerate those shots + re-stitch, ~1 hr | **Yes** — biggest visible gain per hour |
| B5 | **"22.79 lakh registered / 22+ lakh appeared"** | Script edit → re-narrate → re-split → image remap, ~1 hr, and re-splitting risks new scene boundaries | Judgement call — see below |
| B6 | **"Alleged"/attribution pass on the script** | Same cost as B5, and shares the re-narration | **Yes, bundle with B5** |
| B7 | Character consistency in existing shots | Would mean regenerating most of 132 images | **No** — that is a rebuild; fix from the next episode |

**On B5/B6:** both need re-narration, so do them together or not at all. The factual correction is
worth it if the number is wrong, and the attribution pass matters more than it looks — the episode
names a real, currently-charged individual, and charges are not convictions. If you want a single
recommendation: do B5+B6 as one pass, since the marginal cost of the second is zero.

---

## C. Strategy — your call, no code

- **Repositioning to evergreen "how India's systems work"** (70/20/10 mix). Review 1's structural
  point is stronger than its production notes: breaking news has a short earning window and a
  punishing cadence. The pilot's own topic list already suggests the evergreen version
  ("How exam paper leaks actually work", "Why India has so few medical seats").
- **Character age 10–14 → 20–24.** Review 2 is right that this must be decided *now*, before a pose
  library is built, because the library is the expensive artifact. Note the tension: the younger
  design is more distinctive and more appropriate for a student-exam story; the older reads better
  for political and economic explainers. This is a brand decision, not a technical one.
- **Publish cadence** — both reviews say ship 3–5 videos before judging results.

---

## What I would not do yet

- **LoRA training (Review 2, Level 3).** Review 2 itself says start at Levels 1–2. A LoRA before the
  design is final trains in the wrong character.
- **Three-layer background/character/prop generation for parallax.** Real technique, but it triples
  generation and compositing work for motion our stitch cannot yet drive per-layer. Revisit after A8.
- **Forcing 8–12 Mbps export.** Sensible for live-action; flat cartoon art at CRF 18 is already
  near-transparent at ~900 kbps, and forcing 8 Mbps mostly pads bits YouTube discards. One-line
  change (`DELIVERY_CRF`) if wanted.

---

## Suggested order

1. **B1–B3** (an hour, no re-render risk) → pilot is publishable.
2. **Decide the character age** — blocks A4/A5.
3. **A1–A3** (quotas + checks) → the next episode's mix is right by construction.
4. **B4** if the pilot is worth another hour before publishing.
5. **A4–A6** (pose library + lock + QC) → the "AI feel" fix that compounds.
6. **A7–A11** as capacity allows.
