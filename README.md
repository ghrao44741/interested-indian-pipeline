# Interested Indian — Video Pipeline

Separate project from That's Why / Aeonium Glow, but **shares the same
underlying pipeline scripts** that live in `shorts_pipeline2/` (a sibling
folder, not inside this one). This folder holds only:

- Per-episode content (script, source audio, generated assets, output)
- Anything specific to this channel's brand/format

It does NOT hold its own copies of `auto_split_scenes*.py`,
`generate_source_audio.py`, `stitch_video.py`, etc. Those stay in
`shorts_pipeline2/` as the single source of truth, so a fix or feature
added there (like the Stage 3 timestamped-script export) benefits every
channel immediately, instead of drifting across duplicated copies.

## Assumed layout

```
parent-folder/
├── shorts_pipeline2/              <- pipeline scripts (unchanged, shared)
│   ├── auto_split_scenes_v1_stage3_export.py
│   ├── generate_source_audio.py
│   ├── stitch_video.py
│   ├── generate_cta_card.py
│   └── generate_audio.py          <- Aeonium/That's Why specific (manifest-driven)
└── interested_indian_pipeline/    <- this folder
    ├── ep01/
    ├── ep02/
    └── ...
```

If your actual folder names differ, adjust the relative paths (`../shorts_pipeline2/...`)
in the commands below accordingly.

## Running pipeline scripts against an episode here

Since scripts are invoked from `shorts_pipeline2/` but need to operate on
a project folder that lives *outside* it, point `--project` at a relative
path that reaches back out to this folder:

```
cd shorts_pipeline2

python generate_source_audio.py --list-voices
python generate_source_audio.py --project ../interested_indian_pipeline/ep01 \
    --script ../interested_indian_pipeline/ep01/script_south_india_tax_devolution.txt \
    --voice en-US-AndrewNeural --preview 2

python generate_source_audio.py --project ../interested_indian_pipeline/ep01 \
    --script ../interested_indian_pipeline/ep01/script_south_india_tax_devolution.txt \
    --voice en-US-AndrewNeural --out narration.mp3

python auto_split_scenes_v1_stage3_export.py --audio narration.mp3 \
    --project ../interested_indian_pipeline/ep01 \
    --video-type LongVideo --fragment-max-seconds 6
```

All the pipeline scripts already write output relative to `--project`, so
this works without any code changes — it's purely a matter of which path
you pass.

## Git conventions for this repo

- **Tracked:** README files, narration scripts (`script_*.txt`),
  `manifest.json`, `timestamped_script.txt` — anything text-based that
  represents a decision or piece of content worth having history on.
- **Ignored** (see `.gitignore`): everything in `audio/`, `source_audio/`,
  `images/`, `videos/`, `output/` — generated binary media. Regenerable
  from the tracked files plus the pipeline scripts, and not something git
  handles well at scale. Back these up separately if you want them kept
  (e.g. a synced Drive folder) rather than committing them.
- **Commit granularity:** one commit per meaningful pipeline milestone
  per episode is a reasonable default — e.g. "ep01: add script",
  "ep01: manifest + scene split", "ep01: final voice selected" — rather
  than one commit per tiny file edit. Keeps `git log` readable as a
  production history across episodes.
- **Branching:** probably unnecessary at this scale (one person, mostly
  linear episode-by-episode work) unless you start experimenting with
  alternate cuts of the same episode — in which case a short-lived branch
  per experiment, merged or discarded, keeps `main` as the always-good
  version.
