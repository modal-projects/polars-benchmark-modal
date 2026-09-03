"""Render the five charts used in README.md.

``python docs/make_charts.py`` writes only the PNG files in ``docs/``. The
measurements are kept in this module so chart regeneration needs no network or
benchmark artifacts.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT_DIR = Path(__file__).parent
COLORS = ("#4b57d8", "#8f97e8", "#c9ccd6")
TEXT_COLOR = "#1c1e26"

plt.rcParams.update(
    {
        "figure.dpi": 200,
        "font.size": 9,
        "text.color": TEXT_COLOR,
        "axes.labelcolor": TEXT_COLOR,
        "xtick.color": TEXT_COLOR,
        "ytick.color": TEXT_COLOR,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "axes.edgecolor": "#d5d8e0",
        "ytick.left": False,
    }
)

READ_SPEED = {
    "Modal Volume": {"pinned": 2.42, "unpinned": 2.31},
    "CloudBucketMount": {"pinned": 0.57, "unpinned": 0.36},
    "s3:// (boto3, 8 streams)": {"pinned": 0.23, "unpinned": 0.19},
}

COST_ROWS = (
    ("Volume warm, unpinned", 0.014, 0.0),
    ("Volume warm, pinned", 0.049, 0.0),
    ("Volume first fill, pinned", 0.062, 0.0),
    ("Volume first fill, unpinned", 0.036, 2.39),
    ("s3://, pinned", 0.301, 0.0),
    ("s3://, unpinned", 0.305, 31.01),
    ("CloudBucketMount, unpinned", 1.14, None),
)

PLACEMENT_ROWS = (
    ("none", "southcentralus", "southcentralus", 157, 0.014, 206, 2.41),
    ("us", "us-east-1", "us-east-1", 291, 0.038, 323, 0.043),
    ("us-east", "us-east-2", "us-east-1", 278, 0.037, 276, 0.036),
    ("us-east-1", "us-east-1", "us-east-1", 321, 0.049, 315, 0.049),
)

# fmt: off
INPUT_PATH_SECONDS = {
    "Volume warm unpinned": {
        1: 3.24, 2: 0.31, 3: 4.80, 4: 3.22, 5: 3.22, 6: 1.06,
        7: 4.39, 8: 3.04, 9: 6.53, 10: 3.13, 11: 0.65, 12: 1.49,
        13: 3.89, 14: 1.23, 15: 1.22, 16: 0.79, 17: 1.64, 18: 4.98,
        19: 1.91, 20: 2.93, 21: 17.39, 22: 1.46,
    },
    "Volume warm pinned": {
        1: 5.29, 2: 0.61, 3: 13.41, 4: 6.54, 5: 4.61, 6: 1.70,
        7: 9.69, 8: 5.34, 9: 11.95, 10: 4.92, 11: 1.16, 12: 2.75,
        13: 8.28, 14: 2.25, 15: 2.38, 16: 1.31, 17: 2.61, 18: 10.27,
        19: 3.43, 20: 6.00, 21: 34.16, 22: 2.53,
    },
    "s3:// pinned": {
        1: 37.59, 2: 6.18, 3: 88.69, 4: 35.86, 5: 51.77, 6: 30.36,
        7: 57.34, 8: 55.73, 9: 60.45, 10: 57.47, 11: 3.53, 12: 32.35,
        13: 21.56, 14: 46.48, 15: 39.94, 16: 3.30, 17: 41.34, 18: 51.07,
        19: 62.48, 20: 54.37, 21: 122.44, 22: 6.45,
    },
}
# fmt: on

EXPECTED_GEOMEANS = (2.28, 4.23, 31.76)


def chart_title(ax, heading: str, subheading: str) -> None:
    ax.set_title(heading, loc="left", fontsize=10, pad=24)
    ax.text(
        0,
        1.03,
        subheading,
        transform=ax.transAxes,
        fontsize=8,
        color="#6b7280",
        va="bottom",
    )


def input_path_chart() -> Path:
    """Compare each query on the two Volume placements and direct S3."""
    geomeans = []
    for index, seconds in enumerate(INPUT_PATH_SECONDS.values()):
        geomean = math.exp(sum(math.log(value) for value in seconds.values()) / 22)
        assert round(geomean, 2) == EXPECTED_GEOMEANS[index]
        geomeans.append(geomean)

    fig, ax = plt.subplots(figsize=(7.4, 3.2))
    width = 0.27
    slots = [*range(22), 23]
    for index, (label, seconds) in enumerate(INPUT_PATH_SECONDS.items()):
        values = [*seconds.values(), geomeans[index]]
        positions = [slot + (index - 1) * width for slot in slots]
        ax.bar(positions, values, width=width, color=COLORS[index], zorder=2)
    ax.axvline(22, color="#d5d8e0", linewidth=1, zorder=1)
    ax.set_yscale("log")
    ax.set_ylim(0.2, 200)
    ax.set_xticks(
        slots,
        [f"q{query}" for query in range(1, 23)] + ["geomean"],
        fontsize=7,
    )
    ax.set_yticks([1, 10, 100], ["1s", "10s", "100s"])
    ax.grid(axis="y", color="#eceef3", zorder=1)
    chart_title(
        ax,
        "Query time by input path",
        "scale factor 100, 4 CPU / 16 GiB, log scale",
    )
    ax.legend(
        handles=[
            plt.Rectangle((0, 0), 1, 1, color=color, label=label)
            for label, color in zip(INPUT_PATH_SECONDS, COLORS)
        ],
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.1),
        ncols=3,
        fontsize=8,
    )
    path = OUT_DIR / "input-path.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def read_speed_chart() -> Path:
    """Show the measured whole-dataset read speed for each input path."""
    fig, ax = plt.subplots(figsize=(6.8, 2.7))
    positions = list(range(len(READ_SPEED)))
    height = 0.32
    for index, placement in enumerate(("pinned", "unpinned")):
        values = [data[placement] for data in READ_SPEED.values()]
        bars = ax.barh(
            [position + (index - 0.5) * height for position in positions],
            values,
            height=height,
            color=COLORS[index],
            label=placement,
            zorder=2,
        )
        for bar, value in zip(bars, values):
            ax.text(
                value + 0.025,
                bar.get_y() + bar.get_height() / 2,
                f"{value:.2f}",
                va="center",
                fontsize=8,
            )
    ax.set_yticks(positions, list(READ_SPEED))
    ax.invert_yaxis()
    ax.set_xlim(0, 2.8)
    ax.set_xlabel("GB/s")
    ax.grid(axis="x", color="#eceef3", zorder=1)
    chart_title(
        ax,
        "Sequential read speed by input path",
        "whole 26.5 GB dataset, 8 parallel streams, no query engine",
    )
    ax.legend(frameon=False, loc="lower right", fontsize=8)
    path = OUT_DIR / "read-speed.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def placement_chart() -> Path:
    """Compare warm and cold Volume suite wall time across selectors."""
    fig, ax = plt.subplots(figsize=(6.8, 3.1))
    positions = list(range(len(PLACEMENT_ROWS)))
    width = 0.32
    for offset, wall_index, cost_index, landed_index, color, label in (
        (-width / 2, 3, 4, 1, COLORS[0], "warm"),
        (width / 2, 5, 6, 2, COLORS[1], "cold"),
    ):
        bars = ax.barh(
            [position + offset for position in positions],
            [row[wall_index] for row in PLACEMENT_ROWS],
            height=width,
            color=color,
            label=label,
            zorder=2,
        )
        for bar, row in zip(bars, PLACEMENT_ROWS):
            cost = row[cost_index]
            ax.text(
                row[wall_index] + 6,
                bar.get_y() + bar.get_height() / 2,
                f"{row[landed_index]} ${cost:.3f}"
                if cost < 1
                else f"{row[landed_index]} ${cost:.2f}",
                va="center",
                fontsize=8,
            )
    ax.set_yticks(
        positions,
        [requested for requested, *_ in PLACEMENT_ROWS],
        fontsize=8,
    )
    ax.invert_yaxis()
    ax.set_xlim(0, 390)
    ax.set_xlabel("wall seconds")
    ax.grid(axis="x", color="#eceef3", zorder=1)
    chart_title(
        ax,
        "Warm and cold Volume suite by placement",
        "scale factor 100, 4 CPU / 16 GiB, landed region and cost labelled",
    )
    ax.legend(
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        ncols=2,
        fontsize=8,
    )
    path = OUT_DIR / "placement.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def cost_chart() -> Path:
    """Show compute and transfer components of each suite cost."""
    fig, ax = plt.subplots(figsize=(6.6, 3.1))
    positions = list(range(len(COST_ROWS)))
    baseline = 0.01
    for position, (label, compute, transfer) in enumerate(COST_ROWS):
        ax.barh(
            position,
            compute - baseline,
            left=baseline,
            height=0.58,
            color=COLORS[0],
            label="compute" if position == 0 else None,
            zorder=3,
        )
        if transfer is None:
            ax.barh(
                position,
                34.44 - compute,
                left=compute,
                height=0.58,
                color=COLORS[2],
                hatch="///",
                edgecolor="#8d929e",
                label="transfer not measurable (modeled range)"
                if position == 6
                else None,
                zorder=2,
            )
            ax.text(34.44 * 1.04, position, "$34.44 upper bound (modeled)", va="center")
        else:
            if transfer:
                ax.barh(
                    position,
                    transfer,
                    left=compute,
                    height=0.58,
                    color=COLORS[1],
                    label="S3 transfer" if position == 3 else None,
                    zorder=2,
                )
            total = compute + transfer
            formatted = f"${total:.3f}" if total < 1 else f"${total:.2f}"
            ax.text(total * 1.06, position, formatted, va="center", fontsize=8)
    ax.set_xscale("log")
    ax.set_xlim(0.01, 60)
    ax.set_yticks(positions, [row[0] for row in COST_ROWS], fontsize=8)
    ax.invert_yaxis()
    ax.set_xticks([0.01, 0.1, 1, 10], ["$0.01", "$0.10", "$1", "$10"])
    ax.grid(axis="x", color="#eceef3", zorder=1)
    chart_title(
        ax,
        "Cost per 22-query run",
        "compute + S3 transfer, log scale, transfer upper bound is modeled",
    )
    ax.legend(
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        ncols=3,
        fontsize=7,
    )
    path = OUT_DIR / "cost.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def cumulative_cost_chart() -> Path:
    """Compare modeled cost over ten repeated suite runs."""
    runs = list(range(1, 11))
    volume = [0.0130 + run * 0.0138 for run in runs]
    pinned_s3 = [run * 0.301 for run in runs]
    unpinned_s3 = [run * 31.32 for run in runs]

    fig, ax = plt.subplots(figsize=(6.6, 3.0))
    lines = (
        (volume, COLORS[0], "pinned fill, then unpinned Volume"),
        (pinned_s3, COLORS[1], "pinned s3://"),
        (unpinned_s3, COLORS[2], "unpinned s3://"),
    )
    for values, color, label in lines:
        ax.plot(runs, values, color=color, linewidth=2, label=label, zorder=3)
        ax.text(10.15, values[-1], label, color=color, va="center", fontsize=7)
    ax.set_yscale("log")
    ax.set_xlim(1, 16)
    ax.set_ylim(0.01, 500)
    ax.set_xticks(runs)
    ax.set_xlabel("suite runs")
    ax.set_yticks(
        [0.01, 0.1, 1, 10, 100],
        ["$0.01", "$0.10", "$1", "$10", "$100"],
    )
    ax.set_ylabel("cumulative cost, dollars")
    ax.grid(axis="y", color="#eceef3", zorder=1)
    ax.text(
        0.03,
        0.04,
        "Volume storage of $2.22/month is not included in the line.",
        transform=ax.transAxes,
        fontsize=7,
        color="#525866",
    )
    chart_title(
        ax,
        "Cumulative cost over repeated suite runs",
        "modeled values, log scale",
    )
    path = OUT_DIR / "cumulative-cost.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


if __name__ == "__main__":
    for chart in (
        read_speed_chart(),
        placement_chart(),
        cost_chart(),
        input_path_chart(),
        cumulative_cost_chart(),
    ):
        print(chart)
