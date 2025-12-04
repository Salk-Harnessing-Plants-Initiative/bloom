# Design Document: Agentic AI Integration

## Context

Bloom is a full-stack web application for biological/scientific data management and visualization. Currently, users must manually navigate UIs or write code to query data and run analysis pipelines. This proposal adds agentic AI capabilities to enable natural language interaction with the system.

**Key Constraints:**

- Must be generalizable (not specific to biology)
- Must work with free/open-source models (no required API keys)
- Must integrate with existing Next.js frontend and PostgreSQL database
- Must support extensibility for domain-specific features

**Stakeholders:**

- Core Team: Needs generalizable, maintainable system
- Elizabeth: Needs pipeline orchestration (sleap-roots, GAPIT)
- External Users: Need ability to add custom integrations

## Goals / Non-Goals

### Goals

1. **Enable Natural Language Queries**: Users can ask questions like "Show me experiments for species X"
2. **Generalizable Core**: Works for any scientific domain, not just biology
3. **Free Models First**: Prioritize Ollama-based free models
4. **Extensible**: Plugin system for custom integrations
5. **Production-Ready**: Proper validation, error handling, monitoring
6. **Multi-Language Support**: Agent orchestrates entire polyglot codebase (Python, TypeScript, etc.)

### Non-Goals

1. **Not replacing existing UI**: Agent complements, doesn't replace Next.js frontend
2. **Not biology-specific in core**: Domain features are optional plugins
3. **Not requiring paid APIs**: Free models are default, paid models optional
4. **Not a chatbot**: Focused on data operations, not general conversation

## Decisions

### Decision 1: Flask → FastAPI Complete Migration

**Choice**: Replace Flask entirely with FastAPI

**Rationale:**

- FastAPI provides native async support (better for AI agent workflows)
- Pydantic integration for automatic request/response validation
- FastMCP integrates seamlessly with FastAPI
- Auto-generated API documentation at `/docs`
- Better performance for concurrent requests
- Cleaner architecture (one API service instead of two)

**Alternatives Considered:**

- **Keep Flask + Add FastAPI**: More complex architecture, two services to maintain
- **Stick with Flask only**: No FastMCP support, harder async patterns

**Trade-offs:**

- ✅ Cleaner long-term architecture
- ✅ Better async support
- ❌ Migration effort required
- ❌ Breaking change (minimal frontend impact expected)

### Decision 2: LangGraph + LangChain for Agent Framework

**Choice**: Use LangGraph for workflow orchestration + LangChain for model/tool wrappers

**Rationale:**

- LangGraph provides state machine pattern perfect for multi-step workflows
- Conditional branching matches our "classify → route → execute" pattern
- LangChain provides model wrappers for Ollama, OpenAI, Anthropic
- Built-in checkpointing for long-running operations
- Production-ready with LangSmith monitoring (optional)
- Large community and ecosystem

**Alternatives Considered:**

- **Cline**: IDE-focused, not suitable for backend service
- **Custom framework**: More control but reinventing wheel, maintenance burden
- **LangChain only**: Less structured than LangGraph for complex workflows

**Trade-offs:**

- ✅ Battle-tested framework
- ✅ Strong ecosystem
- ✅ Good documentation
- ❌ Learning curve for team
- ❌ Dependency on LangChain ecosystem

### Decision 3: Guardrails AI for Validation

**Choice**: Use Guardrails AI library + custom validators

**Rationale:**

- 50+ pre-built validators (PII, SQL injection, toxic content, etc.)
- Don't reinvent the wheel for common validations
- Easy to add custom domain-specific validators
- Production-ready, used by many companies
- Clear separation: Pydantic for structure, Guardrails for content

**Alternatives Considered:**

- **Custom validation from scratch**: More control but lots of work
- **NeMo Guardrails**: More complex, overkill for our needs
- **LangChain validators only**: Limited, not comprehensive enough

**Trade-offs:**

- ✅ Leverages existing battle-tested validators
- ✅ Easy to extend with custom validators
- ✅ Clear validation patterns
- ❌ Additional dependency
- ❌ Learning curve for Guardrails API

### Decision 4: Free Models via Ollama as Default

**Choice**: Prioritize free Ollama models, support paid models as optional

**Rationale:**

- No API costs for users
- Full data privacy (runs locally)
- No rate limits
- Modern free models are highly capable (Qwen2.5 14B ≈ GPT-3.5)
- Users can optionally add paid models if desired

**Models Selected:**

- **Qwen2.5 14B**: Best free reasoning model
- **Qwen2.5-Coder 7B**: Best free code generation
- **Llama 3.2 3B**: Ultra-fast for simple tasks
- **Gemma 2 9B**: Fast, good for data extraction

