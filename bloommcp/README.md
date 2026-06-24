# bloom-mcp

FastMCP server exposing SLEAP root-trait analysis tools (QC, descriptive stats,
dimensionality reduction, clustering, outlier detection, correlation, and
visualization) over the Model Context Protocol, backed by the bloom Supabase
database.

## Layout

Installable `uv` package under `src/bloom_mcp/`:

- `bloom_mcp.server` — the FastMCP app and `/health` endpoint (`main()` entry point)
- `bloom_mcp.tools` — MCP tool modules and the high-level `workflows`
- `bloom_mcp.storage` — versioned, append-only analysis-artifact storage
- `bloom_mcp.supabase_client` — single point of Supabase access

## Running

```bash
uv run bloom-mcp          # or: python -m bloom_mcp
```

`SUPABASE_URL` and `BLOOM_AGENT_KEY` are validated at startup (and lazily on
first Supabase access); `import bloom_mcp` itself requires no environment.

## Development

```bash
uv sync                   # installs the package + dev group
uv run pytest             # runs the Supabase-free test suite
```

`make bloommcp-smoke` (from the repo root, with the dev stack up + migrated) drives a
workflow end-to-end through the **real** Supabase storage and asserts the committed run's
v3 provenance — the live counterpart to the Supabase-free suite above. See
`openspec/changes/add-bloommcp-live-persistence-smoke/`.
