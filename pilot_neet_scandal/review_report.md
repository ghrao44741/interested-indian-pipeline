# Image Review Report — ep01
**Generated:** 2026-07-28 17:06
**Model:** claude-sonnet-5
**Images reviewed:** 116 / 116  (all present)

## Summary
✓ PASS : 97
⚠ WARN : 8
✗ FAIL : 11
— SKIP : 0  ← image not found in images/
💥 ERROR: 0

---

## Results

| Shot | File | Verdict | Style | Content | Overlay | Ratio | Artifacts | Typos | On-topic | Watermark | Notes |
|------|------|---------|-------|---------|---------|-------|-----------|-------|----------|-----------|-------|
| SHOT 11 | `SCENE-012.png` | ⚠ WARN | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Intended visual called for three blocked paths (calendar, crossed-out rupee, closed door) but only two crossroads are shown, with the coin and door icons merged onto a single right-hand path. |
| SHOT 18 | `SCENE-019.png` | ⚠ WARN | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Matches intended visual well (mascot, papers vs money scale tipping right, worried expression); 'RESERVE...OF INDIA' text on notes is a bit blurry/illegible but not a confirmed typo. |
| SHOT 24 | `SCENE-026.png` | ⚠ WARN | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | A large 'The Interested Indian' title is rendered directly into the illustration itself (not just the small bottom-right badge), which counts as a hallucinated title artifact; otherwise the cartoon accurately shows the exhausted runner reacting to the rocket-shoe runner zooming past. |
| SHOT 33 | `SCENE-037.png` | ✗ FAIL | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | Large hallucinated 'The Interested Indian' title text is rendered prominently in the illustration itself, taking up a significant portion of the frame, distinct from the small correct channel badge in the bottom-right corner. |
| SHOT 37 | `SCENE-041.png` | ✗ FAIL | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | A large hallucinated title card 'The Interested Indian' is rendered across the top of the illustration, which the prompt explicitly forbids; otherwise mascot, globe with India, building, stars, and trophy match the intended visual well. |
| SHOT 38 | `SCENE-042.png` | ✗ FAIL | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | A large hallucinated title 'The Interested Indian' is rendered as a heading across the top of the illustration, which the style guide explicitly forbids in the generated image (overlay text/badges are handled separately); otherwise the mascot, scroll, and 2017 calendar match the intended visual well. |
| SHOT 45 | `SCENE-053.png` | ⚠ WARN | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | Checklist board repeats the nonsensical label 'in progress.' on every single line (even beside checkmarks meant to indicate completion), which looks like a generation artifact rather than meaningful list items, though otherwise the mascot, minister figure, podium, and Supreme Court icon match the intended cartoon scene. |
| SHOT 52 | `SCENE-060.png` | ✗ FAIL | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✓ | ✓ | A large hallucinated title text ('The Interested Indian' repeated oddly with a duplicate 'Indian' below) is baked into the illustration itself, which is a generation artifact/typo issue despite the mascot and scene elements matching the intended visual well. |
| SHOT 58 | `SCENE-067.png` | ⚠ WARN | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Mascot is not shown wearing a judge's black robe as described, otherwise matches the intended shocked expression and cockroach speech bubble concept well. |
| SHOT 63 | `SCENE-076.png` | ✗ FAIL | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ | ✓ | ✓ | A large hallucinated 'The Interested Indian' title card is rendered at the top of the illustration, and faint garbled duplicate text ('millions of followers', 'millions of foanifesto') is baked into the image behind the people icons, both of which are generation artifacts that should not appear in the raw illustration. |
| SHOT 66 | `SCENE-082.png` | ✗ FAIL | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | Mascot and composition match intended visual well, but most floating profile icon labels are garbled AI-hallucinated gibberish text rather than real words, which is a clear generation artifact/typo issue. |
| SHOT 67 | `SCENE-083.png` | ⚠ WARN | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Large title text 'The Interested Indian' at top is a big hallucinated heading rather than the small corner badge, otherwise matches intended visual well. |
| SHOT 68 | `SCENE-084.png` | ✗ FAIL | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | Content matches intended visual (mascot pointing at sashed cockroach logo with crowd stacks) but a large hallucinated title 'The Interested Indian' is rendered across the top of the illustration itself, which should not be baked into the generated image. |
| SHOT 73 | `SCENE-090.png` | ⚠ WARN | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Matches intended visual well with mascot pointing at school and climate protest panels, but the protest signs read 'CLIMATE ACTION NOW' and 'SAVE OUR PLANET' which are generic phrasing not tied specifically to Ladakh as narration implies, otherwise minor. |
| SHOT 76 | `SCENE-093.png` | ✗ FAIL | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | A large hallucinated 'The Interested Indian' title heading is rendered directly into the illustration at the top of the frame, which is not appropriate for this shot's cartoon content and duplicates the intentional small bottom-right channel badge. |
| SHOT 90 | `SCENE-108.png` | ✗ FAIL | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | This is labeled a PHOTO shot requiring a real photorealistic stock photograph, but the image is a fully cartoon-illustrated scene with a mascot, cartoon kids, and stylized web lines, violating the required photorealism for this shot type. |
| SHOT 94 | `SCENE-114.png` | ✗ FAIL | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | The monument depicted is a domed mausoleum/tomb-style structure with minarets and staircases, not the real Jantar Mantar (which consists of large geometric stone astronomical instruments like sundials) — a clear factual mismatch with the required landmark despite the mascot and collage composition otherwise matching the intended style. |
| SHOT 105 | `SCENE-126.png` | ✗ FAIL | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | Large hallucinated 'The Interested Indian' title text spans the top of the frame like a title card, which violates the no-large-title rule; otherwise mascot, calendars, arrow, and gap concept match the intended visual well. |
| SHOT 114 | `SCENE-136.png` | ⚠ WARN | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ | Stray debug label 'SHOT 114' visible in top-right corner is an unintended artifact; otherwise mascot, two report documents with distinct portraits, and checkmarks match the intended visual well. |