**Alternatives Considered:**

- **Paid models only**: Excludes users without API keys
- **Smaller models only**: Less capable for complex reasoning
- **Larger models**: Too resource-intensive for most users

**Trade-offs:**

- ✅ No cost barrier for users
- ✅ Privacy and control
- ✅ Predictable performance
- ❌ Requires local compute resources
- ❌ Slower than hosted APIs

### Decision 5: Generic Job Execution Interface

**Choice**: Core system has generic `execute_job(endpoint, params)`, not pipeline-specific tools

**Rationale:**

- Team wants generalizability
- Works with ANY external HTTP service (Argo, Kubernetes Jobs, cloud functions)
- Bloom's pipelines become first-party plugins/examples
- Other users can add their own integrations
- Clean separation of concerns

**Pattern:**

```python
# Core (generalizable)
@mcp.tool()
def execute_job(endpoint: str, params: dict) -> dict:
    """Trigger any HTTP service"""

# Optional plugin (Bloom-specific example)
@mcp.tool()
def run_sleap_roots(dataset_id: str, params: dict) -> dict:
    """Convenience wrapper using execute_job"""
    return execute_job(
        endpoint=config["sleap_roots_endpoint"],
        params={...}
    )
```

**Alternatives Considered:**

- **Hard-code pipelines**: Not generalizable, rejected by team
- **No pipeline support**: Doesn't meet Elizabeth's needs
- **Complex plugin system upfront**: Overengineering for Phase 1

**Trade-offs:**

- ✅ Generalizable core
- ✅ Satisfies both requirements (general + specific)
- ✅ Extensible for any user
- ❌ Slight indirection for Bloom-specific use cases

### Decision 6: FastMCP for Tool Exposure

**Choice**: Use FastMCP to automatically expose Python functions as MCP tools

**Rationale:**

- No manual MCP server implementation needed
- Simple `@mcp.tool()` decorator
- Automatic JSON schema generation
- Seamless FastAPI integration
- Maintained by FastMCP team

**Pattern:**

```python
@mcp.tool()
def query_database(table: str, filters: dict) -> dict:
    """Query database - automatically becomes MCP tool"""
```

**Alternatives Considered:**

- **Manual MCP implementation**: Too much boilerplate
- **REST-only (no MCP)**: Harder for agents to use
- **Auto-convert REST to MCP**: Less optimized than purpose-built tools

**Trade-offs:**

- ✅ Minimal code
- ✅ Automatic schema generation
- ✅ Purpose-built for agents
- ❌ Dependency on FastMCP
- ❌ Two APIs to maintain (REST + MCP)

## Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────┐
│            Nginx (Reverse Proxy)            │
│  /          → Next.js (3000)                │
│  /api/*     → FastAPI (5003)                │
│  /ai/mcp    → FastMCP (5003)                │
└─────────────────────────────────────────────┘
                    │
        ┌───────────┴───────────┐
        ▼                       ▼
┌─────────────┐         ┌─────────────┐
│   Next.js   │         │  FastAPI    │
│  (Frontend) │         │  + FastMCP  │
│             │         │  + LangGraph│
└─────────────┘         └──────┬──────┘
                               │
         ┌─────────────────────┼─────────────────┐
         ▼                     ▼                 ▼
  ┌────────────┐      ┌───────────────┐  ┌────────────┐
  │ Supabase   │      │ External MCP  │  │  Ollama    │
  │ PostgreSQL │      │ Servers       │  │  Models    │
  └────────────┘      └───────────────┘  └────────────┘
```

### Component Interaction

```
User Query: "Show me experiments for species X"
    │
    ▼
┌──────────────────┐
│  Next.js Frontend│
│  (or direct API) │
└────────┬─────────┘
         │ POST /api/agent/query
         ▼
┌──────────────────────────────┐
│  FastAPI + Guardrails AI     │
│  1. Pydantic validates struct│
│  2. Guardrails validates content│
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│      LangGraph Workflow      │
│  1. Classify query type      │
│  2. Route to handler         │
│  3. Execute operation        │
│  4. Summarize results        │
└────────┬─────────────────────┘
         │
         ├──────────┬───────────────┬──────────┐
         ▼          ▼               ▼          ▼
   ┌─────────┐ ┌─────────┐   ┌──────────┐ ┌────────┐
   │Supabase │ │FastMCP  │   │External  │ │Ollama  │
   │MCP      │ │ Tools   │   │MCP Servers│ │Models  │
   └─────────┘ └─────────┘   └──────────┘ └────────┘
```

### Data Flow: Natural Language Query

```
1. User Input
   "Which experiments have more than 100 images?"

2. FastAPI Endpoint (/api/agent/query)
   - Pydantic validates: query string, max_results int
   - Guardrails AI: Remove PII, check toxic language

3. LangGraph: classify_query node
   - Uses Llama 3.2 3B (fast model)
   - Determines: "database_query" type

4. LangGraph: execute_database_query node
   - Calls FastMCP tool: query_database()
   - Guardrails AI: Validate table exists, prevent SQL injection
   - Execute: SELECT * FROM experiments WHERE image_count > 100

5. LangGraph: summarize_results node
   - Uses Qwen2.5 14B (reasoning model)
   - Formats results as natural language
   - Guardrails AI: Remove any PII from results

6. Return Response
   {
     "answer": "Found 15 experiments with >100 images...",
     "results": [...],
     "status": "success"
   }
```

### Multi-Language Codebase Interaction

**Key Insight**: Agent doesn't execute non-Python code - it orchestrates via APIs/tools

```
┌─────────────────────────────────┐
│  Python FastAPI Agent           │
│  (Orchestration Layer)          │
└───────┬─────────────────────────┘
        │
        │ How it interacts with entire codebase:
        │
        ├── Next.js (TypeScript)
        │   └─> HTTP calls to Next.js API endpoints
        │   └─> Git MCP (read/search TypeScript files)
        │
        ├── PostgreSQL (SQL)
        │   └─> Supabase MCP (database queries)
        │
        ├── MinIO (S3 API)
        │   └─> boto3 (Python S3 client)
        │
        ├── External Services (Any Language)
        │   └─> execute_job() generic HTTP calls
        │
        └── Configuration (YAML/JSON/ENV)
            └─> Filesystem MCP (read config files)
```

## Technology Stack

### Core Technologies

| Component          | Technology               | Purpose                     |
| ------------------ | ------------------------ | --------------------------- |
| API Framework      | FastAPI                  | REST API + async support    |
| MCP Server         | FastMCP                  | Auto-expose tools to agents |
| Agent Framework    | LangGraph + LangChain    | Workflow orchestration      |
| Validation         | Pydantic + Guardrails AI | Request/response validation |
| Models             | Ollama (free)            | LLM inference               |
| Database           | PostgreSQL (Supabase)    | Data storage                |
| File Storage       | MinIO (S3)               | Object storage              |
| Package Management | uv                       | Fast Python package manager |

### Dependencies

**Note**: Bloom uses `uv` for Python package management. Use `uv add <package>` instead of `pip install`.

```bash
# Install core dependencies with uv
uv add fastapi fastmcp langgraph langchain-community guardrails-ai

# Sync dependencies (in Docker or after pulling)
uv sync

# Models
ollama  # or via HTTP API

# External MCP Servers (separate processes)
# - Supabase MCP
# - Filesystem MCP
# - Git MCP
# - Memory MCP (Phase 2+)
```

## Security & Safety

### Multi-Layer Validation

```
Request → Pydantic → Guardrails AI → Business Logic → Guardrails AI → Response
          (structure)  (content)                        (content)
```

**Example:**

```python
@app.post("/api/agent/query")
async def agent_query(request: AgentQueryRequest):  # ← Pydantic validates structure
    safe_query = input_guard.validate(request.query)  # ← Guardrails validates content
    result = await agent.run(safe_query)
    safe_result = output_guard.validate(result)  # ← Guardrails sanitizes output
    return {"answer": safe_result}
```

### Guardrail Categories

1. **Pre-Built (Guardrails AI)**:

   - DetectPII: Remove emails, phones, SSNs
   - ValidSQL: Prevent SQL injection
   - ToxicLanguage: Block harmful content
   - ValidLength: Enforce size limits

2. **Custom (Bloom-Specific)**:

   - validate_species: Check species exists in DB
   - validate_table_name: Ensure table exists + permissions
   - check_resource_quota: Limit concurrent jobs

3. **Pydantic (Structural)**:
   - Type checking (str, int, dict)
   - Range validation (min/max)
   - Regex patterns
   - Custom `@validator` methods

### Authentication Strategy

**Options Under Consideration:**

**Option A**: Supabase RLS (Row Level Security)

- ✅ Per-user data isolation
- ✅ Leverage existing auth
- ❌ More complex query construction

**Option B**: Service Account

- ✅ Simpler query patterns
- ✅ Faster (no RLS overhead)
- ❌ Requires manual permission checking

**Recommended**: Hybrid

- User queries → Supabase RLS
- Agent internal operations → Service account
- Document both patterns

## Risks / Trade-offs

### Risk 1: Model Performance on Complex Queries

**Risk**: Free models may struggle with complex multi-step reasoning

**Mitigation:**

- Use model router to select stronger models for complex tasks
- Qwen2.5 14B performs well on benchmarks
- Support optional paid models for users who need them
- Implement fallback to simpler queries if complex fails

**Acceptance Criteria**: 80%+ success rate on standard queries (Phase 2 testing)

### Risk 2: Resource Usage (Ollama)

**Risk**: Running Ollama locally requires significant compute

**Mitigation:**

- Document resource requirements (8GB+ RAM for 14B model)
- Provide smaller model options (Llama 3.2 3B for resource-constrained)
- Support remote Ollama instances
- Consider hosted Ollama services for production

**Monitoring**: Track model inference latency and memory usage

### Risk 3: Migration Disruption (Flask → FastAPI)

**Risk**: Video generation breaks during migration

**Mitigation:**

- Migrate video generation first, test thoroughly
- Keep Flask running until FastAPI verified working
- Maintain same API contract (minimal frontend changes)
- Comprehensive integration tests before removing Flask

**Rollback Plan**: Can revert to Flask if critical issues found

### Risk 4: Guardrails False Positives

**Risk**: Overly aggressive validation blocks legitimate queries

**Mitigation:**

- Tune thresholds (e.g., ToxicLanguage threshold=0.8)
- Log all blocked requests for review
- Provide override mechanism for admin users
- Iteratively adjust based on real usage

**Monitoring**: Track validation failure rates

### Risk 5: Plugin Complexity Creep

**Risk**: Plugin system becomes too complex to use

**Mitigation:**

- Keep Phase 1 simple (generic tools only)
- Phase 3 adds plugins as examples, not framework
- Provide clear template and documentation
- Defer complex plugin marketplace to future

**Success Metric**: External user creates custom integration successfully

## Migration Plan

### Phase 1: Flask → FastAPI (Week 1-2)

1. Create FastAPI structure alongside Flask
2. Port video generation to FastAPI
3. Test video generation thoroughly
4. Update Next.js to call FastAPI
5. Verify in development
6. Deploy to production
7. Remove Flask after 1 week of stable FastAPI

**Rollback**: Keep Flask code in git history, can restore if needed

### Phase 2: Add Agent Capabilities (Week 3-4)

1. Install FastMCP, LangChain, LangGraph
2. Create basic workflow (classify → execute → respond)
3. Add Guardrails AI validation
4. Test with simple queries
5. Deploy to development
6. Collect feedback

**Rollback**: Agent endpoints are additive, can disable without affecting existing API

### Phase 3: Plugins (Week 5-6)

1. Document plugin pattern
2. Create sleap-roots example
3. Create GAPIT example
4. Test plugin loading
5. Document for external users

**Rollback**: Plugins are optional, core unaffected

## Open Questions

1. **Ollama Hosting**: Docker-compose or separate service?

   - **Recommendation**: Separate service for production (easier scaling)
   - Document both options

2. **GPU Allocation**: Does Ollama need GPU?

   - **Recommendation**: CPU-only for dev, optional GPU for prod
   - Document performance difference

3. **User Approval UI**: How to implement approval workflow?

   - **Options**:
     - A) Async job queue + frontend polling
     - B) WebSocket real-time notifications
     - C) Email approval links
   - **Recommendation**: Start with A (simpler), add B later if needed

4. **Audit Log Storage**: Database or separate logging service?

   - **Recommendation**: PostgreSQL table for now, consider dedicated service later

5. **Cost Tracking**: How to monitor LLM costs if users add paid models?
   - **Recommendation**: LangSmith integration (optional), log all API calls

## Success Criteria

### Phase 1 Success

- [ ] FastAPI running, Flask removed
- [ ] Video generation working
- [ ] Basic agent can answer: "Show me experiments for species X"
- [ ] Free models (Qwen2.5 14B) working
- [ ] < 5 second response time for simple queries

### Phase 2 Success

- [ ] 5+ skills with guardrails
- [ ] 80%+ success rate on test queries
- [ ] PII detection working
- [ ] SQL injection prevented
- [ ] Resource quotas enforced

### Phase 3 Success

- [ ] sleap-roots plugin working (example)
- [ ] Plugin documentation complete
- [ ] At least 1 external user creates custom plugin

## Timeline Notes

**No specific dates** - user controls scheduling. These are complexity estimates:

- Phase 1: 4-6 weeks effort
- Phase 2: 3-4 weeks effort
- Phase 3: 4-6 weeks effort
- Phase 4: TBD (future)

Phases can overlap, be parallelized, or sequenced based on team bandwidth.
