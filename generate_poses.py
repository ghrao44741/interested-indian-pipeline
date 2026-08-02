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
from generation_gate import GateBlocked, require_generation_ready  # noqa: E402


def _write_spec_atomic(spec: dict) -> None:
    """Temp file plus os.replace, so a crash mid-write cannot truncate the spec.

    The spec carries every asset's provenance; a half-written one is worse than
    no write at all.
    """
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(SPEC_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
        os.replace(tmp, SPEC_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


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


class AssetIntegrityError(RuntimeError):
    """An asset failed verification against its approved record."""


MIN_TRANSPARENT_PCT = 40.0
MAX_FRINGE_PIXELS = 400


def verify_asset(path: Path, record: dict) -> tuple[bool, list[str], dict]:
    """The single verification used by both replay and the validator.

    Returns (ok, problems, observed). The approved hash in `record` is the
    authority — the observed digest is reported, never written back over it.
    Replay previously stored the observed hash as if it were approved, so a
    tampered file recorded its own tampering as the truth.
    """
    problems, observed = [], {}
    if not path.exists():
        return False, [f"missing file: {path}"], observed

    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    observed["sha256"] = digest
    expected = record.get("sha256")
    if expected and digest != expected:
        problems.append(f"sha256 mismatch — expected {expected[:16]}…, "
                        f"observed {digest[:16]}…")

    img = Image.open(path)
    observed["mode"] = img.mode
    if img.mode != "RGBA":
        problems.append(f"mode is {img.mode}, expected RGBA")
    img = img.convert("RGBA")

    dims = f"{img.size[0]}x{img.size[1]}"
    observed["dimensions"] = dims
    if record.get("dimensions") and dims != record["dimensions"]:
        problems.append(f"dimensions {dims}, expected {record['dimensions']}")

    alpha = validate_alpha(img)
    observed["alpha"] = alpha
    if not alpha["corners_transparent"]:
        problems.append("corners are not transparent")
    if alpha["transparent_pct"] < MIN_TRANSPARENT_PCT:
        problems.append(f"only {alpha['transparent_pct']}% transparent "
                        f"(minimum {MIN_TRANSPARENT_PCT}%)")
    if alpha["fringe_pixels_sampled"] > MAX_FRINGE_PIXELS:
        problems.append(f"{alpha['fringe_pixels_sampled']} fringe pixels "
                        f"(maximum {MAX_FRINGE_PIXELS})")
    if not alpha["subject_bbox"]:
        problems.append("empty subject bounding box")

    return (not problems), problems, observed


def generate_batch(spec: dict, client, batch: int = 1, force: bool = False) -> list[dict]:
    # Character-scope preflight: no episode manifest exists when the channel's own
    # assets are made, but drifted masters or a broken registry must still stop the
    # run before a single image is paid for.
    require_generation_ready(None, f"pose batch {batch}")
    pl = spec["pose_library"]
    status = pl.get("status", "")
    approved_batches = pl.get("approved_batches", [])
    authorized = (f"batch-{batch}-authorized" in status
                  or status == f"authorized-batch-{batch}")
    # An already-approved batch stays runnable for replay/verification — that is
    # how provenance gets re-checked without generating. It just may not create
    # new images unless the caller is explicit.
    if not authorized and batch not in approved_batches:
        sys.exit(f"  pose library status is {status!r} — batch {batch} not authorized")
    replay_only = not authorized and batch in approved_batches
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

    # Existing provenance, so a replay preserves records instead of destroying them.
    prior = {r["id"]: r for r in pl.get(f"{poses_key}_results", []) if r.get("ok")}
    registry = pl.get("registry", {})

    approved_ids = {pid for pid, e in registry.items()
                    if e.get("status", "").startswith("approved")}

    # A batch-wide --force over approved poses would spend one paid generation per
    # pose and overwrite each one's raw provenance. Replacement is deliberately a
    # single-pose, single-call operation instead.
    if force:
        clash = sorted({p["id"] for p in pl[poses_key]} & approved_ids)
        if clash:
            sys.exit(
                f"  refusing batch-wide --force over {len(clash)} APPROVED pose(s): "
                f"{', '.join(clash[:4])}{' …' if len(clash) > 4 else ''}\n"
                f"  Replacement is one pose at a time:\n"
                f"      python generate_poses.py --replacement-candidate <pose_id>")

    for pose in pl[poses_key]:
        out = out_dir / f"host_{pose['id']}.png"

        if out.exists() and not force:
            # Preserve the existing record rather than skipping it. Skipping
            # appended nothing, and the caller then overwrote batch_N_results with
            # that shorter list — a rerun with every file present wiped the entire
            # provenance history.
            kept = prior.get(pose["id"]) or registry.get(pose["id"])
            if not kept:
                # An unrecorded file is not evidence of anything. Appending a
                # failure keeps the result list complete, so the caller cannot
                # mistake a short list for success.
                print(f"  ✗ {out.name} exists but has NO provenance record")
                results.append({"id": pose["id"], "ok": False, "replayed": True,
                                "error": "file present but no approved record; "
                                         "restore from git or regenerate as a candidate"})
                continue

            ok, problems, observed = verify_asset(out, kept)
            # The approved sha256 is preserved verbatim; the observed digest is
            # reported separately and never written over it.
            record = {**kept, "ok": ok, "replayed": True,
                      "path": str(out.relative_to(PIPELINE_DIR)).replace("\\", "/"),
                      "observed": observed}
            if not ok:
                record["error"] = "; ".join(problems)
                print(f"  ✗ {out.name}: {record['error']}")
            else:
                print(f"  keep {out.name} (verified)")
            results.append(record)
            continue

        if replay_only and not force:
            # The batch is approved; creating a new image here would silently
            # replace an approved asset with an unreviewed one.
            print(f"  ⚠ {out.name} missing from an APPROVED batch — refusing to "
                  f"generate. Re-run with --force to deliberately replace it.")
            results.append({"id": pose["id"], "ok": False,
                            "error": "missing from approved batch; use --force"})
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


def generate_replacement_candidate(spec: dict, client, pose_id: str) -> int:
    """One approved pose, one paid call, one self-contained candidate version.

    Everything a replacement produces — raw response, processed asset and its
    record — lives under character/pose_candidates/<id>/vNN/. Nothing is written
    to character/poses/ or character/pose_sources/, so neither the approved asset
    nor its original raw provenance can be overwritten: the previous behaviour
    routed the processed file to a candidate path but still wrote the raw over
    the approved pose's own source.
    """
    require_generation_ready(None, f"replacement candidate for {pose_id}")
    pl = spec["pose_library"]
    registry = pl.get("registry", {})

    # Validate before spending anything.
    entry = registry.get(pose_id)
    if entry is None:
        print(f"  ✗ unknown pose {pose_id!r}; registered: {sorted(registry)}")
        return 2
    if not entry.get("status", "").startswith("approved"):
        print(f"  ✗ {pose_id} is {entry.get('status')!r}, not approved — "
              f"replacement applies to approved assets only")
        return 2
    approved_file = PIPELINE_DIR / entry["path"]
    if not approved_file.exists():
        print(f"  ✗ approved asset missing at {approved_file} — restore it from git "
              f"before creating a replacement candidate")
        return 2

    body = PIPELINE_DIR / spec["references"]["body_master"]
    face = PIPELINE_DIR / spec["references"]["face_master"]
    for p in (body, face):
        if not p.exists():
            print(f"  ✗ approved master missing: {p}")
            return 2

    brief_src = next((p for b in (1, 2) for p in pl.get(f"batch_{b}", [])
                      if p["id"] == pose_id), None)
    if brief_src is None:
        print(f"  ✗ no brief recorded for {pose_id}")
        return 2

    root = PIPELINE_DIR / "character" / "pose_candidates" / pose_id
    n = 1
    while (root / f"v{n:02d}").exists():
        n += 1
    vdir = root / f"v{n:02d}"
    vdir.mkdir(parents=True, exist_ok=True)

    auth = spec["master_authority"]
    brief = (
        f"Reference 1 (body master, APPROVED): authority for "
        f"{', '.join(auth['body_master'])}.\n"
        f"Reference 2 (face master v3, APPROVED): authority for "
        f"{', '.join(auth['face_master'])}.\n"
        f"Pose: {brief_src['brief']}.\n"
        "Both hands must be fully visible, anatomically correct, five fingers each. "
        "Carry no prop other than any the pose itself requires. "
        "Leave clear empty space around the gesture. "
        "Isolated character on a plain uniform background, no scenery, no shadow."
    )
    prompt = build_prompt(spec, brief,
                          expression=brief_src.get("expression", "neutral, relaxed mouth"),
                          framing="full_body")

    print(f"  replacement candidate for {pose_id} -> {vdir.relative_to(PIPELINE_DIR)}")
    handles = []
    try:
        handles = [open(body, "rb"), open(face, "rb")]
        resp = client.images.edit(model=MODEL, image=handles, prompt=prompt,
                                  size=SIZE, n=1)     # exactly one call
        data = _extract(resp)
    except Exception as e:
        print(f"  ✗ generation failed ({e})")
        return 1
    finally:
        for h in handles:
            h.close()
    if not data:
        print("  ✗ no image returned")
        return 1

    raw = vdir / "raw.png"
    raw.write_bytes(data)
    img = Image.open(raw).convert("RGBA")
    check = validate_alpha(img)
    if not check["has_alpha"] or not check["corners_transparent"]:
        img, _removal = remove_flat_background(img)
    asset = vdir / "asset.png"
    img.save(asset)

    ok, problems, observed = verify_asset(asset, {"dimensions": entry.get("dimensions")})
    record = {
        "id": pose_id, "version": n, "status": "pending-approval",
        "path": str(asset.relative_to(PIPELINE_DIR)).replace("\\", "/"),
        "raw": str(raw.relative_to(PIPELINE_DIR)).replace("\\", "/"),
        "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
        "dimensions": observed.get("dimensions"),
        "model": MODEL,
        "generation_mode": "images.edit (dual reference: body_master + face_master)",
        "references": [str(body.relative_to(PIPELINE_DIR)).replace("\\", "/"),
                       str(face.relative_to(PIPELINE_DIR)).replace("\\", "/")],
        "replaces": {"pose_id": pose_id, "approved_sha256": entry.get("sha256")},
        "created": date.today().isoformat(),
        "verification": {"ok": ok, "problems": problems, "alpha": observed.get("alpha")},
    }
    (vdir / "candidate.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    if not ok:
        # The raw response stays in the version directory for diagnosis, but an
        # unverified candidate is never registered.
        print(f"  ✗ candidate failed verification: {'; '.join(problems)}")
        print(f"     kept at {vdir.relative_to(PIPELINE_DIR)} for diagnosis; NOT registered")
        return 1

    fresh = load_spec()
    fresh["pose_library"].setdefault("replacement_candidates", []).append(record)
    _write_spec_atomic(fresh)
    print(f"  ✓ candidate v{n:02d} verified and recorded as pending-approval")
    print(f"    approved asset untouched: {entry['path']}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Generate transparent pose assets")
    ap.add_argument("--batch", type=int, default=1, help="Which authorized batch to generate")
    ap.add_argument("--replacement-candidate", metavar="POSE_ID", default=None,
                    help="Generate ONE replacement candidate for a single approved pose. "
                         "Writes only under character/pose_candidates/<id>/vNN/.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    spec = load_spec()

    # Preflight here as well as inside the generators. get_client() reads the key
    # and constructs the OpenAI client, so gating only inside generate_batch()
    # would build a client for a run that can never be allowed to spend. The
    # inner calls stay: a direct caller of generate_batch() must not be able to
    # skip the check by not going through this CLI.
    try:
        require_generation_ready(None, "pose generation")
    except GateBlocked as e:
        print(f"\n{e}")
        print("\nNo client was created and nothing was generated.")
        return 1

    if args.replacement_candidate:
        if args.force:
            ap.error("--force is meaningless with --replacement-candidate; "
                     "each request creates a new version")
        return generate_replacement_candidate(spec, get_client(),
                                              args.replacement_candidate)

    results = generate_batch(spec, get_client(), batch=args.batch, force=args.force)

    # Whole-set validation: a short, duplicated or partly-failing result list is
    # a failure, however healthy the individual entries look.
    expected_ids = [p["id"] for p in spec["pose_library"][f"batch_{args.batch}"]]
    got_ids = [r.get("id") for r in results]
    set_problems = []
    if len(got_ids) != len(expected_ids):
        set_problems.append(f"expected {len(expected_ids)} results, got {len(got_ids)}")
    if len(set(got_ids)) != len(got_ids):
        dupes = sorted({i for i in got_ids if got_ids.count(i) > 1})
        set_problems.append(f"duplicate result ids: {dupes}")
    if set(got_ids) != set(expected_ids):
        set_problems.append(f"id mismatch — missing {sorted(set(expected_ids) - set(got_ids))}, "
                            f"unexpected {sorted(set(got_ids) - set(expected_ids))}")
    failed = [r for r in results if not r.get("ok")]
    if failed:
        set_problems.append(f"{len(failed)} asset(s) failed: "
                            + "; ".join(f"{r['id']}: {r.get('error', '?')}" for r in failed))
    if set_problems:
        print("\n  ✗ batch verification FAILED — spec not written:")
        for p in set_problems:
            print(f"     - {p}")
        return 1

    newly_generated = [r for r in results if r.get("ok") and not r.get("replayed")]
    if not newly_generated:
        # Nothing was created, so there is nothing to record. Rewriting the spec
        # anyway would churn the file — and the requirement is that a pure replay
        # leaves it byte-for-byte identical.
        kept = [r for r in results if r.get("ok")]
        print(f"\n  {len(kept)}/{len(results)} preserved from existing assets — "
              f"no generation, spec untouched")
        return 0 if len(kept) == len(results) else 1

    # Candidates are recorded separately. Writing them into batch_N_results would
    # overwrite the approved provenance with unreviewed entries — the approved
    # registry and files must stay untouched until an explicit promotion.
    candidates = [r for r in newly_generated if "pose_candidates" in r.get("path", "")]
    if candidates:
        fresh = load_spec()
        key = f"batch_{args.batch}_candidates"
        fresh["pose_library"][key] = (fresh["pose_library"].get(key, []) + candidates)
        _write_spec_atomic(fresh)
        print(f"\n  {len(candidates)} candidate(s) recorded under {key}, "
              f"status pending-approval — approved assets untouched")
        return 0

    fresh = load_spec()
    existing = fresh["pose_library"].get(f"batch_{args.batch}_results", [])
    if len(results) < len([r for r in existing if r.get("ok")]):
        # Guard the guard: never shrink a results list. If this fires, something
        # upstream failed and the spec must not be rewritten from it.
        sys.exit(f"  refusing to write {len(results)} record(s) over "
                 f"{len(existing)} existing — provenance would be lost")
    fresh["pose_library"][f"batch_{args.batch}_results"] = results
    _write_spec_atomic(fresh)

    ok = [r for r in results if r.get("ok")]
    print(f"\n  {len(ok)}/{len(results)} poses generated")
    return 0 if len(ok) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
