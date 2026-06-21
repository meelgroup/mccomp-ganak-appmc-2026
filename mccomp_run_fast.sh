#/usr/bin/env bash
file=$1
if [ -z "$file" ]; then
    echo "Usage: $0 <path_to_mccomp_run_file>"
    exit 1
fi

FAST=(--arjuncmsmult 0.1 --arjunoraclemult 0.1 --arjunbackwmaxc 50 --tditers 50 --tdsteps 30000 --arjunextendmaxconfl 3000)
if grep -qE '^c t (mc|pmc)' "$file"; then
    echo "c o The file seems to be unweighted. Will run with --mode 0."
    ./ganak_static --mode 0 --maxcache=19000 "${FAST[@]}" "$file"
elif grep -qE '^c t (wmc|pwmc)' "$file"; then
    echo "c o The file seems to be weighted. Will run with --mode 7."
    ./ganak_static --mode 7 --maxcache=14000 "${FAST[@]}" "$file"
elif grep -qE '^c t (amc-complex)' "$file"; then
    echo "c o The file seems to be complex number-weighted. Will run with --mode 6."
    ./ganak_static --mode 6 --maxcache=9000 "${FAST[@]}" "$file"
else
  echo "c o The file does not contain a valid mccomp header."
  exit 1
fi
