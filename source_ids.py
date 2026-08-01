"""
source_ids.py — persistent content identity for scenes and visual assets.

Why this exists
---------------
Artwork used to be keyed by `SCENE-NNN`, which is a *position*. Every
re-narration re-runs WhisperX, and any wording change renumbers the scenes — so
image 82 silently became the artwork for a different sentence. That happened
three times in one session (133 -> 132 -> 135 scenes).

Four distinct identities, deliberately not collapsed into one
-------------------------------------------------------------
    source_id         SRC-001     persistent script identity (survives re-splits)
    scene_id          SCENE-001   current audio/display order — transient
    shot_instance_id  SRC-001-I01 current split identity — transient, recomputed
    visual_asset_id   VIS-001-A   persistent approved-visual identity

Only `source_id` and `visual_asset_id` may key artwork. `shot_instance_id` is
recomputed from whatever the current audio boundaries happen to be, so it must
never be used to inherit approved work — an earlier version of this module used
`SRC-001-S01` suffixes for that and they silently referred to different words
after a re-split.

Many-to-many by design
----------------------
The splitter cuts long sentences at commas and merges short ones, so a shot can
draw on several source units and a unit can span several shots. Shots therefore
carry `source_ids` as a list.

Visual assets are anchored *within* a source unit by the text they were approved
against, so a re-split re-attaches them by overlap rather than by position. A
unit may legitimately own several visuals (a 30-second sentence should not hold
one image), and one visual may serve several audio scenes.

Scenes are built from WhisperX's transcript, not the script, so recognised words
are aligned back to the canonical script here. Mishearings ("guest papers" for
"guess papers") make exact matching impossible; alignment is fuzzy on purpose and
anything it cannot place confidently becomes NEEDS_REVIEW rather than being bound
to artwork.

The text fingerprint is change *detection* only — never identity. An intentional
edit keeps its SRC id and reports a changed fingerprint.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

SIDECAR_NAME = "source_units.json"
SIDECAR_VERSION = 2

# Alignment confidence below which a shot's source attribution is not trusted.
AMBIGUOUS_BELOW = 0.55
# Similarity below which a shot does not match any existing visual slot.
VISUAL_MATCH_MIN = 0.50
# Two slots scoring within this of each other are a tie — refuse to guess.
VISUAL_TIE_EPSILON = 0.05
# Similarity at/above which a new sentence is considered an edit of an old one.
UNIT_MATCH_MIN = 0.75

IDENTITY_OK = "ok"
IDENTITY_BLOCKED = "blocked"
NEEDS_REVIEW = "NEEDS_REVIEW"


class IdentityError(RuntimeError):
    """Raised when identity cannot be established safely."""


# Abbreviations that end in a period but do not end a sentence. Without these,
# "1.3 lakh M.B.B.S. seats" and "Neet U.G. is moving to..." each split into
# several bogus units. The segment is [A-Z][a-z]? so mixed-case abbreviations
# like "Ph.D." are protected too, not just all-caps runs. Requiring a leading
# capital keeps it from swallowing ordinary prose or decimals.
_ABBREV = r"(?:[A-Z][a-z]?\.){2,}|(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|approx|No)\."


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — comparison only."""
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower()).split())


def fingerprint(text: str) -> str:
    """Short stable hash of normalised text — change detection, NOT identity."""
    return hashlib.sha1(normalise(text).encode("utf-8")).hexdigest()[:12]


