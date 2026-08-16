#!/usr/bin/env bash
#
# Creates the local .env file from .env.example and generates the secrets that must be random
# per-machine (the job token and the unsubscribe signing secret).
#
# This script is deliberately non-destructive: if .env already exists it is left completely
# alone. Re-running `make setup` must never clobber a developer's local configuration.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"

if [ -f "${ENV_FILE}" ]; then
  echo ".env already exists — leaving it untouched."
  exit 0
fi

if [ ! -f "${ENV_EXAMPLE}" ]; then
  echo "ERROR: ${ENV_EXAMPLE} is missing. Cannot create .env." >&2
  exit 1
fi

cp "${ENV_EXAMPLE}" "${ENV_FILE}"

# Generate two independent random secrets. `openssl rand -hex 32` gives 256 bits of entropy,
# which is far more than these need, but there is no reason to be stingy.
JOB_TOKEN="$(openssl rand -hex 32)"
UNSUBSCRIBE_SECRET="$(openssl rand -hex 32)"

# Replace the empty placeholders in the freshly copied file.
# The `.bak` suffix is required for portability: BSD sed (macOS) demands an argument to -i,
# GNU sed does not. Passing an empty string works on neither, so we write a backup and delete it.
sed -i.bak "s|^JOB_TOKEN=.*|JOB_TOKEN=${JOB_TOKEN}|" "${ENV_FILE}"
sed -i.bak "s|^UNSUBSCRIBE_TOKEN_SECRET=.*|UNSUBSCRIBE_TOKEN_SECRET=${UNSUBSCRIBE_SECRET}|" "${ENV_FILE}"
rm -f "${ENV_FILE}.bak"

echo "Created .env from .env.example, with freshly generated JOB_TOKEN and UNSUBSCRIBE_TOKEN_SECRET."
echo "Supabase keys will be filled in automatically when the local stack starts."
