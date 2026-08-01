"""
source_ids.py — persistent content identity for scenes and visual assets.

Why this exists
---------------
Artwork used to be keyed by `SCENE-NNN`, which is a *position*. Every
re-narration re-runs WhisperX, and any wording change renumbers the scenes — so
image 82 silently became the artwork for a different sentence. That happened
three times in one session (133 -> 132 -> 135 scenes), each time needing a manual
similarity remap to avoid shipping mismatched visuals.

Identity now comes from the script, before narration exists:

    SRC-001, SRC-002, ...      one per canonical script sentence ("source unit")
    SRC-001-S01, SRC-001-S02   shots derived from that unit

`SCENE-NNN` remains display order only and carries no identity.

Many-to-many by design
----------------------
The splitter cuts long sentences at commas and merges short ones, so a shot can
draw on several source units and a unit can span several shots. Every shot
therefore carries `source_ids` as a *list*, never a scalar.

Scenes are built from WhisperX's transcript of the audio, not from the script, so
the recognised words are aligned back to the canonical script here. Mishearings
("guest papers" for "guess papers") make an exact match impossible; alignment is
fuzzy on purpose, and anything it cannot place confidently is marked ambiguous
rather than silently bound to artwork.

The text fingerprint is for change *detection* only. It is never identity — an
intentional edit to a sentence keeps its SRC id and changes its fingerprint.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path

SIDECAR_NAME = "source_units.json"

# Confidence below which a shot's source attribution is not trusted.
AMBIGUOUS_BELOW = 0.55

# Abbreviations that end in a period but do not end a sentence. Without these,
# "1.3 lakh M.B.B.S. seats" and "Neet U.G. is moving to..." each split into
# several bogus units.
#
# The segment is [A-Z][a-z]? rather than [A-Z] so mixed-case abbreviations like
# "Ph.D." are protected too, not just all-caps runs like "M.B.B.S." and "U.G.".
# Requiring a leading capital keeps it from swallowing ordinary prose or decimals.
_ABBREV = r"(?:[A-Z][a-z]?\.){2,}|(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|approx|No)\."


def normalise(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace, for comparison only."""
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split())


def fingerprint(text: str) -> str:
    """Short stable hash of normalised text — change detection, NOT identity."""
    return hashlib.sha1(normalise(text).encode("utf-8")).hexdigest()[:12]


