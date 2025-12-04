# Implementation Tasks: Agentic AI Integration

## Phase 1: Core Agent System (MVP)

### 1. Flask → FastAPI Migration

- [ ] 1.1 Create FastAPI project structure

  - [ ] Create `fastapi/` directory
  - [ ] Set up `fastapi/main.py` with FastAPI app
  - [ ] Create `fastapi/config.py` for configuration
  - [ ] Create `fastapi/models/` for Pydantic models
  - [ ] Create `fastapi/services/` for business logic
  - [ ] Create `fastapi/routes/` for API endpoints

- [ ] 1.2 Port VideoWriter to FastAPI

  - [ ] Review `flask/videowriter.py` implementation
  - [ ] Create `fastapi/services/video_writer.py`
  - [ ] Convert to async pattern
  - [ ] Port S3/MinIO integration (boto3)
  - [ ] Port image processing logic
  - [ ] Create Pydantic model for VideoGenerationRequest

- [ ] 1.3 Migrate video generation endpoints

  - [ ] Create `/api/videos/generate` endpoint in FastAPI
  - [ ] Port JWT authentication middleware
  - [ ] Test video generation locally
  - [ ] Verify S3 integration works

- [ ] 1.4 Update Docker configuration

  - [ ] Create `fastapi/Dockerfile`
  - [ ] Update `docker-compose.dev.yml` (add FastAPI, remove Flask)
  - [ ] Update `docker-compose.prod.yml` (add FastAPI, remove Flask)
  - [ ] Update environment variables
  - [ ] Test docker-compose builds

- [ ] 1.5 Update Nginx routing

  - [ ] Modify nginx config to route `/api/*` to FastAPI
  - [ ] Add route for `/ai/mcp` to FastAPI
  - [ ] Test routing in development
  - [ ] Verify in production

- [ ] 1.6 Verify and remove Flask
  - [ ] Test all video generation functionality
  - [ ] Verify Next.js frontend works with new API
  - [ ] Remove Flask service from docker-compose
  - [ ] Delete `flask/` directory
  - [ ] Update documentation

### 2. FastMCP Integration

- [ ] 2.1 Install and configure FastMCP

  - [ ] Add `fastmcp` to requirements.txt
  - [ ] Create `fastapi/mcp_server.py`
  - [ ] Initialize FastMCP instance
  - [ ] Mount MCP app at `/ai/mcp`
  - [ ] Test MCP server responds

- [ ] 2.2 Create core MCP tools (generalizable)

  - [ ] `@mcp.tool()` for `query_database()` - any PostgreSQL table
  - [ ] `@mcp.tool()` for `execute_job()` - any HTTP endpoint
  - [ ] `@mcp.tool()` for `check_job_status()` - generic status check
  - [ ] `@mcp.tool()` for `read_file()` - file operations
  - [ ] `@mcp.tool()` for `search_files()` - pattern matching

- [ ] 2.3 Test FastMCP server
  - [ ] Verify MCP tools are exposed at `/ai/mcp`
  - [ ] Test tool calling from sample agent
  - [ ] Validate JSON schemas
  - [ ] Test error handling

### 3. External MCP Servers

- [ ] 3.1 Integrate Supabase MCP

  - [ ] Install Supabase MCP server
  - [ ] Configure connection to PostgreSQL
  - [ ] Test database queries via MCP
  - [ ] Document configuration

- [ ] 3.2 Integrate Filesystem MCP

  - [ ] Install official Filesystem MCP server
  - [ ] Configure access controls and allowed directories
  - [ ] Test file operations
  - [ ] Document configuration

- [ ] 3.3 Integrate Git MCP
  - [ ] Install official Git MCP server
  - [ ] Configure repository access
  - [ ] Test git operations
  - [ ] Document configuration

### 4. Agent Framework (LangChain + LangGraph)

- [ ] 4.1 Install dependencies

  - [ ] Add `langgraph` to requirements.txt
  - [ ] Add `langchain-community` to requirements.txt
  - [ ] Add `langchain-core` to requirements.txt
  - [ ] Install in Docker image
  - [ ] Verify imports work

