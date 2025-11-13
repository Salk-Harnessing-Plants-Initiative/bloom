#!/bin/bash

# Script to set up environment files for Bloom project
# Usage: ./scripts/setup-env.sh [dev|prod]

set -e

ENV_TYPE="${1:-dev}"

if [[ "$ENV_TYPE" != "dev" && "$ENV_TYPE" != "prod" ]]; then
    echo "[ERROR] Invalid environment type. Use 'dev' or 'prod'"
    echo "Usage: ./scripts/setup-env.sh [dev|prod]"
    exit 1
fi

echo "Setting up Bloom environment files for: $ENV_TYPE"
echo ""

# Check if .env file already exists
if [ -f ".env.$ENV_TYPE" ]; then
    echo "[WARNING] .env.$ENV_TYPE already exists"
    read -p "Do you want to overwrite it? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "[ABORTED] Existing .env.$ENV_TYPE was not modified."
        exit 0
    fi
fi

# Copy example file
EXAMPLE_FILE=".env.${ENV_TYPE}.example"
if [ ! -f "$EXAMPLE_FILE" ]; then
    echo "[ERROR] $EXAMPLE_FILE not found!"
    echo "This file should be in the repository root."
    exit 1
fi

echo "Copying $EXAMPLE_FILE to .env.$ENV_TYPE..."
cp "$EXAMPLE_FILE" ".env.$ENV_TYPE"

echo "[SUCCESS] Created .env.$ENV_TYPE"
echo ""
echo "Generating secure random values..."

# Generate random values
JWT_SECRET=$(openssl rand -base64 48 | tr -d '\n')
SUPAVISOR_ENC_KEY=$(openssl rand -hex 32)
VAULT_ENC_KEY=$(openssl rand -base64 24 | tr -d '\n')
SECRET_KEY_BASE=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -d '\n')
MINIO_PASSWORD=$(openssl rand -base64 16 | tr -d '\n')
DASHBOARD_PASSWORD=$(openssl rand -base64 12 | tr -d '\n')

# Replace placeholders with generated values
sed -i.bak "s|<your-jwt-secret-min-32-chars>|$JWT_SECRET|g" ".env.$ENV_TYPE"
sed -i.bak "s|<your-supavisor-encryption-key>|$SUPAVISOR_ENC_KEY|g" ".env.$ENV_TYPE"
sed -i.bak "s|<your-vault-encryption-key>|$VAULT_ENC_KEY|g" ".env.$ENV_TYPE"
sed -i.bak "s|<your-secret-key-base>|$SECRET_KEY_BASE|g" ".env.$ENV_TYPE"
sed -i.bak "s|<your-secure-postgres-password>|$POSTGRES_PASSWORD|g" ".env.$ENV_TYPE"
sed -i.bak "s|<your-minio-password>|$MINIO_PASSWORD|g" ".env.$ENV_TYPE"
sed -i.bak "s|<your-dashboard-password>|$DASHBOARD_PASSWORD|g" ".env.$ENV_TYPE"

# Clean up backup file
rm -f ".env.$ENV_TYPE.bak"

echo "[SUCCESS] Generated secure random values for:"
echo "   - JWT_SECRET"
echo "   - SUPAVISOR_ENC_KEY"
echo "   - VAULT_ENC_KEY"
echo "   - SECRET_KEY_BASE"
echo "   - POSTGRES_PASSWORD"
echo "   - MINIO_ROOT_PASSWORD"
echo "   - DASHBOARD_PASSWORD"
echo ""
echo "[IMPORTANT] You still need to manually configure:"
echo ""
echo "1. Generate Supabase API keys:"
echo "   Visit: https://supabase.com/docs/guides/self-hosting#api-keys"
echo "   Or use this JWT generator: https://jwt.io/"
echo "   "
echo "   Update these in .env.$ENV_TYPE:"
echo "   - ANON_KEY"
echo "   - SERVICE_ROLE_KEY"
echo "   - NEXT_PUBLIC_SUPABASE_ANON_KEY (same as ANON_KEY)"
echo ""
echo "2. Set your preferred usernames:"
echo "   - MINIO_ROOT_USER (default: <your-minio-username>)"
echo "   - DASHBOARD_USERNAME (default: <your-dashboard-username>)"
echo ""
echo "3. Update tokens if needed:"
echo "   - LOGFLARE_PUBLIC_ACCESS_TOKEN"
echo "   - LOGFLARE_PRIVATE_ACCESS_TOKEN"
echo ""
echo "4. Configure optional settings:"
echo "   - STUDIO_DEFAULT_ORGANIZATION"
echo "   - STUDIO_DEFAULT_PROJECT"
echo "   - OPENAI_API_KEY (if using AI features)"
echo ""
echo "Edit .env.$ENV_TYPE to complete the setup"
echo ""
echo "After editing, start the stack with:"
if [ "$ENV_TYPE" == "dev" ]; then
    echo "   make dev-up"
else
    echo "   make prod-up"
fi
echo ""
echo "[WARNING] NEVER commit .env.$ENV_TYPE to git!"
