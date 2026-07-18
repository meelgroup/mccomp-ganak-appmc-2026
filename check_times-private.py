#!/usr/bin/env python3
"""
Cross-check the *timing* of every private-results run against itself.

For each <track>/logs/<instance>.cnf/ directory we have four views of how long
the run took, produced by three independent mechanisms:

  A. solver-*    every line is prefixed by runsolver --timestamp with
                 "<cputime>/<walltime>\\t".  The prefix on the LAST line is
                 runsolver's opinion of when the solver stopped talking.
  B. solver-*    the solver's own accounting, printed near the end:
                     c o Total time [Arjun+GANAK]: <s>
                 (plus per-thread "Total time (this thread)" lines).
  C. watcher-*   runsolver's final report:
                     Real time (s): / CPU time (s):
  D. vars-*      the machine-readable version of C: WCTIME / CPUTIME.

C and D come from the same measurement, so they must agree to the digit; any
difference there means the files were mismatched or truncated.  A vs C/D is a
real correlation check: the last timestamp must be <= the final total, and only
slightly less (the gap is whatever happened after the last printed line).  B vs
A tells us whether the solver's internal clock agrees with the external one --
this is where clock skew, a wrong CLOCK_* source, or time spent in a phase the
solver does not account for would show up.

Usage:  ./check_times-private.py [root]        (default: private-results/)
"""

import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).parent.resolve() / "private-results"

CPU_LIMIT_S = 3600.0    # runsolver -C 3600 (the binding limit on this cluster)
WALL_LIMIT_S = 3700.0   # runsolver -W 3700

# How much disagreement we tolerate before we shout.
TOL_IDENTICAL = 0.01    # C vs D: same measurement, must be identical
TOL_TAIL = 5.0          # A vs C/D: work done after the last printed line
TOL_SOLVER = 10.0       # B vs A: solver's own clock vs runsolver's

TS_RE = re.compile(r"^(\d+\.\d+)/(\d+\.\d+)\t")
TOTAL_RE = re.compile(r"c o Total time \[Arjun\+GANAK\]:\s*([0-9.]+)")
THREAD_RE = re.compile(r"c o Total time \(this thread\):\s*([0-9.]+)")
WATCHER_REAL_RE = re.compile(r"^Real time \(s\):\s*([0-9.]+)", re.M)
WATCHER_CPU_RE = re.compile(r"^CPU time \(s\):\s*([0-9.]+)", re.M)


def read(path):
    return path.read_text(errors="replace")


def parse_vars(path):
    out = {}
    for line in read(path).splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def parse_solver(path):
    """Return (last_cpu_ts, last_wall_ts, total_time, max_thread_time)."""
    last_cpu = last_wall = None
    total = None
    max_thread = None
    for line in read(path).splitlines():
        m = TS_RE.match(line)
        if m:
            last_cpu, last_wall = float(m.group(1)), float(m.group(2))
        m = TOTAL_RE.search(line)
        if m:
            total = float(m.group(1))          # keep the last one
        m = THREAD_RE.search(line)
        if m:
            t = float(m.group(1))
            max_thread = t if max_thread is None else max(max_thread, t)
    return last_cpu, last_wall, total, max_thread


def parse_watcher(path):
    txt = read(path)
    real = WATCHER_REAL_RE.search(txt)
    cpu = WATCHER_CPU_RE.search(txt)
    return (float(real.group(1)) if real else None,
            float(cpu.group(1)) if cpu else None)


