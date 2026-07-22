"""
generate_india_map.py — Generate accurate India state-highlight maps from GeoJSON.

Renders state outline maps in The Interested Indian's minimalist doodle style:
warm cream background (#FAF7F2), black ink line art, flat colour fills.

Usage:
    # Download GeoJSON first (one-time):
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/geohacker/india/master/state/india_state.geojson" -OutFile "india_states.geojson"

    # List available state names in the dataset
    python generate_india_map.py --list-states

    # Single group — all same colour
    python generate_india_map.py --states "Tamil Nadu,Kerala,Andhra Pradesh" --color orange --out ep01/images/SCENE-XXX.png

    # Per-state colours (KEY=value pairs, comma-separated)
    python generate_india_map.py \
        --state-colors "Tamil Nadu=teal,Kerala=green,Andhra Pradesh=yellow" \
        --labels \
        --out ep01/images/SCENE-003.png

    # Two-group comparison (e.g. south vs north)
    python generate_india_map.py \
        --group1 "Karnataka,Tamil Nadu,Kerala" --color1 orange \
        --group2 "Uttar Pradesh,Bihar" --color2 grey \
        --out ep01/images/SCENE-XXX.png

    # Add state name labels to any mode
    python generate_india_map.py --states "Karnataka" --labels --out ep01/images/SCENE-XXX.png

Requirements:
    pip install matplotlib shapely
    GeoJSON: india_states.geojson in same folder (or pass --geojson path)

Note on state names:
    The geohacker/india dataset uses pre-reorganisation names.
    Telangana (carved from Andhra Pradesh in 2014) may not exist separately.
    Run --list-states to verify. If missing, use "Andhra Pradesh" to cover both.
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as MplPath
except ImportError:
    print("❌ matplotlib not found. Run: pip install matplotlib")
    sys.exit(1)

try:
    from shapely.geometry import shape, MultiPolygon, Polygon
    from shapely.ops import unary_union
except ImportError:
    print("❌ shapely not found. Run: pip install shapely")
    sys.exit(1)

# ── Style ──────────────────────────────────────────────────────────────────────
BG_COLOR     = "#FAF7F2"   # warm cream white
LINE_COLOR   = "#1A1A1A"   # near-black ink
DEFAULT_FILL = "#E8E4DF"   # unhighlighted states
LINE_WIDTH   = 0.7
LABEL_SIZE   = 7           # font size for state labels

COLOR_MAP = {
    "orange": "#E8763A",
    "grey":   "#9E9E9E",
    "blue":   "#5B8DB8",
    "green":  "#6BAA75",
    "teal":   "#3D9C9C",
    "red":    "#C0392B",
    "yellow": "#F0C040",
    "purple": "#9B59B6",
}

FIG_W, FIG_H = 12.0, 6.75
DPI = 160

GEOJSON_DEFAULT = "india_states.geojson"


# ── GeoJSON helpers ────────────────────────────────────────────────────────────

def load_geojson(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["features"]


def get_name(feature: dict) -> str:
    props = feature["properties"]
    for key in ("NAME_1", "ST_NM", "name", "STATE", "Name"):
        val = props.get(key)
        if val:
            return str(val).strip()
    return ""


def list_states(features: list):
    names = sorted(get_name(f) for f in features)
    print(f"State/UT names in dataset ({len(names)} total):\n")
    for n in names:
        print(f"  {n}")


# ── Geometry → matplotlib patches ─────────────────────────────────────────────

def geom_to_patches(geom, **kw):
    patches = []
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for poly in polys:
        if not isinstance(poly, Polygon):
            continue
        verts, codes = [], []
        for ring in [poly.exterior] + list(poly.interiors):
            coords = list(ring.coords)
            verts  += coords
            codes  += [MplPath.MOVETO] + [MplPath.LINETO] * (len(coords) - 2) + [MplPath.CLOSEPOLY]
        patches.append(PathPatch(MplPath(verts, codes), **kw))
    return patches


def centroid(geom):
    """Return (x, y) centroid of a geometry."""
    try:
        c = geom.centroid
        return c.x, c.y
    except Exception:
        return None


# ── Render ─────────────────────────────────────────────────────────────────────

def render_map(features: list, lookup: dict, label_states: set, out_path: str,
               label_overrides: dict = None):
    """
    lookup: {state_name_lower: hex_color}
    label_states: set of state_name_lower values to label
    """
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), facecolor=BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.set_aspect("equal")
    ax.axis("off")

    missing = set(k for k, v in lookup.items() if v != DEFAULT_FILL)

    for feat in features:
        name  = get_name(feat)
        color = lookup.get(name.lower(), DEFAULT_FILL)
        if name.lower() in missing and color != DEFAULT_FILL:
            missing.discard(name.lower())
        geom  = shape(feat["geometry"])
        for p in geom_to_patches(geom, facecolor=color,
                                  edgecolor=LINE_COLOR, linewidth=LINE_WIDTH):
            ax.add_patch(p)

        # Label
        if name.lower() in label_states:
            cx, cy = centroid(geom) or (None, None)
            display_name = (label_overrides or {}).get(name.lower(), name)
            if cx is not None:
                ax.text(cx, cy, display_name, ha="center", va="center",
                        fontsize=LABEL_SIZE, fontweight="bold",
                        color=LINE_COLOR, zorder=5,
                        bbox=dict(boxstyle="round,pad=0.15", fc=BG_COLOR,
                                  ec="none", alpha=0.7))

    if missing:
        print(f"⚠  States not found in dataset (check spelling with --list-states):")
        for m in sorted(missing):
            print(f"   '{m}'")

    combined = unary_union([shape(f["geometry"]) for f in features])
    minx, miny, maxx, maxy = combined.bounds
    pad = 1.0
    ax.set_xlim(minx - pad, maxx + pad)
    ax.set_ylim(miny - pad, maxy + pad)

    plt.tight_layout(pad=0)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    print(f"✓ Saved → {out_path}  ({int(FIG_W*DPI)}×{int(FIG_H*DPI)}px)")


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_color(s: str) -> str:
    return s if s.startswith("#") else COLOR_MAP.get(s.lower(), s)


def name_set(csv: str) -> frozenset:
    return frozenset(n.strip().lower() for n in csv.split(",") if n.strip())


def main():
    p = argparse.ArgumentParser(
        description="India state-highlight map generator — The Interested Indian style"
    )
    p.add_argument("--geojson", default=GEOJSON_DEFAULT)
    p.add_argument("--list-states", action="store_true",
                   help="Print available state names and exit")

    # Single-group mode
    p.add_argument("--states", default=None,
                   help="Comma-separated states to highlight (all same colour)")
    p.add_argument("--color", default="orange",
                   help="Colour for --states (name or hex, default: orange)")

    # Per-state colour mode
    p.add_argument("--state-colors", default=None,
                   help='Per-state colours: "Tamil Nadu=teal,Kerala=green,Andhra Pradesh=yellow"')

    # Two-group mode
    p.add_argument("--group1", default=None)
    p.add_argument("--color1", default="orange")
    p.add_argument("--group2", default=None)
    p.add_argument("--color2", default="grey")

    # Labels
    p.add_argument("--labels", action="store_true",
                   help="Print state names on highlighted states")
    p.add_argument("--label-override", default=None,
                   help='Override displayed label for a state: "Andhra Pradesh=Telangana & AP"')

    p.add_argument("--out", default="india_map.png")
    args = p.parse_args()

    gj = Path(args.geojson)
    if not gj.exists():
        print(f"❌ GeoJSON not found: {gj}")
        print(f"\nDownload it:")
        print(f'  Invoke-WebRequest -Uri "https://raw.githubusercontent.com/geohacker/india/master/state/india_state.geojson" -OutFile "{gj}"')
        sys.exit(1)

    features = load_geojson(str(gj))

    if args.list_states:
        list_states(features)
        return

    # Build lookup: {name_lower: color}
    lookup      = {}
    label_set   = set()

    if args.state_colors:
        # "Tamil Nadu=teal,Kerala=green,Andhra Pradesh=yellow"
        for part in args.state_colors.split(","):
            part = part.strip()
            if "=" not in part:
                print(f"⚠  Skipping malformed entry (expected 'State=color'): '{part}'")
                continue
            state, color = part.rsplit("=", 1)
            state = state.strip().lower()
            lookup[state] = parse_color(color.strip())
            if args.labels:
                label_set.add(state)
    elif args.group1 or args.group2:
        if args.group1:
            for n in name_set(args.group1):
                lookup[n] = parse_color(args.color1)
                if args.labels:
                    label_set.add(n)
        if args.group2:
            for n in name_set(args.group2):
                lookup[n] = parse_color(args.color2)
                if args.labels:
                    label_set.add(n)
    elif args.states:
        for n in name_set(args.states):
            lookup[n] = parse_color(args.color)
            if args.labels:
                label_set.add(n)
    else:
        print("❌ Specify --states, --state-colors, --group1/--group2, or --list-states")
        p.print_help()
        sys.exit(1)

    # Parse label overrides
    label_overrides = {}
    if args.label_override:
        for part in args.label_override.split(","):
            part = part.strip()
            if "=" in part:
                state, override = part.split("=", 1)
                label_overrides[state.strip().lower()] = override.strip()

    render_map(features, lookup, label_set, args.out, label_overrides)


if __name__ == "__main__":
    main()
