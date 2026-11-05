# bloom-mcp

FastMCP server exposing SLEAP root-trait analysis tools (QC cleaning/inspection, PCA,
clustering, outlier detection, and plotting) over the Model Context Protocol, backed by the
bloom Supabase database. Every analysis/plotting tool delegates to
[`sleap-roots-analyze`](https://github.com/talmolab/sleap-roots-analyze) — bloom-mcp is a thin
MCP surface over it, not a second home for analysis code.

**Connecting Claude to the hosted server?** See
[docs/connecting-claude-code.md](docs/connecting-claude-code.md) — this README covers running and
developing the server itself, not connecting a client to it.

## Layout

Installable `uv` package under `src/bloom_mcp/`:

- `bloom_mcp.server` — the FastMCP app and `/health` endpoint (`main()` entry point)
- `bloom_mcp.sections` — every MCP tool lives here, one section sub-server per
  contributor/package, one file per tool (see
  `docs/2026-06-29-bloom-mcp-contributor-namespacing.md`). `sections.sleap_roots` wraps
  `sleap-roots-analyze` (PCA, QC, clustering, outlier removal, plotting);
  `sections.core` holds the cross-cutting discovery tools.
- `bloom_mcp.tools` — shared helpers only (`_ports`, `_qc_shared`, `_consumer_utils`),
  not tools themselves
- `bloom_mcp.manifest` — versioned, append-only analysis-artifact manifest/bookkeeping
- `bloom_mcp.supabase_client` — single point of Supabase access

## Running

```bash
uv run bloom-mcp          # or: python -m bloom_mcp
```

`SUPABASE_URL` and `BLOOM_AGENT_KEY` are validated at startup (and lazily on
first Supabase access); `import bloom_mcp` itself requires no environment.

### Salk connector (tunnel)

Outside callers reach bloommcp only through the Salk connector: AIRC signs people in with Salk
and forwards requests through a Cloudflare tunnel to Caddy's tunnel entrance (`caddy:8880`),
which sets `X-Bloom-Tunnel` to `BLOOMMCP_TUNNEL_SECRET` and passes on the person's email in
`X-Salk-User` (`bloom_mcp.tunnel`).

- bloommcp reads `X-Salk-User` only when the request carries exactly one `X-Bloom-Tunnel` equal
  to the secret. Otherwise the request is handled as before and `X-Salk-User` is ignored.
- The email must be one `@salk.edu` address and belong to an active Bloom account
  (`bloom_user_for_email`, called as `bloom_agent`; cached 60 s). Otherwise: 403, or 503 if the
  lookup fails. A POST holding one JSON-RPC request gets the refusal as a JSON-RPC error instead
  (HTTP 200, code `-32001`, `data.status` 403 or 503), since MCP clients show its message.
- An admitted request runs with `BLOOMMCP_API_KEY` in place of a caller token, and its usage is
  recorded under the Bloom account's id. Queries still run as `bloom_agent`.

What this trusts: Caddy's tunnel entrance, which only Caddy and bloommcp hold the secret for; it
never leaves the stack. Any container on the stack's network can still reach `caddy:8880` and set
`X-Salk-User` there; those are our own services.

| Variable | Purpose |
| --- | --- |
| `BLOOMMCP_TUNNEL_SECRET` | Turns the tunnel path on. At least 32 characters, and needs `BLOOMMCP_API_KEY`; startup fails otherwise. Unset (dev, CI): off. |
| `SITE_URL` | Bloom's address, named in the "create an account" refusal. |

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
