"""
Bloom LangChain Agent - Uses PostgREST (Supabase) for data queries
Supports multiple LLM providers: OpenAI, Local (vLLM)
"""
import os
from typing import Optional, Literal

from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langchain_openai import ChatOpenAI
from langchain_core.messages import trim_messages
from psycopg_pool import AsyncConnectionPool

import logging
from rich.logging import RichHandler
from rich.traceback import install
from tools import all_tools, generic_tools, scrna_tools, cyl_tools
from config import FRONTEND_URL

install()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(message)s",
    handlers=[RichHandler()]
)

#################################################### Agent Setup ####################################################

# Postgres connection URL for persistent conversation memory.
# Use LANGCHAIN_POSTGRES_URL directly, or build from individual POSTGRES_* env vars.
_pg_url_override = os.getenv("LANGCHAIN_POSTGRES_URL")
if _pg_url_override:
    POSTGRES_URL = _pg_url_override
else:
    _pg_password = os.getenv("POSTGRES_PASSWORD")
    if not _pg_password:
        raise RuntimeError(
            "Database configuration required: set LANGCHAIN_POSTGRES_URL or POSTGRES_PASSWORD"
        )
    POSTGRES_URL = "postgresql://{user}:{password}@{host}:{port}/{db}".format(
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=_pg_password,
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "postgres"),
    )


async def setup_checkpointer() -> AsyncPostgresSaver:
    """Create and initialize the PostgresSaver checkpointer.

    Opens an async connection pool to Postgres and auto-creates the
    checkpoint tables (checkpoints, checkpoint_blobs, checkpoint_writes)
    if they don't exist yet. Returns the checkpointer instance.

    Note: setup() uses CREATE INDEX CONCURRENTLY which cannot run inside
    a transaction block. We use a separate autocommit connection for the
    one-time table creation, then the pool for runtime operations.
    """
    from psycopg import AsyncConnection

    pool = AsyncConnectionPool(
        conninfo=POSTGRES_URL,
        min_size=2,
        max_size=10,
        open=False,
    )
    await pool.open()

    # Setup requires autocommit (CREATE INDEX CONCURRENTLY can't run in a transaction)
    async with await AsyncConnection.connect(POSTGRES_URL, autocommit=True) as conn:
        setup_cp = AsyncPostgresSaver(conn)
        await setup_cp.setup()

    checkpointer = AsyncPostgresSaver(pool)
    logger = logging.getLogger(__name__)
    logger.info(f"PostgresSaver initialized with pool (min=2, max=10) → {POSTGRES_URL.split('@')[-1]}")
    return checkpointer


def trim_conversation(state):
    """Pre-model hook: trim messages to fit within model context limits.

    Uses llm_input_messages so the full history remains in the checkpointer
    but only recent messages are sent to the LLM. Keeps last ~8000 tokens
    """
    messages = state["messages"]
    trimmed = trim_messages(
        messages,
        strategy="last",
        max_tokens=8000,
        token_counter=lambda msgs: sum(len(str(m.content)) for m in msgs),  # char-based approx: ~4 chars per token
        include_system=True,
        allow_partial=False,
        start_on="human",
    )
    return {"llm_input_messages": trimmed}

AVAILABLE_MODELS = {
    "openai": ["gpt-4o"],
    "local": ["Qwen/Qwen3-8B"],
}

# Local LLM configuration (OpenAI-compatible endpoint, e.g., vLLM)
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL")


def get_llm(
    provider: Literal["openai", "local"] = "openai",
    model: Optional[str] = None,
):
    """
    Factory function to create LLM instance based on provider.
    API keys are read from server-side environment variables only.

    Args:
        provider: "openai" or "local"
        model: Model name (defaults to first model in AVAILABLE_MODELS for provider)

    Returns:
        LLM instance configured for the specified provider
    """
    if not model:
        model = AVAILABLE_MODELS.get(provider, ["gpt-4o-mini"])[0]

    if provider == "local":
        if not LOCAL_LLM_URL:
            raise ValueError("LOCAL_LLM_URL environment variable required for local LLM provider")
        return ChatOpenAI(
            model=model,
            base_url=LOCAL_LLM_URL,
            api_key="not-needed",  # vLLM doesn't require a real key
            temperature=0.0,
        )

    else:  # Default to OpenAI
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY environment variable required for OpenAI provider")
        return ChatOpenAI(
            model=model,
            api_key=key,
            temperature=0.0,
        )


# System prompts for different tool configurations
# Note: {frontend_url} will be replaced at runtime with the actual URL
# Tool usage instructions are NOT repeated here — the LLM reads each tool's docstring automatically.
# The prompt only provides context the LLM can't infer from tool descriptions alone.

