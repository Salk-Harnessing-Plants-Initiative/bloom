#!/bin/bash
# =============================================================================
# Apply env-config values to GitHub Secrets via `gh` CLI
#
# Runs `./scripts/generate-env-config.sh <env>` and sets every emitted
# "<SECRET_NAME> | <value>" pair as a repository secret. Creates new secrets,
# overwrites existing ones. Safe to re-run.
#
# Usage:
#   ./scripts/apply-env-secrets.sh prod
#   ./scripts/apply-env-secrets.sh staging
#   ./scripts/apply-env-secrets.sh all       # applies both prod and staging
#
# Environment:
#   GITHUB_REPO   override the target repo (default:
#                 Salk-Harnessing-Plants-Initiative/bloom)
#   DRY_RUN=1     print what would be set, don't actually call gh
#
# Requires:
#   - gh CLI installed and authenticated (`gh auth status` passes)
#   - scripts/generate-env-config.sh present in the repo
#
# Does NOT touch:
#   - Cryptographic secrets (PROD_JWT_SECRET, PROD_POSTGRES_PASSWORD, etc.)
#     Those come from `scripts/generate-secrets.sh` and should be applied
#     separately with deliberate care.
#   - Deploy-access secrets (DEPLOY_HOST, DEPLOY_SSH_KEY, DEPLOY_HOST_KEY,
#     DEPLOY_USER). Those are set manually from the server.
# =============================================================================

set -euo pipefail

ENV="${1:-}"
REPO="${GITHUB_REPO:-Salk-Harnessing-Plants-Initiative/bloom}"
DRY_RUN="${DRY_RUN:-0}"

if [ -z "$ENV" ] || [[ ! "$ENV" =~ ^(prod|staging|all)$ ]]; then
  echo "Usage: $0 <prod|staging|all>" >&2
  echo "" >&2
  echo "Optional:" >&2
  echo "  GITHUB_REPO   override repo (default: $REPO)" >&2
  echo "  DRY_RUN=1     print actions without applying" >&2
  exit 1
fi

# Preflight: gh must be installed + authenticated
if ! command -v gh >/dev/null 2>&1; then
  echo "::error::gh CLI not found. Install: https://cli.github.com/" >&2
  exit 2
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "::error::gh CLI not authenticated. Run: gh auth login" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GENERATOR="${SCRIPT_DIR}/generate-env-config.sh"

if [ ! -x "$GENERATOR" ]; then
  echo "::error::$GENERATOR not found or not executable" >&2
  exit 2
fi

apply_one_env() {
  local env="$1"
  local applied=0
  local failed=0

  echo ""
  echo "=========================================="
  echo "Applying $env config secrets to $REPO"
  echo "=========================================="

  # Capture generator output
  local output
  output=$("$GENERATOR" "$env")

  # Parse "NAME | VALUE" rows — skip header/separator lines
  while IFS='|' read -r name value; do
    name=$(echo "$name" | xargs)      # trim surrounding whitespace
    value=$(echo "$value" | xargs)    # trim surrounding whitespace

    # Skip empty rows, header row ("Secret Name"), and separator dashes
    if [ -z "$name" ] || [[ "$name" == "Secret Name"* ]] || [[ "$name" == ---* ]]; then
      continue
    fi

    # Only apply PROD_/STAGING_ prefixed names (safety guardrail — generator
    # could in theory emit other lines; this script only sets env-scoped
    # secrets).
    if [[ ! "$name" =~ ^(PROD_|STAGING_) ]]; then
      continue
    fi

    if [ "$DRY_RUN" = "1" ]; then
      printf "  [DRY RUN] %-45s = %s\n" "$name" "$value"
      applied=$((applied + 1))
      continue
    fi

    if gh secret set "$name" --repo "$REPO" --body "$value" >/dev/null 2>&1; then
      printf "  \u2713 %s\n" "$name"
      applied=$((applied + 1))
    else
      printf "  \u2717 %s (failed)\n" "$name"
      failed=$((failed + 1))
    fi
  done <<< "$(echo "$output" | grep '|' || true)"

  echo ""
  echo "Applied: $applied  Failed: $failed"
  if [ "$failed" -gt 0 ]; then
    return 1
  fi
  return 0
}

case "$ENV" in
  prod|staging)
    apply_one_env "$ENV"
    ;;
  all)
    apply_one_env "prod"
    apply_one_env "staging"
    ;;
esac

echo ""
echo "=========================================="
echo "Done. Verify with:"
echo "  gh secret list --repo $REPO | grep -E 'PROD_|STAGING_'"
echo "=========================================="
