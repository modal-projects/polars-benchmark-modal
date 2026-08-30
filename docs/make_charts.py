"""Render the charts in README.md from the measurements taken with this repo.

``python docs/make_charts.py`` writes ``docs/input-path.png``, ``docs/bytes.png``,
``docs/cost.png``, ``docs/cumulative-cost.png`` and ``docs/queries.png``.
Requires matplotlib.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT_DIR = Path(__file__).parent

# Scale factor 100, 4 CPU / 16 GiB: q1..q22 seconds within the 22-query suite,
# by input path.
# fmt: off
INPUT_PATH_SECONDS = {
    "Modal Volume": [
        3.19, 0.28, 5.54, 3.78, 2.86, 0.85, 4.24, 2.64, 5.90, 2.96, 0.48,
        1.33, 3.66, 1.19, 1.09, 0.65, 1.45, 5.38, 1.91, 2.57, 17.75, 1.00,
    ],
    "Direct S3": [
        55.65, 11.48, 124.85, 47.74, 105.91, 58.87, 110.77, 110.90, 113.70,
        112.93, 6.05, 64.31, 30.51, 95.64, 78.95, 8.34, 74.87, 89.56, 106.48,
        101.41, 196.94, 5.81,
    ],
    "CloudBucketMount": [
        196.19, 53.39, 344.98, 191.67, 520.85, 245.49, 313.99, 437.22, 413.91,
        286.60, 17.80, 229.96, 82.98, 282.43, 272.02, 11.13, 268.80, 233.01,
        374.92, 296.19, 567.47, 32.54,
    ],
}
# fmt: on

# Geometric mean of the same three suites, in seconds.
INPUT_PATH_GEOMEAN = (2.10, 55.45, 182.17)

# GB read out of the bucket per 22-query run.
S3_GB_PER_RUN = {
    "Modal Volume\n(first run only)": 26.5,
    "Direct S3\n(every run)": 344.6,
}

# Dollars per 22-query run at 4 CPU / 16 GiB, compute plus S3 transfer at
# $0.09/GB, as (low, high). Only the mount's transfer has to be bounded rather
# than measured, so only its bar spans a range.
RUN_COST = {
    "Volume, warm": (0.015, 0.015),
    "Volume, first run": (2.41, 2.41),
    "CloudBucketMount": (3.53, 35.58),
    "Direct S3": (31.32, 31.32),
}

# Warm-Volume query seconds for q1..q22, by requested resources.
# fmt: off
QUERY_SECONDS = {
    "4 CPU / 16 GiB": [
        3.19, 0.28, 5.54, 3.78, 2.86, 0.85, 4.24, 2.64, 5.90, 2.96, 0.48,
        1.33, 3.66, 1.19, 1.09, 0.65, 1.45, 5.38, 1.91, 2.57, 17.75, 1.00,
    ],
    "8 CPU / 32 GiB": [
        2.74, 0.28, 4.77, 3.18, 2.54, 0.77, 3.60, 2.52, 5.74, 2.82, 0.45,
        1.21, 3.03, 1.00, 0.94, 0.55, 1.29, 5.32, 1.49, 2.32, 16.17, 1.16,
    ],
    "32 CPU / 128 GiB": [
        1.49, 0.33, 3.21, 2.18, 1.47, 0.57, 2.27, 1.86, 3.36, 1.55, 0.43,
        0.79, 1.98, 0.64, 0.77, 0.38, 0.77, 2.42, 0.86, 1.43, 8.92, 0.93,
    ],
}
# fmt: on

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


def title(ax, heading: str, subheading: str) -> None:
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


def legend(ax, labels, colors) -> None:
    ax.legend(
        handles=[
            plt.Rectangle((0, 0), 1, 1, color=color, label=label)
            for label, color in zip(labels, colors)
        ],
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.1),
        ncols=len(labels),
        fontsize=8,
    )


def input_path_chart() -> Path:
    """All 22 queries on the three input paths, and the geometric mean of each."""
    fig, ax = plt.subplots(figsize=(7.4, 3.2))
    width = 0.27
    slots = range(23)
    for index, seconds in enumerate(INPUT_PATH_SECONDS.values()):
        values = [*seconds, INPUT_PATH_GEOMEAN[index]]
        positions = [slot + (index - 1) * width for slot in slots]
        ax.bar(positions, values, width=width, color=COLORS[index], zorder=2)
    ax.axvline(21.5, color="#d5d8e0", linewidth=1, zorder=1)
    ax.set_yscale("log")
    ax.set_ylim(0.2, 1200)
    ax.set_xticks(
        slots,
        [f"q{query}" for query in range(1, 23)] + ["geo.\nmean"],
        fontsize=7,
    )
    ax.set_yticks([1, 10, 100, 1000], ["1s", "10s", "100s", "1000s"])
    ax.grid(axis="y", color="#eceef3", zorder=1)
    title(
        ax,
        "Query time by input path (log scale)",
        "scale factor 100, 4 CPU / 16 GiB, within the 22-query suite",
    )
    legend(ax, list(INPUT_PATH_SECONDS), COLORS)
    path = OUT_DIR / "input-path.png"
    fig.savefig(path, bbox_inches="tight")
    return path


def bytes_chart() -> Path:
    """Why the cached path is cheap: it reads the dataset once, not per query."""
    fig, ax = plt.subplots(figsize=(5.2, 2.0))
    positions = range(len(S3_GB_PER_RUN))
    ax.barh(positions, list(S3_GB_PER_RUN.values()), height=0.5, color=COLORS[0])
    for position, gigabytes in enumerate(S3_GB_PER_RUN.values()):
        ax.text(
            gigabytes + 8,
            position,
            f"{gigabytes:.1f} GB, {gigabytes / 26.5:.1f}x the dataset",
            va="center",
            fontsize=8,
        )
    ax.set_xlim(0, 500)
    ax.set_yticks(positions, list(S3_GB_PER_RUN), fontsize=8)
    ax.invert_yaxis()
    ax.set_xticks(
        [0, 100, 200, 300, 400], ["0", "100 GB", "200 GB", "300 GB", "400 GB"]
    )
    title(
        ax,
        "Data read out of S3 per 22-query run",
        "scale factor 100, dataset is 26.5 GB in eight Parquet tables",
    )
    path = OUT_DIR / "bytes.png"
    fig.savefig(path, bbox_inches="tight")
    return path


def cost_chart() -> Path:
    fig, ax = plt.subplots(figsize=(5.2, 2.4))
    labels = list(RUN_COST)
    positions = range(len(labels))
    ax.barh(
        positions,
        [high for _, high in RUN_COST.values()],
        height=0.6,
        color=COLORS[1],
        zorder=2,
    )
    ax.barh(
        positions,
        [low for low, _ in RUN_COST.values()],
        height=0.6,
        color=COLORS[0],
        zorder=3,
    )
    for position, bounds in enumerate(RUN_COST.values()):
        amount = " to ".join(
            f"\\${cost:.2f}" if cost >= 1 else f"\\${cost:.3f}"
            for cost in sorted(set(bounds))
        )
        ax.text(max(bounds) * 1.1, position, amount, va="center", fontsize=8)
    ax.set_xscale("log")
    ax.set_xlim(0.01, 200)
    ax.set_yticks(positions, labels)
    ax.invert_yaxis()
    ax.set_xticks([0.01, 0.1, 1, 10, 100], ["$0.01", "$0.10", "$1", "$10", "$100"])
    ax.grid(axis="x", color="#eceef3", zorder=1)
    title(
        ax,
        "Cost per 22-query run (log scale)",
        "4 CPU / 16 GiB, compute + S3 transfer at \\$0.09/GB, pale bar is modeled",
    )
    path = OUT_DIR / "cost.png"
    fig.savefig(path, bbox_inches="tight")
    return path


def cumulative_cost_chart() -> Path:
    """What each input path costs over repeated runs of the same suite."""
    fig, ax = plt.subplots(figsize=(5.2, 2.8))
    runs = range(1, 11)
    volume = [2.41 + 0.015 * (run - 1) for run in runs]
    direct_s3 = [31.32 * run for run in runs]
    mount_low = [3.53 * run for run in runs]
    mount_high = [35.58 * run for run in runs]
    ax.fill_between(runs, mount_low, mount_high, color=COLORS[2], alpha=0.5, zorder=2)
    for bound in (mount_low, mount_high):
        ax.plot(runs, bound, color="#9ea3ad", linewidth=1, linestyle="--", zorder=2)
    ax.plot(runs, direct_s3, color=COLORS[1], linewidth=2, zorder=3)
    ax.plot(runs, volume, color=COLORS[0], linewidth=2, zorder=4)
    for values in (direct_s3, volume):
        ax.text(10.15, values[-1], f"\\${values[-1]:,.2f}", fontsize=8, va="center")
    ax.text(6.6, direct_s3[5] + 14, "Direct S3", fontsize=8)
    ax.text(7.4, volume[6] + 12, "Modal Volume", fontsize=8)
    ax.text(3.5, 75, "CloudBucketMount,\nmodeled range", fontsize=8)
    ax.set_xlim(1, 10)
    ax.set_ylim(0, 380)
    ax.set_xticks(list(runs))
    ax.set_yticks([0, 100, 200, 300], ["$0", "$100", "$200", "$300"])
    ax.set_xlabel("suite runs", fontsize=8)
    ax.grid(axis="y", color="#eceef3", zorder=1)
    title(
        ax,
        "Cost of the same suite, run again and again",
        "scale factor 100, 4 CPU / 16 GiB, cache filled on the first run",
    )
    path = OUT_DIR / "cumulative-cost.png"
    fig.savefig(path, bbox_inches="tight")
    return path


def queries_chart() -> Path:
    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    width = 0.27
    for index, seconds in enumerate(QUERY_SECONDS.values()):
        positions = [query + (index - 1) * width for query in range(22)]
        ax.bar(positions, seconds, width=width, color=COLORS[index], zorder=2)
    ax.set_xticks(range(22), [f"q{query}" for query in range(1, 23)], fontsize=7)
    ax.set_yticks([0, 5, 10, 15, 20], ["0", "5s", "10s", "15s", "20s"])
    ax.grid(axis="y", color="#eceef3", zorder=1)
    title(
        ax,
        "PDS-H query time off the Volume",
        "scale factor 100, warm cache, totals of 70.7s / 63.9s / 38.6s",
    )
    legend(ax, list(QUERY_SECONDS), COLORS)
    path = OUT_DIR / "queries.png"
    fig.savefig(path, bbox_inches="tight")
    return path


if __name__ == "__main__":
    for chart in (
        input_path_chart(),
        bytes_chart(),
        cost_chart(),
        cumulative_cost_chart(),
        queries_chart(),
    ):
        print(chart)