def _words(text: str) -> list[str]:
    return normalise(text).split()


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, keeping dotted abbreviations intact."""
    # Protect abbreviation periods, split, then restore.
    protected = re.sub(_ABBREV, lambda m: m.group(0).replace(".", "\0"), text)
    parts = re.split(r"(?<=[.!?])\s+", protected)
    out = []
    for p in parts:
        s = p.replace("\0", ".").strip()
        if s:
            out.append(s)
    return out


def build_source_units(script_text: str) -> list[dict]:
    """Canonical, ordered source units for a script. Ids are assigned once."""
    units = []
    for para_idx, para in enumerate(
            [p for p in script_text.split("\n") if p.strip()]):
        for sent in split_sentences(para):
            units.append({
                "id": f"SRC-{len(units) + 1:03d}",
                "paragraph": para_idx,
                "text": sent,
                "fingerprint": fingerprint(sent),
            })
    return units


# ── production script selection ───────────────────────────────────────────────

# Working copies that must never be mistaken for the production script.
SCRIPT_VARIANT_SUFFIXES = ("_previous", "_draft", "_draft_tagged", "_tagged",
                           "_old", "_backup", "_pre_b5b6")


def pick_production_script(project_dir: Path):
    """The project's real script, ignoring drafts and backups.

    Lives here rather than in pipeline_agents so the split stage can use it
    without importing the orchestrator (and its heavy deps). Callers used
    sorted(glob("script_*.txt"))[-1], which sorts 'script_x_PREVIOUS.txt' *after*
    'script_x.txt' — so the reviewers silently diffed against the previous draft.
    """
    matches = sorted(Path(project_dir).glob("script_*.txt"))
    if not matches:
        return None
    primary = [m for m in matches
               if not any(m.stem.lower().endswith(s) for s in SCRIPT_VARIANT_SUFFIXES)]
    if not primary:
        return matches[-1]
    chosen = min(primary, key=lambda m: len(m.stem))
    if len(primary) > 1:
        # Genuinely ambiguous — say so rather than pick silently. Callers that
        # know better (the orchestrator has episode_state's script_path) should
        # pass the script explicitly instead of relying on this.
        others = ", ".join(m.name for m in primary if m != chosen)
        print(f"  ⚠ {len(primary)} candidate scripts in {Path(project_dir).name}; "
              f"using {chosen.name} (also present: {others}). Pass --script to be explicit.")
    return chosen


# ── sidecar io ────────────────────────────────────────────────────────────────

def sidecar_path(project_dir: Path) -> Path:
    return Path(project_dir) / SIDECAR_NAME


def load_units(project_dir: Path) -> list[dict]:
    p = sidecar_path(project_dir)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("units", [])


def sync_units(project_dir: Path, script_text: str) -> tuple[list[dict], dict]:
    """Create or update the sidecar, preserving ids across script edits.

    Ids survive intentional wording changes: existing units are matched to new
    sentences in order by similarity, so an edited sentence keeps its id and
    reports a changed fingerprint. Genuinely new sentences get new ids. Nothing
    is renumbered.
    """
    new_units = build_source_units(script_text)
    old_units = load_units(project_dir)
    report = {"added": [], "changed": [], "removed": [], "unchanged": 0}

    if old_units:
        old_norm = [normalise(u["text"]) for u in old_units]
        used, cursor = set(), 0
        for unit in new_units:
            target = normalise(unit["text"])
            best_i, best_r = None, 0.0
            # monotonic window keeps matching stable on long scripts
            for i in range(cursor, min(cursor + 8, len(old_units))):
                if i in used:
                    continue
                r = difflib.SequenceMatcher(None, target, old_norm[i]).ratio()
                if r > best_r:
                    best_r, best_i = r, i
            if best_i is not None and best_r >= 0.75:
                unit["id"] = old_units[best_i]["id"]
                used.add(best_i)
                cursor = best_i + 1
                if old_units[best_i]["fingerprint"] != unit["fingerprint"]:
                    report["changed"].append(unit["id"])
                else:
                    report["unchanged"] += 1
            else:
                report["added"].append(unit["id"])
        report["removed"] = [u["id"] for i, u in enumerate(old_units) if i not in used]

    payload = {"version": 1, "units": new_units}
    sidecar_path(project_dir).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return new_units, report


# ── alignment ─────────────────────────────────────────────────────────────────

def align_scenes(scenes: list[dict], units: list[dict], text_key: str = "script") -> list[dict]:
    """Attach source_ids + shot ids to ASR-derived scenes.

    Builds one word stream per side and runs a single sequence alignment, so a
    scene that spans two units or a unit split across three scenes both fall out
    naturally — rather than assuming one unit equals one scene.

    Returns per-scene dicts: source_ids, shot_id, source_span, match_confidence,
    merged_source, source_match ("ok" | "ambiguous").
    """
    unit_words: list[str] = []
    word_owner: list[str] = []          # unit id per word position
    for u in units:
        w = _words(u["text"])
        unit_words.extend(w)
        word_owner.extend([u["id"]] * len(w))

    scene_words: list[str] = []
    scene_span: list[tuple[int, int]] = []
    for s in scenes:
        w = _words(s.get(text_key, ""))
        scene_span.append((len(scene_words), len(scene_words) + len(w)))
        scene_words.extend(w)

    matcher = difflib.SequenceMatcher(None, scene_words, unit_words, autojunk=False)
    # scene word index -> unit word index
    mapping: dict[int, int] = {}
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            mapping[a + k] = b + k

    per_unit_counter: dict[str, int] = {}
    results = []
    for idx, (lo, hi) in enumerate(scene_span):
        owners, matched = [], 0
        for i in range(lo, hi):
            j = mapping.get(i)
            if j is not None:
                matched += 1
                owner = word_owner[j]
                if owner not in owners:
                    owners.append(owner)
        total = max(hi - lo, 1)
        confidence = matched / total

        if owners:
            primary = owners[0]
            per_unit_counter[primary] = per_unit_counter.get(primary, 0) + 1
            shot_id = f"{primary}-S{per_unit_counter[primary]:02d}"
        else:
            primary, shot_id = None, None

        results.append({
            "source_ids": owners,
            "shot_id": shot_id,
            "source_span": [lo, hi],
            "match_confidence": round(confidence, 3),
            "merged_source": len(owners) > 1,
            "source_match": "ok" if owners and confidence >= AMBIGUOUS_BELOW else "ambiguous",
        })
    return results
