"""Figures. Every one states how much of what it shows is still guessed.

Ground rule 3 asks reports to say what fraction of their inputs are assumed.
A figure is a report, arguably the most-quoted kind, so the provenance line
is part of the drawing, not part of the caption someone might crop off.

Imports `core` and `analysis` only; like the rest of `analysis/`, it must not
reach into `app/` or `sim/`. Results arrive as plain rows.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean, stdev
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")  # written to files, never shown
import matplotlib.pyplot as plt  # noqa: E402

__all__ = ["sweep_figure", "arm_figures", "utilisation_figure"]

CONFIDENCE_95 = 1.96
SECONDS_PER_MINUTE = 60.0

# A muted, colour-blind-safe sequence. Distinguishable in print and by shape as
# well as hue, because an arm chart is read at a glance and often photocopied.
SERIES = ["#1f6b52", "#b3401f", "#3f5c8a", "#8a6d1f", "#6b4a7a"]
MARKERS = ["o", "s", "^", "D", "v"]
INK = "#16211d"
MUTED = "#6a736d"
GRID = "#dde3dd"


def _style(ax) -> None:
    ax.set_facecolor("white")
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.label.set_color(MUTED)
    ax.xaxis.label.set_color(MUTED)


def _finish(fig, ax, title: str, provenance: str, path: Path) -> Path:
    ax.set_title(title, color=INK, fontsize=12, loc="left", pad=12)
    fig.text(0.01, 0.015, provenance, color=MUTED, fontsize=8)
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, facecolor="white")
    plt.close(fig)
    return path


def _interval(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return (float("nan"), 0.0)
    if len(values) < 2:
        return (values[0], 0.0)
    return (mean(values), CONFIDENCE_95 * stdev(values) / len(values) ** 0.5)


def _series(rows: Iterable[dict], key: str) -> list[float]:
    return [row[key] for row in rows if row.get(key) is not None]


def sweep_figure(points, out_dir: str | Path = "out", key: str = "wait_p90_walkup_s") -> Path:
    """One line per arm across the swept parameter, with its interval shaded.

    The interval is the point of the picture. A sweep over assumed inputs whose
    arms sit inside each other's bands has not found a difference, however
    cleanly the means separate.
    """
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    _style(ax)

    arms = list(dict.fromkeys(point.arm for point in points))
    path = points[0].path
    scale = SECONDS_PER_MINUTE if key.endswith("_s") else 1.0

    for index, arm in enumerate(arms):
        chosen = sorted(
            (point for point in points if point.arm == arm), key=lambda p: p.value
        )
        xs = [point.value for point in chosen]
        stats = [_interval(_series(point.result.rows, key)) for point in chosen]
        ys = [value / scale for value, _ in stats]
        lo = [(value - half) / scale for value, half in stats]
        hi = [(value + half) / scale for value, half in stats]

        colour = SERIES[index % len(SERIES)]
        ax.fill_between(xs, lo, hi, color=colour, alpha=0.13, linewidth=0)
        ax.plot(xs, ys, color=colour, marker=MARKERS[index % len(MARKERS)],
                markersize=5, linewidth=2, label=arm)

    ax.set_xlabel(path)
    ax.set_ylabel("walk-up wait p90 (min)" if key.endswith("_s") else key)
    legend = ax.legend(frameon=False, fontsize=9)
    for text in legend.get_texts():
        text.set_color(INK)

    provenance = points[0].result.params.provenance_report().detail()
    return _finish(
        fig, ax, f"{key} against {path}", f"{provenance}; shaded band is the 95% interval",
        Path(out_dir) / f"sweep_{path.replace('.', '_')}_{key}.png",
    )


def arm_figures(results, out_dir: str | Path = "out") -> list[Path]:
    """The two comparisons the plan asks for: what the queue costs people, and
    what it costs the till."""
    out = Path(out_dir)
    names = [result.arm.name for result in results]
    provenance = results[0].params.provenance_report().detail()
    written: list[Path] = []

    # waits, as bars with their intervals
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    _style(ax)
    stats = [_interval(_series(r.rows, "wait_p90_walkup_s")) for r in results]
    ax.bar(
        names,
        [value / SECONDS_PER_MINUTE for value, _ in stats],
        yerr=[half / SECONDS_PER_MINUTE for _, half in stats],
        color=[SERIES[i % len(SERIES)] for i in range(len(names))],
        width=0.6, capsize=4, error_kw={"ecolor": MUTED, "linewidth": 1},
    )
    ax.set_ylabel("walk-up wait p90 (min)")
    plt.setp(ax.get_xticklabels(), rotation=12, ha="right")
    written.append(_finish(
        fig, ax, "What the queue costs the person standing in it",
        f"{provenance}; bars are the mean of seeds, whiskers the 95% interval",
        out / "waits_by_arm.png",
    ))

    # what was lost, which is the revenue case
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    _style(ax)
    lost = [_interval(_series(r.rows, "lost_fraction")) for r in results]
    ax.bar(
        names,
        [value * 100 for value, _ in lost],
        yerr=[half * 100 for _, half in lost],
        color=[SERIES[i % len(SERIES)] for i in range(len(names))],
        width=0.6, capsize=4, error_kw={"ecolor": MUTED, "linewidth": 1},
    )
    ax.set_ylabel("demand lost (%)")
    plt.setp(ax.get_xticklabels(), rotation=12, ha="right")
    written.append(_finish(
        fig, ax, "Customers who balked or gave up waiting",
        f"{provenance}; bars are the mean of seeds, whiskers the 95% interval",
        out / "lost_by_arm.png",
    ))

    return written


def utilisation_figure(
    utilisation: dict[str, float], provenance: str, out_dir: str | Path = "out",
    title: str = "Station load at the peak hour",
) -> Path:
    """Horizontal bars, sorted, because the question is only ever which one is
    at the top."""
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    _style(ax)

    pairs = sorted(
        ((name, value) for name, value in utilisation.items() if name),
        key=lambda pair: pair[1],
    )
    names = [name for name, _ in pairs]
    values = [value * 100 for _, value in pairs]

    # the constraint is the only bar anyone acts on, so it is the only one in
    # the signal colour
    colours = [SERIES[0]] * len(names)
    if colours:
        colours[-1] = SERIES[1]

    ax.barh(names, values, color=colours, height=0.62)
    ax.set_xlabel("busy (%)")
    ax.set_xlim(0, max(100, max(values, default=0) * 1.1))
    for index, value in enumerate(values):
        ax.text(value + 1.5, index, f"{value:.0f}%", va="center", color=MUTED, fontsize=9)

    return _finish(fig, ax, title, provenance, Path(out_dir) / "station_load.png")
