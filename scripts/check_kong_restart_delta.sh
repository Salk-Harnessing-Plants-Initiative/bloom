#!/usr/bin/env bash
# =============================================================================
# Delta-based crash-loop check for the kong container after a deliberate
# config-reload restart (issue #634).
#
# Called from:
#   .github/workflows/deploy.yml (deploy-production + deploy-staging jobs'
#     "Kong crash-loop check" steps, over SSH on the deploy host)
#   tests/unit/test_check_kong_restart_delta_script.py (behavioral tests)
#
# Single source of truth for the delta/threshold decision, so the workflow
# and the tests can never drift.
#
# Usage:
#   check_kong_restart_delta.sh <before-restart-count> <threshold> -- <docker compose command...>
#
# The caller (deploy.yml) restarts kong itself as the normal, successful
# path of applying a kong.yml change — so RestartCount is expected to move
# by exactly 1 even when nothing is wrong. Only a delta beyond <threshold>
# additional (unplanned) restarts indicates Docker's `restart: unless-stopped`
# policy is retrying a crashing container, i.e. a bad kong.yml. See
# openspec/changes/fix-kong-reload-on-deploy/design.md Decision 2/3.
#
# Everything after `--` is the exact `docker compose -f ... --env-file ...
# [-p ...]` prefix the calling job already uses for every other compose
# invocation in that job — passed through so this script never hardcodes
# prod/staging-specific flags itself.
#
# Exit codes:
#   0 — delta is within threshold (or the container is otherwise fine)
#   1 — the check itself failed: crash loop detected (kong stopped), the
#       container doesn't exist, or RestartCount couldn't be parsed
#   2 — usage error (wrong arguments)
# =============================================================================

set -euo pipefail

if [ "$#" -lt 4 ] || [ "$3" != "--" ]; then
  echo "Usage: $0 <before-restart-count> <threshold> -- <docker compose command...>" >&2
  exit 2
fi

before="$1"
threshold="$2"
shift 3
compose_cmd=("$@")

if [ "${#compose_cmd[@]}" -eq 0 ]; then
  echo "Usage: $0 <before-restart-count> <threshold> -- <docker compose command...>" >&2
  exit 2
fi

if ! [[ "$before" =~ ^[0-9]+$ ]]; then
  echo "::error::before-restart-count must be a non-negative integer (got '$before')" >&2
  exit 2
fi

if ! [[ "$threshold" =~ ^[0-9]+$ ]]; then
  echo "::error::threshold must be a non-negative integer (got '$threshold')" >&2
  exit 2
fi

cid=$("${compose_cmd[@]}" ps -q kong)
if [ -z "$cid" ]; then
  echo "::error::kong container is not running — cannot check its restart count" >&2
  exit 1
fi

after=$(docker inspect --format='{{.RestartCount}}' "$cid")

# Do not silently coerce a malformed reading to 0 — that could compute a
# negative delta and mask a real problem as "within threshold".
if ! [[ "$after" =~ ^[0-9]+$ ]]; then
  echo "::error::could not read kong's RestartCount from docker inspect (got '$after')" >&2
  exit 1
fi

delta=$((after - before))

if [ "$delta" -gt "$threshold" ]; then
  echo "::error::Kong restarted $delta times unexpectedly after config reload (threshold: $threshold) — likely a bad kong.yml. Stopping kong to halt further crash-restarts." >&2
  echo '::group::kong logs'
  "${compose_cmd[@]}" logs --tail=100 kong || true
  echo '::endgroup::'
  "${compose_cmd[@]}" stop kong || true
  exit 1
fi

echo "kong restart delta: $delta"
