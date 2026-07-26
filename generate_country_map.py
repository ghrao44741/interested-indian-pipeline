"""
generate_country_map.py — Generic country sub-division map generator

Renders accurate, color-coded 16:9 map PNGs from any GeoJSON boundary file.
Two fixes over the India-only version:
  1. Auto-detects the region name field (no hardcoded "NAME_1");
     override with --name-field if needed.
  2. Latitude-based aspect-ratio correction via ax.set_aspect() so countries
     far from the equator (UK, Canada, Scandinavia …) render without squishing.

Everything else is identical to generate_india_map.py — same brand colours,
same highlight/callout/legend system, same 1280×720 output.

SETUP (run once):
    pip install geopandas matplotlib requests --break-system-packages

GEOJSON SOURCES:
    GADM   : https://gadm.org/download_country.html  (Level-1 = states/provinces)
    Natural Earth: https://www.naturalearthdata.com/downloads/10m-cultural-vectors/
    India  : auto-downloaded to data/india_states.geojson when --geojson is omitted

USAGE — single region:
    python generate_country_map.py --geojson uk.geojson --highlight "Scotland"

USAGE — two comparison groups:
    python generate_country_map.py --geojson canada.geojson \\
        --highlight "Ontario,Quebec" --highlight2 "British Columbia,Alberta"

USAGE — list all region names in a GeoJSON:
    python generate_country_map.py --geojson my_file.geojson --list-regions

USAGE — override name field:
    python generate_country_map.py --geojson my_file.geojson \\
        --name-field ADM1_NAME --highlight "Bavaria"

USAGE — pipeline integration:
    python generate_country_map.py --geojson data/india_states.geojson \\
        --project ep01 --shot 3 --highlight "Karnataka" \\
        --title "Karnataka in Context"

BACKWARD COMPAT — works as India-only replacement (omit --geojson):
    python generate_country_map.py --highlight "Karnataka,Tamil Nadu"

NOTE — visual parity with generate_india_map.py: that script colors non-highlighted Indian
states by North/South/East/West/Central/Northeast region BY DEFAULT. Here that's opt-in via
--india-regions (deliberately not auto-applied just because the India GeoJSON was detected —
this script stays generic, and a comparison episode wants matching, uncluttered styling on
both sides). Add --india-regions if you want that same regional-coloring look for India alone.
"""

import argparse
import math
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent
DATA_DIR = PIPELINE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# India fallback (backward-compatible with generate_india_map.py usage)
INDIA_GEOJSON_URL = (
    "https://gist.githubusercontent.com/jbrobst/"
    "56c13bbbf9d97d187fea01ca62ea5112/raw/"
    "e388c4cae20aa53cb5090210a42ebb9b765c0a36/india_states.geojson"
)
INDIA_GEOJSON_LOCAL = DATA_DIR / "india_states.geojson"

# ── Channel visual DNA (unchanged from generate_india_map.py) ─────────────────

BG_COLOR     = "#FAF7F2"
OCEAN_COLOR  = "#D8EAF2"
BORDER_COLOR = "#5C4030"
TITLE_COLOR  = "#2C1A0E"
DEFAULT_FILL = "#DDD8CC"   # all non-highlighted regions get this

HIGHLIGHT_PALETTE = [
    "#C0392B",   # crimson  (primary)
    "#1A2B4C",   # navy     (secondary)
    "#E8763A",   # orange
    "#1E4D2B",   # forest green
    "#D4AF37",   # gold
]

# India region colours — kept for backward compat when using India GeoJSON
_INDIA_REGION_COLORS = {
    "north":     "#C8D4E8",
    "south":     "#B8D4B8",
    "east":      "#B8D8D8",
    "west":      "#EED4B0",
    "central":   "#E4DCA8",
    "northeast": "#DBC0C0",
}
_INDIA_REGION_MAP: dict[str, str] = {}
for _region, _states in {
    "north": [
        "Delhi", "Haryana", "Himachal Pradesh", "Jammu & Kashmir",
        "Jammu and Kashmir", "Ladakh", "Punjab", "Rajasthan",
        "Uttar Pradesh", "Uttarakhand", "Uttaranchal", "Chandigarh",
    ],
    "south": [
        "Andhra Pradesh", "Karnataka", "Kerala", "Tamil Nadu", "Telangana",
        "Puducherry", "Pondicherry", "Lakshadweep",
        "Andaman & Nicobar Island", "Andaman and Nicobar Islands",
    ],
    "east":      ["Bihar", "Jharkhand", "Odisha", "Orissa", "West Bengal"],
    "west":      ["Goa", "Gujarat", "Maharashtra",
                  "Dadra and Nagar Haveli", "Dadra & Nagar Haveli",
                  "Daman & Diu", "Daman and Diu"],
    "central":   ["Chhattisgarh", "Madhya Pradesh"],
    "northeast": ["Arunachal Pradesh", "Assam", "Manipur", "Meghalaya",
                  "Mizoram", "Nagaland", "Sikkim", "Tripura"],
}.items():
    for _s in _states:
        _INDIA_REGION_MAP[_s.lower()] = _region

