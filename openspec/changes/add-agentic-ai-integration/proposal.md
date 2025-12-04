# Proposal: Add Agentic AI Integration to Bloom

## Why

Bloom currently requires users to manually navigate UIs and write code to query datasets, run analysis pipelines, and visualize results. This creates friction for researchers who want to ask natural language questions like "Which clusters express gene AT1G01010?" or "Show me experiments with drought stress conditions."

Adding agentic AI capabilities will:

1. **Enable natural language interaction** with biological data
2. **Provide intelligent data exploration** and visualization
3. **Reduce technical barriers** for domain scientists
4. **Accelerate research workflows** through AI-assisted analysis
5. **Create extensible platform** for custom integrations

**Design Philosophy**: Build generalizable core capabilities that work for any scientific domain, with Bloom's biological pipelines as first-party examples.

## What Changes

### Migration: Flask → FastAPI + FastMCP

- **BREAKING**: Replace Flask service entirely with FastAPI
- **FastMCP Integration**: Automatically expose API functions as MCP tools for AI agents
- **Reason**: FastAPI provides better async support, automatic API docs, and seamless FastMCP integration
- **Migration**: Port video generation from Flask to FastAPI

### Phase 1: Core Agent System (Generalizable MVP)

- **Replace Flask with FastAPI** service
- **Implement FastMCP** to auto-expose functions as agent tools
- **Integrate external MCP servers**: Supabase, Filesystem, Git
- **Create core agent tools**:
  - Database querying (any PostgreSQL database)
  - File operations (read/write/search)
  - Generic job execution (trigger ANY external HTTP service)
  - Job status monitoring
  - Video generation (migrated from Flask)
- **Build agent framework** with LangGraph + LangChain for orchestration
- **Support free/open-source models** via Ollama (Llama 3.1, Qwen2.5)

### Phase 2: Skills & Guardrails System

- **Implement generalized skill system** (model-agnostic, inspired by Claude Skills)
- **Integrate Guardrails AI** for validation and safety:
  - Use pre-built validators (PII detection, SQL injection, toxic content)
  - Create custom validators for Bloom-specific validation
  - Resource usage limits (compute, storage, API calls)
  - Input validation and sanitization
  - Output formatting and confidence thresholds
  - Permission checks and audit logging
- **Pydantic models** for request/response validation in FastAPI
- **Model routing** - select appropriate model per task type
- **Testing framework** for agent reliability

### Phase 3: Optional Integrations (Plugin System)

- **Example integrations** (Bloom-specific, not required):
  - sleap-roots pipeline orchestration
  - GAPIT GWAS pipeline
  - ESM2 protein embedding queries
- **Plugin architecture** for users to add custom tools
- **Configuration system** for external services
- **Documentation** on extending with custom pipelines

### Phase 4: Advanced Capabilities (Vector DB & RAG)

- **Add pgvector** to PostgreSQL for semantic search
- **Implement RAG** for:
  - Documentation retrieval (generic)
  - Domain-specific knowledge bases (configurable)
  - Scientific literature search (optional)
- **Integrate marimo notebooks** for interactive visualization
- **Conversation memory** for multi-turn interactions

## Impact

### Affected Specifications

- **NEW**: `specs/agentic-ai/spec.md` - Core agent capabilities
- **NEW**: `specs/fastapi-backend/spec.md` - FastAPI service architecture
- **NEW**: `specs/mcp-integration/spec.md` - FastMCP and MCP server integration
- **MODIFIED**: `specs/development-workflow/spec.md` - FastAPI replaces Flask in docker-compose

### Affected Code

- **NEW**: `fastapi/` - New FastAPI service directory (replaces `flask/`)
- **NEW**: `packages/bloom-agents/` - Agent orchestration logic (LangGraph + LangChain)
- **NEW**: `packages/bloom-skills/` - Skill system and guardrails
- **NEW**: `fastapi/integrations/` - Optional plugin examples (sleap-roots, GAPIT)
- **MODIFIED**: `docker-compose.dev.yml` - Replace Flask with FastAPI service
- **MODIFIED**: `docker-compose.prod.yml` - Replace Flask with FastAPI service
- **MODIFIED**: `nginx/` - Update routing from Flask to FastAPI
- **REMOVED**: `flask/` - Migrate video generation to FastAPI, then remove

### Migration Strategy

