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
# Word-level overlap below which a shot does not match an existing visual slot.
# Token-based on purpose: character similarity rated "alpha beta" against
# "gamma delta" at 0.571 — two fragments with no word in common — and happily
# handed them the same visual.
VISUAL_TOKEN_MIN = 0.40
# Two slots scoring within this of each other are a tie — refuse to guess.
VISUAL_TIE_EPSILON = 0.05
# Similarity at/above which a new sentence is considered an edit of an old one.
UNIT_MATCH_MIN = 0.75
# Fuzzy unit matches this close together are ambiguous — report, do not assign.
UNIT_TIE_EPSILON = 0.05

IDENTITY_OK = "ok"
IDENTITY_BLOCKED = "blocked"
NEEDS_REVIEW = "NEEDS_REVIEW"

# Visual slot lifecycle. Automatic allocation only ever produces PLANNED — a
# slot the router intends to fill. Nothing may be called approved artwork until
# a human has approved it, and migration never promotes a slot.
SLOT_PLANNED = "planned"
SLOT_GENERATED = "generated"
SLOT_APPROVED = "approved"

# Shared only-as-glue words: if two fragments overlap on nothing else, they are
# not the same visual.
_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "been", "but", "by", "for", "from",
    "had", "has", "have", "he", "her", "his", "in", "is", "it", "its", "of",
    "on", "or", "she", "so", "that", "the", "their", "them", "they", "this",
    "to", "was", "were", "which", "with", "you", "your",
}


class IdentityError(RuntimeError):
    """Raised when identity cannot be established safely."""


class ResolutionError(IdentityError):
    """A supplied migration resolution is invalid and cannot be applied."""


class MigrationBlocked(IdentityError):
    """A script edit cannot be migrated without a human decision.

    Raised *before* anything is written. The previous sidecar is left byte-for-
    byte intact, because guessing here would discard a retired unit's approved
    visual history to satisfy a match nobody confirmed.

    `ambiguities` holds structured records, not prose, so a CLI or UI can present
    and apply a decision without parsing human-readable text. Each record:

        key               stable id to resolve against
        kind              which side is contested
        candidates        [{index, text}] competing new sentences
        old_source_ids    competing existing ids
        scores            {"<candidate_index>:<source_id>": similarity}
        allowed_actions   ["reuse", "new"]
        message           human-readable, for logs only
    """

    def __init__(self, ambiguities: list[dict], proposal: list[dict] | None = None):
        self.ambiguities = ambiguities
        self.proposal = proposal or []
        summary = "; ".join(a["message"] for a in ambiguities[:3])
        super().__init__(
            f"{len(ambiguities)} ambiguous source migration(s) require resolution: "
            + summary + (" …" if len(ambiguities) > 3 else ""))


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


def _content_tokens(text: str) -> set[str]:
    """Meaningful words. Falls back to all words when everything is a stopword."""
    toks = set(_words(text))
    content = toks - _STOPWORDS
    return content or toks


def token_f1(a: str, b: str) -> float:
    """Word-level overlap between two fragments, 0..1.

    Primary reuse test for visual slots. Character similarity is unusable here:
    it scored two fragments sharing no word at all above the reuse threshold.
    """
    ta, tb = _content_tokens(a), _content_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not inter:
        return 0.0
    precision = len(inter) / len(tb)
    recall = len(inter) / len(ta)
    return 2 * precision * recall / (precision + recall)


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
    # Identity must be unique across the whole sidecar, not just the active
    # units: an id present in both collections is simultaneously live and
    # retired, and a next_seq below an existing id will reissue it.
    active = [u["id"] for u in payload.get("units", [])]
    retired = [u["id"] for u in payload.get("retired_units", [])]

    dupes = sorted({i for i in active if active.count(i) > 1})
    if dupes:
        raise IdentityError(f"refusing to write sidecar with duplicate active ids: {dupes}")

    rdupes = sorted({i for i in retired if retired.count(i) > 1})
    if rdupes:
        raise IdentityError(f"refusing to write sidecar with duplicate retired ids: {rdupes}")

    overlap = sorted(set(active) & set(retired))
    if overlap:
        raise IdentityError(
            f"refusing to write sidecar with ids both active and retired: {overlap}")

    highest = max((_seq_of(i) for i in active + retired), default=0)
    if payload.get("next_seq", 1) <= highest:
        raise IdentityError(
            f"refusing to write sidecar with next_seq={payload.get('next_seq')} at or below "
            f"the highest existing id ({highest}) — it would reissue a live or retired id")

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


