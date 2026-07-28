"""Single source of truth for which MCP tools are "foundational" — always
included in the agent's toolset regardless of tool_set/mcp_tool_names
routing (routes/chat.py), and marked on the GET /langchain/mcp-tools
response so the web client can filter its tool picker without keeping its
own copy of this list (server.py).

inspect_data_quality was dropped (bloom-mcp devendor-bloommcp-analysis;
redundant with qc_inspect). Matched prefix-aware (exact name or
"<section>_<name>") so the Phase-2 sections migration's namespacing (e.g.
core_list_available_experiments) doesn't silently empty this set — see
bloommcp/tests/test_devendor_invariants.py::test_tool_name_lists_match_live_registry.
"""

ALWAYS_INCLUDE_MCP_TOOLS = {
    "list_available_experiments",
    "load_experiment_data",
    "list_existing_analyses",
}


def is_foundational_tool(tool_name: str) -> bool:
    return tool_name in ALWAYS_INCLUDE_MCP_TOOLS or any(
        tool_name.endswith(f"_{base}") for base in ALWAYS_INCLUDE_MCP_TOOLS
    )
