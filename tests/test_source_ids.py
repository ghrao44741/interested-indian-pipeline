"""Deterministic tests for persistent content identity.

Fixtures only — no model calls, no project data, no topic-specific assumptions.
The central property under test: a re-narration must not move approved artwork,
and anything uncertain must refuse to bind rather than guess.

Run:  python tests/test_source_ids.py
"""

import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import source_ids as si  # noqa: E402
from source_ids import (  # noqa: E402
    IDENTITY_BLOCKED, IDENTITY_OK, IdentityError, NEEDS_REVIEW, align_scenes,
    assign_visual_assets, build_source_units, fingerprint, identity_state,
    pick_production_script, split_sentences, sync_units,
)

A = "First sentence here."
B = "Second sentence here."
C = "Third sentence here."
SCRIPT = f"{A} {B} {C}\n"

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def scenes_from(texts):
    return [{"id": f"SCENE-{i+1:03d}", "script": t} for i, t in enumerate(texts)]


def ids_for(script, base=SCRIPT):
    """Sync `base` then `script` in a fresh project; return (units, report)."""
    td = Path(tempfile.mkdtemp())
    sync_units(td, base)
    return sync_units(td, script)


def assert_unique(units, label):
    ids = [u["id"] for u in units]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    check(f"{label}: ids unique", not dupes, f"duplicates {dupes} in {ids}")


print("\nsentence splitting")
check("abbreviations do not split a sentence",
      len(split_sentences("Dr. Alvarez holds a Ph.D. and runs it. She agreed.")) == 2)
check("all-caps abbreviations survive",
      len(split_sentences("He has 1.3 lakh M.B.B.S. seats to fill. That is few.")) == 2)

print("\nfingerprint")
check("ignores case/punctuation", fingerprint("The council met!") == fingerprint("the council met"))
check("changes with wording", fingerprint("the council met") != fingerprint("the council left"))

print("\nID ALLOCATION (the reported defect)")
units, rep = ids_for(f"Brand new opening line. {A} {B} {C}\n")
assert_unique(units, "insert at start")
check("insert at start: new id is above every prior id",
      units[0]["id"] == "SRC-004", f"got {units[0]['id']}")
check("insert at start: original keeps its id",
      units[1]["id"] == "SRC-001", f"got {units[1]['id']}")

units, _ = ids_for(f"{A} Inserted middle line. {B} {C}\n")
assert_unique(units, "insert in middle")
check("insert in middle: originals keep ids",
      [u["id"] for u in units] == ["SRC-001", "SRC-004", "SRC-002", "SRC-003"],
      str([u["id"] for u in units]))

units, _ = ids_for(f"{A} {B} {C} Appended tail line.\n")
assert_unique(units, "insert at end")
check("insert at end: new id allocated above max", units[-1]["id"] == "SRC-004")

units, rep = ids_for(f"{A} {C}\n")
assert_unique(units, "deletion")
check("deletion reports the removed id", rep["removed"] == ["SRC-002"], str(rep["removed"]))

print("\nREORDER PRESERVES IDENTITY (not merely uniqueness)")
units, _ = ids_for(f"{C} {B} {A}\n")
assert_unique(units, "reorder")
by_text = {u["text"]: u["id"] for u in units}
check("full reorder keeps every original id",
      (by_text[A], by_text[B], by_text[C]) == ("SRC-001", "SRC-002", "SRC-003"),
      str(by_text))

units, _ = ids_for(f"{B} {C}\n\n{A}\n", base=f"{A}\n\n{B} {C}\n")
by_text = {u["text"]: u["id"] for u in units}
check("paragraph reorder keeps ids",
      (by_text[A], by_text[B], by_text[C]) == ("SRC-001", "SRC-002", "SRC-003"), str(by_text))

units, _ = ids_for(f"{C} {A} Second sentence here now edited.\n")
by_text = {u["text"]: u["id"] for u in units}
check("moved sentence keeps id while another is edited",
      by_text[C] == "SRC-003" and by_text[A] == "SRC-001", str(by_text))
