"""Workflow tools — high-level analysis pipelines exposed as single MCP tools.

Each module here defines one workflow that orchestrates now-private helpers
from the corresponding `bloom_mcp/tools/<module>_tools.py` and writes outputs
via `AnalysisWriter` so every run produces a versioned directory under
`<OUTPUT_DIR>/<experiment>/<tool_class>/v<N>_*/`.
"""