def save_units(project_dir: Path, units: list[dict]) -> dict:
    """Persist unit changes (e.g. visual-slot allocation) without losing metadata.

    The one supported way for any stage to write the sidecar. The split stage
    previously called _write_atomic() directly with
    `next_seq = max(current ids) + 1`, which *lowered* the counter whenever the
    highest-numbered unit had been deleted — recycling a retired id onto a new
    sentence. The high-water mark only ever moves up, and unknown keys written
    by future versions are preserved rather than dropped.
    """
    sidecar = load_sidecar(project_dir)
    payload = dict(sidecar)                       # keep unknown/future metadata
    payload["version"] = sidecar.get("version", SIDECAR_VERSION)
    payload["units"] = units
    payload["next_seq"] = max(
        sidecar.get("next_seq", 1),               # never regress
        _highest_seq(units) + 1,
    )
    _write_atomic(sidecar_path(project_dir), payload)
    return payload


def _match_units(candidates: list[dict], old_units: list[dict],
                 retired_units: list[dict] | None = None) -> tuple[dict, dict, list[str]]:
    """Map candidate index -> old unit index, staged and one-to-one.

    Exact fingerprints match globally first, so reordering a script preserves
    every id. The previous version only searched a forward window from a moving
    cursor, so reversing three sentences kept one id and issued two new ones —
    the text was unchanged but the identities were lost.

    Duplicate fingerprints are consumed in order from a queue, so repeated
    identical sentences map deterministically. Only what is left over is matched
    fuzzily, and a fuzzy match that is not clearly better than its runner-up is
    reported as ambiguous rather than assigned.
    """
    matched: dict[int, int] = {}
    restored: dict[int, dict] = {}
    taken: set[int] = set()
    ambiguous: list[str] = []
    retired_units = retired_units or []

    # Stage 1 — exact fingerprint, position-independent, ordered queues for dupes.
    by_fp: dict[str, list[int]] = {}
    for i, u in enumerate(old_units):
        by_fp.setdefault(u["fingerprint"], []).append(i)
    for ci, cand in enumerate(candidates):
        queue = by_fp.get(cand["fingerprint"])
        if queue:
            oi = queue.pop(0)
            matched[ci] = oi
            taken.add(oi)

    # Stage 1b — a sentence that comes back reclaims its retired id, along with
    # whatever visual history it had approved. Restoring beats issuing a new id
    # and re-approving the same artwork.
    retired_by_fp: dict[str, list[dict]] = {}
    for u in retired_units:
        retired_by_fp.setdefault(u["fingerprint"], []).append(u)
    for ci, cand in enumerate(candidates):
        if ci in matched:
            continue
        queue = retired_by_fp.get(cand["fingerprint"])
        if queue:
            restored[ci] = queue.pop(0)

    # Stage 2 — fuzzy on the remainder, globally greedy and one-to-one.
    #
    # Contention is checked in BOTH directions. One candidate matching two old
    # units equally well is ambiguous, and so is two candidates competing for the
    # same old unit — otherwise whichever sentence happened to come first would
    # claim the id and the other would silently get a new one.
    remaining_old = [i for i in range(len(old_units)) if i not in taken]
    open_cands = [ci for ci in range(len(candidates))
                  if ci not in matched and ci not in restored]

    pairs = []
    for ci in open_cands:
        target = normalise(candidates[ci]["text"])
        for oi in remaining_old:
            score = _ratio(target, normalise(old_units[oi]["text"]))
            if score >= UNIT_MATCH_MIN:
                pairs.append((score, ci, oi))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))     # deterministic ordering

    # An edge is competitive when it is within epsilon of the best score
    # available to BOTH of its endpoints. A candidate with a clear winner then
    # has exactly one competitive edge; anything more is a genuine contest.
    best_c: dict[int, float] = {}
    best_o: dict[int, float] = {}
    for s, ci, oi in pairs:
        best_c[ci] = max(best_c.get(ci, 0.0), s)
        best_o[oi] = max(best_o.get(oi, 0.0), s)
    competitive = [(s, ci, oi) for s, ci, oi in pairs
                   if s >= best_c[ci] - UNIT_TIE_EPSILON
                   and s >= best_o[oi] - UNIT_TIE_EPSILON]

    # Connected components over the bipartite graph. Taking only the first rival
    # hid every additional equally-plausible option: a candidate tying three old
    # units surfaced two of them, so the operator could not choose the third.
    parent: dict[tuple, tuple] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for _, ci, oi in competitive:
        union(("c", ci), ("o", oi))

    groups: dict[tuple, dict] = {}
    for _, ci, oi in competitive:
        g = groups.setdefault(find(("c", ci)), {"c": set(), "o": set()})
        g["c"].add(ci)
        g["o"].add(oi)

    for root in sorted(groups, key=lambda r: (sorted(groups[r]["c"]), sorted(groups[r]["o"]))):
        cand_idxs = sorted(groups[root]["c"])
        old_idxs = sorted(groups[root]["o"])

        if len(cand_idxs) == 1 and len(old_idxs) == 1:
            ci, oi = cand_idxs[0], old_idxs[0]
            matched[ci] = oi
            taken.add(oi)
            continue

        old_ids = [old_units[o]["id"] for o in old_idxs]
        if len(cand_idxs) == 1:
            kind = "candidate_matches_several_units"
            message = (f"'{candidates[cand_idxs[0]]['text'][:40]}' matches "
                       f"{', '.join(old_ids)} equally")
        elif len(old_idxs) == 1:
            kind = "unit_claimed_by_several_candidates"
            message = (f"{old_ids[0]} is claimed equally by "
                       + ", ".join(f"'{candidates[c]['text'][:28]}'" for c in cand_idxs))
        else:
            kind = "mixed_component"
            message = (f"{len(cand_idxs)} candidates and {len(old_ids)} units "
                       f"({', '.join(old_ids)}) are mutually ambiguous")

        record = {
            "kind": kind,
            "message": message,
            "candidates": [{"index": c, "text": candidates[c]["text"]} for c in cand_idxs],
            "old_source_ids": old_ids,
            "_old_indexes": {old_units[o]["id"]: o for o in old_idxs},
            # every candidate/source score in the component, not just the ties
            "scores": {f"{c}:{old_units[o]['id']}":
                       round(_ratio(normalise(candidates[c]["text"]),
                                    normalise(old_units[o]["text"])), 3)
                       for c in cand_idxs for o in old_idxs},
            "allowed_actions": ["reuse", "new"],
        }
        # Deterministic in the complete component, so the same contest always
        # produces the same key regardless of iteration order.
        record["key"] = _ambiguity_key(
            f"{kind}|{'|'.join(sorted(old_ids))}|"
            f"{'|'.join(sorted(c['text'] for c in record['candidates']))}")
        ambiguous.append(record)

    return matched, restored, ambiguous


