"""
generate_india_map.py
The Interested Indian — Accurate State/Region Highlight Map Generator

Why this exists: text-to-image models (Gemini/Imagen included) do not have
reliable spatial reasoning for precise geography. They will draw an India
outline that looks right at a glance, but place a specific state's shape
or position incorrectly — Karnataka shown in the wrong part of the
peninsula, borders that don't match neighbors, etc. This is not a prompt
wording problem; it's a fundamental limitation of how these models
generate images. Since half of this channel's content lives or dies on
"is that state where it's supposed to be," maps should not be generated
by a diffusion model at all.

This script renders maps directly from real Indian state boundary data
(GeoJSON, sourced from geohacker/india — a commonly used public dataset
derived from GADM administrative boundaries), so placement and shape are
always correct. Output matches the channel's existing visual style: dark
charcoal background, bold black outlines, flat high-contrast color blocks
— no gradients, no drop shadows, no textures. Text-free by design (per
the Stage 3 convention of generating base backgrounds text-free, with
labels/arrows added in post-production).

SETUP (run once):
    pip install geopandas shapely matplotlib

USAGE — single state highlighted:
    python generate_india_map.py --geojson india_states.geojson \\
        --highlight Karnataka --out ep01/images/map_karnataka.png

USAGE — multiple states, same highlight color (e.g. "the southern states"):
    python generate_india_map.py --geojson india_states.geojson \\
        --highlight "Karnataka,Tamil Nadu,Kerala,Andhra Pradesh,Telangana" \\
        --out ep01/images/map_south_states.png

USAGE — comparison map, two groups in different colors (e.g. gainers vs losers):
    python generate_india_map.py --geojson india_states.geojson \\
        --highlight "Karnataka,Tamil Nadu,Kerala" --highlight-color "#8B0000" \\
        --highlight2 "Uttar Pradesh,Bihar" --highlight2-color "#1E4D2B" \\
        --out ep01/images/map_comparison.png

State names must match the GeoJSON's NAME_1 field exactly (run with
--list-states to see all valid names/spellings — note some differ from
current usage, e.g. "Orissa" not "Odisha", "Uttaranchal" not "Uttarakhand",
and undivided "Jammu and Kashmir", since this dataset predates several
state renames/splits).
"""

import argparse
import sys

import geopandas as gpd
import matplotlib.pyplot as plt

BACKGROUND = "#1A2B4C"  # Interested Indian's finalized brand background (deep navy)
BASE_FILL = "#2A2A2A"
OUTLINE = "#000000"
DEFAULT_HIGHLIGHT = "#8B0000"   # crimson, per brand palette


def load_states(geojson_path: str):
    gdf = gpd.read_file(geojson_path)
    if "NAME_1" not in gdf.columns:
        print(f"\n✗ Unexpected GeoJSON structure — no NAME_1 field found in {geojson_path}")
        sys.exit(1)
    return gdf


def validate_states(gdf, requested: list, label: str):
    valid_names = set(gdf["NAME_1"].unique())
    unknown = [s for s in requested if s not in valid_names]
    if unknown:
        print(f"\n✗ Unknown state name(s) in --{label}: {unknown}")
        print(f"  Run with --list-states to see valid names for this GeoJSON.")
        sys.exit(1)


def render_map(gdf, highlight: list, highlight_color: str,
                highlight2: list, highlight2_color: str,
                output_path: str, transparent: bool):
    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=150)  # 16:9

    if not transparent:
        fig.patch.set_facecolor(BACKGROUND)
        ax.set_facecolor(BACKGROUND)

    gdf.plot(ax=ax, facecolor=BASE_FILL, edgecolor=OUTLINE, linewidth=1.8)

    if highlight:
        gdf[gdf["NAME_1"].isin(highlight)].plot(
            ax=ax, facecolor=highlight_color, edgecolor=OUTLINE, linewidth=2.5
        )

    if highlight2:
        gdf[gdf["NAME_1"].isin(highlight2)].plot(
            ax=ax, facecolor=highlight2_color, edgecolor=OUTLINE, linewidth=2.5
        )

    ax.set_axis_off()
    ax.set_aspect("equal")
    plt.tight_layout(pad=0)
    plt.savefig(
        output_path,
        facecolor="none" if transparent else BACKGROUND,
        transparent=transparent,
        bbox_inches="tight",
        pad_inches=0.15,
    )
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--geojson", required=True, help="Path to the India states GeoJSON file")
    parser.add_argument("--highlight", default="", help="Comma-separated state name(s) to highlight")
    parser.add_argument("--highlight-color", default=DEFAULT_HIGHLIGHT, help="Fill color for --highlight states (hex)")
    parser.add_argument("--highlight2", default="", help="Optional second group of states, for comparison maps")
    parser.add_argument("--highlight2-color", default="#1E4D2B", help="Fill color for --highlight2 states (hex)")
    parser.add_argument("--out", required=False, help="Output PNG path. Required unless --list-states.")
    parser.add_argument("--transparent", action="store_true", help="Transparent background instead of dark charcoal, for compositing in the video editor")
    parser.add_argument("--list-states", action="store_true", help="Print all valid state names in the GeoJSON and exit")
    args = parser.parse_args()

    gdf = load_states(args.geojson)

    if args.list_states:
        for name in sorted(gdf["NAME_1"].unique()):
            print(f"  {name}")
        return

    if not args.out:
        parser.error("--out is required unless using --list-states")

    highlight = [s.strip() for s in args.highlight.split(",") if s.strip()]
    highlight2 = [s.strip() for s in args.highlight2.split(",") if s.strip()]

    if highlight:
        validate_states(gdf, highlight, "highlight")
    if highlight2:
        validate_states(gdf, highlight2, "highlight2")

    render_map(gdf, highlight, args.highlight_color, highlight2, args.highlight2_color, args.out, args.transparent)
    print(f"✓ Saved {args.out}")


if __name__ == "__main__":
    main()
