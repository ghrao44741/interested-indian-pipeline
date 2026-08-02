"""
generate_character.py — canonical character package for the channel host.

Reads character/character_spec.json (the single source of truth) and produces:

    character/canonical/master-front.png      generated once, from the text spec
    character/canonical/*.png                 every other view, ANCHORED to master
    character/expressions/expr_*.png          expression sheet, ANCHORED to master
    character/canonical/contact_sheet.png     one image for approval

Why anchoring matters
---------------------
Only the master is generated from text alone. Every other view goes through
images.edit() with the master attached, because independently generating a
front, a profile and six expressions produces nine subtly different people —
drift introduced before the pose library even starts, which is precisely the
problem this package exists to prevent.

Usage
-----
    python generate_character.py --master            # step 1, then look at it
    python generate_character.py --views             # step 2, needs an approved master
    python generate_character.py --contact-sheet     # assemble for approval

Nothing here touches an episode. This is a channel asset, independent of any
project's manifest or narration.
"""

import argparse
import base64
import json
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent
SPEC_PATH = PIPELINE_DIR / "character" / "character_spec.json"
MODEL = "gpt-image-2"
SIZE = "1024x1024"


def load_spec() -> dict:
    if not SPEC_PATH.exists():
        sys.exit(f"No character spec at {SPEC_PATH}")
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def get_client():
    try:
        from dotenv import load_dotenv
        import openai
    except ImportError:
        sys.exit("pip install openai python-dotenv")
    load_dotenv(PIPELINE_DIR / ".env")
    import os
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        sys.exit("OPENAI_API_KEY not set in .env")
    return openai.OpenAI(api_key=key)


def _style_line(spec: dict) -> str:
    return ("Flat 2D editorial cartoon illustration. "
            + "; ".join(spec["style"]["rules"])
            + f". Background plain {spec['style']['palette']['background_default']}.")


def _extract(response) -> bytes | None:
    for item in getattr(response, "data", []) or []:
        b64 = getattr(item, "b64_json", None)
        if b64:
            return base64.b64decode(b64)
    return None


def build_prompt(spec: dict, brief: str, expression: str = "neutral expression",
                 framing: str = "full_body") -> str:
    """Identity block + what may change + what may not. Order is deliberate.

    Negatives are framing-aware: "no cropped hands or feet" belongs only to
    full-body shots. Applying it universally contradicts a waist-up portrait or
    a head-and-shoulders expression, which crop limbs by definition.
    """
    negatives = spec["negative_prompt"]
    extra = spec.get("negative_prompt_by_framing", {}).get(framing, "")
    if extra:
        negatives = f"{negatives}, {extra}"

    output_rule = {
        "full_body": "single character, full figure visible head to feet",
        "waist_up": "single character, framed from the waist up, head fully visible",
        "head_shoulders": "single character, head and shoulders only, face filling "
                          "most of the frame, centered and symmetrical",
    }.get(framing, "single character")

    return (
        f"IDENTITY (must be preserved exactly):\n{spec['identity']['prompt_block']}\n\n"
        f"SCENE:\n{brief}\n\n"
        f"EXPRESSION:\n{expression}\n\n"
        f"STYLE:\n{_style_line(spec)}\n\n"
        f"ALLOWED TO CHANGE: {', '.join(spec['variable'])}.\n"
        f"MUST NOT CHANGE: {', '.join(spec['immutable'])}.\n\n"
        f"AVOID: {negatives}\n\n"
        f"OUTPUT: {output_rule}, plain flat background, no text anywhere in the image."
    )