check("the edited sentence keeps SRC-002 rather than a new id",
      by_text.get("Second sentence here now edited.") == "SRC-002", str(by_text))

dup_base = f"{A} {B} {A}\n"
units, _ = ids_for(f"{B} {A} {A}\n", base=dup_base)
assert_unique(units, "duplicate identical sentences reordered")
check("duplicate sentences consume ids deterministically",
      sorted(u["id"] for u in units) == ["SRC-001", "SRC-002", "SRC-003"],
      str([u["id"] for u in units]))

big = " ".join(f"Filler sentence number {i}." for i in range(1, 13))
units, _ = ids_for(f"{big} {A} {B} {C}\n")
by_text = {u["text"]: u["id"] for u in units}
check("insertion larger than the old 8-unit window still keeps ids",
      (by_text[A], by_text[B], by_text[C]) == ("SRC-001", "SRC-002", "SRC-003"),
      str((by_text[A], by_text[B], by_text[C])))

_, rep = ids_for("First sentence here!! Firsts sentence here.\n", base=f"{A}\n")
check("an indistinct fuzzy match is reported, not guessed",
      rep["ambiguous"] or len(rep["added"]) >= 1, str(rep))

units, _ = ids_for(f"{A} {B} {C} {A} {B}\n")
assert_unique(units, "repeated sentences")
check("repeated sentence gets a distinct id, not a stolen one",
      len({u["id"] for u in units}) == 5, str([u["id"] for u in units]))

units, _ = ids_for("Yes. No. Maybe. " + SCRIPT)
assert_unique(units, "short common phrases")

units, _ = ids_for("First sentence here and then some. Second sentence here.\n")
assert_unique(units, "sentence merge/split")

print("\nids never reused after deletion")
td = Path(tempfile.mkdtemp())
sync_units(td, SCRIPT)
sync_units(td, f"{A} {C}\n")            # drop B (SRC-002)
units, _ = sync_units(td, f"{A} {C} Totally different closing line.\n")
assert_unique(units, "post-deletion allocation")
check("a deleted id is not recycled", "SRC-002" not in [u["id"] for u in units],
      str([u["id"] for u in units]))

print("\nduplicate-id validation and atomic write")
try:
    si._write_atomic(Path(tempfile.mkdtemp()) / "x.json",
                     {"units": [{"id": "SRC-001"}, {"id": "SRC-001"}]})
    check("duplicate ids are rejected", False, "no exception raised")
except IdentityError:
    check("duplicate ids are rejected", True)

td = Path(tempfile.mkdtemp())
sync_units(td, SCRIPT)
before = si.sidecar_path(td).read_text(encoding="utf-8")
with mock.patch("source_ids.os.replace", side_effect=OSError("disk full")):
    try:
        sync_units(td, f"{A} {B} {C} New line.\n")
    except OSError:
        pass
check("failed write leaves the previous sidecar intact",
      si.sidecar_path(td).read_text(encoding="utf-8") == before)
check("no temp files left behind",
      not list(td.glob("*.tmp")), str(list(td.glob("*.tmp"))))

print("\nscript selection blocks rather than guesses")
td = Path(tempfile.mkdtemp())
try:
    pick_production_script(td, strict=True)
    check("missing script raises in strict mode", False, "no exception")
except IdentityError:
    check("missing script raises in strict mode", True)
(td / "script_one.txt").write_text("x", encoding="utf-8")
(td / "script_two.txt").write_text("y", encoding="utf-8")
try:
    pick_production_script(td, strict=True)
    check("multiple scripts raise in strict mode", False, "no exception")
except IdentityError:
    check("multiple scripts raise in strict mode", True)

print("\nalignment: many-to-many")
units = build_source_units(SCRIPT)
res = align_scenes(scenes_from(["First sentence", "here."]), units)
check("one unit across two shots keeps one owner",
      {r["source_ids"][0] for r in res} == {"SRC-001"}, str(res))