- **Complete replacement**: Migrate Flask video generation to FastAPI first
- **Port VideoWriter**: Convert `flask/videowriter.py` to async FastAPI compatible class
- **Update endpoints**: `/api/videos/*` now handled by FastAPI
- **Frontend impact**: Minimal - maintain same API contract
- **Breaking changes**: Direct Flask endpoint dependencies need update (minimal expected)

## Technical Architecture

### Service Layout

```
┌─────────────────────────────────────────────────────────┐
│                  Nginx Reverse Proxy                    │
│  • /              → Next.js (port 3000)                │
│  • /api/*         → FastAPI REST (port 5003)           │
│  • /ai/mcp        → FastMCP Agent Tools (port 5003)    │
└─────────────────────────────────────────────────────────┘
                            │
           ┌────────────────┴────────────────┐
           ▼                                 ▼
   ┌─────────────┐                  ┌─────────────┐
   │   Next.js   │                  │  FastAPI    │
   │   Frontend  │                  │  + FastMCP  │
   │  (Users)    │                  │  + LangGraph│
   └─────────────┘                  └──────┬──────┘
                                           │
                   ┌───────────────────────┼───────────────────────┐
                   ▼                       ▼                       ▼
           ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
           │  Supabase    │       │    MinIO     │       │  External    │
           │  PostgreSQL  │       │   Storage    │       │  Services    │
           │  (+ pgvector)│       │              │       │  (Any HTTP)  │
           └──────────────┘       └──────────────┘       └──────────────┘
```

### FastMCP Integration Pattern

FastMCP **automatically** creates MCP tools from Python functions - no manual MCP server implementation needed:

```python
from fastmcp import FastMCP
from fastapi import FastAPI

# Traditional REST API
app = FastAPI(title="Bloom API")

# FastMCP automatically creates MCP server
mcp = FastMCP("Bloom AI")

# ✅ This decorator automatically makes function available to AI agents
@mcp.tool()
def query_database(
    table: str,
    filters: dict,
    limit: int = 100
) -> dict:
    """
    Query any table in the database.

    Works with any PostgreSQL database - not specific to biology.
    Agents can query experiments, datasets, or any custom tables.
    """
    return execute_query(table, filters, limit)

@mcp.tool()
def execute_job(
    endpoint: str,
    params: dict,
    async_mode: bool = True
) -> dict:
    """
    Execute external job via HTTP POST.

    GENERIC - works with:
    - Argo Workflows
    - Kubernetes Jobs
    - Cloud Functions
    - RunAI / GPU clusters
    - Any HTTP service

    Example:
        execute_job(
            endpoint="http://cluster.example.com/api/jobs",
            params={"pipeline": "analysis", "data": "dataset-123"}
        )
    """
    response = await http_client.post(endpoint, json=params)
    return {"job_id": response.json()["id"], "status": "submitted"}

@mcp.tool()
def check_job_status(endpoint: str, job_id: str) -> dict:
    """Check status of any async job"""
    response = await http_client.get(f"{endpoint}/{job_id}")
    return response.json()

# Mount MCP server into FastAPI (automatic MCP protocol handling)
mcp_app = mcp.http_app(path='/mcp')
app.mount("/ai", mcp_app)

# Keep traditional REST endpoints for Next.js frontend
@app.post("/api/videos/generate")
async def generate_video(request: VideoRequest):
    """Video generation - migrated from Flask"""
    return await video_service.create(request)
```

**Key Point**: You don't implement an MCP server - FastMCP does it automatically. You just write functions and add `@mcp.tool()`.

### Generalizable Core + Optional Plugins

**Core Agent Tools (Phase 1 - Universal):**

```python
# These work for ANY scientific application, not just biology
@mcp.tool()
def query_database(table: str, filters: dict) -> dict:
    """Query any PostgreSQL table"""

@mcp.tool()
def execute_job(endpoint: str, params: dict) -> dict:
    """Trigger any external HTTP service"""

@mcp.tool()
def read_file(path: str) -> str:
    """Read any file"""

@mcp.tool()
def search_files(pattern: str, directory: str) -> list:
    """Search for files"""
```

**Optional Plugins (Phase 3 - Examples):**