def record_master(spec: dict, key: str, path: Path, status: str) -> None:
    """Stamp provenance on an approved master so it is identifiable later.

    An anchor with no recorded hash cannot be told apart from a regenerated
    lookalike, which is how a pose library silently ends up anchored to the
    wrong face.
    """
    import hashlib
    from datetime import date
    from PIL import Image

    data = path.read_bytes()
    with Image.open(path) as im:
        w, h = im.size
    entry = spec["masters"].setdefault(key, {})
    entry.update({
        "path": str(path.relative_to(PIPELINE_DIR)).replace("\\", "/"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "pixel_dimensions": f"{w}x{h}",
        "created": date.today().isoformat(),
        "status": status,
    })
    SPEC_PATH.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  recorded {key}: {w}x{h}, sha256 {entry['sha256'][:16]}…")


def generate_face_master(spec: dict, client, force: bool = False) -> Path | None:
    """The single face master, anchored to the approved body master.

    Anchored rather than generated fresh so the outfit, palette and hair
    silhouette carry over; the brief asks only for the face to mature.
    """
    body = PIPELINE_DIR / spec["references"]["body_master"]
    if not body.exists():
        sys.exit(f"  body master missing at {body}")
    out = PIPELINE_DIR / spec["references"]["face_master"]
    if out.exists() and not force:
        print(f"  face master already exists: {out}  (--force to regenerate)")
        return out

    fb = spec["face_master_brief"]
    prior = PIPELINE_DIR / spec["references"].get("face_master_v2_archived", "")
    refs = [body]

    if "corrections" in fb and prior.exists():
        # Controlled refinement: two references, each with a stated job. The body
        # master carries hair, glasses and overall identity; the previous face
        # master carries the mature jaw, neck and eye size that were approved.
        refs.append(prior)
        brief = (
            f"{fb['framing']}. This is a CONTROLLED REFINEMENT of the second "
            f"attached image, not a redesign.\n"
            f"Reference 1 (body master): authority for hair silhouette, glasses "
            f"style and overall identity.\n"
            f"Reference 2 (previous face master): authority for facial maturity "
            f"and close-up detail.\n"
            f"PRESERVE from reference 2: " + "; ".join(fb["preserve_from_v2"]) + ".\n"
            f"CORRECT: " + " ".join(f"({i+1}) {c}." for i, c in enumerate(fb["corrections"]))
            + f"\nLighting: {fb['lighting']}."
        )
    else:
        brief = (f"{fb['framing']}. Refine the face so it clearly reads as late twenties: "
                 + "; ".join(fb.get("refinements_from_body_master", []))
                 + f". Lighting: {fb['lighting']}.")

    prompt = build_prompt(spec, brief, expression=fb["expression"],
                          framing="head_shoulders")
    print(f"  generating the face master from {len(refs)} reference(s): "
          f"{', '.join(r.name for r in refs)}…")
    handles = []
    try:
        handles = [open(r, "rb") for r in refs]
        image_arg = handles if len(handles) > 1 else handles[0]
        resp = client.images.edit(model=MODEL, image=image_arg, prompt=prompt,
                                  size=SIZE, n=1)
    except Exception as e:
        # No silent fall back to a single reference: quietly dropping the
        # maturity anchor would undo exactly what this refinement preserves.
        print(f"  ✗ anchored generation failed ({e})")
        return None
    finally:
        for h in handles:
            h.close()
    data = _extract(resp)
    if not data:
        print("  ✗ no image returned")
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"  ✓ {out}")
    record_master(spec, "face_master", out, "pending-approval")
    return out


def generate_master(spec: dict, client, force: bool = False) -> Path:
    out = PIPELINE_DIR / spec["references"]["body_master"]
    if out.exists() and not force:
        print(f"  master already exists: {out}  (--force to regenerate)")
        return out
    out.parent.mkdir(parents=True, exist_ok=True)

    view = next(v for v in spec["canonical_views"] if v["id"] == "master-front")
    prompt = build_prompt(spec, view["brief"])
    print("  generating master from the text spec (the only unanchored image)…")
    resp = client.images.generate(model=MODEL, prompt=prompt, size=SIZE, quality="high", n=1)
    data = _extract(resp)
    if not data:
        sys.exit("  ✗ no image returned for master")
    out.write_bytes(data)
    print(f"  ✓ {out}")
    return out


def generate_anchored(spec: dict, client, master: Path, brief: str,
                      out: Path, expression: str = "neutral expression") -> bool:
    """One view, anchored to the approved master via images.edit()."""
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(spec, brief, expression)
    try:
        with open(master, "rb") as fh:
            resp = client.images.edit(model=MODEL, image=fh, prompt=prompt, size=SIZE, n=1)
        data = _extract(resp)
        if not data:
            print(f"  ✗ no image returned for {out.name}")
            return False
        out.write_bytes(data)
        print(f"  ✓ {out.name}")
        return True
    except Exception as e:
        # Deliberately not falling back to an unanchored generate(): a view that
        # silently ignores the master is exactly the drift this guards against.
        print(f"  ✗ {out.name}: anchored generation failed ({e})")
        return False


