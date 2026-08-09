#!/usr/bin/env bash
# =============================================================================
# Poll a container's Docker healthcheck status until `healthy` or a timeout
# elapses (issue #634 PR #635 round-3 review: extracted out of deploy.yml so
# this loop is unit-testable, mirroring check_kong_restart_delta.sh's
# existing extraction rationale — before this, the poll loop was duplicated
# inline four times (forward-path restart + rollback restart, each for prod
# and staging) with zero execution-level test coverage).
#
# Called from:
#   .github/workflows/deploy.yml (deploy-production + deploy-staging jobs'
#     "Restart Kong config" and "Rollback on failure" steps, over SSH)
#   tests/unit/test_wait_for_kong_healthy_script.py (behavioral tests)
#
# Usage:
#   wait_for_kong_healthy.sh <container-id> [timeout-seconds] [poll-interval-seconds]
#
# Prints the final health status ("healthy", "unhealthy", "starting", or
# "unknown" if `docker inspect` itself failed) to stdout, and nothing else —
# callers capture it with `status=$(...)`.
#
# A timeout is NOT itself treated as an error by this script: exit 0 means
# "healthy" was observed before the timeout; exit 1 means the timeout
# elapsed without ever seeing "healthy". Callers decide what a non-healthy
# result means for them — the forward path's crash-loop check (RestartCount
# delta) decides pass/fail there, while the rollback path treats it as a
# reason to warn instead of claiming a clean rollback. See
# openspec/changes/fix-kong-reload-on-deploy/design.md Decision 5.
#
# Exit codes:
#   0 — reported healthy within the timeout
#   1 — timeout elapsed without reporting healthy
#   2 — usage error (missing container id)
# =============================================================================

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <container-id> [timeout-seconds] [poll-interval-seconds]" >&2
  exit 2
fi

cid="$1"
timeout_seconds="${2:-120}"
poll_interval="${3:-3}"

elapsed=0
status=unknown
while [ "$elapsed" -lt "$timeout_seconds" ]; do
  status=$(docker inspect --format='{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)
  if [ "$status" = "healthy" ]; then
    break
  fi
  sleep "$poll_interval"
  elapsed=$((elapsed + poll_interval))
done

printf '%s\n' "$status"

if [ "$status" = "healthy" ]; then
  exit 0
fi
exit 1
