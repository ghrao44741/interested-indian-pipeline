# The Interested Indian — Channel DNA

<!-- GENERATED from channel.json by render_channel_dna.py. Do not edit:
     edits are detected and refused when the channel loads. -->

- **channel id**: `interested_indian`
- **DNA version**: 1
- **language**: English

## Promise

Why India works the way it does — one exam, city, industry, policy or social contradiction at a time

## Audience

- **primary**: English-speaking Indians and the diaspora
- **secondary**: Globally curious viewers, including Americans

## Editorial

Tone: curious, witty, skeptical, evidence-first

- Original, human-reviewed documentary and explainer work.
- Never use a policy term without immediately translating it.
- Explain the system, not just the event.
- A claim without a source is not a claim.

### Structural inspiration — Just a FLAM

Structural inspiration only — pacing, essay shape, the habit of following a system to its consequence.

Never:
- Copying the character or any part of its design
- Copying titles, wording or phrasing
- Copying jokes or bits
- Copying thumbnails or any visual asset

## Narrative structure

1. Present a surprising contradiction.
2. Explain the historical cause.
3. Explain the system or incentive.
4. Show the present-day consequence.
5. Return to and resolve the opening question.

## Content portfolio

Editorial planning guidance across the slate, not a mechanical per-episode quota. Relevance always outranks the split.

| share | area |
|---|---|
| 60% | Economics, technology, education, infrastructure and institutions |
| 25% | History, geography, cities, states and culture |
| 15% | Scandals, corruption and active controversies |

## Host

- **enabled**: yes
- **kind**: Fictional illustrated presenter
- **never a real expert**: yes
- **target presence**: 25–30% (soft ceiling 35%)
- **max consecutive appearances**: 2
- **used for**: hooks, transitions, explanations, reactions, conclusions
- **not used for**: the primary evidence visual
- **character spec**: `character/character_spec.json` (legacy_pipeline_root)

## Voice

- **selection**: `pending`
- **approved profile**: none on file
- **working default (unapproved)**: edge / en-IN-PrabhatNeural
- while pending, synthesis may only write into `voice_previews/` (legacy_pipeline_root)
- Natural international English.
- Correct pronunciation of Indian names and terms.

## Visual style

| token | colour |
|---|---|
| cream | `#FAF7F2` |
| crimson | `#8B0000` |
| forest | `#1E4D2B` |
| navy | `#1A2B4C` |

Letterbox padding: `#1A2B4C`

- Flat digital cartoon style; no photorealistic rendering of illustrated scenes.
- Accurate geography is rendered, never drawn by a generative model.
- Text inside an image is rendered deterministically, never generated.

## Renderer capabilities

| visual type | renderer |
|---|---|
| CHART | `matplotlib_chart` |
| DOCUMENT | `deterministic_document` |
| HOST_COMPOSITE | `approved_pose_compositor` |
| ILLUSTRATION | `flux_illustration` |
| MAP | `india_geojson` |
| PHOTO | `pexels` |
| REENACTMENT | `flux_reenactment` |
| TIMELINE | `deterministic_timeline` |

## Evidence

- provenance required for: PHOTO, DOCUMENT
- reconstruction label: “Reconstruction — not the original document”
- Deterministic layout naming its source. Never an AI-generated facsimile of a real document.

## Safety

- Attribute allegations to whoever made them.
- Distinguish allegation, charge and conviction.
- Do not trivialise victims.
- The fictional host is never presented as a health, legal, financial or political adviser.
- Preserve source and reconstruction labels.

Human editorial review required for: sensitive claims, named individuals, active legal proceedings.

## Economics

- currency: USD
- no per-image price on file — plans report cost as unpriced rather than estimating one

No verified per-image price is on file. plan_visuals.py reports 'not priced' rather than an invented figure; add an episode_shot entry only from a real invoice.
