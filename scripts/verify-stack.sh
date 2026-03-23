#!/usr/bin/env bash
# =============================================================================
# Bloom v2 - Post-deployment Stack Verification
#
# Usage:
#   ./scripts/verify-stack.sh          Verify all services
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

pass() { echo -e "  ${GREEN}✓${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=============================================="
echo " Bloom v2 Stack Verification"
echo " $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "=============================================="
echo ""

# =============================================================================
# 1. Container Status
# =============================================================================
echo -e "${BLUE}[1/3] Container Status${NC}"

EXPECTED_SERVICES=(
  "bloom-nginx"
  "bloom-web"        # may be auto-named by compose
  "langchain-agent"  # may be auto-named
  "bloommcp"         # may be auto-named
  "supabase-kong"
  "db-prod"
  "supabase-minio"
  "supabase-storage"
  "supabase-pooler"
  "studio"
  "supabase-imgproxy-prod"
  "supabase-meta-prod"
  "supabase-analytics-prod"
)

# Get running containers
RUNNING=$(docker ps --format '{{.Names}}' 2>/dev/null || true)

if [[ -z "$RUNNING" ]]; then
  fail "No Docker containers are running!"
  echo ""
  echo -e "${RED}Stack is not running. Start with: make prod-up${NC}"
  exit 1
fi

RUNNING_COUNT=$(echo "$RUNNING" | wc -l | tr -d ' ')
echo "  Found $RUNNING_COUNT running containers"
echo ""

for svc in "${EXPECTED_SERVICES[@]}"; do
  # Check exact match or partial match (compose project prefix)
  if echo "$RUNNING" | grep -qi "$svc"; then
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "no-healthcheck")
    if [[ "$STATUS" == "healthy" ]]; then
      pass "$svc — running (healthy)"
    elif [[ "$STATUS" == "no-healthcheck" || "$STATUS" == "" ]]; then
      pass "$svc — running"
    elif [[ "$STATUS" == "unhealthy" ]]; then
      fail "$svc — running but UNHEALTHY"
    else
      pass "$svc — running ($STATUS)"
    fi
  else
    fail "$svc — NOT FOUND"
  fi
done

# =============================================================================
# 2. Service Health Probes
# =============================================================================
echo ""
echo -e "${BLUE}[2/3] Service Health Probes${NC}"

# Database
if docker exec db-prod pg_isready -U supabase_admin -q 2>/dev/null; then
  pass "PostgreSQL — accepting connections"
else
  fail "PostgreSQL — not ready"
fi

# Kong Gateway (internal)
if docker exec supabase-kong curl -sf http://localhost:8000/ >/dev/null 2>&1; then
  pass "Kong Gateway — responding on :8000"
else
  # Kong may return non-200 on / but still be alive
  if docker exec supabase-kong curl -sf -o /dev/null -w '%{http_code}' http://localhost:8000/ 2>/dev/null | grep -qE "2[0-9]{2}|4[0-9]{2}"; then
    pass "Kong Gateway — responding on :8000"
  else
    fail "Kong Gateway — not responding"
  fi
fi

# Nginx (host-level)
if curl -sf -o /dev/null -w '%{http_code}' http://localhost:80/test/ 2>/dev/null | grep -q "200"; then
  pass "Nginx — responding on :80"
elif curl -sf http://localhost:80/ >/dev/null 2>&1; then
  pass "Nginx — responding on :80"
else
  fail "Nginx — not responding on :80"
fi

# Bloom Web
BLOOM_WEB_CONTAINER=$(echo "$RUNNING" | grep -i "bloom.*web" | head -1 || true)
if [[ -n "$BLOOM_WEB_CONTAINER" ]]; then
  if docker exec "$BLOOM_WEB_CONTAINER" curl -sf http://localhost:3000/ >/dev/null 2>&1; then
    pass "Bloom Web — responding on :3000"
  else
    # Next.js might not have curl, try wget
    if docker exec "$BLOOM_WEB_CONTAINER" wget -qO- http://localhost:3000/ >/dev/null 2>&1; then
      pass "Bloom Web — responding on :3000"
    else
      fail "Bloom Web — not responding on :3000"
    fi
  fi
else
  fail "Bloom Web — container not found"
fi

# LangChain Agent
LANGCHAIN_CONTAINER=$(echo "$RUNNING" | grep -i "langchain" | head -1 || true)
if [[ -n "$LANGCHAIN_CONTAINER" ]]; then
  if docker exec "$LANGCHAIN_CONTAINER" curl -sf http://localhost:5002/health >/dev/null 2>&1; then
    pass "LangChain Agent — healthy on :5002"
  else
    fail "LangChain Agent — health check failed"
  fi
else
  fail "LangChain Agent — container not found"
fi

# MinIO
if docker exec supabase-minio curl -sf http://localhost:9000/minio/health/live >/dev/null 2>&1; then
  pass "MinIO — healthy on :9000"
else
  fail "MinIO — health check failed"
fi

# Studio (if exposed)
if curl -sf -o /dev/null http://localhost:55323/ 2>/dev/null; then
  pass "Supabase Studio — responding on :55323"
else
  fail "Supabase Studio — not responding on :55323"
fi

# =============================================================================
# 3. MinIO Buckets
# =============================================================================
echo ""
echo -e "${BLUE}[3/3] MinIO Bucket Verification${NC}"

EXPECTED_BUCKETS=(
  "images"
  "species_illustrations"
  "tus-files"
  "video"
  "scrna"
  "experiment-log-images"
  "plates-images"
  "plate-blob-storage"
  "bloom-storage"
)

# Try to list buckets via mc inside the minio-init container, or directly
MC_OUTPUT=$(docker exec supabase-minio sh -c 'mc alias set local http://localhost:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD >/dev/null 2>&1 && mc ls local --json 2>/dev/null' 2>/dev/null || true)

if [[ -n "$MC_OUTPUT" ]]; then
  for bucket in "${EXPECTED_BUCKETS[@]}"; do
    if echo "$MC_OUTPUT" | grep -q "\"key\":\"${bucket}/\""; then
      pass "Bucket: $bucket"
    else
      fail "Bucket missing: $bucket"
    fi
  done
else
  echo "  (Could not verify buckets — mc not available in MinIO container)"
  echo "  Verify manually: mc ls <alias>/"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "=============================================="
TOTAL=$((PASS + FAIL))
echo -e " Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC} / $TOTAL checks"
echo "=============================================="
echo ""

if (( FAIL > 0 )); then
  echo -e "${RED}Stack verification FAILED. Check the services above.${NC}"
  echo ""
  echo "Troubleshooting:"
  echo "  docker ps -a                          # See all containers (including stopped)"
  echo "  docker logs <container-name>          # Check logs for failed service"
  echo "  make prod-logs                        # Tail all service logs"
  exit 1
else
  echo -e "${GREEN}All stack checks PASSED!${NC}"
  exit 0
fi
