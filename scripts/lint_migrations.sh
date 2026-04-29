#!/usr/bin/env bash
# =============================================================================
# Lint new migration files in a PR.
#
# Checks (forward-only — historical files on BASE_REF are grandfathered):
#   1. Filename must match YYYYMMDDHHMMSS_lowercase_name.sql
#   2. Timestamp must be strictly greater than every timestamp on BASE_REF
#      (prevents out-of-order application; see #130).
#
# Usage:
#   scripts/lint_migrations.sh [BASE_REF]
#     BASE_REF defaults to origin/main. Pass a different ref for testing.
#
# Exit codes:
#   0  all new migration files pass both checks
#   1  any check fails (error annotation emitted to stdout)
#   2  could not fetch BASE_REF to compare (misconfiguration)
# =============================================================================

set -euo pipefail

BASE_REF="${1:-origin/main}"

# Fetch only if it's a remote ref and we can reach it. Fail fast if missing —
# "silent fallback to 0" would make every new migration pass the lint trivially.
if [[ "$BASE_REF" == origin/* ]]; then
  remote_branch="${BASE_REF#origin/}"
  if ! git fetch origin "$remote_branch" --depth=1 2>/dev/null; then
    echo "::error title=lint_migrations: cannot fetch base ref::git fetch origin ${remote_branch} failed. Cannot compare timestamps."
    exit 2
  fi
fi

# Biggest 14-digit timestamp on BASE_REF's supabase/migrations/ tree.
# Anchored to start-of-filename via basename + grep so embedded digits
# inside other path segments are never picked up.
MAX_BASE_TS=$(
  git ls-tree -r --name-only "$BASE_REF" -- supabase/migrations/ 2>/dev/null \
    | xargs -I{} basename {} 2>/dev/null \
    | grep -oE '^[0-9]{14}' \
    | sort -n \
    | tail -1 \
    || true
)
# If BASE_REF has no migrations at all (first-ever), baseline is zero.
MAX_BASE_TS="${MAX_BASE_TS:-00000000000000}"

failed=0
checked=0

shopt -s nullglob
for f in supabase/migrations/*.sql; do
  fname=$(basename "$f")

  # Grandfather: any file already on BASE_REF is assumed-valid (historical).
  # We only lint files added in this branch relative to the base.
  if git cat-file -e "${BASE_REF}:supabase/migrations/${fname}" 2>/dev/null; then
    continue
  fi

  checked=$((checked + 1))

  # Check 1: filename pattern.
  if ! echo "$fname" | grep -qE '^[0-9]{14}_[a-z0-9_]+\.sql$'; then
    echo "::error title=Invalid migration filename::$fname must match YYYYMMDDHHMMSS_lowercase_name.sql (14-digit timestamp, underscore, lowercase a-z0-9 + underscores, .sql extension)"
    failed=1
    continue
  fi

  # Check 2: timestamp must be strictly greater than the latest on BASE_REF.
  ts=$(echo "$fname" | grep -oE '^[0-9]{14}')
  if [ "$ts" -le "$MAX_BASE_TS" ]; then
    echo "::error title=Stale migration timestamp::$fname (timestamp $ts) is not newer than the latest migration on ${BASE_REF} (timestamp $MAX_BASE_TS). Rename with a later YYYYMMDDHHMMSS."
    failed=1
    continue
  fi
done
shopt -u nullglob

if [ "$failed" -ne 0 ]; then
  exit 1
fi

echo "Migration lint passed (checked ${checked} new file(s) against ${BASE_REF}; latest base timestamp ${MAX_BASE_TS})."