```python
# fastapi/integrations/sleap_roots.py
# This is an EXAMPLE - users can create similar plugins

from fastapi.config import get_integration_config

@mcp.tool()
def run_sleap_roots_pipeline(
    dataset_id: str,
    model_name: str = "arabidopsis_primary_root",
    gpu_enabled: bool = True
) -> dict:
    """
    Convenience wrapper for sleap-roots pipeline.

    This is an EXAMPLE integration. Users can create similar
    functions for their own pipelines.
    """
    config = get_integration_config("sleap-roots")

    return await execute_job(
        endpoint=config["endpoint"],  # From env/config
        params={
            "pipeline": "sleap-roots",
            "dataset_id": dataset_id,
            "model": model_name,
            "gpu": gpu_enabled
        }
    )

# Configuration (env vars or config file)
# SLEAP_ROOTS_ENDPOINT=http://runai.salk.edu/api/jobs
# GAPIT_ENDPOINT=http://runai.salk.edu/api/gapit
# Users set their own endpoints
```

**Why this works:**

- ✅ Core system is domain-agnostic
- ✅ Bloom's pipelines are first-party examples
- ✅ Users can add their own integrations
- ✅ No one is forced to use features they don't need
- ✅ Clear separation: core vs. plugins

### External MCP Servers to Integrate

These are **pre-built open-source** MCP servers that FastAPI will connect to:

#### Phase 1 (Essential)

1. **Supabase MCP Server**

   - Purpose: Database query operations
   - Reference: https://supabase.com/docs/guides/getting-started/mcp
   - Pre-built by Supabase team

2. **Filesystem MCP Server**

   - Purpose: File operations with access controls
   - Reference: https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem
   - Official Anthropic server

3. **Git MCP Server**
   - Purpose: Repository operations, version control
   - Reference: https://github.com/modelcontextprotocol/servers/tree/main/src/git
   - Official Anthropic server

#### Phase 2 (Enhanced)

4. **Memory MCP Server**
   - Purpose: Persistent conversation memory
   - Reference: https://github.com/modelcontextprotocol/servers/tree/main/src/memory
   - Knowledge graph-based

#### Phase 3 (Advanced - Optional)

5. **Qdrant MCP Server**
   - Purpose: Vector database for semantic search
   - Reference: https://github.com/modelcontextprotocol/servers
   - For RAG and embedding search

**Note**: These are all pre-built servers. You just configure and run them - no implementation needed.

### Model Strategy: Free & Open-Source Priority

**Philosophy**: Prioritize free models so users don't need API accounts. Support paid models as optional upgrades.

#### Primary Models (FREE via Ollama)

**1. General Reasoning & Complex Tasks**

- **Qwen2.5 14B Instruct** (FREE, recommended)

  - Best free reasoning model
  - Excellent tool calling
  - Strong code generation
  - Reference: https://ollama.com/library/qwen2.5

- **Llama 3.1 8B Instruct** (FREE, faster alternative)
  - Good all-around performance
  - Lower resource usage
  - Reference: https://ollama.com/library/llama3.1

**2. Code Generation (Notebooks, Scripts)**

- **Qwen2.5-Coder 7B** (FREE)
  - Best free code model
  - Excellent for Python/data analysis
  - Reference: https://ollama.com/library/qwen2.5-coder

**3. Fast Inference (Simple Tasks)**

- **Gemma 2 9B** (FREE)
  - Very fast, efficient
  - Good for data extraction, formatting
  - Reference: https://ollama.com/library/gemma2

**4. Ultra-Fast (Classification, Simple Queries)**

- **Llama 3.2 3B** (FREE)
  - Extremely fast
  - Low resource usage
  - Reference: https://ollama.com/library/llama3.2

#### Optional Paid Models (User Choice)

Users can optionally configure paid models if they have API keys:

- **Claude 3.5 Sonnet** - Best reasoning (requires Anthropic API key)
- **GPT-4 Turbo** - Strong alternative (requires OpenAI API key)
- **Gemini 1.5 Pro** - Good free tier available

#### Model Router Strategy

```python
def select_model(task_type: str, complexity: str = "medium") -> str:
    """
    Automatically select appropriate free model based on task.
    Users can override in config.
    """

    if task_type == "code_generation":
        return "qwen2.5-coder:7b"  # Best for code

    elif task_type == "complex_reasoning":
        if complexity == "high":
            return "qwen2.5:14b"  # Best reasoning
        else:
            return "llama3.1:8b"  # Faster

    elif task_type == "simple_query":
        return "llama3.2:3b"  # Ultra-fast

    elif task_type == "data_extraction":
        return "gemma2:9b"  # Good at structured output

    # Default to best free all-around model
    return "qwen2.5:14b"
```

**Configuration via Environment:**

