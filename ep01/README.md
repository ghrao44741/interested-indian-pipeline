# ep01
"Why South Indian States Collect More Tax Than They Get Back"

## Folder structure
- `source_audio/` — full-episode voiceover goes here (output of generate_source_audio.py)
- `audio/`        — per-scene cut audio clips (output of auto_split_scenes_v1_stage3_export.py)
- `images/`       — one image per SCENE-XXX id (or per visual_group_id if scenes are grouped)
- `videos/`       — image-to-video clips per scene
- `output/`       — final stitched video (output of stitch_video.py)
- `script_south_india_tax_devolution.txt` — Stage 2 narration script
- `manifest.json` and `timestamped_script.txt` — created once auto_split_scenes runs (not yet present)

## Pipeline order for this episode
Scripts live in `C:\Bakcup_Asus\Aeonium_Glow\shorts_pipeline2\` — see the
top-level `README.md` in `interested_indian_pipeline\` for the full path
convention (this folder sits as a sibling to `Aeonium_Glow\`, so it's
**two** levels up from `shorts_pipeline2\`, not one). Run from inside
`shorts_pipeline2\`:

1. Pick a voice: `python generate_source_audio.py --list-voices`
2. Preview candidates: `python generate_source_audio.py --project ..\..\interested_indian_pipeline\ep01 --script ..\..\interested_indian_pipeline\ep01\script_south_india_tax_devolution.txt --voice <candidate> --preview 2`
3. Full voiceover: `python generate_source_audio.py --project ..\..\interested_indian_pipeline\ep01 --script ..\..\interested_indian_pipeline\ep01\script_south_india_tax_devolution.txt --voice <chosen> --out narration.mp3`
4. Split into scenes: `python auto_split_scenes_v1_stage3_export.py --audio narration.mp3 --project ..\..\interested_indian_pipeline\ep01 --video-type LongVideo --fragment-max-seconds 6`
5. Paste `timestamped_script.txt` into chat for Stage 3 (image prompts + editing cues)
6. Generate images (Google Flow) → save into `images\` named per scene/group id
7. Image-to-video (Meta AI / Grok) → save into `videos\`
8. `python stitch_video.py --project ..\..\interested_indian_pipeline\ep01 --video-type LongVideo`

