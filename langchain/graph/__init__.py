"""Graph layer for the Bloom agent runtime.

Submodules:
    state    — typed state schema (`AgentState`) shared across nodes
    freeform — factory for the freeform leaf subgraph (today's behavior)

Subsequent sub-proposals (top router, domain subgraphs, MCP analysis leaves,
parallel recipes) will add their own modules here. The package is intentionally
shallow so each future addition is a small reviewable file.
"""
