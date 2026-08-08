"""renderers.py — the allowlist of renderers a Channel Pack may name
(Task 2A), and the single, immutable execution registry a dispatcher and its
hash both read from (Task 2B-B2a).

A pack declares capabilities as `{"MAP": "india_geojson", ...}`: a visual type
mapped to a renderer *id*, never to an importable module path. Configuration does
not get to choose code by string. Every id a pack names must appear here, or the
pack is refused at load time — before anything is planned, let alone generated.

`implemented=False` names a renderer that does not exist yet. The same convention
is used by generation_gate.PAID_ENTRY_POINTS: a test asserts the named module is
genuinely absent, so an entry cannot quietly stay unimplemented once someone
writes the file.

`RENDERERS` is the ONLY execution registry. There is no second, parallel
`ADAPTERS` mapping: `dispatch_adapter()` (what a canonical dispatcher calls to
get the function to run) and `projection_for_hash()` (what
visual_routes.renderer_registry_projection() hashes) both read this exact
object, so dispatch can never be redirected through a mapping the hash never
saw. Every entry is deep-frozen at import time — a renderer's provider, model,
prompt policy, or provider parameters cannot be mutated at runtime; the only
way to change one is to edit this file and get a new hash.

    python renderers.py --list
"""

import argparse
import sys
from pathlib import Path
from types import MappingProxyType

PIPELINE_DIR = Path(__file__).parent

import prompt_policy
import renderer_adapters as _ra

# cost_category is what a plan reports and a human approves against:
#   free_local  — deterministic render on this machine, no account involved
#   free_api    — an external call that costs nothing but is rate limited
#   paid_api    — spends money per invocation
#   derived     — composites assets that were already paid for
COST_CATEGORIES = ("free_local", "free_api", "paid_api", "derived")


