"""
generate_poses.py — transparent pose assets for the approved host.

Every pose is generated from BOTH approved masters (body master v4 for full-body
identity, clothing and proportions; face master v3 for the face). There is no
single-reference or unanchored fallback: a pose that quietly dropped one master
would reintroduce either the superseded face or a redesigned outfit, which is
exactly what the locked checkpoint exists to prevent.

Transparency is requested natively where the API supports it. If the returned
image is fully opaque, a deterministic background removal runs instead — the spec
permits "a validated background-removal stage with per-asset verification", and
every asset is verified either way. Which route produced a given asset is
recorded, never assumed.

Run:  python generate_poses.py --batch-1
"""

import argparse
import base64
import hashlib
import json
import sys
from collections import deque
from datetime import date
from pathlib import Path

from PIL import Image

PIPELINE_DIR = Path(__file__).parent
SPEC_PATH = PIPELINE_DIR / "character" / "character_spec.json"
MODEL = "gpt-image-2"
SIZE = "1024x1024"

sys.path.insert(0, str(PIPELINE_DIR))
from generate_character import build_prompt, get_client, load_spec  # noqa: E402


def _extract(response) -> bytes | None:
    for item in getattr(response, "data", []) or []:
        b64 = getattr(item, "b64_json", None)
        if b64:
            return base64.b64decode(b64)
    return None


def remove_flat_background(img: Image.Image, tol: int = 26) -> tuple[Image.Image, dict]:
    """Flood-fill the flat background from the edges and make it transparent.

    Edge-seeded flood fill rather than a global colour match: the kurta is nearly
    the same cream as the background, so removing every matching pixel would
    punch holes straight through the clothing. Only background connected to the
    frame edge is cleared.
    """
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    seed = px[0, 0]

    def near(c):
        return all(abs(c[i] - seed[i]) <= tol for i in range(3))

    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if near(px[x, y]):
                q.append((x, y)); seen[y * w + x] = 1
    for y in range(h):
        for x in (0, w - 1):
            if near(px[x, y]) and not seen[y * w + x]:
                q.append((x, y)); seen[y * w + x] = 1

    cleared = 0
    while q:
        x, y = q.popleft()
        px[x, y] = (px[x, y][0], px[x, y][1], px[x, y][2], 0)
        cleared += 1
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and near(px[nx, ny]):
                seen[ny * w + nx] = 1
                q.append((nx, ny))

    return img, {"method": "edge_seeded_flood_fill", "tolerance": tol,
                 "cleared_fraction": round(cleared / (w * h), 4)}