```bash
# .env - Free defaults (no API keys required)
DEFAULT_MODEL=qwen2.5:14b
CODE_MODEL=qwen2.5-coder:7b
FAST_MODEL=llama3.2:3b

# Optional: Users can add paid models
OPENROUTER_API_KEY=  # Optional
ANTHROPIC_API_KEY=   # Optional
ENABLE_PAID_MODELS=false  # Default off
```

**Why Free Models First:**

- ✅ No API costs for users
- ✅ Full data privacy (runs locally)
- ✅ No rate limits or quotas
- ✅ Works offline (development/testing)
- ✅ Modern free models are highly capable
- ✅ Qwen2.5 14B ≈ GPT-3.5 Turbo performance

### Agent Framework: LangGraph + LangChain

**What You Need:**

Both libraries work together - **LangGraph is built on top of LangChain**:

```bash
# Installation (LangGraph includes LangChain as dependency)
pip install langgraph langchain-community langchain-core
```

**What Each Provides:**

**LangChain** (base library):

- Model wrappers (Ollama, OpenAI, Anthropic)
- Tool calling abstractions
- Prompt templates
- Memory management
- Output parsers
- Chain compositions
- Reference: https://docs.langchain.com/oss/python/langchain/quickstart

**LangGraph** (workflow orchestration):

- State machines and directed graphs
- Conditional branching
- Checkpointing and persistence
- Complex multi-step agent patterns
- Built on top of LangChain
- Reference: https://python.langchain.com/docs/langgraph

**Why Both:**

- ✅ LangChain = building blocks (models, tools, prompts)
- ✅ LangGraph = orchestration (workflows, state management)
- ✅ LangGraph requires LangChain to function
- ✅ Together they provide complete agent framework

**Example Using Both:**

```python
# From LangChain - model wrappers and tools
from langchain_community.llms import Ollama
from langchain.tools import Tool
from langchain.prompts import ChatPromptTemplate
from langchain.agents import AgentExecutor

# From LangGraph - workflow orchestration
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolExecutor

# Use LangChain for model setup
reasoning_llm = Ollama(model="qwen2.5:14b")
code_llm = Ollama(model="qwen2.5-coder:7b")
fast_llm = Ollama(model="llama3.2:3b")

# Use LangChain for tools
tools = [
    Tool(
        name="query_database",
        func=query_supabase,
        description="Query PostgreSQL database"
    ),
    Tool(
        name="execute_job",
        func=trigger_external_job,
        description="Execute external HTTP job"
    )
]

# Use LangGraph for workflow
class AgentState(TypedDict):
    messages: List[str]
    query_type: str
    results: Optional[dict]

workflow = StateGraph(AgentState)

# Nodes use LangChain models
workflow.add_node("classify", lambda s: classify_query(s, fast_llm))
workflow.add_node("execute", lambda s: run_query(s, reasoning_llm))
workflow.add_node("summarize", lambda s: format_response(s, reasoning_llm))

# LangGraph handles routing
workflow.add_conditional_edges(
    "classify",
    route_based_on_type,
    {
        "database": "execute",
        "job": "execute",
        "unknown": END
    }
)

workflow.set_entry_point("classify")
agent = workflow.compile()
```

**Key Benefits:**

- ✅ Built for complex, stateful workflows
- ✅ Graph-based architecture (perfect for pipelines)
- ✅ Supports multiple models (Ollama, OpenAI, Anthropic)
- ✅ Built-in checkpointing and state management
- ✅ Production-ready with monitoring (LangSmith)
- ✅ Strong Python ecosystem
- ✅ Actively maintained by LangChain team

**Inspiration from Cline's "Plan & Act":**

```python
# Implement two-phase workflow (Plan → Execute)
@workflow.node("plan")
def create_execution_plan(state: AgentState) -> AgentState:
    """Generate plan before executing (Cline-inspired)"""
    plan = reasoning_llm.generate_plan(state["query"])
    return {"plan": plan, "approval_required": True}

@workflow.node("await_approval")
def request_approval(state: AgentState) -> AgentState:
    """Human-in-the-loop approval gate"""
    # Show plan to user, wait for approval
    approved = await get_user_approval(state["plan"])
    return {"approved": approved}

@workflow.node("execute")
def execute_plan(state: AgentState) -> AgentState:
    """Execute approved plan step-by-step"""
    results = []
    for step in state["plan"]["steps"]:
        result = execute_step(step)
        results.append(result)
    return {"results": results}

# Add conditional edge based on approval
workflow.add_conditional_edges(
    "await_approval",
    lambda s: "execute" if s["approved"] else END,
    {"execute": "execute", END: END}
)
```

