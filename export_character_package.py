"""
export_character_package.py — assemble the Checkpoint 1 review package.

Pure post-processing of assets that already exist. Generates nothing and calls
no API, so running it cannot introduce character drift or cost anything.

Produces under character/exports/:
    contact_sheet_11view.png   the labeled canonical sheet
    expression_faces.png       six expressions cropped to the face and enlarged
    master-front_full.png      the master at full resolution
    character_spec.json        the spec as reviewed
    mobile_preview.png         how the host reads at phone scale

The face sheet exists because the expression views came back full-body, so the
faces render small on the canonical sheet — the very thing that has to be judged
before deciding whether to rerun them.
"""

import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PIPELINE_DIR = Path(__file__).parent
SPEC_PATH = PIPELINE_DIR / "character" / "character_spec.json"
OUT_DIR = PIPELINE_DIR / "character" / "exports"

CREAM = (250, 247, 242)
NAVY = (26, 43, 76)
MUTED = (110, 100, 88)


def font(bold: bool, size: int):
    name = "arialbd.ttf" if bold else "arial.ttf"
    try:
        return ImageFont.truetype(rf"C:\Windows\Fonts\{name}", size)
    except OSError:
        return ImageFont.load_default()


def head_box(img: Image.Image) -> tuple[int, int, int, int]:
    """Locate the head by the hair mass, so crops follow the figure.

    The hair is the darkest region in the upper part of the frame; taking its
    bounding box and expanding downward captures the face regardless of where
    the figure stands. A fixed crop would clip whenever the pose shifts.
    """
    w, h = img.size
    grey = img.convert("L")
    limit = int(h * 0.45)
    mask = grey.point(lambda p: 1 if p < 90 else 0)

    # Density, not darkness. A plain dark-pixel bbox spans the whole figure,
    # because the navy outlines are as dark as the hair — measured identical
    # bboxes at every threshold from 25 to 90. The head is the only *solid*
    # dark mass; outlines below it are thin, so row density separates them.
    rows = [sum(mask.crop((0, y, w, y + 1)).getdata()) for y in range(limit)]
    peak = max(rows) if rows else 0
    if peak == 0:
        return int(w * 0.30), 0, int(w * 0.70), int(h * 0.30)

    cutoff = peak * 0.15
    dense = [y for y, v in enumerate(rows) if v >= cutoff]
    top_y = dense[0]
    bottom_y = top_y
    gap = 0
    for y in range(top_y, limit):
        if rows[y] >= cutoff:
            bottom_y, gap = y, 0
        else:
            gap += 1
            if gap > 24:            # head has ended, not a gap between features
                break

    band = mask.crop((0, top_y, w, bottom_y + 1)).getbbox()
    x0, x1 = (band[0], band[2]) if band else (int(w * 0.35), int(w * 0.65))
    cx = (x0 + x1) // 2
    head_h = bottom_y - top_y

    # square crop around the head, a little below for chin and collar
    box = int(max(x1 - x0, head_h) * 1.5)
    top = max(0, top_y - int(head_h * 0.30))
    left = max(0, cx - box // 2)
    return left, top, min(w, left + box), min(h, top + box)


def head_height(img: Image.Image) -> int:
    """Measured hair-top to chin-ish extent, used to normalise scale."""
    x0, y0, x1, y1 = head_box(img)
    return y1 - y0


def build_master_comparison(spec: dict) -> Path:
    """Body-master face beside the face master, normalised to equal head height.

    Scaling both crops to the same pixel box would be misleading — the faces
    occupy different fractions of their source images, so like would not be
    compared with like. Normalising on measured head height is what makes the
    eye-size and jaw-length changes readable.
    """
    body_p = PIPELINE_DIR / spec["references"]["body_master"]
    face_p = PIPELINE_DIR / spec["references"]["face_master"]
    for p in (body_p, face_p):
        if not p.exists():
            sys.exit(f"missing {p}")

    TARGET_HEAD = 420          # px, both faces normalised to this head height

    # Labels come from the spec, not from hardcoded strings. They were fixed at
    # "(v1)"/"(v2)" and so kept calling the current v3 image "v2" after it was
    # replaced — a caption that silently disagrees with the file it sits under.
    def _tag(key: str) -> str:
        m = spec["masters"].get(key, {})
        ver = m.get("approved_version") or m.get("version")
        state = m.get("status", "unknown")
        return f"v{ver} · {state}" if ver else state

    tiles = []
    for label, path, sub in (
            ("body master", body_p, f"{_tag('body_master')} — face cropped from full body"),
            ("face master", face_p, f"{_tag('face_master')} — authority for face/hair/glasses")):
        img = Image.open(path).convert("RGB")
        box = head_box(img)
        crop = img.crop(box)
        h_head = head_height(img)
        scale = TARGET_HEAD / max(h_head, 1)
        new_size = (max(1, int(crop.width * scale)), max(1, int(crop.height * scale)))
        tiles.append((label, sub, crop.resize(new_size, Image.LANCZOS), path))

    cell_w = max(t[2].width for t in tiles)
    cell_h = max(t[2].height for t in tiles)
    pad, header, label_h = 18, 64, 74
    W = 2 * cell_w + 3 * pad
    H = header + cell_h + label_h + 2 * pad

    sheet = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(sheet)
    d.text((pad, 14), "Face comparison at equal head height (normalised, not cropped to fit)",
           font=font(True, 22), fill=NAVY)
    d.text((pad, 40), f"both faces scaled so head height = {TARGET_HEAD}px",
           font=font(False, 15), fill=MUTED)

    for i, (label, sub, im, path) in enumerate(tiles):
        x = pad + i * (cell_w + pad)
        y = header + pad
        # bottom-align so the jawlines sit on a shared baseline
        sheet.paste(im, (x + (cell_w - im.width) // 2, y + (cell_h - im.height)))
        d.rectangle([x, y, x + cell_w, y + cell_h], outline=(214, 206, 194), width=2)
        d.text((x + 2, y + cell_h + 8), label, font=font(True, 20), fill=NAVY)
        d.text((x + 2, y + cell_h + 32), sub, font=font(False, 15), fill=MUTED)
        d.text((x + 2, y + cell_h + 52), Path(path).name, font=font(False, 13), fill=MUTED)

    # shared eye-line and chin-line, so the differences are measurable by eye
    for frac, name in ((0.42, "eye line"), (0.92, "chin line")):
        yy = header + pad + int(cell_h * frac)
        d.line([(pad, yy), (W - pad, yy)], fill=(200, 120, 110), width=1)
        d.text((W - pad - 66, yy - 16), name, font=font(False, 12), fill=(170, 90, 80))

    out = OUT_DIR / "face_master_comparison.png"
    sheet.save(out)
    print(f"  ✓ {out.name}  (equal head height {TARGET_HEAD}px)")
    return out


def build_expression_faces(spec: dict) -> Path:
    tiles = []
    for expr in spec["expressions"]:
        p = PIPELINE_DIR / spec["naming"]["expression"].format(expression_id=expr["id"])
        if p.exists():
            tiles.append((expr["id"], expr["brief"], p))
    if not tiles:
        sys.exit("no expression images found")

    cell, pad, label_h, header = 380, 16, 62, 58
    cols = 3
    rows = (len(tiles) + cols - 1) // cols
    W = cols * (cell + pad) + pad
    H = header + rows * (cell + label_h + pad) + pad
    sheet = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(sheet)
    d.text((pad, 14), "Expression sheet — faces cropped and enlarged from the "
                      "full-body renders (no regeneration)", font=font(True, 20), fill=NAVY)

    for i, (name, brief, path) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad)
        y = header + pad + r * (cell + label_h + pad)
        img = Image.open(path).convert("RGB")
        face = img.crop(head_box(img)).resize((cell, cell), Image.LANCZOS)
        sheet.paste(face, (x, y))
        d.rectangle([x, y, x + cell, y + cell], outline=(214, 206, 194), width=2)
        d.text((x + 2, y + cell + 8), name, font=font(True, 20), fill=NAVY)
        wrapped = brief if len(brief) < 46 else brief[:44] + "…"
        d.text((x + 2, y + cell + 32), wrapped, font=font(False, 15), fill=MUTED)

    out = OUT_DIR / "expression_faces.png"
    sheet.save(out)
    print(f"  ✓ {out.name}  ({len(tiles)} faces at {cell}px)")
    return out


def build_mobile_preview(spec: dict) -> Path:
    """How the host actually reads on a phone, at true scale.

    A 390pt-wide viewport with a 16:9 video frame is the real judging context —
    character detail that only survives at desktop size is not usable.
    """
    PHONE_W, PHONE_H = 390, 844
    sheet = Image.new("RGB", (PHONE_W, PHONE_H), (232, 228, 220))
    d = ImageDraw.Draw(sheet)
    d.text((14, 12), "Phone scale (390pt wide)", font=font(True, 15), fill=NAVY)

    master = Image.open(PIPELINE_DIR / spec["references"]["body_master"]).convert("RGB")

    # 1) the host inside a 16:9 video frame at full phone width
    fw, fh = PHONE_W - 28, int((PHONE_W - 28) * 9 / 16)
    frame = Image.new("RGB", (fw, fh), CREAM)
    fig = master.copy()
    fig.thumbnail((int(fh * 0.92), int(fh * 0.92)), Image.LANCZOS)
    frame.paste(fig, ((fw - fig.width) // 2, fh - fig.height))
    sheet.paste(frame, (14, 36))
    d.rectangle([14, 36, 14 + fw, 36 + fh], outline=(200, 192, 180), width=1)
    d.text((14, 36 + fh + 6), "in a 16:9 frame, full width", font=font(False, 14), fill=MUTED)

    # 2) the same figure at quarter width — the retention-critical size
    y = 36 + fh + 30
    qw = fw // 2
    qh = int(qw * 9 / 16)
    qframe = Image.new("RGB", (qw, qh), CREAM)
    qfig = master.copy()
    qfig.thumbnail((int(qh * 0.92), int(qh * 0.92)), Image.LANCZOS)
    qframe.paste(qfig, ((qw - qfig.width) // 2, qh - qfig.height))
    sheet.paste(qframe, (14, y))
    d.rectangle([14, y, 14 + qw, y + qh], outline=(200, 192, 180), width=1)
    d.text((14, y + qh + 6), "quarter-screen (feed / multitask)", font=font(False, 14), fill=MUTED)

    # 3) face detail at phone scale.
    #
    # This previously sourced the six expression renders. Those are archived as
    # provisional, so the panel silently rendered blank — a missing input read as
    # an empty row rather than an error. It now uses the current face master and
    # says so when one is absent.
    y2 = y + qh + 32
    d.text((14, y2), "face detail at phone scale", font=font(True, 15), fill=NAVY)
    face_p = PIPELINE_DIR / spec["references"].get("face_master", "")
    src = face_p if face_p.exists() else (PIPELINE_DIR / spec["references"]["body_master"])
    if not src.exists():
        d.text((14, y2 + 24), "no master available", font=font(False, 14), fill=(170, 90, 80))
    else:
        img = Image.open(src).convert("RGB")
        crop = img.crop(head_box(img))
        xs = 14
        for px, caption in ((112, "close-up"), (84, "mid"), (64, "small / feed")):
            sheet.paste(crop.resize((px, px), Image.LANCZOS), (xs, y2 + 22 + (112 - px)))
            d.text((xs, y2 + 142), caption, font=font(False, 12), fill=MUTED)
            xs += px + 14
        d.text((14, y2 + 162), f"source: {src.name}", font=font(False, 12), fill=MUTED)

    out = OUT_DIR / "mobile_preview.png"
    sheet.save(out)
    print(f"  ✓ {out.name}  (390x844)")
    return out


def main():
    if not SPEC_PATH.exists():
        sys.exit(f"no spec at {SPEC_PATH}")
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    src_sheet = PIPELINE_DIR / spec["references"]["contact_sheet"]
    if src_sheet.exists():
        shutil.copy2(src_sheet, OUT_DIR / "contact_sheet_11view.png")
        print("  ✓ contact_sheet_11view.png")

    master = PIPELINE_DIR / spec["references"]["body_master"]
    if master.exists():
        shutil.copy2(master, OUT_DIR / "master-front_full.png")
        print(f"  ✓ master-front_full.png  ({Image.open(master).size[0]}x"
              f"{Image.open(master).size[1]})")

    shutil.copy2(SPEC_PATH, OUT_DIR / "character_spec.json")
    print("  ✓ character_spec.json")

    if (PIPELINE_DIR / spec["references"].get("face_master", "")).exists():
        build_master_comparison(spec)
    expr_dir = PIPELINE_DIR / spec["references"]["expressions_dir"]
    if any(expr_dir.glob("expr_*.png")):
        build_expression_faces(spec)
    else:
        print("  (no live expression images — archived as provisional, skipping face sheet)")
    build_mobile_preview(spec)
    print(f"\nexports in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
