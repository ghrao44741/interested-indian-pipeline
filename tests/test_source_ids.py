"""Deterministic tests for persistent content identity.

Fixtures only — no model calls, no project data, no topic-specific assumptions.
The point of this module is that a re-narration must not move artwork, so the
central test simulates a re-split (different scene boundaries over the same
script) and asserts the source attribution is unchanged.

Run:  python tests/test_source_ids.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from source_ids import (  # noqa: E402
    align_scenes, build_source_units, fingerprint, split_sentences, sync_units,
)

SCRIPT = (
    "The council met on Tuesday. It approved three measures, rejected one, "
    "and deferred the rest until spring.\n"
    "\n"
    "Dr. Alvarez holds a Ph.D. and runs the review board. She said the vote was close.\n"
)

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(name)


def scenes_from(texts):
    """Minimal scene dicts shaped like the manifest's."""
    return [{"id": f"SCENE-{i+1:03d}", "script": t} for i, t in enumerate(texts)]


print("\nsentence splitting")
sents = split_sentences("Dr. Alvarez holds a Ph.D. and runs the board. She agreed.")
check("abbreviations do not split a sentence", len(sents) == 2, f"got {len(sents)}: {sents}")

print("\nsource units")
units = build_source_units(SCRIPT)
check("ids are sequential and stable", [u["id"] for u in units][:3] == ["SRC-001", "SRC-002", "SRC-003"])
check("fingerprint ignores case/punctuation",
      fingerprint("The council met!") == fingerprint("the council met"))
check("fingerprint changes with wording",
      fingerprint("the council met") != fingerprint("the council adjourned"))

print("\none source unit spanning several shots")
# The splitter cuts long sentences at commas — one unit, three shots.
res = align_scenes(scenes_from([
    "It approved three measures,",
    "rejected one,",
    "and deferred the rest until spring.",
]), units)
owners = {r["source_ids"][0] for r in res if r["source_ids"]}
check("all three shots map to one unit", len(owners) == 1, f"got {owners}")
check("shot suffixes increment", [r["shot_id"].split("-S")[1] for r in res] == ["01", "02", "03"],
      str([r["shot_id"] for r in res]))

print("\nmerged-source shot")
res = align_scenes(scenes_from(["until spring. Dr. Alvarez holds a Ph.D."]), units)
check("shot spanning two units lists both", len(res[0]["source_ids"]) > 1, str(res[0]["source_ids"]))
check("merged_source flag set", res[0]["merged_source"] is True)

print("\nSTABILITY ACROSS A RE-SPLIT (the regression this exists to prevent)")
split_a = scenes_from([
    "The council met on Tuesday.",
    "It approved three measures, rejected one,",
    "and deferred the rest until spring.",
    "Dr. Alvarez holds a Ph.D. and runs the review board.",
    "She said the vote was close.",
])
# Same script, different boundaries: 2+3 merged, 4 split in two.
split_b = scenes_from([
    "The council met on Tuesday. It approved three measures,",
    "rejected one, and deferred the rest until spring.",
    "Dr. Alvarez holds a Ph.D.",
    "and runs the review board.",
    "She said the vote was close.",
])
a = align_scenes(split_a, units)
b = align_scenes(split_b, units)


def owners_of(res_list, needle, scenes):
    for r, s in zip(res_list, scenes):
        if needle in s["script"]:
            return r["source_ids"]
    return None


for phrase in ("vote was close", "deferred the rest", "review board"):
    check(f"'{phrase}' keeps the same source unit across both splits",
          owners_of(a, phrase, split_a) == owners_of(b, phrase, split_b),
          f"{owners_of(a, phrase, split_a)} vs {owners_of(b, phrase, split_b)}")

print("\nambiguous content is not silently bound")
res = align_scenes(scenes_from(["Entirely unrelated sentence about submarine cables."]), units)
check("unmatched scene flagged ambiguous", res[0]["source_match"] == "ambiguous",
      str(res[0]))
check("unmatched scene gets no shot id", res[0]["shot_id"] is None)

print("\nids survive an intentional wording edit")
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    sync_units(td, SCRIPT)
    edited = SCRIPT.replace("She said the vote was close.",
                            "She said the vote was extremely close.")
    new_units, report = sync_units(td, edited)
    edited_unit = next(u for u in new_units if "extremely close" in u["text"])
    original = next(u for u in build_source_units(SCRIPT) if "vote was close" in u["text"])
    check("edited sentence keeps its id", edited_unit["id"] == original["id"],
          f"{edited_unit['id']} vs {original['id']}")
    check("edited sentence reports a changed fingerprint",
          edited_unit["id"] in report["changed"], str(report))
    check("no spurious additions", report["added"] == [], str(report["added"]))

print("\n" + ("=" * 58))
print(f"FAILED: {failures}" if failures else "ALL PASS")
sys.exit(1 if failures else 0)
