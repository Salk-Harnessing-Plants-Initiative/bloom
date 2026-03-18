"""
MCP Server Configuration for LangChain Agent

Defines which MCP servers the agent connects to at startup.
Tools from these servers are merged with the native 35 Bloom tools.
"""
import os

MCP_SERVERS = {}

# Bloom MCP server (new tools: SLEAP-trait-analysis-pipeline, future analysis tools)
BLOOM_MCP_URL = os.getenv("BLOOM_MCP_URL")
if BLOOM_MCP_URL:
    bloom_config = {
        "url": BLOOM_MCP_URL,
        "transport": "streamable_http",
    }
    BLOOMMCP_API_KEY = os.getenv("BLOOMMCP_API_KEY")
    if BLOOMMCP_API_KEY:
        bloom_config["headers"] = {"Authorization": f"Bearer {BLOOMMCP_API_KEY}"}
    MCP_SERVERS["bloom-tools"] = bloom_config

# External: web search (optional, requires API key)
MCP_WEB_SEARCH_KEY = os.getenv("MCP_WEB_SEARCH_KEY")
if MCP_WEB_SEARCH_KEY:
    MCP_SERVERS["web-search"] = {
        "command": "npx",
        "args": ["-y", "@anthropic/mcp-web-search"],
        "env": {"ANTHROPIC_API_KEY": MCP_WEB_SEARCH_KEY},
        "transport": "stdio",
    }
