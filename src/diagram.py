"""
Generate microgrid architecture topology diagram.

Uses matplotlib to draw a block diagram of the islanded charging station
microgrid, showing all components, connections, and rated parameters.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path


def generate_diagram(output_dir="output"):
    """Generate and save the microgrid topology diagram."""
    fig, ax = plt.subplots(1, 1, figsize=(14, 8))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 8)
    ax.axis("off")
    ax.set_title(
        "Charging Station Microgrid Topology (0.4 kV Islanded)",
        fontsize=16, fontweight="bold", pad=20
    )

    _draw_mainbus(ax)
    _draw_pv(ax)
    _draw_wind(ax)
    _draw_load(ax)
    _draw_storage(ax)
    _draw_source(ax)
    _draw_legend(ax)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    filepath = output_path / "topology.png"
    fig.savefig(filepath, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Topology diagram saved to: {filepath}")
    return filepath


def _draw_mainbus(ax):
    """Draw the main 0.4 kV AC busbar."""
    x, y = 7.0, 4.0
    ax.barh(y, 6, height=0.3, left=4, color="steelblue", edgecolor="black")
    ax.text(x, y, "MainBus 0.4 kV AC", ha="center", va="center",
            fontsize=11, fontweight="bold", color="white")


def _draw_pv(ax):
    """Draw the PV generation unit."""
    x, y = 7.0, 6.2
    box = mpatches.FancyBboxPatch(
        (x - 1.0, y - 0.5), 2.0, 1.0,
        boxstyle="round,pad=0.1", facecolor="gold", edgecolor="black"
    )
    ax.add_patch(box)
    ax.text(x, y, "PV System\n3000 kW", ha="center", va="center",
            fontsize=9, fontweight="bold")
    ax.plot([x, x], [y - 0.5, 4.15], "k-", linewidth=1.5)
    ax.text(x + 0.1, (y - 0.5 + 4.15) / 2, "Line_PV\n(0.05 km)",
            fontsize=7, color="gray")


def _draw_wind(ax):
    """Draw the wind generation unit."""
    x, y = 4.0, 4.0
    box = mpatches.FancyBboxPatch(
        (x - 1.0, y - 0.5), 2.0, 1.0,
        boxstyle="round,pad=0.1", facecolor="lightblue", edgecolor="black"
    )
    ax.add_patch(box)
    ax.text(x, y, "Wind Generator\n2000 kW", ha="center", va="center",
            fontsize=9, fontweight="bold")
    ax.plot([x, x], [y - 0.5, 4.15], "k-", linewidth=1.5)
    ax.text(x + 0.1, (y - 0.5 + 4.15) / 2, "Line_Wind\n(0.05 km)",
            fontsize=7, color="gray")


def _draw_load(ax):
    """Draw the EV charger load unit."""
    x, y = 10.0, 4.0
    box = mpatches.FancyBboxPatch(
        (x - 1.0, y - 0.5), 2.0, 1.0,
        boxstyle="round,pad=0.1", facecolor="salmon", edgecolor="black"
    )
    ax.add_patch(box)
    ax.text(x, y, "EV Chargers\n14.4 MW max", ha="center", va="center",
            fontsize=9, fontweight="bold")
    ax.plot([x, x], [y - 0.5, 4.15], "k-", linewidth=1.5)
    ax.text(x + 0.1, (y - 0.5 + 4.15) / 2, "Line_Load\n(0.05 km)",
            fontsize=7, color="gray")


def _draw_storage(ax):
    """Draw the energy storage unit."""
    x, y = 3.0, 2.0
    box = mpatches.FancyBboxPatch(
        (x - 1.5, y - 0.5), 3.0, 1.5,
        boxstyle="round,pad=0.1", facecolor="mediumseagreen", edgecolor="black"
    )
    ax.add_patch(box)
    ax.text(x, y + 0.2, "Energy Storage", ha="center", va="center",
            fontsize=10, fontweight="bold")
    ax.text(x, y - 0.35, "3750 kW | 7500 kWh", ha="center", va="center",
            fontsize=9)
    ax.plot([x, x], [y + 0.5, 3.85], "k-", linewidth=1.5)
    ax.plot([3.0, 3.0 + 0.2], [3.85, 3.85], "k-", linewidth=1.5)
    ax.text(x + 0.3, (y + 0.5 + 3.85) / 2, "Line_Stor\n(0.05 km)",
            fontsize=7, color="gray")


def _draw_source(ax):
    """Draw the Vsource (slack bus reference)."""
    x, y = 11.0, 2.0
    box = mpatches.FancyBboxPatch(
        (x - 1.2, y - 0.4), 2.4, 0.8,
        boxstyle="round,pad=0.1", facecolor="lightgray", edgecolor="black"
    )
    ax.add_patch(box)
    ax.text(x, y, "Vsource (Slack)\nVoltage Reference", ha="center",
            va="center", fontsize=8, fontweight="bold")
    ax.plot([x, x], [y + 0.4, 3.85], "k-", linewidth=1.5)
    ax.plot([11.0, 11.0 - 0.2], [3.85, 3.85], "k-", linewidth=1.5)


def _draw_legend(ax):
    """Draw the color legend."""
    legend_items = [
        mpatches.Patch(facecolor="gold", edgecolor="black", label="PV System"),
        mpatches.Patch(facecolor="lightblue", edgecolor="black", label="Wind System"),
        mpatches.Patch(facecolor="salmon", edgecolor="black", label="Load (EV Chargers)"),
        mpatches.Patch(facecolor="mediumseagreen", edgecolor="black", label="Energy Storage"),
        mpatches.Patch(facecolor="lightgray", edgecolor="black", label="Vsource (Slack)"),
        mpatches.Patch(facecolor="steelblue", edgecolor="black", label="MainBus"),
    ]
    ax.legend(handles=legend_items, loc="lower center",
              ncol=3, fontsize=8, framealpha=0.9,
              bbox_to_anchor=(0.5, -0.12))


if __name__ == "__main__":
    generate_diagram("output")
    print("Done.")
