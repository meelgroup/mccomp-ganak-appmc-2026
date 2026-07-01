#!/usr/bin/env python3
"""
Independent sanity-checker for the Model-Counting-Competition run logs.

It deliberately does NOT read the pre-computed summary/*.csv files.  Everything
below is re-derived straight from the per-run raw logs found under:

    <track>/logs/<instance>.cnf/
        stderr.log      solver stderr
        stdout.log      solver stdout
        runsolver.log   runsolver's own trace (periodic mem samples + final rusage)
        varfile.log     runsolver's machine-readable result (time / mem / signal)

Three independent passes:

    1. crash / error scan   - grep every log for real failure signatures
                              (segfault, abort, assertion, bad_alloc, ...),
                              while ignoring a small allow-list of benign strings.
    2. early-termination     - any run that ended by a signal or non-zero exit
                              *well before* the 3600 s wall-clock limit, which is
                              what a segfault / OOM-kill / crash would look like.
    3. memory usage          - peak RSS per run, summarised (min / max) per track.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WALL_LIMIT_S = 3600.0          # runsolver -W 3600
MEM_LIMIT_MIB = 30000          # runsolver --rss-swap-limit 30000  (~30 GB)
EARLY_MARGIN_S = 10.0          # "well before" the limit = finished >10 s early


# --------------------------------------------------------------------------- #
# Pass 1: patterns that signal a genuine crash / error, and the benign strings
# we know appear in these particular logs and must NOT be treated as failures.
# --------------------------------------------------------------------------- #
ERROR_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"segmentation fault", r"segfault", r"sigsegv",
        r"sigabrt", r"\bsignal (6|11|9)\b", r"core dumped",
        r"double free", r"free\(\): ", r"corrupt",
        r"bad_alloc", r"std::bad_alloc", r"out of memory", r"\boom\b",
        r"terminate called", r"what\(\):", r"uncaught",
        r"stack smashing", r"buffer overflow", r"\boverflow\b",
        r"assertion.*failed", r"assert failed", r"failed assertion",
        r"addresssanitizer", r"undefinedbehaviorsanitizer", r"runtime_error",
        r"\bfatal\b", r"\bpanic\b", r"\bexception\b",
    ]
]

# Lines that match an error pattern above but are harmless in this dataset.
BENIGN_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^terminated",                    # SIGTERM from the timeout watcher
        r"mccomp_run_exact\.sh: terminated",
        r"sched_setaffinity failed",       # CPU-pinning warning, harmless
        r"enable_assertions\s*=\s*off",    # Ganak compile banner
        r"oracle-sparsify.*aborting",      # Ganak internal work-budget cutoff
        r"^#",                             # perf.log / comment header lines
    ]
]


def is_real_error(line: str) -> bool:
    """True if the line looks like a genuine failure (and is not allow-listed)."""
    if any(b.search(line) for b in BENIGN_PATTERNS):
        return False
    return any(e.search(line) for e in ERROR_PATTERNS)


# --------------------------------------------------------------------------- #
# Small parsers for the two runsolver-produced logs.
# --------------------------------------------------------------------------- #
def parse_varfile(path: Path) -> dict:
    """Read runsolver's key=value result file (WCTIME, TIMEOUT, SIGNAL, ...)."""
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


PEAK_RSS_RE = re.compile(r"Max\. memory \(cumulated for all children\) \(KiB\):\s*(\d+)")
MAXVM_RE = re.compile(r"Max\. virtual memory \(cumulated for all children\) \(KiB\):\s*(\d+)")


def parse_peak_mem_kib(path: Path) -> int | None:
    """Peak resident memory (KiB) from runsolver.log's final rusage summary."""
    text = path.read_text(errors="replace")
    m = PEAK_RSS_RE.search(text)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# Collect one record per run directory.
# --------------------------------------------------------------------------- #
def collect_runs():
    runs = []
    for track_dir in sorted(ROOT.glob("track*-exact")):
        track = track_dir.name
        for run_dir in sorted((track_dir / "logs").glob("*.cnf")):
            var = parse_varfile(run_dir / "varfile.log")
            runs.append({
                "track": track,
                "instance": run_dir.name,
                "dir": run_dir,
                "wctime": float(var.get("WCTIME", "nan")),
                "timeout": var.get("TIMEOUT") == "true",
                "memout": var.get("MEMOUT") == "true",
                "signal": var.get("SIGNAL", ""),
                "retcode": var.get("RETCODE", ""),
                "peak_rss_kib": parse_peak_mem_kib(run_dir / "runsolver.log"),
            })
    return runs


