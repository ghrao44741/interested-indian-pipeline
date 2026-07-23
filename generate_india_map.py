"""
generate_india_map.py — India state-level map generator for The Interested Indian

Renders accurate, color-coded 16:9 map PNGs from real state boundary data.
Text-to-image models place state shapes incorrectly — this script uses real
GeoJSON so geography is always exact.

Downloads India state boundaries on first run → cached at data/india_states.geojson.

Visual style: warm cream background, region-coded state fills, highlighted states
in crimson with bold border + name label, optional callout box.

SETUP (run once):
    pip install geopandas matplotlib requests --break-system-packages

USAGE — single state:
    python generate_india_map.py --highlight Karnataka

USAGE — multiple states (comma-separated):
    python generate_india_map.py --highlight "Karnataka,Tamil Nadu,Kerala"

USAGE — two comparison groups:
    python generate_india_map.py \\
        --highlight "Karnataka,Tamil Nadu" --highlight-color "#8B0000" \\
        --highlight2 "Uttar Pradesh,Bihar" --highlight2-color "#1A2B4C"

USAGE — pipeline integration (writes to ep01/images/SCENE-003.png):
    python generate_india_map.py --project ep01 --shot 3 --highlight Kerala \\
        --title "Kerala 1959 — First Article 356 Imposition" \\
        --callout "KERALA: First CM dismissed"

USAGE — list all GeoJSON state names:
    python generate_india_map.py --list-states

Note on state name spellings: the GeoJSON uses older GADM names in some cases
(e.g. "Orissa" not "Odisha", "Uttaranchal" not "Uttarakhand"). Use --list-states
to check. Fuzzy matching handles common variants automatically.
"""

import argparse
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent
DATA_DIR     = PIPELINE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# GeoJSON source — jbrobst's well-known India states gist (GADM-derived)
INDIA_GEOJSON_URL   = (
    "https://gist.githubusercontent.com/jbrobst/"
    "56c13bbbf9d97d187fea01ca62ea5112/raw/"
    "e388c4cae20aa53cb5090210a42ebb9b765c0a36/india_states.geojson"
)
INDIA_GEOJSON_LOCAL = DATA_DIR / "india_states.geojson"

# ── Channel visual DNA ─────────────────────────────────────────────────────────

BG_COLOR     = "#FAF7F2"   # warm cream
OCEAN_COLOR  = "#D8EAF2"   # pale blue
BORDER_COLOR = "#5C4030"   # medium brown
TITLE_COLOR  = "#2C1A0E"   # dark brown

# Muted region fill colours
REGION_COLORS = {
    "north":     "#C8D4E8",   # muted navy
    "south":     "#B8D4B8",   # muted forest green
    "east":      "#B8D8D8",   # muted teal
    "west":      "#EED4B0",   # muted warm orange
    "central":   "#E4DCA8",   # muted gold
    "northeast": "#DBC0C0",   # muted dusty red
}
DEFAULT_FILL = "#DDD8CC"   # fallback warm beige

# Highlight palette — used in order for each group
HIGHLIGHT_PALETTE = [
    "#C0392B",   # crimson  (primary)
    "#1A2B4C",   # navy     (secondary)
    "#E8763A",   # orange
    "#1E4D2B",   # forest green
    "#D4AF37",   # gold
]

# ── State → region ─────────────────────────────────────────────────────────────

_REGION_MAP: dict[str, str] = {}
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
        _REGION_MAP[_s.lower()] = _region


# ── GeoJSON auto-download ──────────────────────────────────────────────────────

def _ensure_geojson():
    if INDIA_GEOJSON_LOCAL.exists():
        return
    print("  📥 Downloading India states GeoJSON (one-time, cached after)…")
    try:
        import requests
        resp = requests.get(INDIA_GEOJSON_URL, timeout=30)
        resp.raise_for_status()
        INDIA_GEOJSON_LOCAL.write_bytes(resp.content)
        print(f"  ✓ Cached → {INDIA_GEOJSON_LOCAL}")
    except Exception as e:
        print(f"  ❌ Download failed: {e}")
        print(f"\n  Manual fix: download India states GeoJSON and save to:\n  {INDIA_GEOJSON_LOCAL}")
        sys.exit(1)


