"""render_channel_dna.py — generate every document derived from a Channel Pack.

`channel.json` is the source of truth. Everything here is a deterministic
function of it:

    CHANNEL_DNA.md      the human-readable brief, inside the pack
    channel_config.json a root-level compatibility adapter
    brand.json          a root-level compatibility adapter

The adapters exist only because three modules still read root-level files
directly. They are generated for the one pack that declares `legacy_adapter`,
and for no other: a pack that does not own them must never read, compare against
or write them. Task 2B migrates those consumers onto the context and the
adapters go away.

Deterministic on purpose. channel_context.load_channel() re-renders each of these
and refuses to load if the file on disk differs, which is what stops a generated
document from quietly becoming a second, editable source of truth — the failure
mode this whole task exists to end.

    python render_channel_dna.py --channel <id>
    python render_channel_dna.py --channel <id> --check
"""

import argparse
import json
import sys
from pathlib import Path

import channel_context as cc

ADAPTER_WARNING = ("GENERATED FILE — DO NOT EDIT. Change the channel pack and "
                   "re-run render_channel_dna.py. Hand edits are detected and "
                   "refused at load time.")


# ── the human-readable brief ─────────────────────────────────────────────────

def render_dna(doc: dict) -> str:
    b, ed, na = doc["brand"], doc["editorial"], doc["narrative"]
    L = [f"# {b['name']} — Channel DNA", "",
         "<!-- GENERATED from channel.json by render_channel_dna.py. Do not edit:",
         "     edits are detected and refused when the channel loads. -->", "",
         f"- **channel id**: `{doc['channel_id']}`",
         f"- **DNA version**: {doc['channel_dna_version']}",
         f"- **language**: {b['language']}", ""]

    L += ["## Promise", "", b["promise"], "",
          "## Audience", "",
          f"- **primary**: {doc['audience']['primary']}",
          f"- **secondary**: {doc['audience']['secondary']}", ""]

    L += ["## Editorial", "", "Tone: " + ", ".join(ed["tone"]), ""]
    L += [f"- {p}" for p in ed["principles"]]
    insp = ed.get("structural_inspiration_only")
    if insp:
        L += ["", f"### Structural inspiration — {insp['source']}", "",
              insp["permitted"], "", "Never:"]
        L += [f"- {p}" for p in insp["prohibited"]]
    L += [""]

    L += ["## Narrative structure", ""]
    L += [f"{s['step']}. {s['beat']}" for s in na["structure"]]
    g = na["portfolio_planning_guidance"]
    L += ["", "## Content portfolio", "", g["note"], "",
          "| share | area |", "|---|---|"]
    L += [f"| {s['share_pct']}% | {s['label']} |" for s in g["shares"]]
    L += [""]

    h = doc["host"]
    L += ["## Host", "", f"- **enabled**: {'yes' if h['enabled'] else 'no'}"]
    if h["enabled"]:
        t = h.get("target_presence_pct", {})
        L += [f"- **kind**: {h.get('kind', '—')}",
              f"- **never a real expert**: {'yes' if h.get('never_a_real_expert') else 'no'}",
              f"- **target presence**: {t.get('min')}–{t.get('max')}% "
              f"(soft ceiling {h.get('soft_ceiling_pct')}%)",
              f"- **max consecutive appearances**: {h.get('max_consecutive')}",
              f"- **used for**: {', '.join(h.get('used_for', [])) or '—'}",
              f"- **not used for**: {', '.join(h.get('not_used_for', [])) or '—'}"]
        ch = doc.get("character", {})
        L += [f"- **character spec**: `{ch.get('spec_path')}` "
              f"({ch.get('path_kind')})"]
    L += [""]

    v = doc["voice"]
    L += ["## Voice", "", f"- **selection**: `{v['selection_status']}`"]
    prof = v.get("approved_profile")
    if prof:
        L += [f"- **approved**: {prof['provider']} / {prof['voice']} — "
              f"{prof['approved_by']}, {prof['approved_at']}"]
    else:
        L += ["- **approved profile**: none on file"]
        wd = v.get("working_default")
        if wd:
            L += [f"- **working default (unapproved)**: {wd['provider']} / {wd['voice']}"]
        L += [f"- while pending, synthesis may only write into "
              f"`{v.get('preview_dir', 'voice_previews')}/`"]
    for r in v.get("requirements", []):
        L.append(f"- {r}")
    L += [""]

    vs = doc["visual_style"]
    L += ["## Visual style", "", "| token | colour |", "|---|---|"]
    L += [f"| {k} | `{val}` |" for k, val in sorted(vs["palette"].items())]
    L += ["", f"Letterbox padding: `{vs['pad_color']}`", ""]
    L += [f"- {r}" for r in vs["rules"]]
    L += ["", "## Renderer capabilities", "", "| visual type | renderer |", "|---|---|"]
    L += [f"| {vt} | `{rid}` |"
          for vt, rid in sorted(doc["renderers"]["capabilities"].items())]
    L += [""]

    ev = doc["evidence"]
    L += ["## Evidence", "",
          f"- provenance required for: {', '.join(ev['require_provenance_for']) or '—'}"]
    if ev.get("reconstruction_label"):
        L.append(f"- reconstruction label: “{ev['reconstruction_label']}”")
    if ev.get("document_policy"):
        L.append(f"- {ev['document_policy']}")
    L += ["", "## Safety", ""]
    L += [f"- {r}" for r in doc["safety"]["rules"]]
    hr = doc["safety"].get("human_review_required_for", [])
    if hr:
        L += ["", f"Human editorial review required for: {', '.join(hr)}."]

    econ = doc["economics"]
    L += ["", "## Economics", "", f"- currency: {econ['currency']}"]
    if econ["image_pricing"]:
        for k, p in sorted(econ["image_pricing"].items()):
            L.append(f"- {k}: {p['cost_usd']} ({p['basis']})")
    else:
        L.append("- no per-image price on file — plans report cost as unpriced rather "
                 "than estimating one")
    if econ.get("_note"):
        L += ["", econ["_note"]]
    return "\n".join(L) + "\n"


