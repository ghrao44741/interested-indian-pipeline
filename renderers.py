"""renderers.py — the allowlist of renderers a Channel Pack may name.

A pack declares capabilities as `{"MAP": "india_geojson", ...}`: a visual type
mapped to a renderer *id*, never to an importable module path. Configuration does
not get to choose code by string. Every id a pack names must appear here, or the
pack is refused at load time — before anything is planned, let alone generated.

`implemented=False` names a renderer that does not exist yet. The same convention
is used by generation_gate.PAID_ENTRY_POINTS: a test asserts the named module is
genuinely absent, so an entry cannot quietly stay unimplemented once someone
writes the file.

Task 2A defines and validates this contract and calls nothing. Wiring the routes
themselves is Task 2B.

    python renderers.py --list
"""

import argparse
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent

# cost_category is what a plan reports and a human approves against:
#   free_local  — deterministic render on this machine, no account involved
#   free_api    — an external call that costs nothing but is rate limited
#   paid_api    — spends money per invocation
#   derived     — composites assets that were already paid for
COST_CATEGORIES = ("free_local", "free_api", "paid_api", "derived")

RENDERERS = {
    "india_geojson": {
        "module": "generate_india_map.py",
        "entry": "main",
        "cost_category": "free_local",
        "implemented": True,
        "note": "Geography is rendered from GeoJSON, never drawn by a model. "
                "India-specific by construction; another channel needing maps "
                "registers its own renderer rather than reusing this one.",
    },
    "matplotlib_chart": {
        "module": "generate_chart.py",
        "entry": "main",
        "cost_category": "free_local",
        "implemented": True,
    },
    "deterministic_timeline": {
        "module": "generate_timeline.py",
        "entry": "main",
        "cost_category": "free_local",
        "implemented": False,
        "note": "Structured dates in, deterministic layout out. No model-rendered text.",
    },
    "deterministic_document": {
        "module": "generate_document.py",
        "entry": "main",
        "cost_category": "free_local",
        "implemented": False,
        "note": "Named source, explicit reconstruction label, text drawn by PIL. "
                "Never an AI facsimile of a real document.",
    },
    "pexels": {
        "module": "search_pexels.py",
        "entry": "main",
        "cost_category": "free_api",
        "implemented": True,
        "note": "Free of charge but writes approved episode artwork, so it is "
                "approval-gated like any paid route.",
    },
    "flux_illustration": {
        "module": "generate_images_flux.py",
        "entry": "main",
        "cost_category": "paid_api",
        "implemented": True,
    },
    "flux_reenactment": {
        "module": "generate_images_flux.py",
        "entry": "main",
        "cost_category": "paid_api",
        "implemented": True,
        "note": "Shares the illustration backend today; a dedicated reenactment "
                "renderer is registered in generation_gate as images.reenactment "
                "and is not written yet.",
    },
    "approved_pose_compositor": {
        "module": "composite_character.py",
        "entry": "render_production",
        "cost_category": "derived",
        "implemented": True,
        "note": "Composites an already-approved pose asset; spends nothing, but "
                "writes approved episode artwork.",
    },
}


class RendererError(RuntimeError):
    """A capability map names something this build cannot honour."""


def get(renderer_id: str) -> dict:
    if renderer_id not in RENDERERS:
        raise RendererError(
            f"unregistered renderer {renderer_id!r}; registered: {sorted(RENDERERS)}")
    return RENDERERS[renderer_id]


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