def generate_views(spec: dict, client) -> None:
    master = PIPELINE_DIR / spec["references"]["body_master"]
    if not master.exists():
        sys.exit("  master not found — run --master first, and look at it before continuing")

    for view in spec["canonical_views"]:
        if view["id"] == "master-front":
            continue
        out = PIPELINE_DIR / spec["naming"]["canonical"].format(view_id=view["id"])
        if out.exists():
            print(f"  skip {out.name} (exists)")
            continue
        generate_anchored(spec, client, master, view["brief"], out)

    for expr in spec["expressions"]:
        out = PIPELINE_DIR / spec["naming"]["expression"].format(expression_id=expr["id"])
        if out.exists():
            print(f"  skip {out.name} (exists)")
            continue
        generate_anchored(spec, client, master,
                          "head and shoulders portrait, front facing, centered",
                          out, expression=f"{expr['id']}: {expr['brief']}")


def build_contact_sheet(spec: dict) -> Path:
    """One sheet for approval — the decision is made on this, not on the checks."""
    from PIL import Image, ImageDraw, ImageFont

    tiles = []
    for view in spec["canonical_views"]:
        p = PIPELINE_DIR / spec["naming"]["canonical"].format(view_id=view["id"])
        if p.exists():
            tiles.append((view["id"], p))
    for expr in spec["expressions"]:
        p = PIPELINE_DIR / spec["naming"]["expression"].format(expression_id=expr["id"])
        if p.exists():
            tiles.append((expr["id"], p))

    if not tiles:
        sys.exit("  nothing generated yet")

    cols, cell, label_h, pad = 4, 320, 34, 12
    rows = (len(tiles) + cols - 1) // cols
    W = cols * (cell + pad) + pad
    H = rows * (cell + label_h + pad) + pad + 46
    sheet = Image.new("RGB", (W, H), (250, 247, 242))
    d = ImageDraw.Draw(sheet)

    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 20)
        small = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 17)
    except OSError:
        font = small = ImageFont.load_default()

    d.text((pad, 12), f"{spec['name']} — canonical package v{spec['version']} "
                      f"(age reads as {spec['identity']['age_reads_as']})",
           font=font, fill=(26, 43, 76))

    for i, (label, path) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad)
        y = 46 + pad + r * (cell + label_h + pad)
        img = Image.open(path).convert("RGB")
        img.thumbnail((cell, cell))
        sheet.paste(img, (x + (cell - img.width) // 2, y))
        d.text((x, y + cell + 6), label, font=small, fill=(60, 50, 40))

    out = PIPELINE_DIR / spec["references"]["contact_sheet"]
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"  ✓ contact sheet: {out}  ({len(tiles)} views)")
    return out


def main():
    ap = argparse.ArgumentParser(description="Generate the canonical character package")
    ap.add_argument("--master", action="store_true", help="Generate the body master only")
    ap.add_argument("--face-master", action="store_true",
                    help="Generate the single face master, anchored to the body master")
    ap.add_argument("--record-body-master", action="store_true",
                    help="Stamp provenance on the existing body master without generating")
    ap.add_argument("--views", action="store_true",
                    help="Generate all views anchored to the master (NOT authorized yet)")
    ap.add_argument("--contact-sheet", action="store_true", help="Assemble the approval sheet")
    ap.add_argument("--force", action="store_true", help="Regenerate the master even if present")
    args = ap.parse_args()

    spec = load_spec()

    if args.record_body_master:
        record_master(spec, "body_master",
                      PIPELINE_DIR / spec["references"]["body_master"],
                      "approved-provisional")
        return 0
    if args.contact_sheet and not (args.master or args.views or args.face_master):
        build_contact_sheet(spec)
        return 0

    client = get_client()
    if args.master:
        generate_master(spec, client, force=args.force)
    if args.face_master:
        generate_face_master(spec, client, force=args.force)
    if args.views:
        generate_views(spec, client)
    if args.contact_sheet:
        build_contact_sheet(spec)
    if not (args.master or args.face_master or args.views or args.contact_sheet):
        ap.error("pass --master, --face-master, --views or --contact-sheet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
