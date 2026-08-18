#!/usr/bin/env bash
#
# Fails the build if a server-side secret has leaked into the compiled browser bundle.
#
# Required by SPEC section 3.2. The rule it enforces is simple: the Supabase service-role key
# grants full admin access to the database, so if it ever appears in JavaScript shipped to a
# browser, every patient record in the system is exposed to anyone who opens dev tools.
#
# Code review is not a reliable control for this — a single stray import is enough. A grep over
# the actual build output is.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="${REPO_ROOT}/apps/web"
BUILD_DIR="${WEB_DIR}/.next"
ENV_FILE="${REPO_ROOT}/.env"

FAILED=0

echo "==> Checking the client bundle for leaked secrets"

# ---------------------------------------------------------------------------------------------
# Check 1 — source-level: no file under apps/web/ may mention the service-role key variable.
# ---------------------------------------------------------------------------------------------
# This catches the mistake at its source, with a useful filename, before you have to reason
# about minified output.
if grep -rIn --exclude-dir=node_modules --exclude-dir=.next \
     -e 'SUPABASE_SERVICE_ROLE_KEY' -e 'service_role' "${WEB_DIR}" 2>/dev/null; then
  echo "FAIL: the files listed above reference the Supabase service-role key." >&2
  echo "      The service-role key is server-side only and must never appear under apps/web/." >&2
  FAILED=1
else
  echo "  ok: no service-role references in apps/web/ source"
fi

# ---------------------------------------------------------------------------------------------
# Check 2 — no secret-bearing variable is exposed through the NEXT_PUBLIC_ prefix.
# ---------------------------------------------------------------------------------------------
# Anything named NEXT_PUBLIC_* is inlined into the browser bundle by Next.js at build time.
if [ -f "${ENV_FILE}" ]; then
  if grep -E '^NEXT_PUBLIC_.*(SERVICE_ROLE|SECRET|JOB_TOKEN|PROVIDER_API_KEY)' "${ENV_FILE}"; then
    echo "FAIL: a secret is exposed through a NEXT_PUBLIC_ variable (see above)." >&2
    echo "      NEXT_PUBLIC_ values are compiled into the browser bundle and are public." >&2
    FAILED=1
  else
    echo "  ok: no secrets behind a NEXT_PUBLIC_ prefix"
  fi
fi

# ---------------------------------------------------------------------------------------------
# Check 3 — build output: search the compiled JavaScript for the literal key value.
# ---------------------------------------------------------------------------------------------
# This is the real test. Checks 1 and 2 look for the shape of the mistake; this one looks for
# the secret itself, so it catches leaks that arrive by any route.
if [ -d "${BUILD_DIR}" ]; then
  SERVICE_KEY="$(grep -E '^SUPABASE_SERVICE_ROLE_KEY=' "${ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2- || true)"

  if [ -n "${SERVICE_KEY}" ]; then
    # Match on the END of the key, not the beginning.
    #
    # This is not a stylistic choice. A Supabase key is a JWT: header.payload.signature. The
    # anon key and the service-role key are issued by the same project with the same algorithm,
    # so their headers are byte-identical and their payloads share a long common prefix:
    #
    #   anon     eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24i…
    #   service  eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vf…
    #
    # The anon key is *supposed* to be in the bundle, so matching on a shared prefix would fail
    # the build on every correct project. The trailing signature is unique to each key, so it
    # identifies the service-role key and nothing else.
    KEY_SUFFIX="${SERVICE_KEY: -32}"
    if grep -rl --include='*.js' --include='*.json' --include='*.map' \
         -F "${KEY_SUFFIX}" "${BUILD_DIR}" 2>/dev/null | head -20; then
      echo "FAIL: the Supabase service-role key was found in the compiled client bundle (files above)." >&2
      FAILED=1
    else
      echo "  ok: service-role key absent from ${BUILD_DIR}"
    fi
  else
    echo "  skip: SUPABASE_SERVICE_ROLE_KEY not set in .env — nothing to search for"
  fi

  # Catch ANY service-role key, not only the one currently in .env.
  #
  # A service-role JWT's payload contains {"role":"service_role"}, so the encoded token contains
  # the base64 of that fragment. Because base64 works in three-byte groups, the encoding depends
  # on the fragment's offset within the payload — hence three patterns, one per alignment.
  #
  # This replaces an earlier check that simply grepped for the literal string "service_role".
  # That produced a false positive the moment the Supabase client was bundled: the library
  # contains the role name in its own source, as plain text, entirely legitimately. Matching the
  # *encoded* form distinguishes "a library mentions the role" from "an actual key is embedded",
  # which is the only distinction that matters.
  ENCODED_SERVICE_ROLE_PATTERNS='InJvbGUiOiJzZXJ2aWNlX3Jvb|Jyb2xlIjoic2VydmljZV9yb2xlI|icm9sZSI6InNlcnZpY2Vfcm9sZ'

  if grep -rlE --include='*.js' --include='*.json' --include='*.map' \
       "${ENCODED_SERVICE_ROLE_PATTERNS}" "${BUILD_DIR}" 2>/dev/null | head -20; then
    echo "FAIL: a Supabase service-role token is embedded in the client bundle (files above)." >&2
    echo "      That key bypasses every access control in the database." >&2
    FAILED=1
  else
    echo "  ok: no service-role token encoded anywhere in ${BUILD_DIR}"
  fi
else
  echo "  skip: ${BUILD_DIR} does not exist — run 'pnpm -C apps/web build' first for a full check"
fi

echo ""
if [ "${FAILED}" -ne 0 ]; then
  echo "Bundle secret check FAILED." >&2
  exit 1
fi

echo "Bundle secret check passed."
