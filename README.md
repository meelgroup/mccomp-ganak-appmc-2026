# Ganak at the model counting competition 2026

## Building

Should build with `./build.sh`. It simply unpacks and builds gmp, mpfr, and flint and then
sets up all the build shell scripts for our self-managed tools, and then builds them all.
This results in a statically linked binary `ganak_static`.

## Run scripts

*  `mccomp_run_approx.sh` -- runs with approximate model counting for
   unweighted, and runs with high precision floating point for weighted counting
*  `mccomp_run_exact.sh` -- runs with exact model counting for unweighted, and
   runs with infinite precision rational numbers for weighted
*  `mccomp_run_fast.sh` -- runs tuned to 5 minute time limit. Runs with exact
   model counting for unweighted, and high precision floating point for
   weighted counting
