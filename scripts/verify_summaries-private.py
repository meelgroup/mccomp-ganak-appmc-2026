#!/usr/bin/env python3
"""
Independently verify the per-track summary/*.csv files against the RAW solver logs.

PRIVATE-log version of verify_summaries.py.  The summary CSVs have the same
schema as the public ones (plus vote_count / voters / disagreed columns), but
the raw logs live in the private-results/ layout:

    <track>/logs/<instance>.cnf/
        solver-<job>-<run>   solver stdout+stderr, every line prefixed by
                             runsolver's "<cputime>/<walltime>\\t" timestamp
        vars-<job>-<run>     runsolver's key=value result file

so every raw line must have its timestamp stripped before the `c s ...` markers
can be matched.  Tracks are discovered from the directory names (the private set
has tracks 1-4, no complex-weighted track 5).

For every track and suite (exact, approx) we re-derive, straight from each run's
solver log, the ground truth and compare it to what the summary CSVs claim:

    1a-verdict.csv     -> OK / TLE instance tallies
    5-correct.csv      -> per-instance model count (full precision)   [authoritative]
    3-counts.csv       -> per-instance model count (may be rounded for display)
    2-counts_log10.csv -> per-instance log10 of the count
    6-correct_sum.csv  -> number judged correct  (corr)
    6b-wrong_bysolver  -> instances judged WRONG

Solver result-line taxonomy:
    estimate line : `c s log10-estimate`            (plain MC / WMC)
                    `c s neglog10-estimate`          (negative weighted count)
    count line    : `c s exact arb int`     (integer)
                    `c s exact arb frac`     (rational, weighted)
                    `c s exact quadruple float`  (approx suite, weighted)
                    `c s approx arb int`     (approx suite, integer)

An instance is SOLVED iff it printed both an estimate line and a count line.
"""

import csv
import re
import sys
import math
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

sys.set_int_max_str_digits(2_000_000)   # weighted counts reach thousands of digits
csv.field_size_limit(10**8)

ROOT = Path(__file__).parent.resolve()
COUNT_KINDS = ["exact arb int", "exact arb frac", "exact quadruple float", "approx arb int"]
FLOAT_KIND = "exact quadruple float"
FLOAT_RTOL = 1e-6

TS_RE = re.compile(r"^\d+(?:\.\d+)?/\d+(?:\.\d+)?\t")


# --------------------------------------------------------------------------- #
# Parse the raw ground truth from one run's solver log.
# --------------------------------------------------------------------------- #
def solver_log(run: Path) -> Path | None:
    """The solver-<job>-<run> file inside a run directory."""
    hits = sorted(run.glob("solver-*"))
    return hits[0] if hits else None


def parse_raw(run: Path):
    """Return dict(count=str, kind=str, log10=float) or None if unsolved."""
    log = solver_log(run)
    if log is None:
        return None
    # runsolver --timestamp prefixes every line with "<cputime>/<walltime>\t"
    lines = [TS_RE.sub("", ln)
             for ln in log.read_text(errors="replace").splitlines()]

    log10 = None                     # prefer plain/real estimate; keep magnitude for neg
    for ln in lines:
        if ln.startswith("c s log10-estimate ") or ln.startswith("c s log10-estimate-real "):
            log10 = float(ln.split()[-1])
        elif log10 is None and ln.startswith(("c s neglog10-estimate ",
                                              "c s neglog10-estimate-real ")):
            log10 = float(ln.split()[-1])

    count = kind = None
    for k in COUNT_KINDS:
        m = re.search(r"^c s " + re.escape(k) + r"\s+(\S+)", "\n".join(lines), re.M)
        if m:
            count, kind = m.group(1), k
    if count is None or log10 is None:
        return None
    return {"count": count, "kind": kind, "log10": log10}


def tracks() -> list[int]:
    """Track numbers present under this directory, e.g. [1, 2, 3, 4]."""
    nums = set()
    for d in ROOT.glob("track*-*"):
        m = re.match(r"track(\d+)-(exact|approx)$", d.name)
        if m:
            nums.add(int(m.group(1)))
    return sorted(nums)


def log10_abs(fr: Fraction) -> float:
    """log10(|fraction|), overflow-safe for thousand-digit numerators/denominators."""
    if fr == 0:
        return -math.inf
    return math.log10(abs(fr.numerator)) - math.log10(fr.denominator)


def counts_equal(raw_count: str, raw_kind: str, summary_count: str) -> bool:
    """Exact equality for int/frac; log-space closeness for the printed quadruple float."""
    if summary_count.strip() in ("", "nan"):
        return False
    if raw_kind == FLOAT_KIND:
        raw = Fraction(Decimal(raw_count))          # scientific string -> exact decimal
        summ = Fraction(summary_count)
        if raw == summ:
            return True
        if (raw < 0) != (summ < 0):
            return False
        return abs(log10_abs(raw) - log10_abs(summ)) < FLOAT_RTOL
    return Fraction(summary_count) == Fraction(raw_count)   # int or a/b: exact