- [ ] 4.2 Create agent orchestration package

  - [ ] Create `packages/bloom-agents/`
  - [ ] Set up package structure
  - [ ] Create `agent_state.py` (TypedDict definitions)
  - [ ] Create `workflow.py` (LangGraph workflow)
  - [ ] Create `tools.py` (LangChain tool wrappers)

- [ ] 4.3 Implement LangGraph workflow

  - [ ] Define `AgentState` TypedDict
  - [ ] Create `classify_query` node (determine query type)
  - [ ] Create `execute_database_query` node
  - [ ] Create `execute_job` node
  - [ ] Create `summarize_results` node
  - [ ] Add conditional edges for routing
  - [ ] Compile workflow to runnable agent
  - [ ] Test workflow execution

- [ ] 4.4 Integrate LangChain models
  - [ ] Configure Ollama connection
  - [ ] Create model wrapper for Qwen2.5 14B
  - [ ] Create model wrapper for Qwen2.5-Coder 7B
  - [ ] Create model wrapper for Llama 3.2 3B
  - [ ] Test model inference
  - [ ] Handle model errors gracefully

### 5. Model Strategy & Routing

- [ ] 5.1 Set up Ollama

  - [ ] Add Ollama to docker-compose (or document separate setup)
  - [ ] Pull required models (Qwen2.5 14B, Qwen2.5-Coder 7B, Llama 3.2 3B)
  - [ ] Configure model serving
  - [ ] Test model availability
  - [ ] Document model installation

- [ ] 5.2 Implement model router

  - [ ] Create `fastapi/services/model_router.py`
  - [ ] Implement `select_model(task_type, complexity)` function
  - [ ] Add configuration for model selection
  - [ ] Add fallback logic for model failures
  - [ ] Test router with different task types

- [ ] 5.3 Add optional paid model support
  - [ ] Add OpenRouter configuration (optional)
  - [ ] Add Anthropic API configuration (optional)
  - [ ] Add environment variable controls
  - [ ] Test with user-provided API keys
  - [ ] Document paid model setup

### 6. Pydantic Models

- [ ] 6.1 Create request/response models

  - [ ] Create `fastapi/models/requests.py`
  - [ ] Define `AgentQueryRequest` model
  - [ ] Define `JobRequest` model
  - [ ] Define `DatabaseQueryRequest` model
  - [ ] Define `VideoGenerationRequest` model
  - [ ] Add validators for each model

- [ ] 6.2 Create response models
  - [ ] Create `fastapi/models/responses.py`
  - [ ] Define `AgentQueryResponse` model
  - [ ] Define `JobResponse` model
  - [ ] Define `DatabaseQueryResponse` model
  - [ ] Add examples for auto-docs

### 7. Testing & Validation

- [ ] 7.1 Unit tests

  - [ ] Test video generation functions
  - [ ] Test MCP tool functions
  - [ ] Test model router
  - [ ] Test database query builder
  - [ ] Test Pydantic model validation

- [ ] 7.2 Integration tests

  - [ ] Test full agent workflow (query → execute → respond)
  - [ ] Test video generation end-to-end
  - [ ] Test MCP tool calling
  - [ ] Test multi-step queries
  - [ ] Test error recovery

- [ ] 7.3 Performance testing
  - [ ] Benchmark FastAPI vs Flask
  - [ ] Test concurrent requests
  - [ ] Measure model inference latency
  - [ ] Profile memory usage
  - [ ] Document performance metrics

## Phase 2: Skills & Guardrails System

### 8. Guardrails AI Integration

- [ ] 8.1 Install and configure Guardrails AI

  - [ ] Add `guardrails-ai` to requirements.txt
  - [ ] Install in Docker image
  - [ ] Configure Guardrails Hub access
  - [ ] Test basic guard functionality