def validate_alpha(img: Image.Image) -> dict:
    """Per-asset transparency verification, including fringing."""
    img = img.convert("RGBA")
    w, h = img.size
    alpha = img.getchannel("A")
    data = list(alpha.getdata())
    n = len(data)
    transparent = sum(1 for a in data if a == 0)
    opaque = sum(1 for a in data if a == 255)
    partial = n - transparent - opaque

    corners = [alpha.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]

    # fringing: background-coloured pixels still opaque and touching transparency
    rgba = img.load()
    fringe = 0
    for y in range(1, h - 1, 2):
        for x in range(1, w - 1, 2):
            r, g, b, a = rgba[x, y]
            if a < 250:
                continue
            if r > 238 and g > 234 and b > 226:      # cream-ish leftover
                if any(rgba[x + dx, y + dy][3] < 40 for dx, dy in
                       ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    fringe += 1

    bbox = img.getbbox()
    return {
        "has_alpha": partial + transparent > 0,
        "transparent_pct": round(transparent / n * 100, 2),
        "partial_pct": round(partial / n * 100, 3),
        "corners_transparent": all(c == 0 for c in corners),
        "fringe_pixels_sampled": fringe,
        "subject_bbox": list(bbox) if bbox else None,
    }


def generate_batch(spec: dict, client, batch: int = 1, force: bool = False) -> list[dict]:
    pl = spec["pose_library"]
    if f"batch-{batch}-authorized" not in pl.get("status", "") and        pl.get("status") != f"authorized-batch-{batch}":
        sys.exit(f"  pose library status is {pl.get('status')!r} — batch {batch} not authorized")
    poses_key = f"batch_{batch}"

    body = PIPELINE_DIR / spec["references"]["body_master"]
    face = PIPELINE_DIR / spec["references"]["face_master"]
    for p in (body, face):
        if not p.exists():
            sys.exit(f"  approved master missing: {p}")

    auth = spec["master_authority"]
    out_dir = PIPELINE_DIR / "character" / "poses"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Raws never land in the runtime directory — a glob there must not be able to
    # find an opaque, unapproved image even transiently.
    raw_dir = PIPELINE_DIR / "character" / "pose_sources" / f"batch{batch}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for pose in pl[poses_key]:
        out = out_dir / f"host_{pose['id']}.png"
        if out.exists() and not force:
            print(f"  skip {out.name} (exists)")
            continue

        brief = (
            f"Reference 1 (body master, APPROVED): authority for "
            f"{', '.join(auth['body_master'])}.\n"
            f"Reference 2 (face master v3, APPROVED): authority for "
            f"{', '.join(auth['face_master'])}.\n"
            f"Pose: {pose['brief']}.\n"
            "Both hands must be fully visible, anatomically correct, five fingers each. "
            "Carry no prop other than any the pose itself requires. "
            "Leave clear empty space around the gesture. "
            "Isolated character on a plain uniform background, no scenery, no shadow "
            "cast onto any surface."
        )
        prompt = build_prompt(spec, brief,
                              expression=pose.get("expression", "neutral, relaxed mouth"),
                              framing="full_body")

        handles, data, native = [], None, False
        try:
            handles = [open(body, "rb"), open(face, "rb")]
            try:
                resp = client.images.edit(model=MODEL, image=handles, prompt=prompt,
                                          size=SIZE, n=1, background="transparent",
                                          output_format="png")
                native = True
            except TypeError:
                for h in handles:
                    h.seek(0)
                resp = client.images.edit(model=MODEL, image=handles, prompt=prompt,
                                          size=SIZE, n=1)
            except Exception as e:
                if "background" not in str(e).lower():
                    raise
                print(f"    (native transparency unsupported: {str(e)[:60]})")
                for h in handles:
                    h.seek(0)
                resp = client.images.edit(model=MODEL, image=handles, prompt=prompt,
                                          size=SIZE, n=1)
            data = _extract(resp)
        except Exception as e:
            # References are never dropped — failing loudly beats a pose anchored
            # to one master.
            print(f"  ✗ {pose['id']}: anchored generation failed ({e})")
            results.append({"id": pose["id"], "ok": False, "error": str(e)[:160]})
            continue
        finally:
            for h in handles:
                h.close()

        if not data:
            results.append({"id": pose["id"], "ok": False, "error": "no image returned"})
            continue

        raw = raw_dir / f"_raw_{pose['id']}.png"
        raw.write_bytes(data)
        img = Image.open(raw).convert("RGBA")

        check = validate_alpha(img)
        removal = None
        if not check["has_alpha"] or not check["corners_transparent"]:
            img, removal = remove_flat_background(img)
            check = validate_alpha(img)
        img.save(out)

        results.append({
            "id": pose["id"],
            "ok": True,
            "path": str(out.relative_to(PIPELINE_DIR)).replace("\\", "/"),
            "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            "dimensions": f"{img.size[0]}x{img.size[1]}",
            "model": MODEL,
            "generation_mode": "images.edit (dual reference: body_master v4 + face_master v3)",
            "references": [str(body.relative_to(PIPELINE_DIR)).replace("\\", "/"),
                           str(face.relative_to(PIPELINE_DIR)).replace("\\", "/")],
            "transparency": "native" if native and removal is None else "background_removed",
            "removal": removal,
            "alpha": check,
            "created": date.today().isoformat(),
            "status": "pending-approval",
            "batch": batch,
            "direction": pose.get("expect_direction"),
            "negative_space": pose.get("expect_negative_space"),
            "props": pose.get("expect_props", []),
            "framing": "full_body",
            "includes_geometry": [],
            "expression": pose.get("expression"),
        })
        print(f"  ✓ {out.name}  ({results[-1]['transparency']}, "
              f"{check['transparent_pct']}% transparent)")

    return results


def main():
    ap = argparse.ArgumentParser(description="Generate transparent pose assets")
    ap.add_argument("--batch", type=int, default=1, help="Which authorized batch to generate")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    spec = load_spec()
    results = generate_batch(spec, get_client(), batch=args.batch, force=args.force)

    fresh = load_spec()
    fresh["pose_library"][f"batch_{args.batch}_results"] = results
    SPEC_PATH.write_text(json.dumps(fresh, indent=2, ensure_ascii=False), encoding="utf-8")

    ok = [r for r in results if r.get("ok")]
    print(f"\n  {len(ok)}/{len(results)} poses generated")
    return 0 if len(ok) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
