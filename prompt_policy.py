"""prompt_policy.py — the sole place any canonical adapter constructs the
text sent to an illustration/reenactment provider (Task 2B-B2a).

Corrects a live defect in generate_images_flux.py's STYLE_PREFIX: that
constant unconditionally injects a full host-identity description into every
prompt, regardless of whether the route's own host_present field is true.
Approval-binding that unchanged would lock in "every illustration forces the
host in," which is wrong today and must not become a canonical guarantee.

Host text is never injected by a generic "if host_present" rule — it branches
on host_method, because the three host states need three different (or no)
instructions:

  - host_present=False              — style + route prompt only, nothing else.
  - approved_pose_composite         — the provider must NOT draw a host at
    all; a deterministic placement instruction reserves clean space (derived
    from the route's own host_placement) for the pose to be composited in
    afterward.
  - reference_anchored_generation   — the adult host-identity description is
    included, plus an instruction to match the attached reference images
    exactly. No fallback path in this module ever drops the reference
    instruction and continues as if it were an ordinary illustration.

This module has no adapter-facing "generic host" branch and no other exit —
every host_method value not covered above (there are only two, per
visual_routes.HOST_METHODS) adds no host text at all, leaving the contract
violation to schema/validate_contract, which already reject it independently.
"""

import hashlib

STYLE_POLICY = (
    "Flat digital cartoon illustration for an educational, friendly, "
    "approachable YouTube video essay, warm cream background (#FAF7F2), "
    "bold black outlines, expressive characters, vibrant colors. No "
    "photorealism, no 3D rendering. 16:9 landscape composition. CRITICAL — "
    "do not render ANY text, words, titles, headings, captions, channel "
    "names, or logos anywhere in the image, even if a name or phrase is "
    "mentioned in the scene description below. All text is added separately "
    "afterward; the illustration itself must be completely wordless except "
    "where a scene explicitly calls for an in-world prop with text on it (a "
    "sign, a document, a banner someone is holding)."
)

# Adult host contract. Deliberately replaces every trace of the earlier
# child-mascot description ("chubby round Indian cartoon boy", spiky hair,
# short arms, gathered-ankle trousers) — none of that survives here. Only
# injected for host_method == "reference_anchored_generation"; never for
# approved_pose_composite (the provider must not draw a host at all there)
# and never when host_present is false.
HOST_IDENTITY_POLICY = (
    "HOST (use this EXACT description every time a host reference render is "
    "requested): stylized Indian male, approximately 25 to 30 years old, thin "
    "round gold glasses, swept-up overlapping dark hair with a controlled "
    "silhouette, adult face/jaw/neck/body proportions throughout, "
    "smaller adult-looking eyes, a longer chin-to-mouth region, no facial "
    "hair, ivory three-button standing-collar kurta, off-white trousers, "
    "brown open-toe sandals. Flat cel-shaded illustration style, consistent "
    "with the surrounding scene."
)

REFERENCE_HOST_POLICY = (
    "Match the attached reference image(s) exactly for the character's "
    "appearance, proportions, and identifying features. Do not deviate from "
    "the reference — this is an anchored render, not a fresh interpretation."
)

# {framing} is filled from the route's own host_placement.framing (falling
# back to host_placement.position, then a generic "the scene" if neither is
# recorded) — never a hardcoded region, since placement is per-route data.
COMPOSITE_PLACEMENT_POLICY_TEMPLATE = (
    "Do not draw any human figure, mascot, or host character anywhere in "
    "this image. Reserve clean, unobstructed visual space at {framing} for a "
    "character to be composited in separately afterward. Keep that region "
    "free of competing detail, text, or other subjects that would collide "
    "with a composited figure."
)

STYLE_SUFFIX = (
    " Bold outlines, vibrant colors, 16:9. No title text, no channel name "
    "rendered in the image."
)

_TYPE_ROUTE_ARGS_KEY = {"ILLUSTRATION": "illustration", "REENACTMENT": "reenactment"}


def typed_prompt(route: dict) -> str | None:
    """The typed route_args prompt for an ILLUSTRATION/REENACTMENT route, or
    None if the route's visual_type has no typed-prompt slot, or the slot is
    itself null. This is the execution authority (Task 2B-B2a amendment 2) —
    build_effective_prompt() below reads this, not route['prompt'], whenever
    it is present."""
    key = _TYPE_ROUTE_ARGS_KEY.get(route.get("visual_type"))
    if key is None:
        return None
    args = (route.get("route_args") or {}).get(key)
    if not isinstance(args, dict):
        return None
    return args.get("prompt")


def build_effective_prompt(route: dict) -> str:
    """The exact string a provider call must use. Derives content from the
    typed route_args prompt (the execution authority) whenever the route's
    visual_type carries one, falling back to route['prompt'] only when no
    typed slot applies (e.g. this function is also exercised directly in
    tests against fixtures that predate a full contract check) — a schema-
    and contract-valid READY ILLUSTRATION/REENACTMENT route always has the
    two in agreement by the time dispatch would ever call this."""
    tp = typed_prompt(route)
    base_prompt = tp if tp is not None else (route.get("prompt") or "")

    parts = [STYLE_POLICY, base_prompt]

    host_present = bool(route.get("host_present"))
    host_method = route.get("host_method")

    if not host_present:
        pass
    elif host_method == "approved_pose_composite":
        placement = route.get("host_placement") or {}
        framing = placement.get("framing") or placement.get("position") or "the scene"
        parts.append(COMPOSITE_PLACEMENT_POLICY_TEMPLATE.format(framing=framing))
    elif host_method == "reference_anchored_generation":
        parts.append(HOST_IDENTITY_POLICY)
        parts.append(REFERENCE_HOST_POLICY)
    # Any other host_present/host_method combination adds no host text here —
    # it is a contract violation the schema/validate_contract already catch
    # independently; this function never guesses at what such a route meant.

    parts.append(STYLE_SUFFIX)
    return " ".join(p for p in parts if p)


def _policy_hash() -> str:
    """Hashes the live constants above, not a hand-copied string — editing
    any of them changes this value automatically, so it can never silently
    drift from what build_effective_prompt() actually produces."""
    payload = "\x1f".join((
        STYLE_POLICY, HOST_IDENTITY_POLICY, REFERENCE_HOST_POLICY,
        COMPOSITE_PLACEMENT_POLICY_TEMPLATE, STYLE_SUFFIX,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


PROMPT_POLICY_VERSION = _policy_hash()
