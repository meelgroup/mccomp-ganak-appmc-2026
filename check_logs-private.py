#!/usr/bin/env python3
"""
Independent sanity-checker for the PRIVATE Model-Counting-Competition run logs.

Same intent as check_logs.py, but for the private-results/ log layout, which the
competition's CRIL cluster produced with a different runsolver invocation and
different file names:

    <track>/logs/<instance>.cnf/
        launcher-<job>-<run>    harness log (node info, /proc/cpuinfo, /proc/meminfo)
        solver-<job>-<run>      solver stdout+stderr, every line prefixed
                                "<cputime>/<walltime>\\t"  (runsolver --timestamp)
        watcher-<job>-<run>     runsolver's own trace (= runsolver.log in public)
        vars-<job>-<run>        runsolver's key=value result (= varfile.log)

Other differences from the public logs:
  * no separate stderr.log - stderr is merged into solver-*
  * vars-* has EXITSTATUS (a raw wait(2) status), not SIGNAL / RETCODE
  * the binding limit is CPU time (-C 3600), not wall clock (-W 3700)
  * the memory limit is 62000 MiB of VSIZE (-M 62000), not 30000 MiB of RSS

Three independent passes:

    1. crash / error scan   - grep every log for real failure signatures
                              (segfault, abort, assertion, bad_alloc, ...),
                              while ignoring a small allow-list of benign strings.
    2. early-termination     - any run that ended by a signal or non-zero exit
                              *well before* the 3600 s CPU limit, which is what a
                              segfault / OOM-kill / crash would look like.
    3. memory usage          - peak RSS and peak VSIZE per run, summarised per track.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
CPU_LIMIT_S = 3600.0           # runsolver -C 3600   (the binding limit here)
WALL_LIMIT_S = 3700.0          # runsolver -W 3700
MEM_LIMIT_MIB = 62000          # runsolver -M 62000  (~62 GB of VSIZE)
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
        r"^#",                             # comment / header lines
        # --- private-log specific ---
        r"^Enforcing VSIZE limit",         # runsolver banner mentioning SIGSEGV
        r"^HardwareCorrupted:",            # /proc/meminfo dump in launcher-*
    ]
]


def is_real_error(line: str) -> bool:
    """True if the line looks like a genuine failure (and is not allow-listed)."""
    if any(b.search(line) for b in BENIGN_PATTERNS):
        return False
    return any(e.search(line) for e in ERROR_PATTERNS)


# --------------------------------------------------------------------------- #
# The private logs use <prefix>-<jobid>-<runid> names, so each run directory is
# addressed by prefix rather than by a fixed file name.
# --------------------------------------------------------------------------- #
def log_file(run_dir: Path, prefix: str) -> Path | None:
    """The single launcher-/solver-/watcher-/vars- file in a run directory."""
    hits = sorted(run_dir.glob(f"{prefix}-*"))
    return hits[0] if hits else None


TS_RE = re.compile(r"^\d+(?:\.\d+)?/\d+(?:\.\d+)?\t")


def strip_ts(line: str) -> str:
    """Drop runsolver's "<cputime>/<walltime>\\t" prefix from a solver-* line."""
    return TS_RE.sub("", line)


# --------------------------------------------------------------------------- #
# Small parsers for the two runsolver-produced logs.
# --------------------------------------------------------------------------- #
def parse_varfile(path: Path | None) -> dict:
    """Read runsolver's key=value result file (WCTIME, TIMEOUT, EXITSTATUS, ...)."""
    out = {}
    if path is None:
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


PEAK_RSS_RE = re.compile(r"Max\. memory \(cumulated for all children\) \(KiB\):\s*(\d+)")
MAXRSS_RE = re.compile(r"maximum resident set size=\s*(\d+)")


