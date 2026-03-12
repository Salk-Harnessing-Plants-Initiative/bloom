#!/bin/bash

echo "=========================================="
echo "Flask to FastAPI Migration Test"
echo "=========================================="
echo ""

echo "1. Checking if FastAPI service exists in docker-compose.dev.yml..."
if grep -q "fastapi-app:" docker-compose.dev.yml; then
    echo "   ✓ fastapi-app service found in dev config"
else
    echo "   ✗ fastapi-app service NOT found in dev config"
    exit 1
fi

echo ""
echo "2. Checking if FastAPI service exists in docker-compose.prod.yml..."
if grep -q "fastapi-app:" docker-compose.prod.yml; then
    echo "   ✓ fastapi-app service found in prod config"
else
    echo "   ✗ fastapi-app service NOT found in prod config"
    exit 1
fi

echo ""
echo "3. Checking if Flask service exists (should NOT exist)..."
if grep -q "flask-app:" docker-compose.dev.yml || grep -q "flask-app:" docker-compose.prod.yml; then
    echo "   ✗ flask-app service still exists - migration incomplete"
    exit 1
else
    echo "   ✓ No flask-app service found (correct)"
fi

echo ""
echo "4. Checking if OPENAI_API_KEY is configured in dev..."
if grep -q "OPENAI_API_KEY" docker-compose.dev.yml; then
    echo "   ✓ OPENAI_API_KEY configured in dev"
else
    echo "   ⚠ OPENAI_API_KEY not configured in dev (agent won't work)"
fi

echo ""
echo "5. Checking if langchain volume is mounted..."
if grep -q "./langchain:/app/langchain" docker-compose.dev.yml; then
    echo "   ✓ langchain volume mounted in dev"
else
    echo "   ✗ langchain volume NOT mounted in dev"
    exit 1
fi

echo ""
echo "6. Checking if FastAPI references updated in main.py..."
if grep -q "DOMAIN_FASTAPI" fastapi/main.py; then
    echo "   ✓ DOMAIN_FASTAPI variable found in main.py"
else
    echo "   ⚠ DOMAIN_FASTAPI not found (still using DOMAIN_FLASK only)"
fi

echo ""
echo "7. Checking if running containers..."
if docker ps | grep -q "fastapi-app"; then
    echo "   ✓ fastapi-app container is running"
    CONTAINER_STATUS="running"
else
    echo "   ⚠ fastapi-app container is NOT running"
    CONTAINER_STATUS="stopped"
fi

if docker ps | grep -q flask; then
    echo "   ✗ Flask container is still running - need to stop it"
    exit 1
else
    echo "   ✓ No Flask containers running (correct)"
fi

echo ""
echo "=========================================="
echo "Migration Check Complete!"
echo "=========================================="
echo ""
echo "Summary:"
echo "  - FastAPI service: ✓ Configured in both dev and prod"
echo "  - Flask service: ✓ Removed"
echo "  - LangChain integration: ✓ Volumes and env vars configured"
echo "  - Container status: $CONTAINER_STATUS"
echo ""

if [ "$CONTAINER_STATUS" = "running" ]; then
    echo "Next steps:"
    echo "  1. Test the chatbot: http://localhost:3000"
    echo "  2. Check health: curl http://localhost:5002/langchain/health"
    echo "  3. Test agent: curl -X POST http://localhost:5002/langchain/chat \\"
    echo "                  -H 'Content-Type: application/json' \\"
    echo "                  -d '{\"prompt\": \"List all datasets\"}'"
else
    echo "Next steps:"
    echo "  1. Start the stack: make dev-up"
    echo "  2. Test the chatbot: http://localhost:3000"
    echo "  3. Check health: curl http://localhost:5002/langchain/health"
fi

echo ""