# ── Name-field auto-detection ──────────────────────────────────────────────────
# Checked in priority order; first match wins.
_NAME_CANDIDATES = [
    "ST_NM",       # India GADM gist
    "NAME_1",      # GADM standard
    "ADM1_NAME",   # UN/OCHA style
    "adm1_name",
    "name_1",
    "shapeName",   # geoBoundaries
    "SHAPENAME",
    "name",
    "Name",
    "NAME",
    "state",
    "State",
    "STATE",
    "region",
    "Region",
    "REGION",
    "province",
    "Province",
    "PROVINCE",
]


def _detect_name_field(columns: list[str]) -> str:
    """Return the first candidate column that exists; fallback to columns[0]."""
    for c in _NAME_CANDIDATES:
        if c in columns:
            return c
    # Last resort: first non-geometry string-ish column
    return columns[0]


# ── GeoJSON helpers ────────────────────────────────────────────────────────────

def _ensure_india_geojson():
    if INDIA_GEOJSON_LOCAL.exists():
        return
    print("  📥 Downloading India states GeoJSON (one-time, cached)…")
    try:
        import requests
        resp = requests.get(INDIA_GEOJSON_URL, timeout=30)
        resp.raise_for_status()
        INDIA_GEOJSON_LOCAL.write_bytes(resp.content)
        print(f"  ✓ Cached → {INDIA_GEOJSON_LOCAL}")
    except Exception as e:
        print(f"  ❌ Download failed: {e}")
        print(f"\n  Manual fix: download the GeoJSON and save to:\n  {INDIA_GEOJSON_LOCAL}")
        sys.exit(1)


def _resolve_geojson(geojson_arg: str | None) -> str:
    """Return absolute path to the GeoJSON; defaults to India if arg is None."""
    if geojson_arg:
        p = Path(geojson_arg)
        if not p.exists():
            # Try relative to pipeline dir
            p2 = PIPELINE_DIR / geojson_arg
            if p2.exists():
                return str(p2)
            print(f"❌ GeoJSON not found: {geojson_arg}")
            sys.exit(1)
        return str(p)
    _ensure_india_geojson()
    return str(INDIA_GEOJSON_LOCAL)


# ── Fuzzy name resolution ──────────────────────────────────────────────────────

def _resolve(wanted: list[str], known: list[str]) -> list[str]:
    known_lower = {k.lower(): k for k in known}
    resolved = []
    for w in wanted:
        wl = w.lower().strip()
        if wl in known_lower:
            resolved.append(known_lower[wl])
            continue
        match = next((k for kl, k in known_lower.items() if wl in kl or kl in wl), None)
        if match:
            resolved.append(match)
        else:
            print(f"  ⚠  Region not matched: '{w}' — run --list-regions to see valid names")
    return resolved


# ── Latitude aspect-ratio correction ──────────────────────────────────────────

def _mean_lat_degrees(gdf) -> float:
    """Return mean latitude of the GeoJSON bounding box in degrees."""
    _, miny, _, maxy = gdf.total_bounds   # minx, miny, maxx, maxy
    return (miny + maxy) / 2.0


def _lat_corrected_aspect(gdf) -> float:
    """
    Visual aspect ratio (width/height) of the bounding box after
    applying the cos(latitude) correction for geographic coordinates.

    In WGS84, 1° longitude spans cos(lat) × the distance of 1° latitude.
    Without this, maps of countries at high latitudes look squished E-W.
    """
    minx, miny, maxx, maxy = gdf.total_bounds
    mean_lat = (miny + maxy) / 2.0
    cos_lat = math.cos(math.radians(mean_lat))
    lon_span = (maxx - minx) * cos_lat
    lat_span = maxy - miny
    return lon_span / lat_span if lat_span else 16 / 9


# ── Core renderer ──────────────────────────────────────────────────────────────

