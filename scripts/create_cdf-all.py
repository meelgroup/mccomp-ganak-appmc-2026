#!/usr/bin/env python3
"""
Per-track CDF (cactus) plots of Ganak solve time, exact vs approx, over the
public and private instance sets combined.  Run from the repo root:

    ./scripts/cdf_cdf-all.py

The public/private split is only about instance availability, so the two sets
are merged into a single curve per suite.  Track 5 exists in the public set only.
"""

import base64
import re
import struct
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).parent.parent.resolve()
PUBLIC = ROOT / "public-results"
PRIVATE = ROOT / "private-results"
OUT_DIR = ROOT / "cdf_plots-all"
WALL_LIMIT_S = 3700.0

FINAL_MARKERS = {
    "exact":  ["c s exact arb int", "c s exact arb frac"],
    "approx": ["c s exact arb int", "c s exact arb frac",
               "c s exact quadruple float", "c s approx arb int"],
}
# Weighted tracks can produce negative counts, where log10 is undefined and the
# solver emits `neglog10-estimate`; both mean "an estimate was produced".
LOG10_MARKERS = ["c s log10-estimate", "c s neglog10-estimate"]

# runsolver prefixes every private-log line with "<cpu>/<wall>\t"
TS_RE = re.compile(r"^\d+(?:\.\d+)?/\d+(?:\.\d+)?\t", re.M)


def wctime_from(text: str) -> float | None:
    for line in text.splitlines():
        if line.startswith("WCTIME="):
            return float(line.split("=", 1)[1])
    return None


def is_solved(text: str, suite: str) -> bool:
    if not any(m in text for m in LOG10_MARKERS):
        return False
    return any(m in text for m in FINAL_MARKERS[suite])


def run_files(run_dir: Path) -> tuple[str, str] | None:
    """(solver output, runsolver vars) for a run dir, either layout."""
    if (run_dir / "stdout.log").exists():
        var = run_dir / "varfile.log"
        return ((run_dir / "stdout.log").read_text(errors="replace"),
                var.read_text(errors="replace") if var.exists() else "")

    solver = sorted(run_dir.glob("solver-*"))
    var = sorted(run_dir.glob("vars-*"))
    if not solver:
        return None
    text = TS_RE.sub("", solver[0].read_text(errors="replace"))
    return text, var[0].read_text(errors="replace") if var else ""


def solved_times(base: Path, track: int, suite: str) -> list[float]:
    logs = base / f"track{track}-{suite}" / "logs"
    if not logs.is_dir():
        return []
    times = []
    for run_dir in sorted(logs.glob("*.cnf")):
        files = run_files(run_dir)
        if files is None:
            continue
        text, var = files
        if not is_solved(text, suite):
            continue
        t = wctime_from(var)
        if t is not None:
            times.append(max(t, 0.01))     # clamp to plot floor (log axis)
    return times


def tracks() -> list[int]:
    nums = set()
    for base in (PUBLIC, PRIVATE):
        for d in base.glob("track*-*"):
            m = re.match(r"track(\d+)-(exact|approx)$", d.name)
            if m:
                nums.add(int(m.group(1)))
    return sorted(nums)


def png_size(png_file: Path) -> tuple[int, int]:
    with open(png_file, "rb") as fh:
        head = fh.read(24)
    return struct.unpack(">II", head[16:24])


def print_png_to_console(png_file: Path) -> None:
    b64 = base64.b64encode(png_file.read_bytes()).decode()
    w, h = png_size(png_file)
    print(f"\033]1337;File=inline=1;width={w}px;height={h}px:{b64}\a")


def plot_track(track: int) -> None:
    fig, ax = plt.subplots(figsize=(8, 6))
    styles = {"exact":  ("tab:blue",   "o"),
              "approx": ("tab:orange", "s")}

    for suite in ("exact", "approx"):
        color, marker = styles[suite]
        pub = solved_times(PUBLIC, track, suite)
        priv = solved_times(PRIVATE, track, suite)
        times = sorted(pub + priv)
        counts = list(range(1, len(times) + 1))
        ax.plot(times, counts, color=color, marker=marker,
                label=f"{suite} ({len(times)} solved: "
                      f"{len(pub)} public + {len(priv)} private)",
                markersize=3, linewidth=1.3)

    ax.set_xscale("log")
    ax.set_xlim(1.0, WALL_LIMIT_S)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Solve time (s)  [log scale]")
    ax.set_ylabel("Instances solved")
    ax.set_title(f"Ganak CDF - Track {track}, public+private  (exact vs approx)")
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
    for track in tracks():
        plot_track(track)
    print(f"\nAll plots written under: {OUT_DIR}")


if __name__ == "__main__":
    main()