SYSTEM_PROMPT_SCRNA = """You are a helpful assistant for the Bloom plant phenotyping platform.
You have READ-ONLY access (GET requests only). You cannot create, update, or delete data.

## Database Tables
- scrna_datasets: id, name, species_id, strain, assembly, annotation
- scrna_cells: id, dataset_id, cell_number, barcode, x, y, cluster_id, replicate
- scrna_genes: id, dataset_id, gene_number, gene_name
- scrna_counts: cell_id, gene_id, count
- scrna_de: id, dataset_id, file_path, cluster_id

## UI Links — Include in Responses
- Expression Explorer: [{frontend_url}/app/expression/{{species_id}}/{{dataset_id}}]({frontend_url}/app/expression/{{species_id}}/{{dataset_id}})
"""

SYSTEM_PROMPT_CYL = """You are a helpful assistant for the Bloom plant phenotyping platform.
You have READ-ONLY access (GET requests only). You cannot create, update, or delete data.

## Database Tables
- cyl_experiments: id, name, scientist_id, species_id, created_at
- cyl_waves: id, experiment_id, name, planting_date
- cyl_plants: id, experiment_id, wave_id, qr_code, accession_id
- cyl_scans: id, plant_id, date_scanned, scanner_id
- cyl_images: id, scan_id, path, angle
- cyl_scan_traits: id, scan_id, trait_name, value
- cyl_scanners: id, name, location

## UI Links — Include in Responses
- Greenhouse: [{frontend_url}/app/greenhouse]({frontend_url}/app/greenhouse)
- Phenotypes: [{frontend_url}/app/phenotypes]({frontend_url}/app/phenotypes)
- Plant Viewer: {frontend_url}/app/greenhouse/{{experiment_id}}/plant/{{plant_id}}
"""

SYSTEM_PROMPT_FULL = """You are a helpful assistant for the Bloom plant phenotyping platform.
You have READ-ONLY access (GET requests only). You cannot create, update, or delete data.

## Two Data Sources — Use the Right Tools

### 1. Database tables → use specialized tools (scrna_*, cyl_*, list_species) or query_database
Tables: scrna_datasets, scrna_cells, scrna_genes, scrna_counts, scrna_de,
species, accessions, cyl_experiments, cyl_waves, cyl_plants, cyl_scans,
cyl_images, cyl_scan_traits, cyl_scanners

### 2. CSV experiment files → use MCP tools (list_available_experiments, load_experiment_data, etc.)
Files like cylinder_alfalfa_gwas_wave2, turface_rice_treatment_exp1 are CSV files
on the filesystem — NOT database tables. Never use query_database for these.

## UI Links — Include in Responses
- Expression Explorer: {frontend_url}/app/expression/{{species_id}}/{{dataset_id}}
- Greenhouse: [{frontend_url}/app/greenhouse]({frontend_url}/app/greenhouse)
- Phenotypes: [{frontend_url}/app/phenotypes]({frontend_url}/app/phenotypes)
- Plant Viewer: {frontend_url}/app/greenhouse/{{experiment_id}}/plant/{{plant_id}}
"""


def create_agent(
    provider: str = "openai",
    model: Optional[str] = None,
    tool_set: str = "all",  # "all", "scrna", "cyl", "generic"
    mcp_tools: list = None,  # Tools from MCP servers (bloommcp, external)
    checkpointer=None,  # PostgresSaver instance (from server lifespan)
):
    """
    Create the LangChain agent with specified LLM provider and tool set.

    Args:
        provider: "openai" or "local"
        model: Model name
        tool_set: Which tools to include:
            - "all": All tools (generic + scrna + cyl)
            - "scrna": Only scRNA-seq tools
            - "cyl": Only cylinder phenotyping tools
            - "generic": Only generic database tools
        mcp_tools: Tools loaded from MCP servers (bloommcp, external MCP servers)
        checkpointer: AsyncPostgresSaver instance for persistent conversation memory
    """
    llm = get_llm(provider=provider, model=model)

    # Select tools and prompt based on tool_set
    if tool_set == "scrna":
        tools = generic_tools + scrna_tools
        system_prompt = SYSTEM_PROMPT_SCRNA
    elif tool_set == "cyl":
        tools = generic_tools + cyl_tools
        system_prompt = SYSTEM_PROMPT_CYL
    elif tool_set == "generic":
        tools = generic_tools
        system_prompt = SYSTEM_PROMPT_FULL
    else:  # "all"
        tools = all_tools
        system_prompt = SYSTEM_PROMPT_FULL

    # Append MCP tools if provided
    if mcp_tools:
        tools = tools + mcp_tools

    # Format prompt with frontend URL for clickable links
    system_prompt = system_prompt.format(frontend_url=FRONTEND_URL)

    return create_react_agent(
        model=llm,
        tools=tools,
        prompt=system_prompt,
        checkpointer=checkpointer,
        pre_model_hook=trim_conversation,
    )
