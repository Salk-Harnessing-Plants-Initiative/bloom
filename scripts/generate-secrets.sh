#!/bin/bash
# =============================================================================
# Generate unique secrets for a Bloom environment (prod/staging/ci)
# Usage:
#   ./scripts/generate-secrets.sh [prod|staging|ci]           # stdout (default)
#   ./scripts/generate-secrets.sh [prod|staging|ci] --file    # write to file
# =============================================================================

set -e

ENV="${1:-prod}"
MODE="${2:-stdout}"
PREFIX=$(echo "$ENV" | tr '[:lower:]' '[:upper:]')

if [ "$MODE" = "--file" ]; then
  # IMPORTANT: .gitignore has a matching rule (.secrets-*.txt) to prevent
  # accidental commit. If you change this filename format, update .gitignore.
  OUTPUT=".secrets-${ENV}-$(date +%s).txt"
  exec > "$OUTPUT"
fi

echo "=================================================="
echo "Generating secrets for: $ENV"
echo "Prefix: ${PREFIX}_"
echo "=================================================="
echo ""
echo "Add these to GitHub → Settings → Secrets → Actions:"
echo ""

# Generate random values
POSTGRES_PASSWORD=$(openssl rand -hex 16)
JWT_SECRET=$(openssl rand -base64 48 | tr -d '\n' | head -c 64)
SUPAVISOR_ENC_KEY=$(openssl rand -hex 32)
VAULT_ENC_KEY=$(openssl rand -base64 24 | tr -d '\n' | head -c 32)
SECRET_KEY_BASE=$(openssl rand -hex 32)
MINIO_PASSWORD=$(openssl rand -hex 12)
# DB_ENC_KEY must be exactly 16 ASCII bytes (AES-128 key size).
# openssl rand -hex 8 = 16 hex chars = 16 ASCII bytes. DO NOT change to rand -hex 16.
# If wrong size, Realtime crashes 90s into startup with cryptic Elixir error.
DB_ENC_KEY=$(openssl rand -hex 8)
DASHBOARD_PASSWORD=$(openssl rand -hex 8)
BLOOMMCP_API_KEY=$(openssl rand -hex 16)

# Generate JWT tokens signed with the JWT_SECRET
# Use dynamic timestamps — issued now, valid for 5 years
# NOTE: Rotate these keys before expiration. Running this script
# regenerates JWTs with fresh timestamps. Update GitHub Secrets and redeploy.
NOW=$(date +%s)
EXP=$((NOW + 157680000))  # 5 years

# Anon key — role: anon
ANON_HEADER=$(echo -n '{"alg":"HS256","typ":"JWT"}' | base64 | tr -d '=' | tr '+/' '-_' | tr -d '\n')
ANON_PAYLOAD=$(echo -n "{\"role\":\"anon\",\"iss\":\"supabase\",\"aud\":\"authenticated\",\"iat\":$NOW,\"exp\":$EXP}" | base64 | tr -d '=' | tr '+/' '-_' | tr -d '\n')
ANON_SIGNATURE=$(echo -n "${ANON_HEADER}.${ANON_PAYLOAD}" | openssl dgst -sha256 -hmac "$JWT_SECRET" -binary | base64 | tr -d '=' | tr '+/' '-_' | tr -d '\n')
ANON_KEY="${ANON_HEADER}.${ANON_PAYLOAD}.${ANON_SIGNATURE}"

# Service role key — role: service_role
SERVICE_PAYLOAD=$(echo -n "{\"role\":\"service_role\",\"iss\":\"supabase\",\"aud\":\"authenticated\",\"iat\":$NOW,\"exp\":$EXP}" | base64 | tr -d '=' | tr '+/' '-_' | tr -d '\n')
SERVICE_SIGNATURE=$(echo -n "${ANON_HEADER}.${SERVICE_PAYLOAD}" | openssl dgst -sha256 -hmac "$JWT_SECRET" -binary | base64 | tr -d '=' | tr '+/' '-_' | tr -d '\n')
SERVICE_ROLE_KEY="${ANON_HEADER}.${SERVICE_PAYLOAD}.${SERVICE_SIGNATURE}"

echo "Secret Name                          | Value"
echo "-------------------------------------|------"
echo "${PREFIX}_POSTGRES_PASSWORD           | $POSTGRES_PASSWORD"
echo "${PREFIX}_JWT_SECRET                  | $JWT_SECRET"
echo "${PREFIX}_ANON_KEY                    | $ANON_KEY"
echo "${PREFIX}_SERVICE_ROLE_KEY            | $SERVICE_ROLE_KEY"
echo "${PREFIX}_SUPAVISOR_ENC_KEY           | $SUPAVISOR_ENC_KEY"
echo "${PREFIX}_VAULT_ENC_KEY               | $VAULT_ENC_KEY"
echo "${PREFIX}_SECRET_KEY_BASE             | $SECRET_KEY_BASE"
echo "${PREFIX}_MINIO_PASSWORD              | $MINIO_PASSWORD"
echo "${PREFIX}_DB_ENC_KEY                  | $DB_ENC_KEY"
echo "${PREFIX}_DASHBOARD_PASSWORD          | $DASHBOARD_PASSWORD"
echo "${PREFIX}_BLOOMMCP_API_KEY            | $BLOOMMCP_API_KEY"
echo ""
echo "=================================================="
echo "IMPORTANT: Save these somewhere safe before closing!"
echo "These values cannot be recovered once this terminal closes."
echo "=================================================="

if [ "$MODE" = "--file" ]; then
  chmod 600 "$OUTPUT"
  exec > /dev/tty
  echo "Secrets written to $OUTPUT (chmod 600)."
  echo "Copy values into GitHub Secrets, then: rm $OUTPUT"
fi
