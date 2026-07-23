"""
generate_chart.py — Chart & stat-card generator for The Interested Indian

Renders data visualisations as 1280×720 PNG using matplotlib.
Used for "chart" scene types: bar charts, timelines, stat callout cards.

Why not use an AI image generator?
  Numbers and chart labels come out wrong in diffusion models — bars mislabelled,
  percentages garbled, axes missing or incorrect. Charts must be rendered from
  real data, not hallucinated. This script takes structured data as JSON and
  renders it accurately every time.

SETUP:
    pip install matplotlib --break-system-packages

CHART TYPES:

  bar   — Horizontal or vertical bar chart. One bar per item.
          --data '[{"label":"Karnataka","value":15},{"label":"UP","value":8}]'
          --title "Tax Devolution per ₹1"

  timeline — Horizontal year-based event timeline.
             --data '[{"year":1950,"event":"Constitution"},{"year":1959,"event":"Kerala dismissed"}]'

  stat  — Big number callout card. One or more key stats centred on screen.
          --data '[{"stat":"91","label":"Times Article 356 imposed"},{"stat":"36","label":"Times in 1970s alone"}]'

  pie   — Pie chart with percentage labels.
          --data '[{"label":"Congress govts","value":40},{"label":"Opposition","value":51}]'

USAGE:
    python generate_chart.py --type bar \\
        --data '[{"label":"Karnataka","value":15},{"label":"Kerala","value":11}]' \\
        --title "Tax per ₹1 collected" --out ep01/images/SCENE-008.png

    python generate_chart.py --type stat \\
        --data '[{"stat":"91","label":"President Rule impositions since 1950"}]' \\
        --out ep01/images/SCENE-012.png

    python generate_chart.py --type timeline \\
        --data '[{"year":1959,"event":"Kerala"},{"year":1977,"event":"9 Congress states"},{"year":1994,"event":"Bommai judgment"}]' \\
        --title "Article 356 — Key Moments" --out ep01/images/SCENE-020.png
"""

import argparse
import json
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).parent

# ── Channel visual DNA ─────────────────────────────────────────────────────────

BG_COLOR    = "#FAF7F2"    # warm cream
TITLE_COLOR = "#2C1A0E"    # dark brown
TEXT_COLOR  = "#2C1A0E"

# Bar colours — rotated through the palette
BAR_PALETTE = [
    "#C0392B",  # crimson
    "#1A2B4C",  # navy
    "#E8763A",  # orange
    "#1E4D2B",  # forest green
    "#D4AF37",  # gold
    "#3D9C9C",  # teal
    "#8B1515",  # dark red
    "#4A6FA5",  # medium blue
]


# ── Renderers ──────────────────────────────────────────────────────────────────

def _setup_fig():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    return fig, ax, plt


def render_bar(data: list[dict], title: str, out_path: Path, horizontal: bool = True):
    """
    Horizontal bar chart — best for comparing named states/groups.
    data: [{"label": str, "value": float, "unit": str (optional)}]
    """
    fig, ax, plt = _setup_fig()

    labels = [d["label"] for d in data]
    values = [float(d["value"]) for d in data]
    unit   = data[0].get("unit", "") if data else ""
    colors = [BAR_PALETTE[i % len(BAR_PALETTE)] for i in range(len(data))]

    if horizontal:
        bars = ax.barh(labels, values, color=colors, height=0.6, edgecolor="white", linewidth=0.5)
        ax.set_xlabel(unit, fontsize=10, color=TEXT_COLOR, labelpad=8)
        ax.set_xlim(0, max(values) * 1.18)
        ax.invert_yaxis()
        # Value labels
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_width() + max(values) * 0.01,
                bar.get_y() + bar.get_height() / 2,
                f"{val:g}{unit}", va="center", ha="left",
                fontsize=10, fontweight="bold", color=TEXT_COLOR,
            )
    else:
        bars = ax.bar(labels, values, color=colors, width=0.6, edgecolor="white", linewidth=0.5)
        ax.set_ylabel(unit, fontsize=10, color=TEXT_COLOR, labelpad=8)
        ax.set_ylim(0, max(values) * 1.18)
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(values) * 0.01,
                f"{val:g}{unit}", ha="center", va="bottom",
                fontsize=10, fontweight="bold", color=TEXT_COLOR,
            )

    ax.tick_params(colors=TEXT_COLOR, labelsize=10)
    for spine in ax.spines.values():
        spine.set_edgecolor("#C8B89A")
    ax.set_facecolor(BG_COLOR)

    if title:
        ax.set_title(title, fontsize=14, fontweight="bold", color=TITLE_COLOR, pad=16)

    _save(fig, plt, out_path)


