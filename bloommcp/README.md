# bloom-mcp

FastMCP server exposing SLEAP root-trait analysis tools (QC cleaning/inspection, PCA,
clustering, outlier detection, and plotting) over the Model Context Protocol, backed by the
bloom Supabase database. Every analysis/plotting tool delegates to
[`sleap-roots-analyze`](https://github.com/talmolab/sleap-roots-analyze) — bloom-mcp is a thin
MCP surface over it, not a second home for analysis code.

## Layout

Installable `uv` package under `src/bloom_mcp/`:

- `bloom_mcp.server` — the FastMCP app and `/health` endpoint (`main()` entry point)
- `bloom_mcp.tools` — granular MCP tool modules (each delegates to `sleap_roots_analyze`)
- `bloom_mcp.sections` — per-contributor/package section sub-servers (see
  `docs/2026-06-29-bloom-mcp-contributor-namespacing.md`)
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

`make bloommcp-smoke` (from the repo root, with the dev stack up + migrated) drives the
granular bloom-mcp tools end-to-end through the **real** Supabase storage and asserts the
committed runs' v3 provenance — the live counterpart to the Supabase-free suite above.
See [docs/local-validation.md](docs/local-validation.md) for prerequisites, what each leg
validates, and troubleshooting (design rationale lives in
`openspec/changes/add-bloommcp-live-persistence-smoke/`).