---

## Issues requiring attention

### ⚠ SHOT 11 · `SCENE-012.png` · WARN
**Notes:** Intended visual called for three blocked paths (calendar, crossed-out rupee, closed door) but only two crossroads are shown, with the coin and door icons merged onto a single right-hand path.
  ✓ Style  ·  ✗ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✓ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ⚠ SHOT 18 · `SCENE-019.png` · WARN
**Notes:** Matches intended visual well (mascot, papers vs money scale tipping right, worried expression); 'RESERVE...OF INDIA' text on notes is a bit blurry/illegible but not a confirmed typo.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✓ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ⚠ SHOT 24 · `SCENE-026.png` · WARN
**Notes:** A large 'The Interested Indian' title is rendered directly into the illustration itself (not just the small bottom-right badge), which counts as a hallucinated title artifact; otherwise the cartoon accurately shows the exhausted runner reacting to the rocket-shoe runner zooming past.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ✗ SHOT 33 · `SCENE-037.png` · FAIL
**Notes:** Large hallucinated 'The Interested Indian' title text is rendered prominently in the illustration itself, taking up a significant portion of the frame, distinct from the small correct channel badge in the bottom-right corner.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ✗ SHOT 37 · `SCENE-041.png` · FAIL
**Notes:** A large hallucinated title card 'The Interested Indian' is rendered across the top of the illustration, which the prompt explicitly forbids; otherwise mascot, globe with India, building, stars, and trophy match the intended visual well.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ✗ SHOT 38 · `SCENE-042.png` · FAIL
**Notes:** A large hallucinated title 'The Interested Indian' is rendered as a heading across the top of the illustration, which the style guide explicitly forbids in the generated image (overlay text/badges are handled separately); otherwise the mascot, scroll, and 2017 calendar match the intended visual well.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ⚠ SHOT 45 · `SCENE-053.png` · WARN
**Notes:** Checklist board repeats the nonsensical label 'in progress.' on every single line (even beside checkmarks meant to indicate completion), which looks like a generation artifact rather than meaningful list items, though otherwise the mascot, minister figure, podium, and Supreme Court icon match the intended cartoon scene.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ✗ SHOT 52 · `SCENE-060.png` · FAIL
**Notes:** A large hallucinated title text ('The Interested Indian' repeated oddly with a duplicate 'Indian' below) is baked into the illustration itself, which is a generation artifact/typo issue despite the mascot and scene elements matching the intended visual well.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✗ No typos  ·  ✓ On-topic  ·  ✓ No watermark
**Typos found:** Large hallucinated title 'The Interested Indian / Indian' rendered into the illustration with awkward duplicate 'Indian' text, taking up top portion of frame

