"""
validate_poses.py — verification package for the pose batch.

Produces the sheets a human needs to approve or reject, and the objective checks
that catch what the eye slides over. Generates nothing and calls no API.

    pose_contact_sheet.png       poses on a checkerboard, so alpha is visible
    pose_identity_sheet.png      every face at equal head height — drift check
    pose_composites.png          each pose over light, dark and scene backgrounds
    pose_edges.png               zoomed silhouette edges — fringing inspection
    pose_validation.json         alpha stats, provenance, hashes, failures
"""

import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PIPELINE_DIR = Path(__file__).parent
SPEC_PATH = PIPELINE_DIR / "character" / "character_spec.json"
OUT = PIPELINE_DIR / "character" / "exports"

sys.path.insert(0, str(PIPELINE_DIR))
from export_character_package import CREAM, MUTED, NAVY, font, head_box, head_height  # noqa: E402
from generate_poses import validate_alpha  # noqa: E402


def checkerboard(size, sq=16):
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    for y in range(0, size[1], sq):
        for x in range(0, size[0], sq):
            if (x // sq + y // sq) % 2:
                d.rectangle([x, y, x + sq, y + sq], fill=(214, 214, 214))
    return img


def load_poses(spec):
    out = []
    for r in spec["pose_library"].get("batch_1_results", []):
        if r.get("ok"):
            p = PIPELINE_DIR / r["path"]
            if p.exists():
                out.append((r, Image.open(p).convert("RGBA")))
    return out


def contact_sheet(poses):
    cell, pad, lab, hdr = 340, 14, 44, 56
    W = len(poses) * (cell + pad) + pad
    H = hdr + cell + lab + 2 * pad
    sheet = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(sheet)
    d.text((pad, 14), "Pose batch 1 — shown on a checkerboard so transparency is visible",
           font=font(True, 21), fill=NAVY)
    for i, (r, im) in enumerate(poses):
        x, y = pad + i * (cell + pad), hdr + pad
        thumb = im.copy(); thumb.thumbnail((cell, cell))
        tile = checkerboard((cell, cell))
        tile.paste(thumb, ((cell - thumb.width) // 2, (cell - thumb.height) // 2), thumb)
        sheet.paste(tile, (x, y))
        d.rectangle([x, y, x + cell, y + cell], outline=(200, 192, 180), width=1)
        d.text((x + 2, y + cell + 6), r["id"], font=font(True, 17), fill=NAVY)
        d.text((x + 2, y + cell + 25), f"{r['alpha']['transparent_pct']}% alpha · {r['transparency']}",
               font=font(False, 13), fill=MUTED)
    p = OUT / "pose_contact_sheet.png"; sheet.save(p); print(f"  ✓ {p.name}")


def identity_sheet(spec, poses):
    T = 300
    tiles = [("face master v3", Image.open(PIPELINE_DIR / spec["references"]["face_master"]).convert("RGB")),
             ("body master v4", Image.open(PIPELINE_DIR / spec["references"]["body_master"]).convert("RGB"))]
    for r, im in poses:
        flat = Image.new("RGB", im.size, (250, 247, 242)); flat.paste(im, (0, 0), im)
        tiles.append((r["id"], flat))

    crops = []
    for label, im in tiles:
        hh = head_height(im)
        if hh <= 0:
            continue
        c = im.crop(head_box(im)); s = T / hh
        crops.append((label, c.resize((max(1, int(c.width * s)), max(1, int(c.height * s))),
                                      Image.LANCZOS)))
    cw = max(c[1].width for c in crops); ch = max(c[1].height for c in crops)
    pad, lab, hdr = 14, 30, 58
    sheet = Image.new("RGB", (len(crops) * (cw + pad) + pad, hdr + ch + lab + 2 * pad), CREAM)
    d = ImageDraw.Draw(sheet)
    d.text((pad, 14), f"Identity at equal head height ({T}px) — masters vs poses",
           font=font(True, 21), fill=NAVY)
    for i, (label, im) in enumerate(crops):
        x, y = pad + i * (cw + pad), hdr + pad
        sheet.paste(im, (x + (cw - im.width) // 2, y + (ch - im.height)))
        d.rectangle([x, y, x + cw, y + ch], outline=(214, 206, 194), width=1)
        d.text((x + 2, y + ch + 6), label, font=font(True, 15), fill=NAVY)
    p = OUT / "pose_identity_sheet.png"; sheet.save(p); print(f"  ✓ {p.name}")


def composites(poses):
    """Each pose over light, dark and a representative scene background."""
    backdrops = [("light", Image.new("RGB", (340, 340), (250, 247, 242))),
                 ("dark", Image.new("RGB", (340, 340), (26, 43, 76)))]
    scene = Image.new("RGB", (340, 340), (232, 221, 199))
    ds = ImageDraw.Draw(scene)
    for i in range(0, 340, 34):                      # simple banded "scene"
        ds.rectangle([0, i, 340, i + 17], fill=(223, 210, 185))
    ds.rectangle([0, 250, 340, 340], fill=(196, 178, 148))
    backdrops.append(("scene", scene))

    cell, pad, hdr, lab = 340, 12, 56, 30
    W = len(poses) * (cell + pad) + pad
    H = hdr + len(backdrops) * (cell + lab + pad) + pad
    sheet = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(sheet)
    d.text((pad, 14), "Poses composited over light, dark and a representative scene",
           font=font(True, 21), fill=NAVY)
    for row, (bname, bg) in enumerate(backdrops):
        for col, (r, im) in enumerate(poses):
            x = pad + col * (cell + pad)
            y = hdr + pad + row * (cell + lab + pad)
            tile = bg.copy()
            t = im.copy(); t.thumbnail((cell - 20, cell - 20))
            tile.paste(t, ((cell - t.width) // 2, cell - t.height), t)
            sheet.paste(tile, (x, y))
            d.rectangle([x, y, x + cell, y + cell], outline=(200, 192, 180), width=1)
            txt = (250, 247, 242) if bname == "dark" else NAVY
            d.text((x + 6, y + 6), f"{r['id']} · {bname}", font=font(True, 14), fill=txt)
    p = OUT / "pose_composites.png"; sheet.save(p); print(f"  ✓ {p.name}")


def edges(poses):
    """Zoom the silhouette edge so fringing is inspectable, not inferred."""
    Z, cell, pad, hdr, lab = 4, 300, 12, 56, 34
    sheet = Image.new("RGB", (len(poses) * (cell + pad) + pad, hdr + cell + lab + 2 * pad), CREAM)
    d = ImageDraw.Draw(sheet)
    d.text((pad, 14), "Edge inspection — silhouette magnified 4x over magenta "
                      "(any halo shows as light pixels)", font=font(True, 19), fill=NAVY)
    for i, (r, im) in enumerate(poses):
        bbox = im.getbbox()
        # sample the left shoulder area, a long continuous edge
        cx = bbox[0] + (bbox[2] - bbox[0]) // 5
        cy = bbox[1] + (bbox[3] - bbox[1]) // 3
        half = cell // (2 * Z)
        crop = im.crop((max(0, cx - half), max(0, cy - half), cx + half, cy + half))
        crop = crop.resize((cell, cell), Image.NEAREST)
        tile = Image.new("RGB", (cell, cell), (255, 0, 255))
        tile.paste(crop, (0, 0), crop)
        x, y = pad + i * (cell + pad), hdr + pad
        sheet.paste(tile, (x, y))
        d.rectangle([x, y, x + cell, y + cell], outline=(200, 192, 180), width=1)
        d.text((x + 2, y + cell + 6), r["id"], font=font(True, 15), fill=NAVY)
        d.text((x + 2, y + cell + 24), f"fringe sample: {r['alpha']['fringe_pixels_sampled']} px",
               font=font(False, 13), fill=MUTED)
    p = OUT / "pose_edges.png"; sheet.save(p); print(f"  ✓ {p.name}")


def main():
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    poses = load_poses(spec)
    if not poses:
        sys.exit("no poses found")
    OUT.mkdir(parents=True, exist_ok=True)

    contact_sheet(poses)
    identity_sheet(spec, poses)
    composites(poses)
    edges(poses)

    print(f"\n{'pose':<30}{'alpha%':>8}{'partial%':>10}{'corners':>9}{'fringe':>8}{'hash':>12}")
    print("-" * 77)
    failures = []
    report = []
    for r, im in poses:
        live = validate_alpha(im)
        digest = hashlib.sha256((PIPELINE_DIR / r["path"]).read_bytes()).hexdigest()
        assert digest == r["sha256"], f"hash drift on {r['id']}"
        print(f"{r['id']:<30}{live['transparent_pct']:>7.1f}%{live['partial_pct']:>9.2f}%"
              f"{'  yes' if live['corners_transparent'] else '   NO':>9}"
              f"{live['fringe_pixels_sampled']:>8}{digest[:10]:>12}")
        if not live["corners_transparent"]:
            failures.append(f"{r['id']}: corners not transparent")
        if live["transparent_pct"] < 40:
            failures.append(f"{r['id']}: only {live['transparent_pct']}% transparent")
        if live["fringe_pixels_sampled"] > 400:
            failures.append(f"{r['id']}: {live['fringe_pixels_sampled']} fringe pixels")
        report.append({**r, "live_alpha": live, "verified_sha256": digest})

    print(f"\nfailures: {failures or 'none'}")
    (OUT / "pose_validation.json").write_text(
        json.dumps({"poses": report, "failures": failures}, indent=2), encoding="utf-8")
    print(f"written: {(OUT / 'pose_validation.json').relative_to(PIPELINE_DIR)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
