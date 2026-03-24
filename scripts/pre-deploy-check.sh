#!/usr/bin/env bash
# =============================================================================
# Bloom v2 - Pre-deployment Security & Configuration Validation
#
# Usage:
#   ./scripts/pre-deploy-check.sh              Check .env.prod (default)
#   ./scripts/pre-deploy-check.sh .env.dev     Check specific env file
# =============================================================================

set -euo pipefail

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

pass() { echo -e "  ${GREEN}✓${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=$((FAIL + 1)); }
warn() { echo -e "  ${YELLOW}!${NC} $1"; WARN=$((WARN + 1)); }

ENV_FILE="${1:-.env.prod}"

echo ""
echo "=============================================="
echo " Bloom v2 Pre-deployment Checks"
echo " Env file: $ENV_FILE"
echo "=============================================="
echo ""

# =============================================================================
# 1. Environment File Checks
# =============================================================================
echo -e "${BLUE}[1/6] Environment File${NC}"

if [[ -f "$ENV_FILE" ]]; then
  pass "$ENV_FILE exists"
else
  fail "$ENV_FILE not found — run scripts/setup-env.sh first"
  echo ""
  echo -e "${RED}Cannot continue without env file. Exiting.${NC}"
  exit 1
fi

# Check permissions (should be 600)
PERMS=$(stat -c "%a" "$ENV_FILE" 2>/dev/null || stat -f "%Lp" "$ENV_FILE" 2>/dev/null)
if [[ "$PERMS" == "600" ]]; then
  pass "File permissions are 600 (owner read/write only)"
else
  fail "File permissions are $PERMS — should be 600. Run: chmod 600 $ENV_FILE"
fi

# Check for trial credentials
if grep -q "TRIAL/TEST CREDENTIALS" "$ENV_FILE" 2>/dev/null; then
  warn "$ENV_FILE contains TRIAL credentials — not suitable for production"
fi

# =============================================================================
# 2. Required Environment Variables
# =============================================================================
echo ""
echo -e "${BLUE}[2/6] Required Environment Variables${NC}"

REQUIRED_VARS=(
  JWT_SECRET
  ANON_KEY
  SERVICE_ROLE_KEY
  POSTGRES_PASSWORD
  POSTGRES_HOST
  POSTGRES_DB
  MINIO_ROOT_USER
  MINIO_ROOT_PASSWORD
  MINIO_DATA_PATH
  DASHBOARD_USERNAME
  DASHBOARD_PASSWORD
  SUPABASE_COOKIE_NAME
  SITE_URL
  NEXT_PUBLIC_SUPABASE_URL
  API_EXTERNAL_URL
  DOMAIN_MAIN
  DB_ENC_KEY
  VAULT_ENC_KEY
  SUPAVISOR_ENC_KEY
  SECRET_KEY_BASE
  BLOOMMCP_API_KEY
  LOGFLARE_PUBLIC_ACCESS_TOKEN
  LOGFLARE_PRIVATE_ACCESS_TOKEN
)

for var in "${REQUIRED_VARS[@]}"; do
  val=$(grep "^${var}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
  if [[ -z "$val" ]]; then
    fail "$var is missing or empty"
  elif [[ "$val" == "GENERATE_FROM_JWT_SECRET" ]]; then
    fail "$var still has placeholder value — generate it from JWT_SECRET"
  elif [[ "$val" == *"your-"* || "$val" == *"CHANGE_TO"* || "$val" == *"changeme"* ]]; then
    fail "$var contains placeholder value: $val"
  else
    pass "$var is set"
  fi
done

# =============================================================================
# 3. Git Security
# =============================================================================
echo ""
echo -e "${BLUE}[3/6] Git Security${NC}"

# Check .gitignore
if grep -q "\.env" .gitignore 2>/dev/null; then
  pass ".env patterns in .gitignore"
else
  fail ".env files not in .gitignore — secrets could be committed!"
fi

# Check no secrets in tracked files
SECRET_PATTERNS="POSTGRES_PASSWORD=|JWT_SECRET=|MINIO_ROOT_PASSWORD=|SERVICE_ROLE_KEY=ey|OPENAI_API_KEY=sk-"
LEAKED=$(git grep -l -E "$SECRET_PATTERNS" -- ':!*.example' ':!*.sh' ':!*.md' ':!*.py' ':!CLAUDE.md' ':!.claude/' 2>/dev/null | head -5 || true)
if [[ -n "$LEAKED" ]]; then
  fail "Possible secrets found in tracked files:"
  echo "$LEAKED" | while read -r f; do echo "       - $f"; done
else
  pass "No secrets detected in tracked git files"
fi

# Check env file is not tracked
if git ls-files --error-unmatch "$ENV_FILE" &>/dev/null; then
  fail "$ENV_FILE is tracked by git! Run: git rm --cached $ENV_FILE"
else
  pass "$ENV_FILE is not tracked by git"
fi

# =============================================================================
# 4. Docker Compose Validation
# =============================================================================
echo ""
echo -e "${BLUE}[4/6] Docker Compose${NC}"

COMPOSE_FILE="docker-compose.prod.yml"
if [[ -f "$COMPOSE_FILE" ]]; then
  pass "$COMPOSE_FILE exists"
else
  fail "$COMPOSE_FILE not found"
fi

# Check for :latest tags
if grep -qE "image:.*:latest" "$COMPOSE_FILE" 2>/dev/null; then
  fail "Found :latest image tags in $COMPOSE_FILE — use pinned versions"
else
  pass "All images use pinned versions"
fi

# Check Docker is available
if command -v docker &>/dev/null; then
  pass "Docker is installed"
  if docker info &>/dev/null; then
    pass "Docker daemon is running"
  else
    fail "Docker daemon is not running or not accessible"
  fi
else
  fail "Docker is not installed"
fi

# =============================================================================
# 5. Directory & Storage Checks
# =============================================================================
echo ""
echo -e "${BLUE}[5/6] Directories & Storage${NC}"

MINIO_PATH=$(grep "^MINIO_DATA_PATH=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
if [[ -n "$MINIO_PATH" && "$MINIO_PATH" != "./"* ]]; then
  if [[ -d "$MINIO_PATH" ]]; then
    pass "MinIO data path exists: $MINIO_PATH"

    # Check available space
    AVAIL_KB=$(df -k "$MINIO_PATH" 2>/dev/null | tail -1 | awk '{print $4}')
    if [[ -n "$AVAIL_KB" ]]; then
      AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
      if (( AVAIL_GB > 100 )); then
        pass "Available disk space: ${AVAIL_GB}GB"
      else
        warn "Only ${AVAIL_GB}GB available at $MINIO_PATH — may be insufficient for 7TB data"
      fi
    fi
  else
    fail "MinIO data path does not exist: $MINIO_PATH"
    echo "       Run: sudo mkdir -p $MINIO_PATH && sudo chown \$USER:\$USER $MINIO_PATH"
  fi
else
  pass "MinIO using local path (dev mode)"
fi

# Check backup directory
if [[ -d "/data/bloom/backups" ]]; then
  pass "Backup directory exists: /data/bloom/backups"
elif [[ -d "/var/lib/bloom/backups" ]]; then
  pass "Backup directory exists: /var/lib/bloom/backups"
else
  warn "No backup directory found — create /data/bloom/backups for DB dumps"
fi

# =============================================================================
# 6. Port Availability
# =============================================================================
echo ""
echo -e "${BLUE}[6/6] Port Availability${NC}"

check_port() {
  local port=$1
  local service=$2
  if ss -tlnp 2>/dev/null | grep -q ":${port} " || lsof -i ":${port}" &>/dev/null; then
    warn "Port $port ($service) is already in use"
  else
    pass "Port $port ($service) is available"
  fi
}

check_port 80 "Nginx HTTP"
check_port 443 "Nginx HTTPS"
check_port 55323 "Supabase Studio"

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "=============================================="
TOTAL=$((PASS + FAIL + WARN))
echo -e " Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}, ${YELLOW}$WARN warnings${NC} / $TOTAL checks"
echo "=============================================="
echo ""

if (( FAIL > 0 )); then
  echo -e "${RED}Pre-deployment checks FAILED. Fix the issues above before deploying.${NC}"
  exit 1
else
  if (( WARN > 0 )); then
    echo -e "${YELLOW}Pre-deployment checks PASSED with warnings. Review before deploying.${NC}"
  else
    echo -e "${GREEN}All pre-deployment checks PASSED. Ready to deploy!${NC}"
  fi
  exit 0
fi
