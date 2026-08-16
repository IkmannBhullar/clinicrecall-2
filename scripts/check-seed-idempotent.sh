#!/usr/bin/env bash
#
# Proves the seed is idempotent (SPEC 7.3: "Seed must be idempotent — re-running does not
# duplicate").
#
# Method: run the seed twice, capture the row counts after each run, and assert they match.
#
# This matters more than it looks. `make setup` runs the seed, `make demo-reset` runs the seed,
# and a nervous person before a demo will run it again for luck. If the seed appended instead of
# reconciling, the demo would show 110 patients and two Sarah Johnsons, and the talk track would
# no longer match the screen.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="${REPO_ROOT}/apps/api"
UV="uv --directory ${API_DIR}"

echo "==> Seed run 1"
${UV} run python -m app.seed >/dev/null
COUNTS_1="$(${UV} run python -m app.seed --counts-only)"

echo "==> Seed run 2"
${UV} run python -m app.seed >/dev/null
COUNTS_2="$(${UV} run python -m app.seed --counts-only)"

echo ""
echo "Row counts after run 1:"
echo "${COUNTS_1}"
echo ""
echo "Row counts after run 2:"
echo "${COUNTS_2}"
echo ""

if [ "${COUNTS_1}" != "${COUNTS_2}" ]; then
  echo "FAIL: the seed is not idempotent — row counts changed on the second run." >&2
  exit 1
fi

echo "Seed is idempotent: counts are identical across runs."
