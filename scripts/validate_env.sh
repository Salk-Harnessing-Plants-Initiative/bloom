#!/usr/bin/env bash
# =============================================================================
# Validate an assembled env file has every key that docker-compose.prod.yml
# references, and that each value is non-empty / non-whitespace / non-#-prefixed.
#
# Called from:
#   .github/workflows/deploy.yml     (prod + staging validate steps)
#   tests/unit/test_env_defaults.py  (negative-path tests)
#
# Single source of truth for the validator logic, so the workflow and the
# tests can never drift.
#
# Usage:
#   scripts/validate_env.sh <env_file> <compose_file>
#
# Exit codes:
#   0 — every required key is present with a non-empty, well-formed value
#   1 — one or more required keys are missing or have bad values (listed in stderr)
#   2 — usage error (wrong number of args, or input files missing)
#
# The required-keys list is derived at call time from ${VAR} references in
# the compose file, so adding a new env var to compose automatically makes
# the validator check for it — no hardcoded list to maintain.
#
# Filtered out of the required list:
#   COMPOSE_PROJECT_NAME              — used by docker-compose itself, not
#                                        an env var the app reads
#   NEXT_PUBLIC_SUPABASE_COOKIE_NAME  — derived from SUPABASE_COOKIE_NAME in
#                                        compose, not set directly in .env
#
# Value regex: ^KEY=[^[:space:]#].*
#   Rejects: KEY=  |  KEY=<whitespace>  |  KEY=#placeholder
#   Accepts: KEY=realvalue  |  KEY=tls internal  |  KEY=p@ss#w0rd
# =============================================================================

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <env_file> <compose_file>" >&2
  exit 2
fi

env_file="$1"
compose_file="$2"

if [ ! -r "$env_file" ]; then
  echo "::error::env file not readable: $env_file" >&2
  exit 2
fi

if [ ! -r "$compose_file" ]; then
  echo "::error::compose file not readable: $compose_file" >&2
  exit 2
fi

# Derive required-keys list from every ${VAR} reference in compose.
required=$(grep -oE '\$\{[A-Z_][A-Z0-9_]*' "$compose_file" \
  | sed 's/^\${//' \
  | sort -u \
  | grep -v -E '^(COMPOSE_PROJECT_NAME|NEXT_PUBLIC_SUPABASE_COOKIE_NAME)$')

missing=()
for k in $required; do
  if ! grep -qE "^${k}=[^[:space:]#].*" "$env_file"; then
    missing+=("$k")
  fi
done

if [ ${#missing[@]} -gt 0 ]; then
  echo "::error::Missing or empty required keys in $env_file:" >&2
  for k in "${missing[@]}"; do
    echo "  - $k" >&2
  done
  exit 1
fi

count=$(echo "$required" | wc -w | tr -d ' ')
lines=$(wc -l < "$env_file" | tr -d ' ')
echo "✓ $env_file validated ($lines lines, $count keys checked)"