def collect(root):
    """Yield one dict per run directory."""
    for track in sorted(p for p in root.iterdir() if (p / "logs").is_dir()):
        for run in sorted((track / "logs").iterdir()):
            if not run.is_dir():
                continue
            files = {kind: sorted(run.glob(kind + "-*"))
                     for kind in ("solver", "watcher", "vars")}
            rec = {"track": track.name, "inst": run.name, "problems": []}
            missing = [k for k, v in files.items() if not v]
            if missing:
                rec["problems"].append("missing file(s): " + ",".join(missing))
                yield rec
                continue
            for k, v in files.items():
                if len(v) > 1:
                    rec["problems"].append(f"{len(v)} {k}-* files, using first")

            rec["job"] = files["solver"][0].name.split("-", 1)[1]
            # all four files must belong to the same job/run id
            ids = {f[0].name.split("-", 1)[1] for f in files.values()}
            if len(ids) > 1:
                rec["problems"].append("job-id mismatch across files: " + ",".join(sorted(ids)))

            (rec["ts_cpu"], rec["ts_wall"],
             rec["total"], rec["thread"]) = parse_solver(files["solver"][0])
            rec["w_wall"], rec["w_cpu"] = parse_watcher(files["watcher"][0])
            v = parse_vars(files["vars"][0])
            rec["v_wall"] = float(v["WCTIME"]) if "WCTIME" in v else None
            rec["v_cpu"] = float(v["CPUTIME"]) if "CPUTIME" in v else None
            rec["timeout"] = v.get("TIMEOUT")
            rec["memout"] = v.get("MEMOUT")
            rec["exit"] = v.get("EXITSTATUS")
            yield rec