# ── root-level compatibility adapters ────────────────────────────────────────

def _provenance(doc: dict) -> dict:
    return {
        "_do_not_edit": ADAPTER_WARNING,
        "_generated_from": f"channels/{doc['channel_id']}/channel.json",
        "_source_sha256": cc.canonical_sha256(doc),
    }


def render_adapters(doc: dict) -> dict[str, str]:
    """The root-level files still read directly by un-migrated modules.

    Only the keys something actually reads are emitted. The vestigial blocks the
    old hand-maintained config carried are preserved in the pack under
    `_legacy_notes` and deliberately not reproduced here — emitting configuration
    that nothing consumes is how the old file came to describe a pipeline that no
    longer existed.
    """
    if not doc.get("legacy_adapter"):
        return {}

    vs = doc["visual_style"]
    config = dict(_provenance(doc))
    config["voice"] = cc._thaw(doc["voice"].get("legacy_config", {}))
    config["stitch"] = {"ken_burns_zoom": vs.get("ken_burns_zoom", 1.05)}
    config["image_pricing"] = cc._thaw(doc["economics"]["image_pricing"])

    brand = dict(_provenance(doc))
    brand["pad_color"] = vs["pad_color"]

    return {
        "channel_config.json": json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        "brand.json": json.dumps(brand, indent=2, ensure_ascii=False) + "\n",
    }


# ── writing ──────────────────────────────────────────────────────────────────

def generated_files(channel_id: str) -> dict[Path, str]:
    """Every file this pack generates, mapped to its expected content."""
    d = cc.pack_dir(channel_id)
    doc = json.loads((d / cc.PACK_NAME).read_text(encoding="utf-8"))
    cc.validate_document(doc, source=str(d / cc.PACK_NAME))
    out = {d / cc.DNA_NAME: render_dna(doc)}
    for name, text in render_adapters(doc).items():
        out[cc.PIPELINE_DIR / name] = text
    return out


def write_all(channel_id: str) -> list[tuple[Path, bool]]:
    results = []
    for path, text in generated_files(channel_id).items():
        changed = (not path.is_file()
                   or path.read_text(encoding="utf-8") != text)
        if changed:
            cc._write_atomic_text(path, text)
        results.append((path, changed))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--check", action="store_true",
                    help="Report drift without writing")
    args = ap.parse_args()

    try:
        expected = generated_files(args.channel)
    except cc.ChannelError as e:
        print(f"{e}", file=sys.stderr)
        return 1

    if args.check:
        drifted = [p for p, t in expected.items()
                   if not p.is_file() or p.read_text(encoding="utf-8") != t]
        for p in expected:
            state = "DRIFTED" if p in drifted else "ok"
            print(f"  {state:8} {p.relative_to(cc.PIPELINE_DIR)}")
        return 1 if drifted else 0

    for path, changed in write_all(args.channel):
        print(f"  {'wrote' if changed else 'unchanged':10} "
              f"{path.relative_to(cc.PIPELINE_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