def _words(text: str) -> list[str]:
    return normalise(text).split()


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, keeping dotted abbreviations intact."""
    protected = re.sub(_ABBREV, lambda m: m.group(0).replace(".", "\0"), text)
    parts = re.split(r"(?<=[.!?])\s+", protected)
    return [p.replace("\0", ".").strip() for p in parts if p.strip()]


def _sentences_of(script_text: str) -> list[dict]:
    """Ordered sentences with paragraph index, no ids assigned yet."""
    out = []
    for para_idx, para in enumerate([p for p in script_text.split("\n") if p.strip()]):
        for sent in split_sentences(para):
            out.append({"paragraph": para_idx, "text": sent,
                        "fingerprint": fingerprint(sent)})
    return out


def build_source_units(script_text: str) -> list[dict]:
    """Fresh units with sequential ids — for a project with no prior sidecar."""
    units = []
    for i, s in enumerate(_sentences_of(script_text), 1):
        units.append({"id": f"SRC-{i:03d}", **s, "visuals": []})
    return units


# ── production script selection ───────────────────────────────────────────────

SCRIPT_VARIANT_SUFFIXES = ("_previous", "_draft", "_draft_tagged", "_tagged",
                           "_old", "_backup", "_pre_b5b6")


def candidate_scripts(project_dir: Path) -> list[Path]:
    """Production-script candidates, excluding drafts and backups."""
    matches = sorted(Path(project_dir).glob("script_*.txt"))
    primary = [m for m in matches
               if not any(m.stem.lower().endswith(s) for s in SCRIPT_VARIANT_SUFFIXES)]
    return primary or matches


def pick_production_script(project_dir: Path, strict: bool = False):
    """The project's real script.

    With `strict` (production), an ambiguous or missing choice raises rather than
    guessing — picking silently is how the reviewers ended up diffing against a
    previous draft. Callers that know better (the orchestrator has
    episode_state's script_path) should pass the script explicitly.
    """
    cands = candidate_scripts(Path(project_dir))
    if not cands:
        if strict:
            raise IdentityError(
                f"No canonical script found in {Path(project_dir).name}. Pass --script, "
                f"or --allow-missing-script to proceed without persistent identity.")
        return None
    if len(cands) > 1:
        names = ", ".join(c.name for c in cands)
        if strict:
            raise IdentityError(
                f"{len(cands)} candidate scripts in {Path(project_dir).name} ({names}). "
                f"Pass --script explicitly.")
        print(f"  ⚠ {len(cands)} candidate scripts ({names}) — using {cands[0].name}. "
              f"Pass --script to be explicit.")
    return cands[0]


# ── sidecar io ────────────────────────────────────────────────────────────────

def sidecar_path(project_dir: Path) -> Path:
    return Path(project_dir) / SIDECAR_NAME


def load_sidecar(project_dir: Path) -> dict:
    p = sidecar_path(project_dir)
    if not p.exists():
        return {"version": SIDECAR_VERSION, "next_seq": 1, "units": []}
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("units", [])
    # v1 sidecars had no allocation counter; derive one that cannot collide.
    data.setdefault("next_seq", _highest_seq(data["units"]) + 1)
    return data


def _seq_of(unit_id: str) -> int:
    m = re.search(r"(\d+)$", unit_id or "")
    return int(m.group(1)) if m else 0


def _highest_seq(units: list[dict]) -> int:
    return max((_seq_of(u.get("id", "")) for u in units), default=0)


def _write_atomic(path: Path, payload: dict) -> None:
    """Validate, then replace in one step.

    A half-written sidecar is worse than none: identity is what everything else
    keys off. Write to a temp file in the same directory and os.replace() it, so
    a crash mid-write leaves the previous sidecar intact.
    """
    ids = [u["id"] for u in payload["units"]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise IdentityError(f"refusing to write sidecar with duplicate ids: {dupes}")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def sync_units(project_dir: Path, script_text: str) -> tuple[list[dict], dict]:
    """Create or update the sidecar, preserving ids across script edits.

    Two passes, in this order, because doing it in one caused real collisions:
    every candidate is matched against existing units first, and only then are
    fresh ids allocated — from a monotonic counter above the highest id ever
    issued. Previously new sentences kept their *positional* id, so inserting a
    sentence at the top produced two units both called SRC-001.
    """
    sidecar = load_sidecar(project_dir)
    old_units = sidecar["units"]
    next_seq = max(sidecar.get("next_seq", 1), _highest_seq(old_units) + 1)

    candidates = _sentences_of(script_text)
    report = {"added": [], "changed": [], "removed": [], "unchanged": 0}

    old_norm = [normalise(u["text"]) for u in old_units]
    old_by_id = {u["id"]: u for u in old_units}
    matched_old: dict[int, int] = {}      # candidate index -> old index

    # Pass 1 — match candidates to existing units. Monotonic window keeps
    # matching stable on long scripts and stops a repeated sentence from
    # stealing an earlier one's id.
    used, cursor = set(), 0
    for ci, cand in enumerate(candidates):
        target = normalise(cand["text"])
        best_i, best_r = None, 0.0
        for i in range(cursor, min(cursor + 8, len(old_units))):
            if i in used:
                continue
            r = _ratio(target, old_norm[i])
            if r > best_r:
                best_r, best_i = r, i
        if best_i is not None and best_r >= UNIT_MATCH_MIN:
            matched_old[ci] = best_i
            used.add(best_i)
            cursor = best_i + 1

    # Pass 2 — assign ids: preserved for matches, freshly allocated otherwise.
    units = []
    for ci, cand in enumerate(candidates):
        if ci in matched_old:
            old = old_units[matched_old[ci]]
            unit = {"id": old["id"], **cand, "visuals": old.get("visuals", [])}
            if old["fingerprint"] != cand["fingerprint"]:
                report["changed"].append(unit["id"])
            else:
                report["unchanged"] += 1
        else:
            unit = {"id": f"SRC-{next_seq:03d}", **cand, "visuals": []}
            next_seq += 1
            report["added"].append(unit["id"])
        units.append(unit)

    report["removed"] = [u["id"] for i, u in enumerate(old_units) if i not in used]

    payload = {"version": SIDECAR_VERSION, "next_seq": next_seq, "units": units}
    _write_atomic(sidecar_path(project_dir), payload)   # validates uniqueness
    return units, report


# ── alignment: ASR scenes -> canonical script ─────────────────────────────────

def align_scenes(scenes: list[dict], units: list[dict], text_key: str = "script") -> list[dict]:
    """Attach source identity to ASR-derived scenes.

    One sequence alignment over both word streams, so a scene spanning two units
    and a unit split across three scenes both fall out naturally — rather than
    assuming one unit equals one scene.

    `asr_word_span` is a position in the *recognised-audio* word stream, not the
    canonical script. It exists for debugging alignment, never for identity.
    """
    unit_words: list[str] = []
    word_owner: list[str] = []
    for u in units:
        w = _words(u["text"])
        unit_words.extend(w)
        word_owner.extend([u["id"]] * len(w))

    scene_words: list[str] = []
    spans: list[tuple[int, int]] = []
    for s in scenes:
        w = _words(s.get(text_key, ""))
        spans.append((len(scene_words), len(scene_words) + len(w)))
        scene_words.extend(w)

    matcher = difflib.SequenceMatcher(None, scene_words, unit_words, autojunk=False)
    mapping: dict[int, int] = {}
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            mapping[a + k] = b + k

    instance_counter: dict[str, int] = {}
    results = []
    for lo, hi in spans:
        owners, matched = [], 0
        for i in range(lo, hi):
            j = mapping.get(i)
            if j is not None:
                matched += 1
                owner = word_owner[j]
                if owner not in owners:
                    owners.append(owner)
        confidence = matched / max(hi - lo, 1)
        ok = bool(owners) and confidence >= AMBIGUOUS_BELOW

        if owners:
            primary = owners[0]
            instance_counter[primary] = instance_counter.get(primary, 0) + 1
            # Transient: recomputed every split. Never key artwork off this.
            instance_id = f"{primary}-I{instance_counter[primary]:02d}"
        else:
            instance_id = None

        results.append({
            "source_ids": owners,
            "shot_instance_id": instance_id,
            "asr_word_span": [lo, hi],
            "match_confidence": round(confidence, 3),
            "merged_source": len(owners) > 1,
            "source_match": IDENTITY_OK if ok else NEEDS_REVIEW,
        })
    return results


# ── persistent visual identity ────────────────────────────────────────────────

def assign_visual_assets(units: list[dict], shots: list[dict],
                         text_key: str = "script") -> tuple[list[dict], list[str]]:
    """Attach a persistent `visual_asset_id` to each shot.

    Visual slots live on the source unit and remember the text they were
    approved against, so a re-split re-attaches them by overlap rather than by
    S01/S02 position. A unit may own several visuals (a 30-second sentence
    should not hold a single image) and one visual may serve several scenes.

    Competing slots within a hair of each other are not guessed at — the shot
    becomes NEEDS_REVIEW so approved artwork is never inherited by the wrong
    words. Mutates `units` (slot allocation) so the caller can persist it.
    """
    by_id = {u["id"]: u for u in units}
    assignments, issues = [], []

    for shot in shots:
        owners = shot.get("source_ids") or []
        if not owners or shot.get("source_match") != IDENTITY_OK:
            assignments.append({"visual_asset_id": None, "visual_match": NEEDS_REVIEW})
            continue

        unit = by_id.get(owners[0])
        if unit is None:
            assignments.append({"visual_asset_id": None, "visual_match": NEEDS_REVIEW})
            continue

        text = normalise(shot.get(text_key, ""))
        slots = unit.setdefault("visuals", [])
        scored = sorted(((_ratio(text, normalise(s["anchor"])), s) for s in slots),
                        key=lambda t: t[0], reverse=True)

        if scored and scored[0][0] >= VISUAL_MATCH_MIN:
            best_r, best = scored[0]
            if len(scored) > 1 and (best_r - scored[1][0]) < VISUAL_TIE_EPSILON:
                issues.append(f"{unit['id']}: '{shot.get(text_key,'')[:40]}' matches "
                              f"{best['id']} and {scored[1][1]['id']} equally")
                assignments.append({"visual_asset_id": None, "visual_match": NEEDS_REVIEW})
                continue
            assignments.append({"visual_asset_id": best["id"],
                                "visual_match": IDENTITY_OK,
                                "visual_confidence": round(best_r, 3)})
        else:
            slot_letter = chr(ord("A") + len(slots))
            slot = {"id": f"VIS-{unit['id'].split('-')[-1]}-{slot_letter}",
                    "anchor": shot.get(text_key, "")}
            slots.append(slot)
            assignments.append({"visual_asset_id": slot["id"],
                                "visual_match": "new",
                                "visual_confidence": 1.0})
    return assignments, issues


def require_clean_identity(manifest_path, action: str = "this stage") -> None:
    """Refuse to proceed when a manifest's identity is blocked.

    Called by anything that assigns or pays for visuals. Generating against
    uncertain identity is how approved artwork ends up on the wrong words, which
    is the whole failure this module exists to prevent — so this raises rather
    than warning.
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        raise IdentityError(f"{action}: no manifest at {manifest_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = data.get("identity_state")
    if state is None:
        raise IdentityError(
            f"{action}: manifest predates persistent identity (no identity_state). "
            f"Re-run the split stage before routing or generating visuals.")
    if state != IDENTITY_OK:
        reasons = "; ".join(data.get("identity_reasons", [])) or "unspecified"
        raise IdentityError(f"{action}: manifest identity is {state} — {reasons}")


def identity_state(scenes: list[dict]) -> tuple[str, list[str]]:
    """Manifest-level identity verdict. Blocked manifests must not be generated from."""
    reasons = []
    unmatched = [s["id"] for s in scenes if s.get("source_match") != IDENTITY_OK]
    if unmatched:
        reasons.append(f"{len(unmatched)} scene(s) not matched to the script: "
                       f"{', '.join(unmatched[:6])}{' …' if len(unmatched) > 6 else ''}")
    novisual = [s["id"] for s in scenes
                if s.get("visual_match") == NEEDS_REVIEW and s.get("source_match") == IDENTITY_OK]
    if novisual:
        reasons.append(f"{len(novisual)} shot(s) with ambiguous visual attribution: "
                       f"{', '.join(novisual[:6])}{' …' if len(novisual) > 6 else ''}")
    return (IDENTITY_BLOCKED if reasons else IDENTITY_OK), reasons
