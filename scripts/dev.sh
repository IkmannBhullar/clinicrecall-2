#!/usr/bin/env bash
#
# `make dev` — bring up the whole stack in one terminal.
#
# Starts the Supabase containers, then runs the API and the web app as child processes and
# streams both logs with a [api] / [web] prefix. Ctrl-C stops everything cleanly.
#
# Deliberately simple: no process manager, no tmux, no extra dependency. During a demo, the
# failure mode of a clever tool is worse than the inconvenience of interleaved logs.
#
# PORTABILITY NOTE — this script is written for bash 3.2, which is the version macOS ships and
# therefore the version most developers on this project will actually run. That rules out three
# things you would otherwise reach for here:
#
#   * `wait -n`  (bash 4.3+)  — replaced with a polling loop below.
#   * `sed -u`   (GNU only)   — BSD sed spells line buffering `-l`; we avoid sed entirely.
#   * pipelines for log prefixing — in a pipeline, `$!` is the PID of the *last* command, not
#     the server, so the cleanup trap would kill the log formatter and leave the server holding
#     its port. Process substitution keeps `$!` pointing at the server.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# 1. Supabase must be up before the API tries to connect to Postgres.
bash scripts/supabase-up.sh

# 2. Track the child PIDs so the trap can shut them down together.
API_PID=""
WEB_PID=""

# Kill a child and everything it spawned. Next.js and uvicorn both fork workers; killing only
# the parent leaves those workers holding ports 3000 and 8000, which breaks the *next*
# `make dev` in a way that is genuinely baffling if it happens mid-demo.
kill_tree() {
  local pid="$1"
  [ -z "${pid}" ] && return 0
  # `set -m` below puts each child in its own process group whose ID equals its PID, so a
  # negative PID signals the whole group.
  kill -TERM "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true
}

cleanup() {
  echo ""
  echo "Shutting down..."
  kill_tree "${API_PID}"
  kill_tree "${WEB_PID}"
  wait 2>/dev/null || true
  echo "Stopped. The Supabase containers are still running — use 'make supabase-stop' to stop them too."
}
trap cleanup EXIT INT TERM

# `set -m` enables job control, which is what gives each background child its own process group.
set -m

echo ""
echo "==> Starting FastAPI on http://127.0.0.1:8000"
uv --directory apps/api run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 \
  > >(while IFS= read -r line; do printf '[api] %s\n' "${line}"; done) 2>&1 &
API_PID=$!

echo "==> Starting Next.js on http://localhost:3000"
pnpm -C apps/web dev \
  > >(while IFS= read -r line; do printf '[web] %s\n' "${line}"; done) 2>&1 &
WEB_PID=$!

set +m

echo ""
echo "  Web app:         http://localhost:3000"
echo "  API docs:        http://127.0.0.1:8000/docs"
echo "  Supabase Studio: http://127.0.0.1:54323"
echo ""
echo "  Press Ctrl-C to stop."
echo ""

# Wait until either child exits, then let the trap tear down the other. `wait -n` would express
# this in one line but does not exist in bash 3.2, so poll instead — once a second is far below
# the threshold where anyone would notice, and costs nothing.
while kill -0 "${API_PID}" 2>/dev/null && kill -0 "${WEB_PID}" 2>/dev/null; do
  sleep 1
done

echo ""
echo "One of the servers exited — shutting the other down."