def render_stat(data: list[dict], title: str, out_path: Path):
    """
    Big-number stat card. Each item = one stat + label.
    data: [{"stat": "91", "label": "Article 356 impositions", "color": "#C0392B" (opt)}]
    """
    fig, ax, plt = _setup_fig()
    ax.set_axis_off()

    n = len(data)
    for i, item in enumerate(data):
        x = (i + 0.5) / n
        color = item.get("color", BAR_PALETTE[i % len(BAR_PALETTE)])

        # Big number
        ax.text(x, 0.62, item["stat"],
                transform=ax.transAxes,
                ha="center", va="center",
                fontsize=96 if n == 1 else 72,
                fontweight="black",
                color=color)

        # Label below
        ax.text(x, 0.35, item["label"],
                transform=ax.transAxes,
                ha="center", va="center",
                fontsize=16 if n == 1 else 13,
                fontweight="bold",
                color=TEXT_COLOR,
                wrap=True)

        # Thin divider between stats
        if i < n - 1:
            ax.axvline(x=(i + 1) / n, color="#C8B89A", linewidth=1, alpha=0.6)

    if title:
        fig.text(0.5, 0.92, title,
                 ha="center", va="top",
                 fontsize=14, fontweight="bold", color=TITLE_COLOR,
                 transform=fig.transFigure)

    _save(fig, plt, out_path)


def render_timeline(data: list[dict], title: str, out_path: Path):
    """
    Horizontal event timeline.
    data: [{"year": int, "event": str, "color": str (opt)}]
    """
    fig, ax, plt = _setup_fig()
    ax.set_axis_off()

    years  = [int(d["year"]) for d in data]
    events = [d["event"] for d in data]
    colors = [d.get("color", BAR_PALETTE[i % len(BAR_PALETTE)]) for i, d in enumerate(data)]

    if not years:
        _save(fig, plt, out_path)
        return

    yr_min, yr_max = min(years), max(years)
    span = max(yr_max - yr_min, 1)

    # Normalise x positions to [0.05, 0.95]
    xs = [0.05 + 0.90 * (y - yr_min) / span for y in years]

    # Spine line
    ax.axhline(y=0.5, xmin=0.03, xmax=0.97, color="#C0392B", linewidth=2.5)

    for i, (x, year, event, color) in enumerate(zip(xs, years, events, colors)):
        # Dot on timeline
        ax.plot(x, 0.5, "o", markersize=14, color=color,
                transform=ax.transAxes, zorder=3)
        # Alternate above/below
        above = (i % 2 == 0)
        y_text  = 0.72 if above else 0.25
        y_year  = 0.63 if above else 0.35
        va_text = "bottom" if above else "top"

        # Year label
        ax.text(x, y_year, str(year),
                transform=ax.transAxes,
                ha="center", va=va_text,
                fontsize=11, fontweight="bold", color=color)

        # Event label (wrap long text)
        words = event.split()
        lines = []
        cur = []
        for w in words:
            cur.append(w)
            if len(" ".join(cur)) > 18:
                lines.append(" ".join(cur[:-1]))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))
        event_str = "\n".join(lines)

        ax.text(x, y_text, event_str,
                transform=ax.transAxes,
                ha="center", va=va_text,
                fontsize=9, color=TEXT_COLOR,
                multialignment="center")

    if title:
        fig.text(0.5, 0.96, title,
                 ha="center", va="top",
                 fontsize=14, fontweight="bold", color=TITLE_COLOR,
                 transform=fig.transFigure)

    _save(fig, plt, out_path)


