#!/usr/bin/env bash
#
# freshness_mutants.sh — THE NEGATIVE CONTROL FOR THE FRESHNESS SUITE.
# [SPEC:CXC-FRESHNESS] [CXC-VISION-EPISTEMICS]
#
# ⛔ WHY THIS EXISTS.  A freshness receiver CAN ONLY REPORT SUCCESS.  If it
# never marks anything stale, every test that asks "did it go unknown?"
# passes — and so does the INVERSE defect, a receiver that condemns
# EVERYTHING including the quiet-by-design namespaces whose silence IS the
# design.  A green env:native_freshness therefore proves nothing on its own:
# it is consistent with a receiver that does nothing at all.
#
# So this breaks each load-bearing rule ON PURPOSE and asserts the suite goes
# RED.  A mutant that PASSES is the finding: it means no test covers that
# rule, and the green run above it was measuring nothing.
#
#   cyd/tools/freshness_mutants.sh
# Exits 0 only when EVERY mutant failed the suite.
#
# ⚠ WHAT IT STILL CANNOT PROVE: recall against the KNOWN rule list only.  A
# green run here is not a correctness proof — it is evidence that these six
# specific defects are detectable, and nothing about a seventh.
set -u

cd "$(dirname "$0")/.." || exit 2

declare -a NAMES=(
  "1  EventDriven gets a timeout too      -> quiet-by-design is CONDEMNED"
  "2  NeverReceived reported as Fresh     -> 'no evidence of death'"
  "3  age stamped on CHANGE not RECEIPT   -> a steady publisher EXPIRES"
  "4  a node never heard from is alive    -> every unbooted board CERTIFIED"
  "5  emit every tick, not on the edge    -> the diagnostic FLOODS the bus"
  "6  WireUnknown loses to the age rule   -> an explicit 0xFD is RELABELLED"
  "7  the sampler term is dropped         -> a LIVE fast publisher FLAPS"
  "8  a class may name an undeclared ns   -> the class fails OPEN"
  "9  impaired == (health != Ok)          -> every UART leaf STOPS JUDGING"
  "10 impairment demotes Fresh too        -> deafness MANUFACTURES a fault"
  "11 class age is the MIN of members     -> the freshest LAUNDERS the rest"
  "12 the beat parser knows JSON only     -> NODE_SILENT dies on FRAMED wires"
)

fail=0
for m in 1 2 3 4 5 6 7 8 9 10 11 12; do
  echo "=============================================================="
  echo "MUTANT ${NAMES[$((m-1))]}"
  echo "=============================================================="
  rm -rf ".pio/build/native_freshness"
  PLATFORMIO_BUILD_FLAGS="-DFRESHNESS_MUTANT=$m" \
    pio run -e native_freshness >/dev/null 2>&1 || {
      echo "  BUILD FAILED for mutant $m — cannot judge the suite"; fail=1; continue; }
  out="$(.pio/build/native_freshness/program 2>&1)"
  rc=$?
  echo "$out" | grep -E '^(FAIL|[0-9]+/[0-9]+|ALL PASS|[0-9]+ FAILED)' | sed 's/^/  /'
  if [ $rc -eq 0 ]; then
    echo "  ⛔ MUTANT $m PASSED THE SUITE — no test covers this rule."
    fail=1
  else
    echo "  ✔ mutant $m correctly detected (exit $rc)"
  fi
  echo
done

# Restore a clean, UNMUTATED build so a later `pio run` is not measuring a
# mutant left behind on disk — a stale binary that reports green is exactly
# the shape of failure this whole file is about.
rm -rf ".pio/build/native_freshness"
pio run -e native_freshness >/dev/null 2>&1

if [ $fail -ne 0 ]; then
  echo "RESULT: at least one mutant SURVIVED — the suite has a blind spot."
  exit 1
fi
echo "RESULT: all ${#NAMES[@]} mutants detected.  The suite can go red."
exit 0