def check(rec):
    """Append every timing discrepancy found for one run."""
    p = rec["problems"].append
    ts_cpu, ts_wall = rec.get("ts_cpu"), rec.get("ts_wall")
    w_wall, w_cpu = rec.get("w_wall"), rec.get("w_cpu")
    v_wall, v_cpu = rec.get("v_wall"), rec.get("v_cpu")
    total, thread = rec.get("total"), rec.get("thread")

    # --- C vs D: same measurement written twice, must be identical ---------
    for name, a, b in (("wall", w_wall, v_wall), ("cpu", w_cpu, v_cpu)):
        if a is None or b is None:
            p(f"watcher/vars {name} time missing (watcher={a} vars={b})")
        elif abs(a - b) > TOL_IDENTICAL:
            p(f"watcher vs vars {name}: {a:.3f} vs {b:.3f} (diff {a-b:+.3f})")

    ref_wall = v_wall if v_wall is not None else w_wall
    ref_cpu = v_cpu if v_cpu is not None else w_cpu

    # --- A vs C/D: the last timestamp must sit just under the final total --
    if ts_wall is None:
        p("solver log has no timestamped lines")
    elif ref_wall is not None:
        d = ref_wall - ts_wall
        if d < -TOL_IDENTICAL:
            p(f"last solver wall timestamp {ts_wall:.3f} EXCEEDS runsolver wall {ref_wall:.3f} ({d:+.3f})")
        elif d > TOL_TAIL:
            p(f"wall gap after last solver line: {d:.3f}s (last ts {ts_wall:.3f}, runsolver {ref_wall:.3f})")
    if ts_cpu is not None and ref_cpu is not None:
        d = ref_cpu - ts_cpu
        if d < -TOL_IDENTICAL:
            p(f"last solver cpu timestamp {ts_cpu:.3f} EXCEEDS runsolver cpu {ref_cpu:.3f} ({d:+.3f})")
        elif d > TOL_TAIL:
            p(f"cpu gap after last solver line: {d:.3f}s (last ts {ts_cpu:.3f}, runsolver {ref_cpu:.3f})")

    # --- B vs A: the solver's own clock vs runsolver's ---------------------
    # "Total time [Arjun+GANAK]" is only printed on a clean finish; a run that
    # hit the limit legitimately has none.
    if total is None:
        if rec.get("timeout") == "false" and rec.get("exit") == "0":
            p("clean exit but no 'Total time [Arjun+GANAK]' line")
    else:
        for label, ref in (("wall", ts_wall), ("cpu", ts_cpu)):
            if ref is None:
                continue
            d = total - ref
            # solver total should never exceed the enclosing runsolver clock
            if label == "cpu" and d > TOL_SOLVER:
                p(f"solver 'Total time' {total:.2f} > runsolver cpu at that point {ref:.2f} ({d:+.2f})")
            if label == "wall" and ref - total > max(TOL_SOLVER, 0.05 * ref):
                p(f"solver 'Total time' {total:.2f} much less than wall {ref:.2f} "
                  f"({ref-total:+.2f} unaccounted)")
    if total is not None and thread is not None and thread > total + TOL_SOLVER:
        p(f"per-thread time {thread:.2f} > overall total {total:.2f}")

    # --- limits: TIMEOUT flag must match the measured times ----------------
    if rec.get("timeout") == "true":
        if ref_cpu is not None and ref_wall is not None \
                and ref_cpu < CPU_LIMIT_S - 1 and ref_wall < WALL_LIMIT_S - 1:
            p(f"TIMEOUT=true but cpu={ref_cpu:.1f} wall={ref_wall:.1f} are both under the limits")
    else:
        if ref_cpu is not None and ref_cpu > CPU_LIMIT_S + 1:
            p(f"TIMEOUT=false but cpu={ref_cpu:.1f} exceeds the {CPU_LIMIT_S:.0f}s limit")
        if ref_wall is not None and ref_wall > WALL_LIMIT_S + 1:
            p(f"TIMEOUT=false but wall={ref_wall:.1f} exceeds the {WALL_LIMIT_S:.0f}s limit")

    # --- sanity: cpu time cannot exceed wall time on a 1-core-billed run ---
    # (the solver may use several cores, so cpu > wall is fine; wall < 0 is not)
    if ref_wall is not None and ref_wall < 0:
        p(f"negative wall time {ref_wall}")


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_ROOT
    if not root.is_dir():
        sys.exit(f"no such directory: {root}")

    recs = list(collect(root))
    for r in recs:
        if "ts_wall" in r:
            check(r)

    # ---------------- per-track summary of the A-vs-C gaps ----------------
    print(f"=== timing correlation over {len(recs)} runs under {root} ===\n")
    hdr = f"{'track':<15} {'runs':>5} {'bad':>4} {'max wall gap':>13} {'max cpu gap':>12} {'max solver-vs-ts':>17}"
    print(hdr)
    print("-" * len(hdr))
    tracks = {}
    for r in recs:
        tracks.setdefault(r["track"], []).append(r)
    for track, rs in sorted(tracks.items()):
        wg = cg = sg = 0.0
        for r in rs:
            if r.get("v_wall") is not None and r.get("ts_wall") is not None:
                wg = max(wg, r["v_wall"] - r["ts_wall"])
            if r.get("v_cpu") is not None and r.get("ts_cpu") is not None:
                cg = max(cg, r["v_cpu"] - r["ts_cpu"])
            if r.get("total") is not None and r.get("ts_wall") is not None:
                sg = max(sg, abs(r["ts_wall"] - r["total"]))
        bad = sum(1 for r in rs if r["problems"])
        print(f"{track:<15} {len(rs):>5} {bad:>4} {wg:>13.3f} {cg:>12.3f} {sg:>17.3f}")

    # ---------------- the actual discrepancies ----------------------------
    bad = [r for r in recs if r["problems"]]
    print(f"\n=== {len(bad)} run(s) with discrepancies ===")
    for r in bad:
        print(f"\n{r['track']}/{r['inst']}  (job {r.get('job', '?')})")
        print(f"   solver last ts: cpu={r.get('ts_cpu')} wall={r.get('ts_wall')}  "
              f"solver total={r.get('total')}")
        print(f"   runsolver:      cpu={r.get('v_cpu')} wall={r.get('v_wall')}  "
              f"TIMEOUT={r.get('timeout')} EXIT={r.get('exit')}")
        for prob in r["problems"]:
            print(f"   ! {prob}")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