# --------------------------------------------------------------------------- #
# Pass 1 - scan the text logs for genuine crash / error signatures.
# --------------------------------------------------------------------------- #
def pass1_error_scan(runs):
    print("=" * 70)
    print("PASS 1  -  crash / error signature scan (stderr, stdout, runsolver)")
    print("=" * 70)
    hits = 0
    for r in runs:
        for logname in ("stderr.log", "stdout.log", "runsolver.log"):
            p = r["dir"] / logname
            if not p.exists():
                continue
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if is_real_error(line):
                    hits += 1
                    print(f"  [{r['track']}/{r['instance']}] {logname}:{n}: {line.strip()}")
    if hits == 0:
        print("  No genuine error / crash signatures found in any log. OK")
    else:
        print(f"  >>> {hits} suspicious line(s) found - inspect above.")
    print()
    return hits


# --------------------------------------------------------------------------- #
# Pass 2 - runs that ended by signal / non-zero exit BEFORE the wall limit.
# A clean timeout ends at ~3600 s; a crash or OOM-kill ends earlier.
# --------------------------------------------------------------------------- #
def pass2_early_termination(runs):
    print("=" * 70)
    print(f"PASS 2  -  early terminations (< {WALL_LIMIT_S - EARLY_MARGIN_S:.0f} s) "
          f"and memory-outs")
    print("=" * 70)

    def terminated_abnormally(r):
        # ended by a signal, or non-zero/non-timeout return code
        killed_by_signal = r["signal"] not in ("", "0")
        bad_retcode = r["retcode"] not in ("", "0")
        return killed_by_signal or bad_retcode or r["memout"]

    suspicious = []
    for r in runs:
        early = r["wctime"] < WALL_LIMIT_S - EARLY_MARGIN_S
        if r["memout"]:
            suspicious.append((r, "MEMOUT (hit memory limit)"))
        elif terminated_abnormally(r) and early:
            suspicious.append((r, f"killed early: signal={r['signal']} "
                                  f"retcode={r['retcode']}"))

    if not suspicious:
        print("  No memory-outs, and every abnormal termination happened at the")
        print(f"  full {WALL_LIMIT_S:.0f} s wall-clock limit (i.e. plain timeouts).")
        print("  -> nothing crashed / OOM-killed early. OK")
    else:
        for r, why in suspicious:
            print(f"  [{r['track']}/{r['instance']}] "
                  f"WCTIME={r['wctime']:.2f}s  {why}")

    # context: how the runs split, and the timing gap between the two groups
    finished = [r for r in runs if not r["timeout"] and r["signal"] in ("", "0")
                and r["retcode"] in ("", "0")]
    timed_out = [r for r in runs if r["timeout"]]
    print()
    print(f"  runs total ........... {len(runs)}")
    print(f"  completed normally ... {len(finished)}  "
          f"(slowest {max((r['wctime'] for r in finished), default=0):.1f} s)")
    print(f"  timed out at limit ... {len(timed_out)}  "
          f"(range {min((r['wctime'] for r in timed_out), default=0):.1f}"
          f"-{max((r['wctime'] for r in timed_out), default=0):.1f} s)")
    print()
    return suspicious


# --------------------------------------------------------------------------- #
# Pass 3 - peak memory usage, min / max per track.
# --------------------------------------------------------------------------- #
def pass3_memory(runs):
    print("=" * 70)
    print("PASS 3  -  peak memory usage per track (from runsolver.log rusage)")
    print(f"           memory limit was {MEM_LIMIT_MIB} MiB (~{MEM_LIMIT_MIB/1024:.0f} GB)")
    print("=" * 70)
    print(f"  {'track':<14}{'runs':>5}{'min MiB':>10}{'max MiB':>10}"
          f"{'max GiB':>9}   peak instance")
    tracks = sorted({r["track"] for r in runs})
    global_max = 0.0
    for track in tracks:
        mems = [(r["peak_rss_kib"] / 1024.0, r["instance"])
                for r in runs if r["track"] == track and r["peak_rss_kib"] is not None]
        if not mems:
            print(f"  {track:<14}  (no memory data)")
            continue
        lo = min(m for m, _ in mems)
        hi_val, hi_inst = max(mems)
        global_max = max(global_max, hi_val)
        print(f"  {track:<14}{len(mems):>5}{lo:>10.1f}{hi_val:>10.1f}"
              f"{hi_val/1024:>9.2f}   {hi_inst}")
    print()
    print(f"  Highest peak across all tracks: {global_max:.1f} MiB "
          f"({global_max/1024:.2f} GiB) "
          f"-> {global_max/MEM_LIMIT_MIB*100:.1f}% of the {MEM_LIMIT_MIB} MiB limit")
    print()


def main():
    runs = collect_runs()
    if not runs:
        print("No run directories found under", ROOT, file=sys.stderr)
        sys.exit(1)
    pass1_error_scan(runs)
    pass2_early_termination(runs)
    pass3_memory(runs)


if __name__ == "__main__":
    main()