def render_map(
    highlight:        list[str],
    highlight_color:  str = HIGHLIGHT_PALETTE[0],
    highlight2:       list[str] | None = None,
    highlight2_color: str = HIGHLIGHT_PALETTE[1],
    title:            str = "",
    callout:          str = "",
    out_path:         Path | None = None,
    transparent:      bool = False,
    all_labels:       bool = False,
    geojson_path:     str | None = None,
    name_field:       str | None = None,
    use_india_regions: bool = False,
):
    """
    Render a color-coded sub-division map from any GeoJSON.

    Parameters
    ----------
    highlight        : list of region names to colour with highlight_color
    highlight2       : optional second group (highlight2_color)
    name_field       : explicit GeoJSON column for region names (auto-detected if None)
    use_india_regions: apply India-specific muted region palette for non-highlighted states
    """
    try:
        import geopandas as gpd
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import matplotlib.patheffects as pe
    except ImportError as e:
        print(f"❌ Missing package: {e}")
        print("   pip install geopandas matplotlib --break-system-packages")
        sys.exit(1)

    src = _resolve_geojson(geojson_path)
    gdf = gpd.read_file(src)

    # ── Name field resolution ──
    columns = list(gdf.columns)
    if name_field:
        if name_field in columns:
            name_col = name_field
        else:
            print(f"  ⚠  --name-field '{name_field}' not in GeoJSON columns: {columns}")
            name_col = _detect_name_field(columns)
            print(f"  ⚠  Falling back to auto-detected column: '{name_col}'")
    else:
        name_col = _detect_name_field(columns)

    print(f"  Name column: '{name_col}'")
    region_names = gdf[name_col].tolist()

    h1 = _resolve(highlight,        region_names)
    h2 = _resolve(highlight2 or [], region_names)
    all_highlighted = set(h1) | set(h2)

    def _fill(rname: str) -> str:
        if rname in h1: return highlight_color
        if rname in h2: return highlight2_color
        if use_india_regions:
            region = _INDIA_REGION_MAP.get(rname.lower())
            return _INDIA_REGION_COLORS.get(region, DEFAULT_FILL)
        return DEFAULT_FILL

    gdf["_fill"] = gdf[name_col].apply(_fill)

    # ── Figure setup (always 1280×720) ────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    bg = "none" if transparent else BG_COLOR
    fig.patch.set_facecolor(bg)
    ax.set_facecolor("none" if transparent else OCEAN_COLOR)
    ax.set_axis_off()

    # Base map
    gdf.plot(ax=ax, color=gdf["_fill"], edgecolor=BORDER_COLOR, linewidth=0.5, zorder=1)

    # ── Latitude correction ────────────────────────────────────────────────────
    # ax.set_aspect(y_per_x) makes 1 data-unit-x render as y_per_x data-units-y.
    # To correct for longitude shrinkage at latitude φ:
    #   1° lon = cos(φ) × 1° lat visually
    #   → to make them equal height, stretch x by 1/cos(φ), i.e. set aspect = 1/cos(φ)
    mean_lat = _mean_lat_degrees(gdf)
    cos_lat = math.cos(math.radians(mean_lat))
    ax.set_aspect(1.0 / cos_lat)

    # Bold highlight borders
    for group, color in [(h1, highlight_color), (h2, highlight2_color)]:
        if group:
            gdf[gdf[name_col].isin(group)].plot(
                ax=ax, facecolor="none", edgecolor=color, linewidth=2.8, zorder=2
            )

    # Region name labels
    for _, row in gdf.iterrows():
        rname = row[name_col]
        is_hi = rname in all_highlighted
        if not is_hi and not all_labels:
            continue
        try:
            centroid = row.geometry.centroid
        except Exception:
            continue
        if is_hi:
            color = highlight_color if rname in h1 else highlight2_color
            ax.annotate(
                rname, xy=(centroid.x, centroid.y),
                ha="center", va="center", fontsize=6.5, fontweight="bold",
                color="white", zorder=3,
                path_effects=[pe.withStroke(linewidth=2.5, foreground=color)],
            )
        else:
            ax.annotate(
                rname, xy=(centroid.x, centroid.y),
                ha="center", va="center", fontsize=5,
                color="#6B5040", zorder=3,
            )

    # Callout box
    if callout:
        ax.text(
            0.02, 0.96, callout, transform=ax.transAxes,
            ha="left", va="top", fontsize=9, fontweight="bold", color="white",
            bbox=dict(boxstyle="round,pad=0.4", facecolor=highlight_color,
                      edgecolor="white", linewidth=1),
            zorder=4,
        )

    # Title
    if title:
        ax.text(
            0.50, 0.97, title, transform=ax.transAxes,
            ha="center", va="top", fontsize=11, fontweight="bold",
            color=TITLE_COLOR, zorder=4,
        )

    # Legend
    handles = []
    if h1:
        handles.append(mpatches.Patch(color=highlight_color,
                        label=", ".join(h1[:3]) + ("…" if len(h1) > 3 else "")))
    if h2:
        handles.append(mpatches.Patch(color=highlight2_color,
                        label=", ".join(h2[:3]) + ("…" if len(h2) > 3 else "")))
    if use_india_regions:
        for region, rcolor in _INDIA_REGION_COLORS.items():
            handles.append(mpatches.Patch(color=rcolor, label=region.capitalize()))
    if handles:
        ax.legend(handles=handles, loc="lower left", fontsize=6.5,
                  framealpha=0.9, facecolor=BG_COLOR, edgecolor="#C8B89A")

    if out_path is None:
        out_path = Path("country_map.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(out_path), dpi=100, bbox_inches="tight",
                facecolor="none" if transparent else BG_COLOR)
    plt.close()
    print(f"  ✓ Map saved → {out_path}  (1280×720)")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--geojson",    default=None,
                        help="Path to GeoJSON (default: India auto-downloaded)")
    parser.add_argument("--name-field", default=None, dest="name_field",
                        help="GeoJSON property holding region names (auto-detected if omitted)")
    parser.add_argument("--highlight",  default="",
                        help="Comma-separated region(s) — primary highlight colour")
    parser.add_argument("--highlight-color", default=HIGHLIGHT_PALETTE[0], dest="h1_color",
                        help=f"Colour for --highlight (default: {HIGHLIGHT_PALETTE[0]})")
    parser.add_argument("--highlight2", default="",
                        help="Second group of regions")
    parser.add_argument("--highlight2-color", default=HIGHLIGHT_PALETTE[1], dest="h2_color",
                        help=f"Colour for --highlight2 (default: {HIGHLIGHT_PALETTE[1]})")
    parser.add_argument("--title",      default="", help="Map title (centred, top)")
    parser.add_argument("--callout",    default="", help="Short callout label (top-left box)")
    parser.add_argument("--project",    default=None, help="Episode folder (e.g. ep01)")
    parser.add_argument("--shot",       default=None, help="Shot number → images/SCENE-NNN.png")
    parser.add_argument("--out",        default=None, help="Explicit output path")
    parser.add_argument("--transparent", action="store_true", help="Transparent background")
    parser.add_argument("--all-labels", action="store_true", dest="all_labels",
                        help="Label ALL regions, not just highlighted ones")
    parser.add_argument("--india-regions", action="store_true", dest="india_regions",
                        help="Apply India muted region palette for non-highlighted states")
    parser.add_argument("--list-regions", action="store_true", dest="list_regions",
                        help="Print all region names in the GeoJSON and exit")
    args = parser.parse_args()

    if args.list_regions:
        try:
            import geopandas as gpd
        except ImportError:
            print("❌ pip install geopandas --break-system-packages")
            sys.exit(1)
        src = _resolve_geojson(args.geojson)
        gdf = gpd.read_file(src)
        col = (args.name_field if args.name_field and args.name_field in gdf.columns
               else _detect_name_field(list(gdf.columns)))
        print(f"\nGeoJSON  : {src}")
        print(f"Columns  : {list(gdf.columns)}")
        print(f"Name col : '{col}'\n")
        for s in sorted(gdf[col].tolist()):
            print(f"  {s}")
        return

    # Resolve output path
    if args.out:
        out_path = Path(args.out)
    elif args.project and args.shot:
        out_path = PIPELINE_DIR / args.project / "images" / f"SCENE-{str(args.shot).zfill(3)}.png"
    elif args.project:
        out_path = PIPELINE_DIR / args.project / "map.png"
    else:
        tag = (args.highlight.split(",")[0].strip().lower().replace(" ", "_") or "map")
        out_path = Path(f"{tag}_map.png")

    h1 = [s.strip() for s in args.highlight.split(",") if s.strip()]
    h2 = [s.strip() for s in args.highlight2.split(",") if s.strip()]

    print(f"\n  GeoJSON     : {args.geojson or 'India (default)'}")
    print(f"  Name field  : {args.name_field or '(auto-detect)'}")
    print(f"  Highlight   : {h1 or '(none)'}")
    if h2:
        print(f"  Highlight2  : {h2}")
    print(f"  Title       : {args.title or '(none)'}")
    print(f"  Output      : {out_path}")

    render_map(
        highlight=h1,
        highlight_color=args.h1_color,
        highlight2=h2,
        highlight2_color=args.h2_color,
        title=args.title,
        callout=args.callout,
        out_path=out_path,
        transparent=args.transparent,
        all_labels=args.all_labels,
        geojson_path=args.geojson,
        name_field=args.name_field,
        use_india_regions=args.india_regions,
    )


if __name__ == "__main__":
    main()