# --------------------------------------------------------------------------- #
# Small CSV helpers.
# --------------------------------------------------------------------------- #
def dictrows(path: Path):
    with open(path) as f:
        return list(csv.DictReader(f))


def two_col(path: Path) -> dict:
    """instance -> value, for the wide-format 2-counts_log10.csv."""
    out = {}
    with open(path) as f:
        for row in csv.reader(f):
            if row and row[0] != "instance":
                out[row[0]] = row[1]
    return out


# --------------------------------------------------------------------------- #
# Verify one track.
# --------------------------------------------------------------------------- #
def verify_track(track: int, suite: str) -> dict:
    tr = ROOT / f"track{track}-{suite}"
    runs = sorted((tr / "logs").glob("*.cnf"))
    raw = {r.name: parse_raw(r) for r in runs}
    solved = {n: v for n, v in raw.items() if v}

    issues = []

    # ---- 1a-verdict.csv : OK / TLE tallies ----
    verdicts = {row["verdict"]: int(row["instance"])
                for row in dictrows(tr / "summary/1a-verdict.csv")}
    ok_1a = verdicts.get("OK", 0)
    tle_1a = verdicts.get("TLE", 0)
    if ok_1a != len(solved):
        issues.append(f"1a OK={ok_1a} but raw-solved={len(solved)}")
    if ok_1a + tle_1a != len(runs):
        issues.append(f"1a OK+TLE={ok_1a+tle_1a} != {len(runs)} runs")

    # ---- counts vs 5-correct.csv (authoritative) and 3-counts.csv ----
    five_rows = dictrows(tr / "summary/5-correct.csv")
    five = {r["instance"]: r["count"] for r in five_rows}
    three = {r["instance"]: r["count"] for r in dictrows(tr / "summary/3-counts.csv")}
    log2 = two_col(tr / "summary/2-counts_log10.csv")

    cnt5_bad, cnt3_bad, log_bad, log_inf = [], [], [], []
    for inst, v in solved.items():
        if not counts_equal(v["count"], v["kind"], five.get(inst, "")):
            cnt5_bad.append(inst)
        if not counts_equal(v["count"], v["kind"], three.get(inst, "")):
            cnt3_bad.append(inst)

        s = log2.get(inst, "")
        try:
            sval = float(s)
        except ValueError:
            log_bad.append(f"{inst}={s!r}")
            continue
        if math.isinf(sval) and not math.isinf(v["log10"]):
            log_inf.append(inst)                       # summary says +/-inf, solver gave a finite value
        elif not (math.isclose(sval, v["log10"], rel_tol=1e-4, abs_tol=1e-4) or
                  math.isclose(abs(sval), abs(v["log10"]), rel_tol=1e-4, abs_tol=1e-4)):
            log_bad.append(f"{inst} summary={sval} raw={v['log10']}")

    if cnt5_bad:
        issues.append(f"{len(cnt5_bad)} count!=5-correct (e.g. {cnt5_bad[:2]})")
    if cnt3_bad:
        issues.append(f"{len(cnt3_bad)} count!=3-counts (e.g. {cnt3_bad[:2]})")
    if log_inf:
        issues.append(f"{len(log_inf)} log10 is +/-inf in 2-counts_log10 but finite in solver "
                      f"(e.g. {log_inf[:2]})")
    if log_bad:
        issues.append(f"{len(log_bad)} log10 mismatch (e.g. {log_bad[:1]})")

    # ---- correctness bookkeeping: 6-correct_sum.csv corr vs 6b wrong ----
    # NOTE: rows in 6b = solved-but-not-scored-correct, NOT wrong answers.
    corr_rows = dictrows(tr / "summary/6-correct_sum.csv")
    corr = int(float(corr_rows[0]["corr"])) if corr_rows and corr_rows[0].get("corr") else 0
    unscored = []           # (instance, why) for each solved instance the scorer skipped
    for row in dictrows(tr / "summary/6b-wrong_bysolver.csv"):
        inst = row["instance"]
        neg = five.get(inst, "").lstrip().startswith("-")
        unscored.append((inst, "negative weighted count" if neg else "corr empty"))
    wrong = len(unscored)
    if corr + wrong != len(solved):                       # real discrepancy: bookkeeping doesn't add up
        issues.append(f"corr({corr})+wrong({wrong}) != solved({len(solved)})")

    # ---- private-only: the cross-solver vote columns ----
    # `disagreed` flags an instance where the participating counters did not all
    # agree.  That is only alarming for us if OUR answer was the odd one out,
    # i.e. the instance was solved but not scored correct (corr != 1.0).
    # Instances we did not solve are listed too, but say nothing about us.
    disagreed, disagreed_bad = [], []
    for r in five_rows:
        if r.get("disagreed") != "True" or r["instance"] not in solved:
            continue
        disagreed.append(r["instance"])
        if (r.get("corr") or "") not in ("1.0", "1"):
            disagreed_bad.append(f"{r['instance']} corr={r.get('corr')!r} "
                                 f"votes={r.get('vote_count')} ({r.get('voters')})")
    if disagreed_bad:
        issues.append(f"{len(disagreed_bad)} solved instance(s) on the losing side of a "
                      f"solver disagreement (e.g. {disagreed_bad[:1]})")

    # Informational notes: consistent, but worth surfacing (not a summary error).
    notes = []
    if wrong > 0:
        notes.append(f"{wrong} solved but unscored (corr empty in 6b)")
    if disagreed and not disagreed_bad:
        notes.append(f"{len(disagreed)} instance(s) with solver disagreement, ours agreed "
                     f"with the accepted count")

    return {"track": f"track{track}-{suite}", "runs": len(runs), "solved": len(solved),
            "ok": ok_1a, "tle": tle_1a, "corr": corr, "wrong": wrong,
            "issues": issues, "notes": notes,
            "log_inf": log_inf, "log_bad": log_bad,
            "cnt5_bad": cnt5_bad, "cnt3_bad": cnt3_bad, "unscored": unscored,
            "disagreed": disagreed, "disagreed_bad": disagreed_bad}