### Pydantic Models for FastAPI Request/Response Validation

**FastAPI uses Pydantic for automatic validation:**

```python
# fastapi/models/requests.py
from pydantic import BaseModel, Field, validator
from typing import Optional, List

class AgentQueryRequest(BaseModel):
    """Natural language query to agent"""
    query: str = Field(..., min_length=1, max_length=1000)
    context: Optional[dict] = None
    max_results: int = Field(default=100, ge=1, le=10000)

    @validator('query')
    def query_not_empty(cls, v):
        if not v.strip():
            raise ValueError('Query cannot be empty')
        return v.strip()

class JobRequest(BaseModel):
    """Execute external job"""
    endpoint: str = Field(..., regex=r'^https?://')
    params: dict
    async_mode: bool = True
    timeout: int = Field(default=300, ge=1, le=3600)

class DatabaseQueryRequest(BaseModel):
    """Query database"""
    table: str = Field(..., regex=r'^[a-z_][a-z0-9_]*$')
    filters: dict = {}
    limit: int = Field(default=100, ge=1, le=10000)
    offset: int = Field(default=0, ge=0)

class VideoGenerationRequest(BaseModel):
    """Video generation (migrated from Flask)"""
    experiment_id: str
    decimation_factor: int = Field(default=4, ge=1, le=10)
    fps: int = Field(default=30, ge=1, le=60)

# Usage in FastAPI
@app.post("/api/agent/query")
async def agent_query(request: AgentQueryRequest):
    """
    FastAPI automatically validates:
    - query is string, 1-1000 chars
    - max_results is int, 1-10000
    - Returns 422 error if invalid
    """
    result = await agent.run(request.query)
    return {"answer": result}
```

### Skill & Guardrail System with Guardrails AI

**Use Guardrails AI library + custom guards:**

```bash
pip install guardrails-ai
```

#### Pre-Built Validators from Guardrails AI

```python
from guardrails import Guard
from guardrails.hub import DetectPII, ValidSQL, ToxicLanguage, ValidLength

# Import pre-built validators (50+ available)
from guardrails.hub import (
    DetectPII,          # Remove personal information
    ValidSQL,           # Prevent SQL injection
    ToxicLanguage,      # Block harmful content
    ValidLength,        # Length limits
    ValidURL,           # URL validation
    RegexMatch,         # Pattern matching
)

# Create guard with pre-built validators
query_guard = Guard().use_many(
    DetectPII(pii_entities=["EMAIL", "PHONE", "SSN"]),
    ToxicLanguage(threshold=0.8),
    ValidLength(min=1, max=1000)
)

# Use in FastAPI endpoint
@app.post("/api/agent/query")
async def agent_query(request: AgentQueryRequest):
    # Automatically validate and sanitize
    validated_query = query_guard.validate(request.query)
    return await agent.run(validated_query)
```

#### Custom Guardrails for Bloom

```python
# packages/bloom-skills/custom_guards.py
from guardrails import register_validator, ValidationResult

@register_validator(name="valid_species", data_type="string")
def validate_species(value, metadata):
    """Check if species exists in database"""
    valid_species = get_valid_species_from_db()
    if value not in valid_species:
        return ValidationResult(
            outcome="fail",
            error_message=f"Invalid species: {value}. Valid: {', '.join(valid_species[:5])}..."
        )
    return ValidationResult(outcome="pass")

@register_validator(name="valid_gene_id", data_type="string")
def validate_gene_id(value, metadata):
    """Validate gene ID format (e.g., AT1G01010)"""
    import re
    pattern = r'^AT[1-5MC]G\d{5}$'  # Arabidopsis gene ID
    if not re.match(pattern, value):
        return ValidationResult(
            outcome="fail",
            error_message=f"Invalid gene ID format: {value}"
        )
    return ValidationResult(outcome="pass")

@register_validator(name="check_resource_quota", data_type="dict")
def check_resource_quota(value, metadata):
    """Prevent exceeding concurrent job limits"""
    max_concurrent = metadata.get("max_concurrent", 5)
    current_jobs = get_running_jobs_count()

    if current_jobs >= max_concurrent:
        return ValidationResult(
            outcome="fail",
            error_message=f"Max concurrent jobs ({max_concurrent}) reached. Current: {current_jobs}"
        )
    return ValidationResult(outcome="pass")

@register_validator(name="valid_table_name", data_type="string")
def validate_table_name(value, metadata):
    """Ensure table exists and user has access"""
    from fastapi.db import get_db_schema

    schema = get_db_schema()
    if value not in schema.tables:
        return ValidationResult(
            outcome="fail",
            error_message=f"Table '{value}' does not exist"
        )

    # Check user permissions (if needed)
    user = metadata.get("user")
    if not has_table_access(user, value):
        return ValidationResult(
            outcome="fail",
            error_message=f"No permission to access table '{value}'"
        )

    return ValidationResult(outcome="pass")
```