- [ ] 8.2 Integrate pre-built validators

  - [ ] Add `DetectPII` guard
  - [ ] Add `ValidSQL` guard
  - [ ] Add `ToxicLanguage` guard
  - [ ] Add `ValidLength` guard
  - [ ] Add `ValidURL` guard
  - [ ] Test each validator

- [ ] 8.3 Create custom Bloom validators

  - [ ] Create `packages/bloom-skills/custom_guards.py`
  - [ ] Implement `validate_species` guard
  - [ ] Implement `validate_gene_id` guard (example for biology)
  - [ ] Implement `check_resource_quota` guard
  - [ ] Implement `validate_table_name` guard
  - [ ] Test custom validators

- [ ] 8.4 Create composite guards
  - [ ] Create `database_guard` (SQL injection + table validation)
  - [ ] Create `query_guard` (PII + toxic language + length)
  - [ ] Create `job_guard` (resource quota + endpoint validation)
  - [ ] Test guard composition

### 9. Skill System

- [ ] 9.1 Create skill framework

  - [ ] Create `packages/bloom-skills/`
  - [ ] Implement `@skill()` decorator
  - [ ] Add model selection per skill
  - [ ] Add fallback model support
  - [ ] Add timeout handling
  - [ ] Integrate with Guardrails AI

- [ ] 9.2 Implement core skills

  - [ ] `query_database` skill with Guardrails validation
  - [ ] `execute_job` skill with resource limits
  - [ ] `read_file` skill with access controls
  - [ ] `search_files` skill with pattern validation
  - [ ] `generate_code` skill for notebooks

- [ ] 9.3 Test skills
  - [ ] Test each skill individually
  - [ ] Test guardrail enforcement
  - [ ] Test model selection
  - [ ] Test fallback logic
  - [ ] Measure success rates

### 10. API Endpoints with Guards

- [ ] 10.1 Create agent query endpoint

  - [ ] Implement `/api/agent/query` with Pydantic + Guardrails
  - [ ] Add input validation (PII, toxic language)
  - [ ] Add output sanitization
  - [ ] Test end-to-end
  - [ ] Document usage

- [ ] 10.2 Create database query endpoint

  - [ ] Implement `/api/database/query` with guards
  - [ ] Add SQL injection prevention
  - [ ] Add table existence validation
  - [ ] Add permission checks
  - [ ] Test with various queries

- [ ] 10.3 Create job execution endpoint
  - [ ] Implement `/api/jobs/submit` with guards
  - [ ] Add resource quota checks
  - [ ] Add user approval workflow (if needed)
  - [ ] Test job submission
  - [ ] Test status monitoring

### 11. Testing Framework

- [ ] 11.1 Create agent test suite

  - [ ] Define standard test queries
  - [ ] Implement test runner
  - [ ] Add success rate tracking
  - [ ] Generate test reports

- [ ] 11.2 Test guardrails

  - [ ] Test PII detection and removal
  - [ ] Test SQL injection prevention
  - [ ] Test resource quota enforcement
  - [ ] Test permission checks
  - [ ] Verify audit logging

- [ ] 11.3 Test complete workflows
  - [ ] Test end-to-end query flows
  - [ ] Test multi-turn conversations
  - [ ] Test error recovery
  - [ ] Achieve 80%+ success rate on standard queries

## Phase 3: Optional Integrations (Plugin System)

### 12. Plugin Architecture

- [ ] 12.1 Design plugin system

  - [ ] Create `fastapi/integrations/` directory
  - [ ] Define plugin interface
  - [ ] Create plugin loader
  - [ ] Add configuration system

- [ ] 12.2 Create configuration management
  - [ ] Support environment variables for endpoints
  - [ ] Support config files (YAML/JSON)
  - [ ] Add validation for plugin configs
  - [ ] Document configuration format

### 13. Example Integrations (Bloom-Specific)

- [ ] 13.1 sleap-roots integration (EXAMPLE)

  - [ ] Create `fastapi/integrations/sleap_roots.py`
  - [ ] Implement `run_sleap_roots_pipeline()` function
  - [ ] Add configuration for RunAI endpoint
  - [ ] Test with sample data
  - [ ] Document usage and configuration

