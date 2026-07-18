#!/usr/bin/env python3
"""
Print every instance flagged `disagreed` in the private summary/5-correct.csv files.

`disagreed=True` means the participating counters (d4, ganak, gpmc) did NOT all
agree on the model count for that instance.  `voters` lists the solvers that
agreed on the ACCEPTED count, `vote_count` is how many.  That is only bad for us
if our own row is not scored correct (corr != 1.0).

Usage:
    ./show_disagreements.py                 # all tracks, counts truncated
    ./show_disagreements.py --full          # print counts in full (huge!)
    ./show_disagreements.py --width 200     # custom truncation width
    ./show_disagreements.py track2-exact    # only these tracks
"""

import csv
import re
import sys
from pathlib import Path

csv.field_size_limit(10**8)

ROOT = Path(__file__).parent.resolve()
FIELDS = ["instance", "verdict", "solver", "count", "count_expected",
          "vote_count", "voters", "disagreed", "corr"]


def shorten(s: str, width: int | None) -> str:
    s = s or ""
    if width is None or len(s) <= width:
        return s
    return f"{s[:width]} ...[{len(s)} chars total]"


def main() -> int:
    argv = sys.argv[1:]
    width: int | None = 70
    if "--full" in argv:
        argv.remove("--full")
        width = None
    if "--width" in argv:
        i = argv.index("--width")
        width = int(argv[i + 1])
        del argv[i:i + 2]

    wanted = set(argv)
    dirs = sorted(d for d in ROOT.glob("track*-*")
                  if re.match(r"track\d+-(exact|approx)$", d.name)
                  and (not wanted or d.name in wanted))
    if not dirs:
        print(f"no matching track directories under {ROOT}", file=sys.stderr)
        return 1

    grand_total = grand_bad = 0
    for d in dirs:
        path = d / "summary" / "5-correct.csv"
        if not path.exists():
            print(f"{d.name}: no {path.relative_to(ROOT)}, skipped")
            continue

        rows = [r for r in csv.DictReader(open(path)) if r.get("disagreed") == "True"]
        # ours is the odd one out only if we solved it but were not scored correct
        bad = [r for r in rows
               if r["verdict"] == "OK" and (r.get("corr") or "") not in ("1.0", "1")]
        grand_total += len(rows)
        grand_bad += len(bad)

        print("=" * 78)
        print(f"{d.name}  --  {len(rows)} disagreed instance(s), "
              f"{len(bad)} where ours was NOT scored correct")
        print(f"source: {path}")
        print("=" * 78)

        for r in rows:
            print()
            for k in FIELDS:
                print(f"  {k:<16} {shorten(str(r.get(k)), width)}")

    print()
    print(f"TOTAL: {grand_total} disagreed instance(s) across {len(dirs)} track(s); "
          f"{grand_bad} where our answer lost the vote.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