def parse_peak_mem_kib(path: Path | None) -> int | None:
    """Peak resident memory (KiB) from watcher-*'s final rusage summary.

    Runs that finish before the first memory sample report 0 in the "Max. memory"
    line, so fall back to the rusage "maximum resident set size" figure.
    """
    if path is None:
        return None
    text = path.read_text(errors="replace")
    vals = [int(m.group(1)) for m in PEAK_RSS_RE.finditer(text)]
    vals += [int(m.group(1)) for m in MAXRSS_RE.finditer(text)]
    return max(vals) if vals else None


def exit_signal(status: str) -> int:
    """Signal number encoded in vars-*'s EXITSTATUS, or 0 if none.

    Two encodings appear: the bare signal number (e.g. 15) when runsolver killed
    the solver itself, and a raw wait(2) status (e.g. 36608 = 143<<8) when the
    wrapper shell relayed the death.  Both mean "killed by that signal".
    """
    try:
        st = int(status)
    except ValueError:
        return 0
    if st == 0:
        return 0
    if st < 128:                 # bare signal number
        return st
    high = st >> 8               # shell-style 128+signal in the exit-code byte
    return high - 128 if high > 128 else 0


# --------------------------------------------------------------------------- #
# Collect one record per run directory.
# --------------------------------------------------------------------------- #
def collect_runs(suite):
    """Collect one record per run for a suite ('exact' or 'approx')."""
    runs = []
    for track_dir in sorted(ROOT.glob(f"track*-{suite}")):
        track = track_dir.name
        for run_dir in sorted((track_dir / "logs").glob("*.cnf")):
            var = parse_varfile(log_file(run_dir, "vars"))
            status = var.get("EXITSTATUS", "")
            sig = exit_signal(status)
            runs.append({
                "track": track,
                "instance": run_dir.name,
                "dir": run_dir,
                "wctime": float(var.get("WCTIME", "nan")),
                "cputime": float(var.get("CPUTIME", "nan")),
                "timeout": var.get("TIMEOUT") == "true",
                "memout": var.get("MEMOUT") == "true",
                "exitstatus": status,
                "signal": str(sig) if sig else "",
                "maxvm_kib": int(var["MAXVM"]) if var.get("MAXVM", "").isdigit() else None,
                "peak_rss_kib": parse_peak_mem_kib(log_file(run_dir, "watcher")),
            })
    return runs


# --------------------------------------------------------------------------- #
# Pass 1 - scan the text logs for genuine crash / error signatures.
# --------------------------------------------------------------------------- #
def pass1_error_scan(runs):
    print("=" * 70)
    print("PASS 1  -  crash / error signature scan (solver, watcher, launcher)")
    print("=" * 70)
    hits = 0
    for r in runs:
        for prefix in ("solver", "watcher", "launcher"):
            p = log_file(r["dir"], prefix)
            if p is None:
                continue
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                line = strip_ts(line)
                if is_real_error(line):
                    hits += 1
                    print(f"  [{r['track']}/{r['instance']}] {prefix}:{n}: {line.strip()}")
    if hits == 0:
        print("  No genuine error / crash signatures found in any log. OK")
    else:
        print(f"  >>> {hits} suspicious line(s) found - inspect above.")
    print()
    return hits