def _ambiguity_key(message: str) -> str:
    """Stable key for an ambiguity, so a resolution can be matched to it."""
    return hashlib.sha1(message.encode("utf-8")).hexdigest()[:10]


def _apply_resolutions(ambiguities: list[dict], resolutions: dict,
                       matched: dict[int, int], taken: set[int]) -> list[dict]:
    """Validate and apply operator decisions. Returns still-unresolved records.

    Rejects rather than guesses: an earlier version treated any truthy value as
    a resolution, so {"action": "reuse", "source_id": "SRC-001"}, "new" and True
    all produced identical output and the requested id was never actually
    assigned.
    """
    resolutions = resolutions or {}
    known = {a["key"] for a in ambiguities}
    unknown = [k for k in resolutions if k not in known]
    if unknown:
        raise ResolutionError(f"unknown resolution key(s): {unknown}")

    assigned_source_ids: dict[str, str] = {}
    unresolved = []

    for amb in ambiguities:
        decision = resolutions.get(amb["key"])
        if decision is None:
            unresolved.append(amb)
            continue
        if not isinstance(decision, dict):
            raise ResolutionError(
                f"{amb['key']}: resolution must be an object with an 'action', got "
                f"{type(decision).__name__}")

        action = decision.get("action")
        if action not in amb["allowed_actions"]:
            raise ResolutionError(
                f"{amb['key']}: action {action!r} not in {amb['allowed_actions']}")

        if action == "new":
            # Every competing candidate is confirmed as new identity; the old
            # units are left unmatched and retire normally.
            continue

        ci = decision.get("candidate_index")
        valid_idxs = [c["index"] for c in amb["candidates"]]
        if ci not in valid_idxs:
            raise ResolutionError(
                f"{amb['key']}: candidate_index {ci!r} not in {valid_idxs}")

        sid = decision.get("source_id")
        if sid not in amb["old_source_ids"]:
            raise ResolutionError(
                f"{amb['key']}: source_id {sid!r} not among {amb['old_source_ids']}")
        if sid in assigned_source_ids:
            raise ResolutionError(
                f"source_id {sid!r} assigned twice (keys {assigned_source_ids[sid]} "
                f"and {amb['key']})")

        oi = amb["_old_indexes"][sid]
        if oi in taken:
            raise ResolutionError(f"{amb['key']}: source_id {sid!r} is no longer available")

        assigned_source_ids[sid] = amb["key"]
        matched[ci] = oi
        taken.add(oi)

    return unresolved