def render_pie(data: list[dict], title: str, out_path: Path):
    """
    Pie chart with percentage labels.
    data: [{"label": str, "value": float}]
    """
    fig, ax, plt = _setup_fig()

    labels = [d["label"] for d in data]
    values = [float(d["value"]) for d in data]
    colors = [BAR_PALETTE[i % len(BAR_PALETTE)] for i in range(len(data))]

    wedges, texts, autotexts = ax.pie(
        values,
        labels=None,
        colors=colors,
        autopct="%1.0f%%",
        startangle=90,
        pctdistance=0.75,
        wedgeprops={"edgecolor": "white", "linewidth": 2},
    )
    for at in autotexts:
        at.set_fontsize(13)
        at.set_fontweight("bold")
        at.set_color("white")

    ax.legend(
        wedges, labels,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=min(len(labels), 4),
        fontsize=10,
        framealpha=0.9,
        facecolor=BG_COLOR,
        edgecolor="#C8B89A",
    )

    if title:
        ax.set_title(title, fontsize=14, fontweight="bold", color=TITLE_COLOR, pad=16)

    _save(fig, plt, out_path)


def _save(fig, plt, out_path: Path):
    import matplotlib
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout(pad=1.2)
    plt.savefig(str(out_path), dpi=100, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close()
    print(f"  ✓ Chart saved → {out_path}  (1280×720)")


# ── CLI ────────────────────────────────────────────────────────────────────────

RENDERERS = {
    "bar":      render_bar,
    "stat":     render_stat,
    "timeline": render_timeline,
    "pie":      render_pie,
}

EXAMPLES = {
    "bar": '[{"label":"Karnataka","value":15,"unit":"p"},{"label":"UP","value":8,"unit":"p"}]',
    "stat": '[{"stat":"91","label":"President Rule impositions since 1950","color":"#C0392B"}]',
    "timeline": '[{"year":1959,"event":"Kerala dismissed"},{"year":1977,"event":"9 Congress states"},{"year":1994,"event":"Bommai judgment"}]',
    "pie": '[{"label":"Politically motivated","value":60},{"label":"Hung assembly","value":30},{"label":"Genuine crisis","value":10}]',
}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--type", required=True, choices=list(RENDERERS),
                        help="Chart type: bar | stat | timeline | pie")
    parser.add_argument("--data", default=None,
                        help="JSON array of data points. See docstring for format per chart type.")
    parser.add_argument("--title",      default="", help="Chart title")
    parser.add_argument("--horizontal", action="store_true", default=True,
                        help="Horizontal bars (default for bar charts)")
    parser.add_argument("--vertical",   action="store_true",
                        help="Vertical bars instead of horizontal")
    parser.add_argument("--project",    default=None, help="Episode folder (e.g. ep01)")
    parser.add_argument("--shot",       default=None, help="Shot number — writes to images/SCENE-{n:03d}.png")
    parser.add_argument("--out",        default=None, help="Explicit output path")
    parser.add_argument("--example",    action="store_true",
                        help="Print example --data JSON for this chart type and exit")
    args = parser.parse_args()

    if args.example:
        print(f"\nExample --data for --type {args.type}:")
        print(f"  {EXAMPLES.get(args.type, '(no example)')}")
        return

    if not args.data:
        parser.error("--data is required (JSON array). Use --example to see the format.")

    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in --data: {e}")
        sys.exit(1)

    # Resolve output path
    if args.out:
        out_path = Path(args.out)
    elif args.project and args.shot:
        out_path = PIPELINE_DIR / args.project / "images" / f"SCENE-{str(args.shot).zfill(3)}.png"
    elif args.project:
        out_path = PIPELINE_DIR / args.project / f"{args.type}_chart.png"
    else:
        out_path = Path(f"{args.type}_chart.png")

    try:
        import matplotlib
        matplotlib.use("Agg")
    except ImportError:
        print("❌ pip install matplotlib --break-system-packages")
        sys.exit(1)

    print(f"\n  Type   : {args.type}")
    print(f"  Title  : {args.title or '(none)'}")
    print(f"  Items  : {len(data)}")
    print(f"  Output : {out_path}")

    if args.type == "bar":
        render_bar(data, args.title, out_path, horizontal=not args.vertical)
    elif args.type == "stat":
        render_stat(data, args.title, out_path)
    elif args.type == "timeline":
        render_timeline(data, args.title, out_path)
    elif args.type == "pie":
        render_pie(data, args.title, out_path)


if __name__ == "__main__":
    main()
