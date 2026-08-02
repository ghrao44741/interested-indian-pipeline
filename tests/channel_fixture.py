"""A minimal Channel Pack for suites whose subject is not the channel.

The gates now derive a channel from the episode, so a fixture project without
one is blocked before any of the checks those suites actually exercise. This
installs the smallest schema-valid pack that keeps them testing what they were
written to test.

Deliberately plain: an approved voice, one registered free renderer, and the
character package referenced where those suites already build it — under the
temp root's own `character/`, through the legacy path kind. Anything more would
make these suites quietly depend on channel behaviour that
tests/test_channel_context.py is the right place to check.
"""

import json
from pathlib import Path

import channel_context as cc
import render_channel_dna as rd

CHANNEL_ID = "fixture_channel"
DNA_VERSION = 1


def pack_document(channel_id: str = CHANNEL_ID) -> dict:
    return {
        "schema_version": 1,
        "channel_id": channel_id,
        "channel_dna_version": DNA_VERSION,
        "brand": {"name": "Fixture", "promise": "A fixture.", "language": "English"},
        "audience": {"primary": "Tests", "secondary": "Tests"},
        "editorial": {"tone": ["plain"], "principles": ["Be a fixture."]},
        "narrative": {
            "structure": [{"step": 1, "beat": "Open."}],
            "portfolio_planning_guidance": {
                "note": "Guidance, not a quota.",
                "shares": [{"share_pct": 100, "label": "Everything"}]}},
        "visual_style": {"palette": {"ink": "#202020"}, "pad_color": "#202020",
                         "rules": ["Flat."], "ken_burns_zoom": 1.05},
        "host": {"enabled": True, "target_presence_pct": {"min": 20, "max": 30},
                 "soft_ceiling_pct": 35, "max_consecutive": 2},
        "character": {"spec_path": "character/character_spec.json",
                      "path_kind": "legacy_pipeline_root"},
        "voice": {"selection_status": "approved",
                  "approved_profile": {"provider": "mock", "voice": "fixture",
                                       "approved_by": "fixture suite",
                                       "approved_at": "2026-01-01T00:00:00+00:00"},
                  "working_default": None, "preview_dir": "voice_previews"},
        "routing": {"policy_version": 1},
        "renderers": {"capabilities": {"PHOTO": "pexels",
                                       "HOST_COMPOSITE": "approved_pose_compositor"}},
        "evidence": {"require_provenance_for": ["PHOTO"]},
        "safety": {"rules": ["Attribute claims."]},
        "economics": {"currency": "USD", "image_pricing": {}},
    }


def install(root: Path, channel_id: str = CHANNEL_ID) -> str:
    """Write the pack (and its generated DNA document) into a temp root."""
    d = root / "channels" / channel_id
    d.mkdir(parents=True, exist_ok=True)
    doc = pack_document(channel_id)
    (d / "channel.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    (d / cc.DNA_NAME).write_text(rd.render_dna(doc), encoding="utf-8")
    return channel_id


def stamp(manifest: dict, channel_id: str = CHANNEL_ID) -> dict:
    manifest["channel_id"] = channel_id
    manifest["channel_dna_version"] = DNA_VERSION
    return manifest
