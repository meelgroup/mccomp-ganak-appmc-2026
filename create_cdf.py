#!/usr/bin/env python3
"""
Per-track CDF (cactus) plots of Ganak solve time, exact vs approx on one plot.

For every track (1..5) we draw two curves - the "exact" and the "approx" run -
on the same axes:

    x axis : solve time in seconds        (LOG scale, like create_graphs_ganak.py)
    y axis : number of instances solved   (LINEAR scale)

An instance counts as SOLVED (per the requested rule) when its stdout.log has a
`c s log10-estimate` line AND a final-count line:

    exact  suite : `c s exact arb int`  OR `c s exact arb frac`
    approx suite : any of the above OR `c s exact quadruple float`
                   OR `c s approx arb int`

The solve time is runsolver's wall-clock time (WCTIME from varfile.log), which is
what the competition harness actually measures.

Each track produces a PNG (also printed inline to the console via the iTerm2
image protocol) and a PDF; both filenames are printed to the console.
"""

import base64
import struct
from pathlib import Path

import matplotlib
matplotlib.use("Agg")            # headless: write files, no interactive window
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "cdf_plots"
WALL_LIMIT_S = 3600.0
TRACKS = [1, 2, 3, 4, 5]

# Final-count marker lines that indicate a produced result, per suite.
FINAL_MARKERS = {
    "exact":  ["c s exact arb int", "c s exact arb frac"],
    "approx": ["c s exact arb int", "c s exact arb frac",
               "c s exact quadruple float", "c s approx arb int"],
}
# A produced count also carries a log-estimate.  For negative weighted counts
# (weighted tracks) log10 is undefined, so the solver emits `neglog10-estimate`
# instead of `log10-estimate` - both mean "estimate was produced".
LOG10_MARKERS = ["c s log10-estimate", "c s neglog10-estimate"]


# --------------------------------------------------------------------------- #
# Data collection
# --------------------------------------------------------------------------- #
def wctime(run_dir: Path) -> float | None:
    """runsolver wall-clock time (seconds) for a run, from varfile.log."""
    var = run_dir / "varfile.log"
    if not var.exists():
        return None
    for line in var.read_text().splitlines():
        if line.startswith("WCTIME="):
            return float(line.split("=", 1)[1])
    return None


def is_solved(stdout: Path, suite: str) -> bool:
    """Solved = has a log10-estimate line AND a final-count line for the suite."""
    if not stdout.exists():
        return False
    text = stdout.read_text(errors="replace")
    if not any(m in text for m in LOG10_MARKERS):
        return False
    return any(m in text for m in FINAL_MARKERS[suite])


def solved_times(track: int, suite: str) -> list[float]:
    """Sorted list of wall-clock times for the solved instances of one track."""
    track_dir = ROOT / f"track{track}-{suite}"
    times = []
    for run_dir in sorted((track_dir / "logs").glob("*.cnf")):
        if is_solved(run_dir / "stdout.log", suite):
            t = wctime(run_dir)
            if t is not None:
                times.append(max(t, 0.01))     # clamp to plot floor (log axis)
    return sorted(times)


# --------------------------------------------------------------------------- #
# Console image printing (iTerm2 inline-image protocol, as in the reference)
# --------------------------------------------------------------------------- #
def png_size(png_file: Path) -> tuple[int, int]:
    with open(png_file, "rb") as fh:
        head = fh.read(24)
    # PNG IHDR: width/height are big-endian uint32 at bytes 16..24
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def print_png_to_console(png_file: Path) -> None:
    data = png_file.read_bytes()
    b64 = base64.b64encode(data).decode()
    w, h = png_size(png_file)
    print(f"\033]1337;File=inline=1;width={w}px;height={h}px:{b64}\a")


# --------------------------------------------------------------------------- #
# Plot one track (exact + approx on the same axes)
# --------------------------------------------------------------------------- #
def plot_track(track: int) -> None:
    fig, ax = plt.subplots(figsize=(8, 6))
    styles = {"exact":  ("tab:blue",   "o"),
              "approx": ("tab:orange", "s")}

    for suite in ("exact", "approx"):
        color, marker = styles[suite]
        times = solved_times(track, suite)
        counts = list(range(1, len(times) + 1))     # y: 1..N solved
        ax.plot(times, counts, color=color, marker=marker,
                label=f"{suite} ({len(times)} solved)",
                markersize=3, linewidth=1.3)

    ax.set_xscale("log")                            # log time, linear count
    ax.set_xlim(0.1, WALL_LIMIT_S)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Solve time (s)  [log scale]")
    ax.set_ylabel("Instances solved")
    ax.set_title(f"Ganak CDF - Track {track}  (exact vs approx)")
    ax.grid(True, which="both", linestyle=":", alpha=0.6)
    ax.legend(loc="lower right")
    fig.tight_layout()

    OUT_DIR.mkdir(exist_ok=True)
    png = OUT_DIR / f"cdf_track{track}.png"
    pdf = OUT_DIR / f"cdf_track{track}.pdf"
    fig.savefig(png, dpi=110)
    fig.savefig(pdf)
    plt.close(fig)

    print(f"\n=== Track {track} ===")
    print_png_to_console(png)
    print(f"PNG: {png}")
    print(f"PDF: {pdf}")


def main() -> None:
    for track in TRACKS:
        plot_track(track)
    print(f"\nAll plots written under: {OUT_DIR}")


if __name__ == "__main__":
    main()