#### Combining Pre-Built + Custom Guards

```python
# packages/bloom-skills/skill.py
from guardrails import Guard
from guardrails.hub import DetectPII, ValidSQL, ValidLength
from .custom_guards import validate_species, validate_gene_id, check_resource_quota, validate_table_name

# Create comprehensive guard
database_guard = Guard().use_many(
    # Pre-built validators
    ValidSQL(),                                    # Prevent SQL injection
    ValidLength(min=1, max=100),                  # Limit table name length
    # Custom Bloom validators
    validate_table_name                            # Check table exists + permissions
)

gene_query_guard = Guard().use_many(
    # Pre-built
    DetectPII(),                                   # Remove PII from results
    # Custom
    validate_species,                              # Validate species
    validate_gene_id                               # Validate gene ID format
)

job_guard = Guard().use_many(
    # Custom
    check_resource_quota                           # Limit concurrent jobs
)

# Use in skill decorator
@skill(
    name="query_database",
    model="qwen2.5:14b",
    fallback_model="llama3.1:8b",
    timeout_seconds=30
)
def query_database(
    table: str,
    filters: dict,
    limit: int = 100
) -> dict:
    """Query database with Guardrails AI validation"""

    # Validate inputs with Guardrails AI
    table = database_guard.validate(table)

    # Execute query
    results = execute_query(table, filters, limit)

    # Validate outputs (remove PII, etc.)
    results = gene_query_guard.validate(results)

    return results

@skill(
    name="execute_external_job",
    model="llama3.1:8b"
)
def execute_job(endpoint: str, params: dict) -> dict:
    """Execute external job with resource limits"""

    # Check resource quota before executing
    job_guard.validate(params, metadata={"max_concurrent": 5})

    # Submit job
    response = await http_client.post(endpoint, json=params)
    return {"job_id": response.json()["id"], "status": "submitted"}
```

#### Complete FastAPI Endpoint with Guards

```python
# fastapi/routes/agent.py
from fastapi import FastAPI, HTTPException
from guardrails import Guard
from guardrails.hub import DetectPII, ToxicLanguage
from .models.requests import AgentQueryRequest
from .custom_guards import validate_species

app = FastAPI()

# Create input guard
input_guard = Guard().use_many(
    DetectPII(),
    ToxicLanguage(threshold=0.8)
)

# Create output guard
output_guard = Guard().use_many(
    DetectPII()  # Remove PII from results
)

@app.post("/api/agent/query")
async def agent_query(request: AgentQueryRequest):
    """
    Natural language query with multi-layer validation:
    1. Pydantic validates structure
    2. Guardrails AI validates content
    3. Agent processes query
    4. Guardrails AI validates output
    """
    try:
        # Validate and sanitize input
        safe_query = input_guard.validate(request.query)

        # Process with agent
        result = await agent.run(safe_query, max_results=request.max_results)

        # Validate and sanitize output
        safe_result = output_guard.validate(result)

        return {"answer": safe_result, "status": "success"}

    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/database/query")
async def database_query(request: DatabaseQueryRequest):
    """
    Database query with validation:
    1. Pydantic validates request structure
    2. Custom guards check table exists + permissions
    3. Pre-built guards prevent SQL injection
    """
    # Pydantic already validated structure

    # Apply Guardrails AI validation
    safe_table = database_guard.validate(
        request.table,
        metadata={"user": get_current_user()}
    )

    results = await execute_query(safe_table, request.filters, request.limit)
    return {"results": results}
```

**Guardrail Types Provided:**

1. **Pre-Built (Guardrails AI Hub)**:

   - PII detection and removal
   - SQL injection prevention
   - Toxic language filtering
   - URL/email validation
   - Length/format constraints
   - Reference: https://hub.guardrailsai.com

2. **Custom Bloom Validators**:

   - Species validation (check database)
   - Gene ID format validation
   - Resource quota enforcement
   - Table existence + permission checks

