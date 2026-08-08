"""reference_registry.py — approved reference assets for
`host_method: reference_anchored_generation` (Task 2B-B2a).

Approved reference ids are exactly `body_master` and `face_master` — this
channel's own approved character masters, read directly from
`character_spec.json`'s existing `masters` block. There is deliberately no
separate `references/` directory, no separate registry file, and no second
path/hash authority: a reference here IS the master, by construction, so it
can never disagree with it the way a duplicated copy could drift.

A master is only exposed as an approved reference when:
  - its own recorded `status` is exactly `"approved"` (checked here, not
    assumed from a status the caller says it already saw elsewhere); and
  - the top-level `references.<key>` pointer agrees with `masters.<key>.path`
    (checked here directly too — defense in depth, the same
    belt-and-suspenders pattern generation_gate._check_pose_selection()
    already applies after pose_registry.resolve() has already succeeded,
    not solely relying on generation_gate._check_masters() having already
    run upstream).

Mirrors pose_registry.py's shape (`registry()`, `resolve()`, `audit()`,
context-optional legacy path) so the two modules read the same way.
"""

import json
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent
SPEC_PATH = PIPELINE_DIR / "character" / "character_spec.json"

APPROVED = "approved"
REFERENCE_IDS = ("body_master", "face_master")


class ReferenceError(RuntimeError):
    """Raised when a reference asset cannot be resolved safely."""


def _spec_path(context=None) -> Path:
    return SPEC_PATH if context is None else Path(context.character_spec_path)


def _character_root(context=None) -> Path:
    """The channel's own approved character directory — the containment
    root a reference's resolved path must never escape. Narrower than
    _asset_base(): the pipeline root also contains every other channel pack
    and every episode folder, so "underneath the pipeline root" is not a
    meaningful containment guarantee on its own.

    The legacy/no-context case is anchored to the fixed PIPELINE_DIR/character
    directory, not to SPEC_PATH — mirroring pose_registry._poses_root()'s
    identical choice. SPEC_PATH is only ever used to locate the registry
    *data* to read; asset resolution in the legacy case always happens
    against the real character directory, so a caller substituting a
    different SPEC_PATH (a test fixture, for instance) to exercise a mutated
    registry still resolves assets against the one real, approved tree."""
    if context is None:
        return (PIPELINE_DIR / "character").resolve()
    return Path(context.character_spec_path).parent


def _asset_base(context=None) -> Path:
    """The directory a registered relative master path is joined to."""
    return PIPELINE_DIR if context is None else Path(context.character_spec_path).parent.parent


def _load_spec(context=None) -> dict:
    return json.loads(_spec_path(context).read_text(encoding="utf-8"))


def registry(*, context=None) -> dict:
    """{"body_master"|"face_master": {"path", "sha256", "status"}}, populated
    only for masters that are exactly status=="approved" and whose top-level
    references.<key> pointer agrees with masters.<key>.path. Never raises —
    a missing/unapproved/disagreeing master is simply absent from the
    result, which is exactly what a caller building `approved_references`
    for visual_routes.validate_contract() needs: an absent id is refused by
    the existing `_verify_approved_reference()` the same way an unknown pose
    id is refused today."""
    spec = _load_spec(context)
    masters = spec.get("masters", {})
    refs = spec.get("references", {})
    out: dict = {}
    for key in REFERENCE_IDS:
        m = masters.get(key)
        if not isinstance(m, dict):
            continue
        if m.get("status") != APPROVED:
            continue
        top_level = refs.get(key)
        if not isinstance(top_level, str) or not top_level:
            continue
        if top_level != m.get("path"):
            continue
        path = m.get("path")
        sha256 = m.get("sha256")
        if not isinstance(path, str) or not path:
            continue
        out[key] = {"path": path, "sha256": sha256, "status": APPROVED}
    return out


def references_asset_base(*, context=None) -> Path:
    return _asset_base(context)


def references_root(*, context=None) -> Path:
    return _character_root(context)


def resolve(reference_id: str, *, context=None) -> Path:
    """Absolute, verified path to an approved reference asset, or raises.

    Delegates path/containment/hash verification to
    visual_routes._verify_asset_path_and_hash() — the same function
    validate_contract() itself uses for every reference-anchored route —
    rather than re-implementing that logic a second time. Imported locally
    to avoid a module-load-order assumption between the two files (neither
    currently imports the other at module scope)."""
    import visual_routes as vr

    reg = registry(context=context)
    record = reg.get(reference_id)
    if record is None:
        raise ReferenceError(
            f"unknown or unapproved reference {reference_id!r}; available: "
            f"{sorted(reg)}")
    problems = vr._verify_asset_path_and_hash(
        reference_id, record,
        asset_base=references_asset_base(context=context),
        root=references_root(context=context),
        kind="reference")
    if problems:
        raise ReferenceError("; ".join(problems))
    return (Path(references_asset_base(context=context)) / record["path"]).resolve()


def audit(*, context=None) -> dict:
    """Mirrors pose_registry.audit()'s {"ok", "problems", "unapproved"}
    three-way split. A master awaiting approval, or one whose top-level
    references.<key> pointer has drifted, is reported as `unapproved`/
    `problems` respectively — never silently treated as available."""
    spec = _load_spec(context)
    masters = spec.get("masters", {})
    refs = spec.get("references", {})
    ok, problems, unapproved = [], [], []
    for key in REFERENCE_IDS:
        m = masters.get(key)
        if not isinstance(m, dict):
            unapproved.append(f"{key} (no master recorded)")
            continue
        if m.get("status") != APPROVED:
            unapproved.append(f"{key} ({m.get('status')!r})")
            continue
        top_level = refs.get(key)
        if top_level != m.get("path"):
            problems.append(
                f"{key}: references.{key} ({top_level!r}) does not agree with "
                f"masters.{key}.path ({m.get('path')!r})")
            continue
        try:
            resolve(key, context=context)
            ok.append(key)
        except ReferenceError as e:
            problems.append(str(e))
    return {"ok": ok, "problems": problems, "unapproved": unapproved}


if __name__ == "__main__":
    a = audit()
    print(f"ok        : {a['ok']}")
    print(f"problems  : {a['problems'] or 'none'}")
    print(f"unapproved: {a['unapproved'] or 'none'}")