# --------------------------------------------------------------------------- #
# Pass 2 - runs that ended by signal / non-zero exit BEFORE the CPU limit.
# A clean timeout burns ~3600 s of CPU; a crash or OOM-kill ends earlier.
# --------------------------------------------------------------------------- #
def pass2_early_termination(runs):
    print("=" * 70)
    print(f"PASS 2  -  early terminations (< {CPU_LIMIT_S - EARLY_MARGIN_S:.0f} s CPU) "
          f"and memory-outs")
    print("=" * 70)

    def terminated_abnormally(r):
        # ended by a signal, or a non-zero exit status
        return r["exitstatus"] not in ("", "0") or r["memout"]

    suspicious = []
    for r in runs:
        early = (r["cputime"] < CPU_LIMIT_S - EARLY_MARGIN_S and
                 r["wctime"] < WALL_LIMIT_S - EARLY_MARGIN_S)
        if r["memout"]:
            suspicious.append((r, "MEMOUT (hit memory limit)"))
        elif terminated_abnormally(r) and early:
            suspicious.append((r, f"killed early: signal={r['signal'] or '-'} "
                                  f"exitstatus={r['exitstatus']}"))

    if not suspicious:
        print("  No memory-outs, and every abnormal termination happened at the")
        print(f"  full {CPU_LIMIT_S:.0f} s CPU limit (i.e. plain timeouts).")
        print("  -> nothing crashed / OOM-killed early. OK")
    else:
        for r, why in suspicious:
            print(f"  [{r['track']}/{r['instance']}] "
                  f"CPUTIME={r['cputime']:.2f}s WCTIME={r['wctime']:.2f}s  {why}")

    # context: how the runs split, and the timing gap between the two groups
    finished = [r for r in runs if not r["timeout"] and r["exitstatus"] in ("", "0")]
    timed_out = [r for r in runs if r["timeout"]]
    print()
    print(f"  runs total ........... {len(runs)}")
    print(f"  completed normally ... {len(finished)}  "
          f"(slowest {max((r['cputime'] for r in finished), default=0):.1f} s CPU)")
    print(f"  timed out at limit ... {len(timed_out)}  "
          f"(CPU range {min((r['cputime'] for r in timed_out), default=0):.1f}"
          f"-{max((r['cputime'] for r in timed_out), default=0):.1f} s)")
    print()
    return suspicious


# --------------------------------------------------------------------------- #
# Pass 3 - peak memory usage, min / max per track.
# The enforced limit is on VSIZE, so report both RSS and VSIZE.
# --------------------------------------------------------------------------- #
def pass3_memory(runs):
    print("=" * 70)
    print("PASS 3  -  peak memory usage per track (from watcher-* rusage / vars-*)")
    print(f"           memory limit was {MEM_LIMIT_MIB} MiB VSIZE "
          f"(~{MEM_LIMIT_MIB/1024:.0f} GB)")
    print("=" * 70)
    print(f"  {'track':<14}{'runs':>5}{'min RSS':>10}{'max RSS':>10}"
          f"{'max VM':>10}{'max GiB':>9}   peak instance")
    tracks = sorted({r["track"] for r in runs})
    global_max = 0.0
    global_max_vm = 0.0
    for track in tracks:
        mems = [(r["peak_rss_kib"] / 1024.0, r["instance"])
                for r in runs if r["track"] == track and r["peak_rss_kib"] is not None]
        vms = [r["maxvm_kib"] / 1024.0
               for r in runs if r["track"] == track and r["maxvm_kib"] is not None]
        if not mems:
            print(f"  {track:<14}  (no memory data)")
            continue
        lo = min(m for m, _ in mems)
        hi_val, hi_inst = max(mems)
        hi_vm = max(vms, default=0.0)
        global_max = max(global_max, hi_val)
        global_max_vm = max(global_max_vm, hi_vm)
        print(f"  {track:<14}{len(mems):>5}{lo:>10.1f}{hi_val:>10.1f}"
              f"{hi_vm:>10.1f}{hi_val/1024:>9.2f}   {hi_inst}")
    print()
    print(f"  Highest peak RSS across all tracks: {global_max:.1f} MiB "
          f"({global_max/1024:.2f} GiB)")
    print(f"  Highest peak VSIZE across all tracks: {global_max_vm:.1f} MiB "
          f"({global_max_vm/1024:.2f} GiB) "
          f"-> {global_max_vm/MEM_LIMIT_MIB*100:.1f}% of the {MEM_LIMIT_MIB} MiB limit")
    print()


def check_suite(suite):
    print("#" * 70)
    print(f"#  SUITE: {suite.upper()}")
    print("#" * 70)
    runs = collect_runs(suite)
    if not runs:
        print(f"  No track*-{suite} directories found - skipping.\n")
        return
    pass1_error_scan(runs)
    pass2_early_termination(runs)
    pass3_memory(runs)


def main():
    # Run the exact suite first, then the approx suite.
    for suite in ("exact", "approx"):
        check_suite(suite)


if __name__ == "__main__":
    main()
