#!/usr/bin/env bash
#
# `make verify` — the executable definition of done (SPEC section 11).
#
# The spec is explicit that a checklist is unverifiable, so "done" is defined as: this script
# exits 0. It runs every gate in order and prints a summary table at the end.
#
# Gates that depend on code from a build phase that has not landed yet report PENDING rather
# than passing. PENDING is treated as failure for the overall exit code, so this script cannot
# report success on a half-built product — but the summary still shows exactly how far along
# the build is.
#
# Deliberately does NOT stop at the first failure: seeing all ten results at once is far more
# useful than fixing them one round-trip at a time.
#
set -uo pipefail   # note: no -e, because we want every gate to run

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

API_DIR="${REPO_ROOT}/apps/api"
WEB_DIR="${REPO_ROOT}/apps/web"
UV="uv --directory ${API_DIR}"
SUPABASE="$(command -v supabase 2>/dev/null || echo "${REPO_ROOT}/.tools/bin/supabase")"

# Collected results, printed as a table at the end.
RESULTS=()
EXIT_CODE=0

# record <status> <name> <detail>
record() {
  RESULTS+=("$1|$2|${3:-}")
  if [ "$1" != "PASS" ]; then EXIT_CODE=1; fi
}

# run_gate <name> <command...>  — runs a command, records PASS/FAIL from its exit status.
run_gate() {
  local name="$1"; shift
  echo ""
  echo "─────────────────────────────────────────────────────────────────────────────"
  echo "  GATE: ${name}"
  echo "─────────────────────────────────────────────────────────────────────────────"
  if "$@"; then
    record "PASS" "${name}"
  else
    record "FAIL" "${name}" "command exited non-zero"
  fi
}

# skip_gate <name> <phase> — records a gate whose implementing phase has not landed.
skip_gate() {
  echo ""
  echo "  GATE: $1 — PENDING (arrives in phase $2)"
  record "PENDING" "$1" "phase $2"
}

echo ""
echo "═════════════════════════════════════════════════════════════════════════════"
echo "  ClinicRecall — make verify"
echo "═════════════════════════════════════════════════════════════════════════════"

# ---------------------------------------------------------------------------------------------
# 1. The local Supabase stack is up
# ---------------------------------------------------------------------------------------------
run_gate "supabase status" "${SUPABASE}" status

# ---------------------------------------------------------------------------------------------
# 2. Migrations apply cleanly
# ---------------------------------------------------------------------------------------------
if [ -f "${API_DIR}/alembic.ini" ]; then
  run_gate "alembic upgrade head" ${UV} run alembic upgrade head
else
  skip_gate "alembic upgrade head" 2
fi

# ---------------------------------------------------------------------------------------------
# 3. Seed is idempotent — run it twice and assert the row counts do not move
# ---------------------------------------------------------------------------------------------
if [ -f "${API_DIR}/app/seed/__init__.py" ] || [ -f "${API_DIR}/app/seed.py" ]; then
  run_gate "seed (idempotent, run twice)" bash "${REPO_ROOT}/scripts/check-seed-idempotent.sh"
else
  skip_gate "seed (idempotent, run twice)" 7
fi

# ---------------------------------------------------------------------------------------------
# 4. Python lint + format
# ---------------------------------------------------------------------------------------------
run_gate "ruff check" ${UV} run ruff check .
run_gate "ruff format --check" ${UV} run ruff format --check .

# ---------------------------------------------------------------------------------------------
# 5. Python types (strict on the two layers the spec names)
# ---------------------------------------------------------------------------------------------
run_gate "mypy (services + schemas)" ${UV} run mypy app/services app/schemas

# ---------------------------------------------------------------------------------------------
# 6. Python tests — includes the org-isolation and idempotency suites
# ---------------------------------------------------------------------------------------------
run_gate "pytest" ${UV} run pytest -q

# ---------------------------------------------------------------------------------------------
# 7. TypeScript types
# ---------------------------------------------------------------------------------------------
if [ -f "${WEB_DIR}/tsconfig.json" ]; then
  run_gate "tsc --noEmit" pnpm -C apps/web exec tsc --noEmit
else
  skip_gate "tsc --noEmit" 8
fi

# ---------------------------------------------------------------------------------------------
# 8. Frontend lint
# ---------------------------------------------------------------------------------------------
if [ -f "${WEB_DIR}/package.json" ]; then
  run_gate "eslint" pnpm -C apps/web lint
else
  skip_gate "eslint" 8
fi

# ---------------------------------------------------------------------------------------------
# 9. Playwright — the full 13-step demo path, with a screenshot at every step
# ---------------------------------------------------------------------------------------------
if [ -f "${WEB_DIR}/playwright.config.ts" ]; then
  run_gate "playwright (13-step demo path)" pnpm -C apps/web test:e2e
else
  skip_gate "playwright (13-step demo path)" 11
fi

# ---------------------------------------------------------------------------------------------
# 10. No server-side secret in the client bundle
# ---------------------------------------------------------------------------------------------
run_gate "bundle secret check" bash "${REPO_ROOT}/scripts/check-bundle-secrets.sh"

# ---------------------------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------------------------
echo ""
echo "═════════════════════════════════════════════════════════════════════════════"
echo "  SUMMARY"
echo "═════════════════════════════════════════════════════════════════════════════"
for entry in "${RESULTS[@]}"; do
  status="${entry%%|*}"
  rest="${entry#*|}"
  name="${rest%%|*}"
  detail="${rest#*|}"
  printf "  %-9s %-38s %s\n" "${status}" "${name}" "${detail}"
done
echo "─────────────────────────────────────────────────────────────────────────────"

if [ "${EXIT_CODE}" -eq 0 ]; then
  echo "  ALL GATES PASSED — the product is demo-ready."
else
  echo "  NOT DEMO-READY — see the failing or pending gates above."
fi
echo ""

exit "${EXIT_CODE}"