res = align_scenes(scenes_from(["here. Second sentence here."]), units)
check("shot spanning two units lists both", len(res[0]["source_ids"]) > 1, str(res[0]["source_ids"]))
check("merged_source flag set", res[0]["merged_source"] is True)
res = align_scenes(scenes_from(["Entirely unrelated words about submarine cables."]), units)
check("unmatched scene needs review", res[0]["source_match"] == NEEDS_REVIEW)
check("unmatched scene gets no instance id", res[0]["shot_instance_id"] is None)

print("\nVISUAL IDENTITY ACROSS A RE-SPLIT (two shots -> three)")
units = build_source_units("Alpha beta gamma delta. Epsilon zeta eta theta.\n")
split_a = scenes_from(["Alpha beta", "gamma delta.", "Epsilon zeta eta theta."])
al_a = align_scenes(split_a, units)
for s, a in zip(split_a, al_a):
    s.update(a)
vis_a, _ = assign_visual_assets(units, split_a)
for s, v in zip(split_a, vis_a):
    s.update(v)
first_pass = {s["script"]: s["visual_asset_id"] for s in split_a}

# Same script, different boundaries: the first sentence now yields three shots.
split_b = scenes_from(["Alpha", "beta gamma", "delta.", "Epsilon zeta eta theta."])
al_b = align_scenes(split_b, units)
for s, a in zip(split_b, al_b):
    s.update(a)
vis_b, _ = assign_visual_assets(units, split_b)
for s, v in zip(split_b, vis_b):
    s.update(v)

check("unchanged sentence keeps its approved visual",
      first_pass["Epsilon zeta eta theta."] ==
      next(s["visual_asset_id"] for s in split_b if s["script"] == "Epsilon zeta eta theta."),
      "visual moved across re-split")
check("visual ids are not S01/S02 positions",
      all(s["visual_asset_id"] is None or "-I0" not in s["visual_asset_id"] for s in split_b))
check("re-split does not silently reuse across different words",
      all(s.get("visual_match") in (IDENTITY_OK, "new", NEEDS_REVIEW) for s in split_b))

print("\nHIGH-WATER MARK THROUGH THE REAL SPLIT-STAGE PATH")
td = Path(tempfile.mkdtemp())
sync_units(td, "One here. Two here. Three here. Four here.")
units, _ = sync_units(td, "One here. Two here. Three here.")   # delete highest (SRC-004)
# the exact call the splitter makes after allocating visual slots
si.save_units(td, units)
after_save = si.load_sidecar(td)["next_seq"]
check("visual-slot save does not lower next_seq", after_save >= 5, f"next_seq={after_save}")
units, _ = sync_units(td, "One here. Two here. Three here. Five here.")
new_id = next(u["id"] for u in units if "Five" in u["text"])
check("id retired by deletion is never recycled", new_id != "SRC-004", f"got {new_id}")
check("new id is above the deleted one", si._seq_of(new_id) > 4, f"got {new_id}")

print("\nsave_units preserves metadata")
td = Path(tempfile.mkdtemp())
sync_units(td, SCRIPT)
raw = si.load_sidecar(td)
raw["future_key"] = {"kept": True}
si._write_atomic(si.sidecar_path(td), raw)
si.save_units(td, si.load_sidecar(td)["units"])
check("unknown/future keys survive a save", si.load_sidecar(td).get("future_key") == {"kept": True})
check("schema version preserved", si.load_sidecar(td)["version"] == si.SIDECAR_VERSION)

print("\nVISUAL REUSE REQUIRES WORD OVERLAP (exact fixture)")
units = build_source_units("Alpha beta gamma delta.\n")
first = scenes_from(["Alpha beta", "gamma delta"])
for s, a in zip(first, align_scenes(first, units)):
    s.update(a)
for s, v in zip(first, assign_visual_assets(units, first)[0]):
    s.update(v)