def _freeze(obj):
    """Recursive deep-freeze — a dict becomes a MappingProxyType of
    recursively-frozen values, a list becomes a tuple. Prevents any runtime
    mutation of a registry entry's provider/model/prompt-policy/provider-
    parameter settings, not merely a shallow one (provider_parameters is
    itself a dict and must be just as immutable as its parent entry)."""
    if isinstance(obj, dict):
        return MappingProxyType({k: _freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_freeze(v) for v in obj)
    return obj


_RAW_RENDERERS = {
    "india_geojson": {
        "module": "generate_india_map.py",
        "entry": "main",
        "adapter": _ra.adapt_map,
        "provider": "local",
        "model_id": None,
        "contract_version": 1,
        "prompt_policy_version": None,
        "provider_parameters": {},
        "supports_reference_input": False,
        "output_transform_version": 1,
        "implemented": True,
        "cost_category": "free_local",
        "note": "Geography is rendered from GeoJSON, never drawn by a model. "
                "India-specific by construction; another channel needing maps "
                "registers its own renderer rather than reusing this one.",
    },
    "matplotlib_chart": {
        "module": "generate_chart.py",
        "entry": "main",
        "adapter": _ra.adapt_chart,
        "provider": "local",
        "model_id": None,
        "contract_version": 1,
        "prompt_policy_version": None,
        "provider_parameters": {},
        "supports_reference_input": False,
        "output_transform_version": 1,
        "implemented": True,
        "cost_category": "free_local",
    },
    "deterministic_timeline": {
        "module": "generate_timeline.py",
        "entry": "main",
        "adapter": None,
        "provider": None,
        "model_id": None,
        "contract_version": 1,
        "prompt_policy_version": None,
        "provider_parameters": {},
        "supports_reference_input": False,
        "output_transform_version": None,
        "implemented": False,
        "cost_category": "free_local",
        "note": "Structured dates in, deterministic layout out. No model-rendered text.",
    },
    "deterministic_document": {
        "module": "generate_document.py",
        "entry": "main",
        "adapter": None,
        "provider": None,
        "model_id": None,
        "contract_version": 1,
        "prompt_policy_version": None,
        "provider_parameters": {},
        "supports_reference_input": False,
        "output_transform_version": None,
        "implemented": False,
        "cost_category": "free_local",
        "note": "Named source, explicit reconstruction label, text drawn by PIL. "
                "Never an AI facsimile of a real document.",
    },
    "pexels": {
        "module": "search_pexels.py",
        "entry": "main",
        "adapter": _ra.adapt_photo,
        "provider": "pexels",
        "model_id": None,
        "contract_version": 1,
        "prompt_policy_version": None,
        "provider_parameters": {},
        "supports_reference_input": False,
        "output_transform_version": 1,
        "implemented": True,
        "cost_category": "free_api",
        "note": "Free of charge but writes approved episode artwork, so it is "
                "approval-gated like any paid route.",
    },
    "flux_illustration": {
        "module": "generate_images_flux.py",
        "entry": "main",
        "adapter": _ra.adapt_flux,
        "provider": "xai",
        "model_id": "grok-imagine-image",
        "contract_version": 1,
        "prompt_policy_version": prompt_policy.PROMPT_POLICY_VERSION,
        "provider_parameters": {"n": 1},
        "supports_reference_input": False,
        "output_transform_version": 1,
        "implemented": True,
        "cost_category": "paid_api",
    },
    "flux_reenactment": {
        "module": "generate_images_flux.py",
        "entry": "main",
        "adapter": _ra.adapt_flux,
        "provider": "xai",
        "model_id": "grok-imagine-image",
        "contract_version": 1,
        "prompt_policy_version": prompt_policy.PROMPT_POLICY_VERSION,
        "provider_parameters": {"n": 1},
        "supports_reference_input": False,
        "output_transform_version": 1,
        "implemented": True,
        "cost_category": "paid_api",
        "note": "Shares the illustration backend today; a dedicated reenactment "
                "renderer is registered in generation_gate as images.reenactment "
                "and is not written yet.",
    },
    "approved_pose_compositor": {
        "module": "composite_character.py",
        "entry": "render_production",
        "adapter": _ra.adapt_host_composite,
        "provider": "local",
        "model_id": None,
        "contract_version": 1,
        "prompt_policy_version": None,
        "provider_parameters": {},
        "supports_reference_input": False,
        "output_transform_version": 1,
        "implemented": True,
        "cost_category": "derived",
        "note": "Composites an already-approved pose asset; spends nothing, but "
                "writes approved episode artwork.",
    },
    "flux_reference_anchor": {
        "module": "renderer_adapters.py",
        "entry": "adapt_flux_reference_anchor",
        "adapter": _ra.adapt_flux_reference_anchor,
        "provider": "openai",
        "model_id": "gpt-image-2",
        "contract_version": 1,
        "prompt_policy_version": prompt_policy.PROMPT_POLICY_VERSION,
        "provider_parameters": {"size": "1536x1024", "n": 1},
        "supports_reference_input": True,
        "output_transform_version": 1,
        "implemented": True,
        "cost_category": "paid_api",
        "note": "Reference-anchored generation via OpenAI's image-edit endpoint, "
                "anchored on both approved character masters (body then face). "
                "Its adapter contains no fallback to an unanchored image-generate call.",
    },
}

# The one, sole execution registry. Every entry deep-frozen — see _freeze()'s
# docstring above for exactly what that closes off.
RENDERERS = _freeze(_RAW_RENDERERS)


class RendererError(RuntimeError):
    """A capability map names something this build cannot honour."""


def get(renderer_id: str) -> MappingProxyType:
    if renderer_id not in RENDERERS:
        raise RendererError(
            f"unregistered renderer {renderer_id!r}; registered: {sorted(RENDERERS)}")
    return RENDERERS[renderer_id]


def dispatch_adapter(renderer_id: str):
    """The ONLY lookup a canonical dispatcher performs to find the callable
    for a renderer_id. Reads RENDERERS directly — there is no second mapping
    this could be redirected through."""
    return get(renderer_id)["adapter"]


def projection_for_hash(renderer_ids) -> dict:
    """The execution-affecting projection of every referenced renderer's
    entry, keyed by renderer_id, sorted. `adapter` (a live callable, not
    JSON-stable on its own) is represented by its qualified name instead —
    changing which function a renderer_id points at still changes this
    projection, and therefore the hash visual_routes.py computes over it."""
    out = {}
    for rid in sorted(set(renderer_ids)):
        entry = get(rid)
        adapter = entry.get("adapter")
        out[rid] = {
            "module": entry["module"],
            "entry": entry["entry"],
            "adapter_qualname": (f"{adapter.__module__}.{adapter.__qualname__}"
                                 if adapter is not None else None),
            "provider": entry.get("provider"),
            "model_id": entry.get("model_id"),
            "contract_version": entry.get("contract_version"),
            "prompt_policy_version": entry.get("prompt_policy_version"),
            "provider_parameters": dict(entry.get("provider_parameters") or {}),
            "supports_reference_input": bool(entry.get("supports_reference_input", False)),
            "output_transform_version": entry.get("output_transform_version"),
            "implemented": bool(entry["implemented"]),
            "cost_category": entry["cost_category"],
        }
    return out


def validate_capabilities(capabilities: dict) -> None:
    """Every declared capability names a registered, implemented renderer.

    Raises rather than warning. A pack that names a renderer nobody wrote would
    otherwise fail at dispatch time, which is after the approval and after the
    earlier shots in the batch have already been produced.
    """
    problems = []
    for visual_type, renderer_id in sorted(capabilities.items()):
        if not isinstance(renderer_id, str):
            problems.append(f"{visual_type}: renderer id must be a string, "
                            f"got {type(renderer_id).__name__}")
            continue
        entry = RENDERERS.get(renderer_id)
        if entry is None:
            problems.append(f"{visual_type}: unregistered renderer {renderer_id!r}")
            continue
        if entry["cost_category"] not in COST_CATEGORIES:
            problems.append(f"{visual_type}: {renderer_id} declares unknown cost "
                            f"category {entry['cost_category']!r}")
    if problems:
        raise RendererError("capability map is not honourable:\n"
                            + "\n".join(f"  - {p}" for p in problems))


def unimplemented(capabilities: dict) -> list[str]:
    """Declared capabilities whose renderer does not exist yet.

    Not an error at load time — a pack may legitimately declare a route before it
    is built — but a plan that reaches one must say so, and Task 2B's dispatch
    must refuse it.
    """
    return sorted(rid for rid in set(capabilities.values())
                  if rid in RENDERERS and not RENDERERS[rid]["implemented"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.parse_args()
    for rid, e in sorted(RENDERERS.items()):
        state = "" if e["implemented"] else "  NOT WRITTEN"
        print(f"  {rid:26} {e['cost_category']:11} {e['module']}{state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