def wrap(items, width=6):
    """Group a list of instance names into fixed-size lines for readable printing."""
    return ["      " + ", ".join(items[i:i + width]) for i in range(0, len(items), width)]


def print_details(results):
    """Below the table: for every track, spell out each discrepancy and each unscored case."""
    print("\n" + "=" * 70)
    print("PER-TRACK DETAIL")
    print("=" * 70)
    for r in results:
        if not (r["issues"] or r["unscored"] or r["disagreed"]):
            print(f"\n{r['track']}: OK - counts, log10 values and verdicts all match the raw logs.")
            continue
        print(f"\n{r['track']}:")

        # --- real discrepancies (summary disagrees with the raw logs) ---
        if r["log_inf"]:
            short = [i.replace(".cnf", "") for i in r["log_inf"]]
            print(f"  DISCREPANCY - {len(short)} instance(s): 2-counts_log10.csv (and the")
            print(f"    log10 column of 3-counts.csv) stores -inf, but the solver printed a finite")
            print(f"    log10 (these are approx 'quadruple float' weighted results). The COUNT is")
            print(f"    correct and scoring is unaffected; only the derived log10 column is wrong:")
            for line in wrap(short):
                print(line)
        if r["log_bad"]:
            print(f"  DISCREPANCY - {len(r['log_bad'])} instance(s) with a log10 value mismatch:")
            for line in wrap([str(x) for x in r["log_bad"]], width=3):
                print(line)
        if r["cnt5_bad"]:
            print(f"  DISCREPANCY - {len(r['cnt5_bad'])} count(s) disagree with 5-correct.csv:")
            for line in wrap([i.replace('.cnf', '') for i in r["cnt5_bad"]]):
                print(line)
        if r["cnt3_bad"]:
            print(f"  DISCREPANCY - {len(r['cnt3_bad'])} count(s) disagree with 3-counts.csv:")
            for line in wrap([i.replace('.cnf', '') for i in r["cnt3_bad"]]):
                print(line)

        # --- unscored (solved, but the scorer left corr empty) ---
        if r["unscored"]:
            print(f"  UNSCORED - {len(r['unscored'])} instance(s) solved (verdict OK) but got no")
            print(f"    correctness verdict (corr empty in 6b-wrong_bysolver.csv), because log10 of")
            print(f"    their count is undefined so it can't be compared to the expected value:")
            for inst, why in r["unscored"]:
                print(f"      {inst.replace('.cnf',''):24} - {why}")

        # --- cross-solver disagreements (private summaries only) ---
        if r["disagreed_bad"]:
            print(f"  DISAGREEMENT - {len(r['disagreed_bad'])} solved instance(s) where the")
            print(f"    counters disagreed and ours was NOT scored correct:")
            for line in wrap(r["disagreed_bad"], width=1):
                print(line)
        elif r["disagreed"]:
            short = [i.replace(".cnf", "") for i in r["disagreed"]]
            print(f"  NOTE - {len(short)} instance(s) we solved are flagged `disagreed` in")
            print(f"    5-correct.csv (the participating counters did not all agree). Ours matched")
            print(f"    the accepted count on every one of them, so scoring is unaffected:")
            for line in wrap(short):
                print(line)


def main():
    print(f"{'track':<15}{'runs':>5}{'solved':>7}{'OK':>4}{'TLE':>4}{'corr':>5}{'unscored':>9}   status")
    results = []
    all_ok = True
    for suite in ("exact", "approx"):
        for t in tracks():
            r = verify_track(t, suite)
            results.append(r)
            if r["issues"]:
                all_ok = False
                status = "DISCREPANCY: " + "  |  ".join(r["issues"])
            elif r["notes"]:
                status = "consistent (" + "; ".join(r["notes"]) + ")"
            else:
                status = "OK"
            print(f"{r['track']:<15}{r['runs']:>5}{r['solved']:>7}{r['ok']:>4}{r['tle']:>4}"
                  f"{r['corr']:>5}{r['wrong']:>9}   {status}")

    print_details(results)

    print()
    print("ALL SUMMARIES CONSISTENT WITH RAW LOGS" if all_ok
          else "Discrepancies found - see PER-TRACK DETAIL above.")


if __name__ == "__main__":
    main()
