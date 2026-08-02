"""
check_character_consistency.py — measure the package against the immutable spec.

Objective checks only, deliberately. These do not decide approval; the contact
sheet does. Their job is to catch the drift a human eye slides over — a skin
tone that crept, a glasses colour that vanished, a head that grew — and to state
plainly what they cannot see.

Measured per asset:
    palette fidelity   share of pixels near each immutable palette colour
    head proportion    head height as a fraction of image height
    framing            whether the crop matches the declared framing
    background         plain flat background as specified
    outline weight     dark-outline pixel share, as a style proxy

Run:  python check_character_consistency.py
"""

import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from export_character_package import head_box  # noqa: E402  (shared detector)

PIPELINE_DIR = Path(__file__).parent
SPEC_PATH = PIPELINE_DIR / "character" / "character_spec.json"


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def share_near(img: Image.Image, rgb: tuple[int, int, int], tol: int = 34) -> float:
    """Fraction of pixels within `tol` of a colour, per channel."""
    small = img.convert("RGB").resize((256, 256), Image.NEAREST)
    px = small.load()
    hit = 0
    for y in range(256):
        for x in range(256):
            r, g, b = px[x, y]
            if abs(r - rgb[0]) <= tol and abs(g - rgb[1]) <= tol and abs(b - rgb[2]) <= tol:
                hit += 1
    return hit / (256 * 256)


def analyse(path: Path, spec: dict, framing: str) -> dict:
    img = Image.open(path).convert("RGB")
    w, h = img.size
    pal = spec["style"]["palette"]

    x0, y0, x1, y1 = head_box(img)
    head_frac = (y1 - y0) / h

    grey = img.convert("L")
    dark = sum(1 for p in grey.resize((256, 256)).getdata() if p < 90) / (256 * 256)

    # background: corners should all be the specified flat colour
    bg = hex_rgb(pal["background_default"])
    corners = [img.getpixel(p) for p in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3))]
    bg_ok = all(all(abs(c[i] - bg[i]) <= 12 for i in range(3)) for c in corners)

    expected = {"head_shoulders": (0.45, 0.95), "waist_up": (0.20, 0.55),
                "full_body": (0.08, 0.24)}[framing]

    return {
        "file": path.name,
        "framing": framing,
        "dimensions": f"{w}x{h}",
        "head_fraction": round(head_frac, 3),
        "framing_ok": expected[0] <= head_frac <= expected[1],
        "framing_expected": f"{expected[0]}-{expected[1]}",
        "skin": round(share_near(img, hex_rgb(pal["skin"])), 4),
        "hair": round(share_near(img, hex_rgb(pal["hair"]), 40), 4),
        "kurta": round(share_near(img, hex_rgb(pal["kurta"])), 4),
        "glasses_gold": round(share_near(img, hex_rgb(pal["glasses"]), 46), 4),
        "outline_share": round(dark, 4),
        "background_flat": bg_ok,
    }


def main() -> int:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    targets = [(PIPELINE_DIR / spec["references"]["face_master"], "head_shoulders"),
               (PIPELINE_DIR / spec["references"]["body_master"], "full_body")]
    for v in spec["planned_next_package"]["views"]:
        targets.append((PIPELINE_DIR / spec["naming"]["canonical"].format(view_id=v["id"]),
                        v["framing"]))
    for e in spec["planned_next_package"]["expressions"]:
        targets.append((PIPELINE_DIR / spec["naming"]["expression"].format(expression_id=e),
                        "head_shoulders"))

    rows = [analyse(p, spec, f) for p, f in targets if p.exists()]
    missing = [p.name for p, _ in targets if not p.exists()]

    hdr = (f"{'asset':<44}{'framing':<15}{'head%':>7}{'fit':>5}"
           f"{'skin':>8}{'hair':>8}{'gold':>8}{'bg':>5}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['file']:<44}{r['framing']:<15}{r['head_fraction']*100:>6.1f}%"
              f"{'  ok' if r['framing_ok'] else ' FAIL':>5}"
              f"{r['skin']*100:>7.1f}%{r['hair']*100:>7.1f}%{r['glasses_gold']*100:>7.1f}%"
              f"{'  y' if r['background_flat'] else '  n':>5}")

    def spread(key):
        vals = [r[key] for r in rows]
        return max(vals) - min(vals)

    print("\nconsistency across the package (max-min):")
    for key, label in (("skin", "skin tone share"), ("hair", "hair share"),
                       ("glasses_gold", "glasses gold share"),
                       ("outline_share", "outline weight")):
        print(f"  {label:<22} {spread(key)*100:5.2f} pp")

    framing_fail = [r["file"] for r in rows if not r["framing_ok"]]
    bg_fail = [r["file"] for r in rows if not r["background_flat"]]
    print(f"\nassets checked : {len(rows)}")
    print(f"framing issues : {framing_fail or 'none'}")
    print(f"background     : {bg_fail or 'all flat as specified'}")
    if missing:
        print(f"MISSING        : {missing}")

    print("\nNot measured here — judge these on the contact sheet:")
    for item in ("hair silhouette shape", "jaw angle and face width",
                 "glasses rim thickness and roundness", "eye size and design",
                 "expression correctness", "perceived age"):
        print(f"  - {item}")

    out = PIPELINE_DIR / "character" / "exports" / "consistency_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"assets": rows, "missing": missing}, indent=2),
                   encoding="utf-8")
    print(f"\nwritten: {out.relative_to(PIPELINE_DIR)}")
    return 1 if (framing_fail or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
