# Image Feedback Loop — `--feedback-loop` and `--consume-feedback`

`review_images.py` can open a local, visual review session for any WARN/FAIL
shots instead of leaving you to interpret a text-only markdown report. You
click the exact spot on the image that's wrong, type a note, and the
pipeline regenerates just that shot from your feedback.

This is built on the shared **Creative Feedback Loop** tool
(`C:\Bakcup_Asus\shared-tools\creative-feedback-loop\creative_feedback_loop.py`
— see its own README for the general-purpose tool, reusable in any project).
This document is specifically about how `review_images.py` uses it.

## Why two separate commands

The feature is split across **two independent processes** that never talk to
each other directly — they only communicate by reading and writing the same
`{project}/cfl_review/session.json` file on disk:

- **`--feedback-loop`** — reviews the flagged shots, opens the visual review
  page in your browser, and then does nothing else but serve that page. It
  never generates an image and never spawns a subprocess.
- **`--consume-feedback`** — watches that same session file for feedback and
  does the actual regeneration work. It never runs a web server and has no
  socket of its own.

This is a deliberate separation of concerns: a crash or slowdown in image
generation can't take down your review page, and vice versa. Run them in two
separate terminals (or two separate background processes).

## Usage

**Terminal 1 — open the review page:**
```bash
python review_images.py --project pilot_neet_scandal --feedback-loop
```
This runs the normal Haiku/Sonnet review pass, and if anything comes back
WARN or FAIL, publishes those shots into a Creative Feedback Loop session and
opens your browser to it. Each candidate's note is pre-filled with the AI
reviewer's own verdict, so you're not starting from a blank page. **This
command blocks on purpose** (Ctrl+C to stop) — the server lives on a thread
inside this process, so it dies the instant the process exits.

**Terminal 2 — watch for your feedback and regenerate:**
```bash
python review_images.py --project pilot_neet_scandal --consume-feedback
```
This loads the *existing* session from disk (it does not start a new review
or a new server) and polls quietly. **This command also blocks** — leave it
running alongside terminal 1 for as long as you're reviewing.

**In the browser:**
1. Click directly on the part of an image that's wrong and type what needs
   to change. You can drop multiple pins on one image, or just add a
   whole-candidate note if there's nothing specific to point at.
2. Press **Generate next batch**. Terminal 2 picks this up within a few
   seconds, regenerates *only* the shots that actually got feedback (a shot
   with no new note, or marked Winner with nothing added, is left untouched
   — this is not a wholesale re-render of everything), and publishes the
   corrected image(s) as a new batch tab.
3. Repeat as many rounds as you need. Every earlier batch stays available in
   its own tab.
4. When a shot looks right, click **Winner** on it, then **Send winner &
   wrap up**. Terminal 2 detects this, marks the session closed, and exits.
   The page becomes read-only.

## What actually happens on "Generate next batch"

For each candidate in the batch you just gave feedback on:

- **Has a pin and/or a note?** → regenerated. The original PROMPT text for
  that shot (from `image_prompts_one_line_per_prompt.md`) gets your feedback
  appended to it (pin coordinates are translated into a rough spatial
  description — "near the top-left of the image: ...", since there's no way
  to hand an image model literal x/y coordinates), then it's passed to
  `generate_images_flux.py --shot N --overwrite --prompt-override "<original
  prompt + your feedback>"` as a subprocess call, followed by
  `add_text_overlays.py` to re-burn the overlay text. Same filename,
  overwritten in place.
- **Marked Winner with no new note, or no feedback at all?** → left alone.
  It simply doesn't appear in the next batch; its history stays visible in
  the earlier batch tab.

## Known limitations

- **Solo review only** — same as the underlying tool: no shareable external
  links, this is a single-machine, single-reviewer loop.
- **`--shot N` scopes both commands** to one shot — useful for testing a fix
  on a single flagged image, but for a real multi-shot review just omit it
  so the full flagged set gets published/watched.
- **The two processes must point at the same `--project`.** `--consume-feedback`
  will refuse to start if `{project}/cfl_review/session.json` doesn't exist
  yet — run `--feedback-loop` first.
- **Restarting `--consume-feedback` mid-review is safe** — it's stateless
  beyond the session file itself, so killing and re-running it just picks up
  wherever `session.json` currently stands.
