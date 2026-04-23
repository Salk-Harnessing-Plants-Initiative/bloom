#!/usr/bin/env bash
# =============================================================================
# One-step pipeline: generate env secrets → show preview → confirm → upload
# to GitHub Secrets. Human checkpoint before anything is pushed.
#
# Usage:
#   ./scripts/setup-env-secrets.sh [prod|staging|ci]
#   ./scripts/setup-env-secrets.sh prod --dry-run
#
# What it does:
#   1. Generates 11 random secrets by calling ./scripts/generate-secrets.sh --raw
#   2. Queries GitHub to see which of the 11 target secret names already exist
#   3. Shows a preview: KEY + full value + [NEW] or [UPDATE] marker
#   4. Prompts for a typed "yes" (not y/N — fat-finger protection)
#   5. On confirm, uploads each via `gh secret set`
#   6. Offers to clear the terminal scrollback when done
#
# Requires:
#   - gh CLI installed and authenticated to the target repo (run `gh auth login`
#     and have write access to this repo's Settings → Secrets)
#   - openssl (used by generate-secrets.sh)
#
# Scope:
#   Uploads only the 11 cryptographically random secrets (generate-secrets.sh
#   handles those). The 5 other env-prefixed secrets that the deploy workflow
#   reads are out of this script's scope and must be set manually:
#     <PREFIX>_OPENAI_API_KEY        — provided by you / OpenAI
#     <PREFIX>_LANGCHAIN_API_KEY     — provided by LangSmith
#     <PREFIX>_MINIO_DATA_PATH       — host filesystem path
#     <PREFIX>_MINIO_ROOT_USER       — MinIO admin user (typically "supabase")
#     <PREFIX>_DASHBOARD_USERNAME    — Studio admin user (typically "admin")
#
# Portability: this script runs in bash 3.2 (macOS default) — no associative
# arrays. State lives in temp files.
# =============================================================================

set -euo pipefail

ENV="${1:-}"
MODE="${2:-}"

if [ -z "$ENV" ] || { [ "$ENV" != "prod" ] && [ "$ENV" != "staging" ] && [ "$ENV" != "ci" ]; }; then
  echo "Usage: $0 [prod|staging|ci] [--dry-run]" >&2
  exit 2
fi

DRY_RUN=false
if [ "$MODE" = "--dry-run" ]; then
  DRY_RUN=true
fi

# --- Pre-flight checks ------------------------------------------------------
command -v gh >/dev/null 2>&1 || {
  echo "::error::gh CLI not found — install from https://cli.github.com/" >&2
  exit 1
}

gh auth status >/dev/null 2>&1 || {
  echo "::error::gh CLI not authenticated — run 'gh auth login' first" >&2
  exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GENERATE_SCRIPT="${SCRIPT_DIR}/generate-secrets.sh"
[ -x "$GENERATE_SCRIPT" ] || {
  echo "::error::generate-secrets.sh not executable at $GENERATE_SCRIPT" >&2
  exit 1
}

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null) || {
  echo "::error::could not determine target repo — run 'gh repo set-default' or from inside a git repo" >&2
  exit 1
}

# --- Generate secrets into a temp file --------------------------------------
# umask 077 so the temp file is never readable by other local users. We clean
# up on EXIT so values don't linger on disk beyond the script's lifetime.
umask 077
TMPDIR_WORK=$(mktemp -d "${TMPDIR:-/tmp}/setup-env-secrets.XXXXXX")
SECRETS_FILE="${TMPDIR_WORK}/secrets"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

echo "Generating ${ENV} secrets (11 cryptographically random values)..." >&2
"$GENERATE_SCRIPT" "$ENV" --raw > "$SECRETS_FILE"

COUNT=$(wc -l < "$SECRETS_FILE" | tr -d ' ')

# --- Fetch existing secret names to detect NEW vs UPDATE --------------------
EXISTING_FILE="${TMPDIR_WORK}/existing"
gh secret list -R "$REPO" --json name --jq '.[].name' 2>/dev/null | sort -u > "$EXISTING_FILE" || true

# --- Preview ----------------------------------------------------------------
echo
echo "╔══════════════════════════════════════════════════════════════════════════════"
echo "║ ${COUNT} secrets to upload to ${REPO}"
echo "╠══════════════════════════════════════════════════════════════════════════════"
while IFS= read -r line; do
  [ -z "$line" ] && continue
  key="${line%%=*}"
  value="${line#*=}"
  if grep -qx "$key" "$EXISTING_FILE"; then
    status="UPDATE"
  else
    status="NEW"
  fi
  # Truncate very long values in the preview (JWTs are ~200 chars) so the
  # table stays readable. Show first 48 chars + ellipsis for anything longer.
  if [ ${#value} -gt 48 ]; then
    display="${value:0:48}…  (${#value} chars)"
  else
    display="$value"
  fi
  printf "║  %-32s [%-6s]  %s\n" "$key" "$status" "$display"
done < "$SECRETS_FILE"
echo "╠══════════════════════════════════════════════════════════════════════════════"
echo "║ Legend: [NEW] secret will be created | [UPDATE] existing secret will be overwritten"
echo "╚══════════════════════════════════════════════════════════════════════════════"
echo

# --- Dry-run short-circuit --------------------------------------------------
if [ "$DRY_RUN" = true ]; then
  echo "Dry run — no secrets uploaded. Re-run without --dry-run to upload."
  exit 0
fi

# --- Human checkpoint -------------------------------------------------------
echo "⚠  Full values are visible in this terminal + scrollback after this runs."
echo "   The script will offer to clear the screen when done."
echo
echo "Type 'yes' (exactly) to upload ${COUNT} secrets to ${REPO}:"
read -r CONFIRMATION

if [ "$CONFIRMATION" != "yes" ]; then
  echo "Cancelled. No secrets uploaded."
  exit 0
fi

# --- Upload -----------------------------------------------------------------
echo
echo "Uploading..."
FAILED_FILE="${TMPDIR_WORK}/failed"
UPLOADED=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  key="${line%%=*}"
  value="${line#*=}"
  printf "  %-32s ... " "$key"
  if gh secret set "$key" -R "$REPO" --body "$value" >/dev/null 2>&1; then
    echo "✓"
    UPLOADED=$((UPLOADED + 1))
  else
    echo "✗ (failed)"
    echo "$key" >> "$FAILED_FILE"
  fi
done < "$SECRETS_FILE"
echo

if [ ! -f "$FAILED_FILE" ]; then
  echo "Done. ${UPLOADED}/${COUNT} uploaded."
else
  FAILED_COUNT=$(wc -l < "$FAILED_FILE" | tr -d ' ')
  echo "Done with errors: ${UPLOADED}/${COUNT} uploaded, ${FAILED_COUNT} failed."
  echo "Failed:"
  while IFS= read -r key; do
    echo "  - $key"
  done < "$FAILED_FILE"
  echo "Re-run the script to retry — uploads are idempotent."
fi

# --- Offer to clear terminal ------------------------------------------------
echo
echo "Clear terminal + scrollback to remove values from history? [y/N]:"
read -r CLEAR
if [ "$CLEAR" = "y" ] || [ "$CLEAR" = "Y" ]; then
  clear
  printf '\e[3J'
  echo "Terminal cleared."
fi
