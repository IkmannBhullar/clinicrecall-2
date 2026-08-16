#!/usr/bin/env bash
#
# `make demo-reset` — restore pristine demo state (SPEC constraint D3).
#
# Every demo mutates data: reminders get sent, appointments get marked scheduled, patients get
# imported. Before the next clinic sees the product, all of that has to go away and the six named
# fixtures in SPEC 7.3 have to be back in their exact documented states.
#
# The constraint is 30 seconds, so this deliberately does NOT tear down and rebuild the Supabase
# containers (minutes). It truncates the application tables and re-runs the seed (seconds).
#
# The Supabase `auth.*` schema is left alone: the demo login must keep working, and recreating
# auth users is both slow and unnecessary.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="${REPO_ROOT}/apps/api"

START_TIME=$(date +%s)

echo "==> Resetting demo data"
echo "    (application tables only — Supabase auth users and the containers are left running)"

uv --directory "${API_DIR}" run python -m app.demo_reset

ELAPSED=$(( $(date +%s) - START_TIME ))

echo ""
echo "Demo data reset in ${ELAPSED}s."

# SPEC D3 names 30 seconds explicitly. Warn loudly rather than fail: a slow reset on a cold
# machine is still a working reset, but you want to know before you are standing in a clinic.
if [ "${ELAPSED}" -gt 30 ]; then
  echo ""
  echo "WARNING: that took longer than the 30 second budget in SPEC D3." >&2
  echo "         Check whether the Supabase containers are under memory pressure." >&2
fi