# ── Name resolution (fuzzy) ────────────────────────────────────────────────────

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
            print(f"  ⚠  State not matched: '{w}' — run --list-states to see valid names")
    return resolved


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
):
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

    _ensure_geojson()
    src = geojson_path or str(INDIA_GEOJSON_LOCAL)
    gdf = gpd.read_file(src)

    # Detect name column
    name_col = next(
        (c for c in ["ST_NM", "NAME_1", "name", "state", "State", "NAME"]
         if c in gdf.columns),
        gdf.columns[0],
    )
    state_names = gdf[name_col].tolist()

    h1 = _resolve(highlight,           state_names)
    h2 = _resolve(highlight2 or [],    state_names)
    all_highlighted = set(h1) | set(h2)

    def _fill(sname: str) -> str:
        if sname in h1: return highlight_color
        if sname in h2: return highlight2_color
        region = _REGION_MAP.get(sname.lower())
        return REGION_COLORS.get(region, DEFAULT_FILL)

    gdf["_fill"] = gdf[name_col].apply(_fill)

    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    bg = "none" if transparent else BG_COLOR
    fig.patch.set_facecolor(bg)
    ax.set_facecolor("none" if transparent else OCEAN_COLOR)
    ax.set_axis_off()

    # Base map
    gdf.plot(ax=ax, color=gdf["_fill"], edgecolor=BORDER_COLOR, linewidth=0.5, zorder=1)

    # Bold borders on highlighted states
    for group, color in [(h1, highlight_color), (h2, highlight2_color)]:
        if group:
            gdf[gdf[name_col].isin(group)].plot(
                ax=ax, facecolor="none", edgecolor=color, linewidth=2.8, zorder=2
            )

    # State name labels
    for _, row in gdf.iterrows():
        sname = row[name_col]
        is_hi = sname in all_highlighted
        if not is_hi and not all_labels:
            continue
        try:
            centroid = row.geometry.centroid
        except Exception:
            continue
        if is_hi:
            color = highlight_color if sname in h1 else highlight2_color
            ax.annotate(
                sname, xy=(centroid.x, centroid.y),
                ha="center", va="center", fontsize=6.5, fontweight="bold",
                color="white", zorder=3,
                path_effects=[pe.withStroke(linewidth=2.5, foreground=color)],
            )
        else:
            ax.annotate(
                sname, xy=(centroid.x, centroid.y),
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
    for region, rcolor in REGION_COLORS.items():
        handles.append(mpatches.Patch(color=rcolor, label=region.capitalize()))
    if handles:
        ax.legend(handles=handles, loc="lower left", fontsize=6.5,
                  framealpha=0.9, facecolor=BG_COLOR, edgecolor="#C8B89A")

    if out_path is None:
        out_path = Path("india_map.png")
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
    parser.add_argument("--highlight", default="",
                        help="Comma-separated state(s) to highlight (crimson by default)")
    parser.add_argument("--highlight-color", default=HIGHLIGHT_PALETTE[0], dest="h1_color",
                        help=f"Colour for --highlight group (default: {HIGHLIGHT_PALETTE[0]})")
    parser.add_argument("--highlight2", default="",
                        help="Second group of states (navy by default)")
    parser.add_argument("--highlight2-color", default=HIGHLIGHT_PALETTE[1], dest="h2_color",
                        help=f"Colour for --highlight2 group (default: {HIGHLIGHT_PALETTE[1]})")
    parser.add_argument("--title",   default="", help="Map title text (centred, top)")
    parser.add_argument("--callout", default="", help="Short callout label (top-left coloured box)")
    parser.add_argument("--project", default=None, help="Episode folder (e.g. ep01)")
    parser.add_argument("--shot",    default=None, help="Shot number — writes to images/SCENE-{n:03d}.png")
    parser.add_argument("--out",     default=None, help="Explicit output path")
    parser.add_argument("--geojson", default=None,
                        help="Path to GeoJSON (default: auto-download to data/india_states.geojson)")
    parser.add_argument("--transparent", action="store_true",
                        help="Transparent background for compositing")
    parser.add_argument("--all-labels", action="store_true", dest="all_labels",
                        help="Show name labels for ALL states, not just highlighted ones")
    parser.add_argument("--list-states", action="store_true", dest="list_states",
                        help="Print all GeoJSON state names and exit")
    args = parser.parse_args()

    if args.list_states:
        _ensure_geojson()
        try:
            import geopandas as gpd
        except ImportError:
            print("❌ pip install geopandas --break-system-packages")
            sys.exit(1)
        src = args.geojson or str(INDIA_GEOJSON_LOCAL)
        gdf = gpd.read_file(src)
        col = next((c for c in ["ST_NM", "NAME_1", "name", "state"] if c in gdf.columns),
                   gdf.columns[0])
        print(f"\nState names (column: {col}):")
        for s in sorted(gdf[col].tolist()):
            print(f"  {s:<35} [{_REGION_MAP.get(s.lower(), '—')}]")
        return

    # Resolve output path
    if args.out:
        out_path = Path(args.out)
    elif args.project and args.shot:
        out_path = PIPELINE_DIR / args.project / "images" / f"SCENE-{str(args.shot).zfill(3)}.png"
    elif args.project:
        out_path = PIPELINE_DIR / args.project / "map.png"
    else:
        tag = (args.highlight.split(",")[0].strip().lower().replace(" ", "_") or "india")
        out_path = Path(f"{tag}_map.png")

    h1 = [s.strip() for s in args.highlight.split(",") if s.strip()]
    h2 = [s.strip() for s in args.highlight2.split(",") if s.strip()]

    print(f"\n  Highlight : {h1}")
    if h2: print(f"  Highlight2: {h2}")
    print(f"  Title     : {args.title or '(none)'}")
    print(f"  Output    : {out_path}")

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
    )


if __name__ == "__main__":
    main()