def sync_units(project_dir: Path, script_text: str,
               resolutions: dict | None = None) -> tuple[list[dict], dict]:
    """Create or update the sidecar, preserving ids across script edits.

    Transactional: either the whole migration is unambiguous and gets written,
    or nothing is written at all. An unresolved ambiguity raises MigrationBlocked
    with the previous sidecar untouched, because the alternative — allocating a
    new id — silently retires the old unit and its approved visual history to
    satisfy a match nobody confirmed.

    `resolutions` maps an ambiguity key (see MigrationBlocked.ambiguities via
    _ambiguity_key) to the operator's decision, allowing the same call to commit
    once a human has chosen.

    Matching is staged: exact fingerprints globally, then retired units (so a
    returning sentence reclaims its id), then fuzzy on the remainder. New ids are
    allocated last, from a monotonic counter above every id ever issued.
    """
    sidecar = load_sidecar(project_dir)
    old_units = sidecar["units"]
    retired = list(sidecar.get("retired_units", []))
    next_seq = max(sidecar.get("next_seq", 1),
                   _highest_seq(old_units) + 1, _highest_seq(retired) + 1)

    candidates = _sentences_of(script_text)
    report = {"added": [], "changed": [], "removed": [], "restored": [], "unchanged": 0}

    matched_old, restored_map, ambiguous = _match_units(candidates, old_units, retired)
    report["ambiguous"] = ambiguous

    # Nothing is written while a migration is unresolved. Allocating a new id
    # here would also retire the old unit — discarding its visual history to
    # satisfy a match nobody confirmed. Caller resolves, then re-runs.
    taken = set(matched_old.values())
    unresolved = _apply_resolutions(ambiguous, resolutions, matched_old, taken)
    if unresolved:
        raise MigrationBlocked(unresolved, proposal=candidates)

    used = set(matched_old.values())
    restored_ids = {u["id"] for u in restored_map.values()}

    units = []
    for ci, cand in enumerate(candidates):
        if ci in matched_old:
            old = old_units[matched_old[ci]]
            unit = {"id": old["id"], **cand, "visuals": old.get("visuals", [])}
            if old["fingerprint"] != cand["fingerprint"]:
                report["changed"].append(unit["id"])
            else:
                report["unchanged"] += 1
        elif ci in restored_map:
            old = restored_map[ci]
            unit = {"id": old["id"], **cand, "visuals": old.get("visuals", [])}
            report["restored"].append(unit["id"])
        else:
            unit = {"id": f"SRC-{next_seq:03d}", **cand, "visuals": []}
            next_seq += 1
            report["added"].append(unit["id"])
        units.append(unit)

    # Removed units are retired, not deleted: their ids stay permanently
    # unavailable and their approved visual history survives, so a sentence that
    # comes back can reclaim both.
    from datetime import datetime
    stamp = datetime.now().isoformat(timespec="seconds")
    still_retired = [u for u in retired if u["id"] not in restored_ids]
    for i, u in enumerate(old_units):
        if i not in used:
            report["removed"].append(u["id"])
            still_retired.append({**u, "retired_at": stamp,
                                  "retired_from_fingerprint": u["fingerprint"]})

    # Built from the existing sidecar so unknown/future keys survive a sync —
    # save_units() already preserved them, but sync rebuilt the payload and
    # silently dropped them.
    payload = dict(sidecar)
    payload["version"] = SIDECAR_VERSION
    payload["units"] = units
    payload["retired_units"] = still_retired
    payload["next_seq"] = max(next_seq, _highest_seq(still_retired) + 1)
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

        raw = shot.get(text_key, "")
        slots = unit.setdefault("visuals", [])
        # Word-level overlap decides reuse. Character similarity is only
        # consulted to break a tie between two already-plausible slots, never to
        # authorise reuse on its own.
        scored = sorted(((token_f1(raw, s["anchor"]), s) for s in slots),
                        key=lambda t: t[0], reverse=True)

        # A shot drawing on several units keeps them all; record which one owns
        # the visual so collapsing to the first is a stated decision, not a
        # silent one.
        extra = {"visual_owner_source_id": owners[0]}
        if len(owners) > 1:
            extra["visual_owner_note"] = (
                f"visual attributed to {owners[0]}; shot also draws on "
                f"{', '.join(owners[1:])}")

        if scored and scored[0][0] >= VISUAL_TOKEN_MIN:
            best_r, best = scored[0]
            if len(scored) > 1 and (best_r - scored[1][0]) < VISUAL_TIE_EPSILON:
                issues.append(f"{unit['id']}: '{raw[:40]}' overlaps {best['id']} and "
                              f"{scored[1][1]['id']} comparably")
                assignments.append({"visual_asset_id": None,
                                    "visual_match": NEEDS_REVIEW, **extra})
                continue
            assignments.append({"visual_asset_id": best["id"],
                                "visual_match": IDENTITY_OK,
                                "visual_state": best.get("state", SLOT_PLANNED),
                                "visual_confidence": round(best_r, 3), **extra})
        else:
            slot_letter = chr(ord("A") + len(slots))
            slot = {"id": f"VIS-{unit['id'].split('-')[-1]}-{slot_letter}",
                    "anchor": raw,
                    "state": SLOT_PLANNED}   # never born approved
            slots.append(slot)
            assignments.append({"visual_asset_id": slot["id"],
                                "visual_match": "new",
                                "visual_state": SLOT_PLANNED,
                                "visual_confidence": 1.0, **extra})
    return assignments, issues


def migrate_visual_slots(units: list[dict]) -> int:
    """Stamp lifecycle state on slots created before it existed.

    Pre-lifecycle slots were allocated by the faulty character-similarity
    matcher, so they are provisional by definition: they migrate as PLANNED and
    are never promoted. Anything already marked approved is left untouched —
    migration must not overwrite approved artwork.
    """
    touched = 0
    for u in units:
        for slot in u.get("visuals", []):
            if "state" not in slot:
                slot["state"] = SLOT_PLANNED
                touched += 1
    return touched


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