### ⚠ SHOT 58 · `SCENE-067.png` · WARN
**Notes:** Mascot is not shown wearing a judge's black robe as described, otherwise matches the intended shocked expression and cockroach speech bubble concept well.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✓ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ✗ SHOT 63 · `SCENE-076.png` · FAIL
**Notes:** A large hallucinated 'The Interested Indian' title card is rendered at the top of the illustration, and faint garbled duplicate text ('millions of followers', 'millions of foanifesto') is baked into the image behind the people icons, both of which are generation artifacts that should not appear in the raw illustration.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✗ No typos  ·  ✓ On-topic  ·  ✓ No watermark
**Typos found:** Faint garbled background text reading 'millions of followers' (duplicated) and 'millions of foanifesto' baked into the illustration — should not be present as these are meant to be added in post; 'foanifesto' is garbled/misspelled.

### ✗ SHOT 66 · `SCENE-082.png` · FAIL
**Notes:** Mascot and composition match intended visual well, but most floating profile icon labels are garbled AI-hallucinated gibberish text rather than real words, which is a clear generation artifact/typo issue.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✓ No artifacts  ·  ✗ No typos  ·  ✓ On-topic  ·  ✓ No watermark
**Typos found:** Silly Street→unclear/garbled | Frolent→garbled nonsense word | Mamerd→garbled nonsense word | Sutmingr→garbled nonsense word | Komigint→garbled nonsense word | Sumergr→garbled nonsense word | Nangern→garbled nonsense word

### ⚠ SHOT 67 · `SCENE-083.png` · WARN
**Notes:** Large title text 'The Interested Indian' at top is a big hallucinated heading rather than the small corner badge, otherwise matches intended visual well.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✓ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ✗ SHOT 68 · `SCENE-084.png` · FAIL
**Notes:** Content matches intended visual (mascot pointing at sashed cockroach logo with crowd stacks) but a large hallucinated title 'The Interested Indian' is rendered across the top of the illustration itself, which should not be baked into the generated image.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ⚠ SHOT 73 · `SCENE-090.png` · WARN
**Notes:** Matches intended visual well with mascot pointing at school and climate protest panels, but the protest signs read 'CLIMATE ACTION NOW' and 'SAVE OUR PLANET' which are generic phrasing not tied specifically to Ladakh as narration implies, otherwise minor.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✓ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ✗ SHOT 76 · `SCENE-093.png` · FAIL
**Notes:** A large hallucinated 'The Interested Indian' title heading is rendered directly into the illustration at the top of the frame, which is not appropriate for this shot's cartoon content and duplicates the intentional small bottom-right channel badge.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ✗ SHOT 90 · `SCENE-108.png` · FAIL
**Notes:** This is labeled a PHOTO shot requiring a real photorealistic stock photograph, but the image is a fully cartoon-illustrated scene with a mascot, cartoon kids, and stylized web lines, violating the required photorealism for this shot type.
  ✗ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✓ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ✗ SHOT 94 · `SCENE-114.png` · FAIL
**Notes:** The monument depicted is a domed mausoleum/tomb-style structure with minarets and staircases, not the real Jantar Mantar (which consists of large geometric stone astronomical instruments like sundials) — a clear factual mismatch with the required landmark despite the mascot and collage composition otherwise matching the intended style.
  ✓ Style  ·  ✗ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✓ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ✗ SHOT 105 · `SCENE-126.png` · FAIL
**Notes:** Large hallucinated 'The Interested Indian' title text spans the top of the frame like a title card, which violates the no-large-title rule; otherwise mascot, calendars, arrow, and gap concept match the intended visual well.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark

### ⚠ SHOT 114 · `SCENE-136.png` · WARN
**Notes:** Stray debug label 'SHOT 114' visible in top-right corner is an unintended artifact; otherwise mascot, two report documents with distinct portraits, and checkmarks match the intended visual well.
  ✓ Style  ·  ✓ Content  ·  ✓ Overlay  ·  ✓ Ratio  ·  ✗ No artifacts  ·  ✓ No typos  ·  ✓ On-topic  ·  ✓ No watermark