check("initial: 'Alpha beta' -> VIS-001-A", first[0]["visual_asset_id"] == "VIS-001-A",
      str(first[0]["visual_asset_id"]))
check("initial: 'gamma delta' -> VIS-001-B (no shared words)",
      first[1]["visual_asset_id"] == "VIS-001-B", str(first[1]["visual_asset_id"]))

# Exercised directly: this fixture is about the visual matcher, and its shot
# order differs from the source order, which align_scenes' word-stream alignment
# would (correctly, but irrelevantly here) reject before the matcher is reached.
second = scenes_from(["Alpha", "delta", "beta gamma"])
for s in second:
    s.update({"source_ids": ["SRC-001"], "source_match": IDENTITY_OK})
for s, v in zip(second, assign_visual_assets(units, second)[0]):
    s.update(v)
check("changed: 'Alpha' -> VIS-001-A", second[0]["visual_asset_id"] == "VIS-001-A",
      str(second[0]["visual_asset_id"]))
check("changed: 'delta' -> VIS-001-B", second[1]["visual_asset_id"] == "VIS-001-B",
      str(second[1]["visual_asset_id"]))
check("changed: 'beta gamma' -> NEEDS_REVIEW (overlaps both)",
      second[2]["visual_match"] == NEEDS_REVIEW and second[2]["visual_asset_id"] is None,
      f"{second[2]['visual_match']} / {second[2]['visual_asset_id']}")

print("\nvisual slot lifecycle")
units = build_source_units("Alpha beta gamma delta.\n")
shots = scenes_from(["Alpha beta"])
for s, a in zip(shots, align_scenes(shots, units)):
    s.update(a)
assigns, _ = assign_visual_assets(units, shots)
check("new slots are born planned, never approved",
      units[0]["visuals"][0]["state"] == si.SLOT_PLANNED)
check("assignment reports the slot state", assigns[0]["visual_state"] == si.SLOT_PLANNED)
before_n = len(units[0]["visuals"])
assign_visual_assets(units, shots)          # identical replay
check("replay does not accumulate planned slots", len(units[0]["visuals"]) == before_n,
      f"{before_n} -> {len(units[0]['visuals'])}")

legacy = [{"id": "SRC-001", "text": "x", "fingerprint": "f",
           "visuals": [{"id": "VIS-001-A", "anchor": "x"},
                       {"id": "VIS-001-B", "anchor": "y", "state": si.SLOT_APPROVED}]}]
touched = si.migrate_visual_slots(legacy)
check("pre-lifecycle slots migrate as planned",
      legacy[0]["visuals"][0]["state"] == si.SLOT_PLANNED and touched == 1)
check("migration never demotes or overwrites approved artwork",
      legacy[0]["visuals"][1]["state"] == si.SLOT_APPROVED)

print("\nmerged-source shots record the attribution decision")
units = build_source_units(f"{A} {B}\n")
merged = scenes_from(["here. Second sentence here."])
for s, a in zip(merged, align_scenes(merged, units)):
    s.update(a)
mv, _ = assign_visual_assets(units, merged)
check("all contributing source_ids retained", len(merged[0]["source_ids"]) > 1)
check("visual owner recorded explicitly", mv[0]["visual_owner_source_id"] == merged[0]["source_ids"][0])
check("multi-source attribution is noted", "also draws on" in mv[0].get("visual_owner_note", ""))

print("\nidentity state blocks downstream work")
state, reasons = identity_state(split_a)
check("clean manifest is ok", state == IDENTITY_OK, str(reasons))
blocked = [{"id": "SCENE-001", "source_match": NEEDS_REVIEW}]
state, reasons = identity_state(blocked)
check("unmatched scene blocks the manifest", state == IDENTITY_BLOCKED)
check("block reason names the scene", "SCENE-001" in reasons[0], str(reasons))

print("\n" + ("=" * 58))
print(f"FAILED ({len(failures)}): {failures}" if failures else "ALL PASS")
sys.exit(1 if failures else 0)