3. **Pydantic Validators**:
   - Type checking (str, int, dict, etc.)
   - Range validation (min/max)
   - Regex patterns
   - Custom validators with `@validator` decorator

**Benefits of This Approach:**

- ✅ Use 50+ pre-built validators (no reinventing wheel)
- ✅ Easy to add custom domain-specific validators
- ✅ Pydantic handles structural validation
- ✅ Guardrails AI handles content validation
- ✅ Layered defense (multiple validation stages)
- ✅ Production-ready (used by many companies)

### Research-Based Design Principles

Based on agent research findings (2024-2025):

**1. 30-40% Autonomous Success Rate**

- Agents solve 30-40% of tasks without human help when goals are clear
- **Application**: Design clear tool interfaces with explicit parameters
- **Implementation**: Structured outputs, unambiguous function signatures

**2. Plan Then Act Pattern**

- Separating planning from execution dramatically improves reliability
- **Application**: LangGraph workflow with explicit planning phase
- **Implementation**: Generate plan → user approval → execute steps

**3. Context is Critical**

- Comprehensive context improves performance significantly
- **Application**: Provide full database schema, API docs, examples in prompts
- **Implementation**: Dynamic context injection based on query type

**4. Domain Models Often Underperform**

- General 7B-14B models beat domain-specific models in most tasks
- **Application**: Use Qwen2.5, Llama 3.1 over biology-specific models
- **Exception**: BioGPT useful for narrow text extraction tasks

**5. Small Models for Specialized Tasks**

- 0.5B-7B models highly effective for narrow, well-defined tasks
- **Application**: Gemma 2 9B for data extraction, Llama 3.2 3B for classification
- **Implementation**: Model router selects appropriate model per task

**6. Human-in-the-Loop Essential**

- Approval gates prevent costly errors
- **Application**: User confirms high-resource operations, destructive actions
- **Implementation**: Async job queue with approval step, guardrail system

## References

### Core Technologies

- **FastAPI**: https://fastapi.tiangolo.com
- **Pydantic**: https://docs.pydantic.dev
- **FastMCP**: https://gofastmcp.com
- **FastMCP + FastAPI Integration**: https://gofastmcp.com/integrations/fastapi
- **Guardrails AI**: https://www.guardrailsai.com
- **Guardrails Hub** (pre-built validators): https://hub.guardrailsai.com

### MCP Ecosystem

- **Model Context Protocol**: https://modelcontextprotocol.io
- **MCP Official Servers**: https://github.com/modelcontextprotocol/servers
- **Awesome MCP Servers**: https://github.com/wong2/awesome-mcp-servers
- **Supabase MCP**: https://supabase.com/docs/guides/getting-started/mcp

### Agent Frameworks

- **LangChain Quickstart**: https://docs.langchain.com/oss/python/langchain/quickstart
- **LangChain Agents**: https://docs.langchain.com/oss/python/langchain/agents
- **LangGraph**: https://python.langchain.com/docs/langgraph
- **LangChain + Ollama**: https://python.langchain.com/docs/integrations/llms/ollama
- **LangSmith** (monitoring): https://smith.langchain.com

### Free Models (Ollama)

- **Ollama Library**: https://ollama.com/library
- **Qwen2.5 14B**: https://ollama.com/library/qwen2.5
- **Qwen2.5-Coder 7B**: https://ollama.com/library/qwen2.5-coder
- **Llama 3.1 8B**: https://ollama.com/library/llama3.1
- **Llama 3.2 3B**: https://ollama.com/library/llama3.2
- **Gemma 2 9B**: https://ollama.com/library/gemma2
- **DeepSeek-R1**: https://ollama.com/library/deepseek-r1

### Research & Best Practices

- **Agent Foundational Studies**: https://github.com/catalystneuro/agent-foundational-studies
- **State of AI Agents 2025**: https://arxiv.org/html/2503.08979v1
- **Agentic AI for Science**: https://arxiv.org/html/2508.11957v1
- **SLMs in Biology**: "Small language models enable rapid and accurate extraction" - PMC12451238
- **LLMs in Biology Survey**: "A survey on LLMs in biology and chemistry" - Nature 2025
- **Cline Plan & Act**: https://cline.ghost.io/plan-smarter-code-faster-clines-plan-act-is-the-paradigm-for-agentic-coding/

### Model Evaluation

- **HuggingFace MTEB Leaderboard**: https://huggingface.co/spaces/mteb/leaderboard
- **Open LLM Leaderboard**: https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard

### Additional Tools

