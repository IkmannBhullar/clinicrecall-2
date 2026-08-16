#!/usr/bin/env bash
#
# Installs the Supabase CLI so `make setup` works from a clean clone on any machine.
#
# Why this script exists instead of "just run brew install":
#   - Homebrew is not present on every machine, and on some machines it is blocked by
#     unrelated tap-trust prompts. Requiring it would break SPEC D5 ("two commands from cold").
#   - We therefore install a pinned binary into the repo-local .tools/bin/ directory, which is
#     gitignored. Nothing outside the repo is touched.
#
# If a `supabase` binary is already on PATH, this script does nothing and defers to it.
#
set -euo pipefail

# Pinned so that a rebuild months from now behaves identically to the demo you rehearsed.
# Bump deliberately, never silently.
SUPABASE_CLI_VERSION="2.114.0"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_BIN="${REPO_ROOT}/.tools/bin"
TARGET="${TOOLS_BIN}/supabase"

# 1. Already available system-wide? Use it and stop.
if command -v supabase >/dev/null 2>&1; then
  echo "supabase CLI already on PATH: $(command -v supabase) ($(supabase --version 2>/dev/null || echo 'unknown version'))"
  exit 0
fi

# 2. Already installed repo-locally? Stop.
if [ -x "${TARGET}" ]; then
  echo "supabase CLI already installed at ${TARGET} ($("${TARGET}" --version 2>/dev/null || echo 'unknown version'))"
  exit 0
fi

# 3. Work out which release asset this machine needs.
case "$(uname -s)" in
  Darwin) OS="darwin" ;;
  Linux)  OS="linux" ;;
  *)      echo "Unsupported OS: $(uname -s). Install the Supabase CLI manually: https://supabase.com/docs/guides/cli" >&2; exit 1 ;;
esac

case "$(uname -m)" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64)  ARCH="amd64" ;;
  *)             echo "Unsupported CPU architecture: $(uname -m)." >&2; exit 1 ;;
esac

ASSET="supabase_${OS}_${ARCH}.tar.gz"
URL="https://github.com/supabase/cli/releases/download/v${SUPABASE_CLI_VERSION}/${ASSET}"

echo "Installing Supabase CLI v${SUPABASE_CLI_VERSION} (${OS}/${ARCH}) into ${TOOLS_BIN}/ ..."

mkdir -p "${TOOLS_BIN}"

# Download and unpack in a temp dir so a failed download never leaves a half-written binary
# in .tools/bin/ that later runs would treat as "already installed".
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

curl --fail --location --silent --show-error "${URL}" --output "${TMP_DIR}/${ASSET}"
tar -xzf "${TMP_DIR}/${ASSET}" -C "${TMP_DIR}" supabase
mv "${TMP_DIR}/supabase" "${TARGET}"
chmod +x "${TARGET}"

echo "Installed: $("${TARGET}" --version)"
