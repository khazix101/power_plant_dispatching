"""
生成微电网架构拓扑示意图。

使用 matplotlib 绘制孤岛充电站微电网的框图，
展示所有组件、连接关系及额定参数。
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path


def generate_diagram(output_dir="output"):
    """生成并保存微电网拓扑示意图。"""
    fig, ax = plt.subplots(1, 1, figsize=(14, 8))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 8)
    ax.axis("off")
    ax.set_title(
        "充电站微电网拓扑 (0.4 kV 孤岛运行)",
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
    print(f"拓扑示意图已保存至: {filepath}")
    return filepath


def _draw_mainbus(ax):
    """绘制 0.4 kV 交流主母线。"""
    x, y = 7.0, 4.0
    ax.barh(y, 6, height=0.3, left=4, color="steelblue", edgecolor="black")
    ax.text(x, y, "主母线 0.4 kV AC", ha="center", va="center",
            fontsize=11, fontweight="bold", color="white")


def _draw_pv(ax):
    """绘制光伏发电单元。"""
    x, y = 7.0, 6.2
    box = mpatches.FancyBboxPatch(
        (x - 1.0, y - 0.5), 2.0, 1.0,
        boxstyle="round,pad=0.1", facecolor="gold", edgecolor="black"
    )
    ax.add_patch(box)
    ax.text(x, y, "光伏系统\n3000 kW", ha="center", va="center",
            fontsize=9, fontweight="bold")
    ax.plot([x, x], [y - 0.5, 4.15], "k-", linewidth=1.5)
    ax.text(x + 0.1, (y - 0.5 + 4.15) / 2, "光伏线路\n(0.05 km)",
            fontsize=7, color="gray")


def _draw_wind(ax):
    """绘制风电发电单元。"""
    x, y = 4.0, 4.0
    box = mpatches.FancyBboxPatch(
        (x - 1.0, y - 0.5), 2.0, 1.0,
        boxstyle="round,pad=0.1", facecolor="lightblue", edgecolor="black"
    )
    ax.add_patch(box)
    ax.text(x, y, "风电发电机\n2000 kW", ha="center", va="center",
            fontsize=9, fontweight="bold")
    ax.plot([x, x], [y - 0.5, 4.15], "k-", linewidth=1.5)
    ax.text(x + 0.1, (y - 0.5 + 4.15) / 2, "风电线路\n(0.05 km)",
            fontsize=7, color="gray")


def _draw_load(ax):
    """绘制充电桩负荷单元。"""
    x, y = 10.0, 4.0
    box = mpatches.FancyBboxPatch(
        (x - 1.0, y - 0.5), 2.0, 1.0,
        boxstyle="round,pad=0.1", facecolor="salmon", edgecolor="black"
    )
    ax.add_patch(box)
    ax.text(x, y, "充电桩\n最大 14.4 MW", ha="center", va="center",
            fontsize=9, fontweight="bold")
    ax.plot([x, x], [y - 0.5, 4.15], "k-", linewidth=1.5)
    ax.text(x + 0.1, (y - 0.5 + 4.15) / 2, "负荷线路\n(0.05 km)",
            fontsize=7, color="gray")


def _draw_storage(ax):
    """绘制储能单元。"""
    x, y = 3.0, 2.0
    box = mpatches.FancyBboxPatch(
        (x - 1.5, y - 0.5), 3.0, 1.5,
        boxstyle="round,pad=0.1", facecolor="mediumseagreen", edgecolor="black"
    )
    ax.add_patch(box)
    ax.text(x, y + 0.2, "储能系统", ha="center", va="center",
            fontsize=10, fontweight="bold")
    ax.text(x, y - 0.35, "3750 kW | 7500 kWh", ha="center", va="center",
            fontsize=9)
    ax.plot([x, x], [y + 0.5, 3.85], "k-", linewidth=1.5)
    ax.plot([3.0, 3.0 + 0.2], [3.85, 3.85], "k-", linewidth=1.5)
    ax.text(x + 0.3, (y + 0.5 + 3.85) / 2, "储能线路\n(0.05 km)",
            fontsize=7, color="gray")


def _draw_source(ax):
    """绘制 Vsource (平衡节点参考)。"""
    x, y = 11.0, 2.0
    box = mpatches.FancyBboxPatch(
        (x - 1.2, y - 0.4), 2.4, 0.8,
        boxstyle="round,pad=0.1", facecolor="lightgray", edgecolor="black"
    )
    ax.add_patch(box)
    ax.text(x, y, "电压源 (平衡节点)\n电压参考", ha="center",
            va="center", fontsize=8, fontweight="bold")
    ax.plot([x, x], [y + 0.4, 3.85], "k-", linewidth=1.5)
    ax.plot([11.0, 11.0 - 0.2], [3.85, 3.85], "k-", linewidth=1.5)


def _draw_legend(ax):
    """绘制颜色图例。"""
    legend_items = [
        mpatches.Patch(facecolor="gold", edgecolor="black", label="光伏系统"),
        mpatches.Patch(facecolor="lightblue", edgecolor="black", label="风电系统"),
        mpatches.Patch(facecolor="salmon", edgecolor="black", label="负荷 (充电桩)"),
        mpatches.Patch(facecolor="mediumseagreen", edgecolor="black", label="储能系统"),
        mpatches.Patch(facecolor="lightgray", edgecolor="black", label="电压源 (平衡节点)"),
        mpatches.Patch(facecolor="steelblue", edgecolor="black", label="主母线"),
    ]
    ax.legend(handles=legend_items, loc="lower center",
              ncol=3, fontsize=8, framealpha=0.9,
              bbox_to_anchor=(0.5, -0.12))


if __name__ == "__main__":
    generate_diagram("output")
    print("完成。")