- **OpenRouter** (multi-model API): https://openrouter.ai
- **Marimo Notebooks**: https://marimo.io
- **pgvector**: https://github.com/pgvector/pgvector
- **Argo Workflows**: https://argoproj.github.io/workflows

## Success Criteria

### Phase 1: Core Agent System (MVP)

- [ ] FastAPI service running (Flask fully replaced)
- [ ] Video generation working in FastAPI
- [ ] FastMCP server exposing tools at `/ai/mcp`
- [ ] Supabase, Filesystem, Git MCP servers integrated
- [ ] LangChain + LangGraph installed and configured
- [ ] Generic database query tool working
- [ ] Generic job execution tool working
- [ ] User can query via natural language: "Show me experiments for species X"
- [ ] Free models working (Qwen2.5 14B, Llama 3.1 8B)
- [ ] Model router selects appropriate model per task
- [ ] LangGraph workflow executes multi-step queries

### Phase 2: Skills & Guardrails

- [ ] 5+ skills with guardrails implemented
- [ ] Resource quotas enforced (query limits, job limits)
- [ ] User approval workflow for high-impact operations
- [ ] Audit logging for all agent actions
- [ ] Test suite with 80%+ success rate on standard queries
- [ ] Error handling and fallback strategies working

### Phase 3: Optional Integrations

- [ ] Example integration: sleap-roots pipeline
- [ ] Example integration: GAPIT pipeline
- [ ] Plugin documentation for custom integrations
- [ ] Configuration system for external services
- [ ] At least one external user creates custom integration

### Phase 4: Advanced Features

- [ ] pgvector integrated for semantic search
- [ ] RAG working for documentation retrieval
- [ ] Marimo notebook generation functional
- [ ] Conversation memory maintains context
- [ ] ESM2 protein embedding search (Bloom-specific)

## Migration Checklist: Flask → FastAPI

### Pre-Migration

- [ ] Audit Flask endpoints and dependencies
- [ ] Document Flask VideoWriter class thoroughly
- [ ] List all environment variables used by Flask
- [ ] Test current video generation end-to-end
- [ ] Benchmark Flask video generation performance

### Implementation

- [ ] Create FastAPI service structure (`fastapi/`)
- [ ] Port VideoWriter to async FastAPI pattern
- [ ] Migrate video generation endpoints (`/api/videos/*`)
- [ ] Port S3/MinIO integration (boto3)
- [ ] Port JWT authentication middleware
- [ ] Add FastMCP integration
- [ ] Install LangChain + LangGraph
- [ ] Create initial MCP tools (database, files, jobs)
- [ ] Build LangGraph workflow for agent orchestration
- [ ] Update Dockerfile (keep Python 3.11)
- [ ] Update docker-compose.dev.yml
- [ ] Update docker-compose.prod.yml
- [ ] Update nginx routing configuration

### Testing

- [ ] Unit tests for video generation
- [ ] Integration test: video generation end-to-end
- [ ] Test S3/MinIO integration
- [ ] Test JWT authentication
- [ ] Test FastMCP tools from agent
- [ ] Test LangGraph workflow execution
- [ ] Test model router with different task types
- [ ] Performance comparison: Flask vs FastAPI
- [ ] Load testing with concurrent requests

### Deployment

- [ ] Deploy FastAPI to development
- [ ] Verify all functionality works
- [ ] Deploy to production
- [ ] Monitor for errors/issues
- [ ] Remove Flask service from docker-compose
- [ ] Delete `flask/` directory
- [ ] Update all documentation
- [ ] Update README with new architecture

## Open Questions for Team

1. **Authentication Strategy**:

   - Should agents use Supabase RLS policies?
   - Or dedicated service account with broader access?
   - How to handle per-user agent contexts?

2. **GPU Cluster Integration**:

   - What is the RunAI API endpoint?
   - Authentication method for job submission?
   - Job monitoring/status check endpoint?

3. **Resource Limits**:

   - Max concurrent jobs per user?
   - Max database query result size?
   - API rate limits for agents?

4. **User Approval Workflow**:

   - Which operations require explicit approval?
   - Async approval via frontend UI?
   - Email/notification system?

5. **Model Hosting**:

   - Run Ollama in docker-compose or separate service?
   - GPU allocation for Ollama (if available)?
   - Fallback strategy if local model fails?

6. **Plugin System**:
   - File-based config or database-stored?
   - UI for users to enable/disable plugins?
   - Marketplace for community plugins (future)?