- [ ] 13.2 GAPIT integration (EXAMPLE)

  - [ ] Create `fastapi/integrations/gapit.py`
  - [ ] Implement `run_gapit_gwas()` function
  - [ ] Add configuration for GAPIT endpoint
  - [ ] Test with sample data
  - [ ] Document usage and configuration

- [ ] 13.3 Plugin documentation
  - [ ] Create plugin development guide
  - [ ] Provide template plugin code
  - [ ] Explain configuration system
  - [ ] Show complete example
  - [ ] Document how users can add custom plugins

## Phase 4: Advanced Capabilities (Optional/Future)

### 14. Vector Database (pgvector)

- [ ] 14.1 Install and configure pgvector

  - [ ] Add pgvector extension to PostgreSQL
  - [ ] Create embedding tables
  - [ ] Add indexes for vector search
  - [ ] Test vector operations

- [ ] 14.2 Implement embedding generation
  - [ ] Choose embedding model
  - [ ] Create embedding service
  - [ ] Batch process existing data
  - [ ] Set up incremental updates

### 15. RAG Implementation

- [ ] 15.1 Create RAG service

  - [ ] Implement document retrieval
  - [ ] Add context injection to prompts
  - [ ] Test retrieval quality
  - [ ] Optimize retrieval performance

- [ ] 15.2 Index documentation
  - [ ] Embed pipeline documentation
  - [ ] Embed API documentation
  - [ ] Embed domain-specific knowledge (optional)
  - [ ] Test semantic search quality

### 16. Marimo Integration

- [ ] 16.1 Set up marimo

  - [ ] Add marimo to requirements
  - [ ] Configure marimo server
  - [ ] Create notebook templates

- [ ] 16.2 Implement notebook generation
  - [ ] Create code generation skill
  - [ ] Generate visualization notebooks
  - [ ] Test notebook execution
  - [ ] Integrate with frontend

### 17. Conversation Memory

- [ ] 17.1 Integrate Memory MCP server

  - [ ] Install and configure Memory MCP
  - [ ] Test knowledge graph storage
  - [ ] Implement context retrieval

- [ ] 17.2 Implement multi-turn conversations
  - [ ] Maintain conversation state
  - [ ] Inject relevant context
  - [ ] Test conversation continuity

## Documentation & Deployment

### 18. Documentation

- [ ] 18.1 Update README

  - [ ] Document new architecture
  - [ ] Add agent capabilities section
  - [ ] Update setup instructions
  - [ ] Add model configuration guide
  - [ ] Add troubleshooting section

- [ ] 18.2 API documentation

  - [ ] Ensure FastAPI auto-docs work at `/docs`
  - [ ] Document MCP tools at `/ai/mcp`
  - [ ] Document agent endpoints
  - [ ] Add usage examples
  - [ ] Add Pydantic model schemas

- [ ] 18.3 Developer guide
  - [ ] Document agent workflow
  - [ ] Explain skill system
  - [ ] Explain Guardrails AI integration
  - [ ] Document plugin development
  - [ ] Add architecture diagrams

### 19. Deployment

- [ ] 19.1 Development deployment

  - [ ] Deploy to development environment
  - [ ] Test all functionality
  - [ ] Monitor logs for errors
  - [ ] Performance testing
  - [ ] Fix any issues

- [ ] 19.2 Production deployment

  - [ ] Deploy to production
  - [ ] Set up monitoring (LangSmith optional)
  - [ ] Configure alerts
  - [ ] Monitor costs and usage
  - [ ] Set up backup/recovery

- [ ] 19.3 User onboarding
  - [ ] Create user guide
  - [ ] Add example queries
  - [ ] Provide tutorials
  - [ ] Collect feedback
  - [ ] Iterate based on feedback

## Validation

- [ ] Run `openspec validate add-agentic-ai-integration --strict`
- [ ] Ensure all tasks are completed before archiving
- [ ] Verify all success criteria from proposal are met
