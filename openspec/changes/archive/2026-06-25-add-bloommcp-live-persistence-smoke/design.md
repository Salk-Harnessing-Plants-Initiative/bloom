## Context

The Tier-2 persistence layer (#323) ships two real adapters — `SupabaseResultStore` ([bloommcp/src/bloom_mcp/result_store/supabase_store.py](bloommcp/src/bloom_mcp/result_store/supabase_store.py)) and `SupabaseReader` ([bloommcp/src/bloom_mcp/data_access/supabase_reader.py](bloommcp/src/bloom_mcp/data_access/supabase_reader.py)) — wired by the `_ports` composition root ([bloommcp/src/bloom_mcp/tools/_ports.py](bloommcp/src/bloom_mcp/tools/_ports.py)). Every test swaps them for fakes. The fakes are faithful (they hash via the same `hash_outputs` helper), but they cannot catch a regression in the real storage round-trip: a content-type that mutates bytes on upload, a manifest write that races a read, a key scheme that diverges between write and read, or a storage-api that returns a different object than was stored. This change closes that gap with one live smoke run on the already-green dev stack.

The non-trivial part is **not** the assertions (they exist in `_live_smoke.py` already) — it is bridging the host↔container environment so the *same* recipe runs locally and in CI, and choosing a CI shape that keeps the persistence signal attributable.

## Goals / Non-Goals

- **Goals**: One reusable `make bloommcp-smoke` target invoked identically by local pre-merge and CI; real `SupabaseResultStore`/`SupabaseReader` exercised end-to-end; assert v3 manifest provenance + hash-matches-stored-bytes + `get_run("latest")` + version advance + Tier-0 import-clean.
- **Non-Goals**: Fixing the dev stack (already green); prod/staging runner coverage; multi-workflow coverage; changing adapters or schema.

## Decisions

### Decision 1: One `make bloommcp-smoke` target, driven by a Python script

The issue mandates a single shared recipe. The make target owns only the **environment bridging** (bucket, host-reachable URL, host temp dirs, `BLOOM_AGENT_KEY` from `.env.dev`) and shells into a Python driver that owns the **assertions**. This keeps the assertion logic testable, diffable, and free of brittle shell quoting, while the Makefile stays a thin, declarative entrypoint consistent with the other dev targets.

- *Alternative — pytest marker (`@pytest.mark.live`)*: rejected. The smoke must `import bloom_mcp` with a *configured* live env, but the bloommcp `conftest.py` deliberately scrubs `SUPABASE_URL`/`BLOOM_AGENT_KEY` at collection time to guarantee Tier-0 ([bloommcp/tests/conftest.py:16-26](bloommcp/tests/conftest.py#L16-L26)). Running a live test under that conftest fights the harness. A standalone script side-steps the collection-time env scrub entirely.
- *Alternative — inline the whole thing in the Makefile*: rejected. The download-and-hash loop and the version-advance assertion are real logic; shell is the wrong tool and would not be unit-reviewable.

### Decision 2: Host↔container env bridging happens in the target, before import

`.env.dev` is written for in-container processes: `SUPABASE_URL=http://kong:8000` and `BLOOM_*_DIR=/app/data/...`. The smoke runs on the **host**, so the target overrides these (host/container facts mirror DEV_SETUP.md §API Gateway):

- `SUPABASE_URL=http://localhost:$KONG_HTTP_PORT` — the host-reachable Kong gateway. The host port is `${KONG_HTTP_PORT}` (Kong publishes `${KONG_HTTP_PORT}:8000`, [docker-compose.dev.yml:159](docker-compose.dev.yml#L159)), which defaults to 8000 but is configurable. The target derives it from `.env.dev` with a `sed -n 's/^KONG_HTTP_PORT=//p' .env.dev | head -1` + `:-8000` default — the same idiom `migrate-local` already uses for `POSTGRES_*` ([Makefile:240-243](Makefile#L240-L243)) — so a developer who remaps the port does not silently hit the wrong gateway.
- `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` → fresh host temp dirs; the traits dir is seeded with the `turface_19_final_data.csv` fixture as `turface.csv`.
- `BLOOM_AGENT_KEY` — sourced from `.env.dev` (the JWT for the `bloom_agent` role; the write path needs `agent_insert/update_bloommcp_data`). The sourcing line is `@`-prefixed and the value is never echoed or placed on a command line.

`bloom_mcp.experiment_utils` caches the dir globals at import, so env must be set **before** `import bloom_mcp`; the driver also hard-sets `eu.TRAITS_DIR/OUTPUT_DIR/PLOTS_DIR` defensively. A module docstring on the driver records this host↔container rationale so the next reader does not re-derive it.

### Decision 3: Append a step to `dev-stack-smoke`, not a sibling job

Add the `make bloommcp-smoke` invocation as a **step on the existing `dev-stack-smoke` job**, after `make migrate-local`/`make check`, rather than standing up a second job that repeats the `init → dev-up → migrate-local → check` preamble.

- **Cost** is the deciding factor. A sibling job pays a **second full `dev-up`** (Docker build of the whole stack) *plus* a first-on-host bloommcp `uv` resolve (the heavy runtime closure — `statsmodels`, `umap`/`numba`, `scipy`, `sleap-roots-analyze`). The `dev-stack-smoke` preamble's `make check` is also redundant for the smoke. Appending a step pays the bloommcp resolve once and reuses the already-built stack.
- **Attribution** is not lost: the GitHub UI names the failing *step*. A red `dev-stack-smoke` with green up/migrate/check and a red `bloommcp-smoke` step is unambiguous — persistence broke, not stack-health.
- *Alternative — sibling job*: cleaner conceptual separation, but the duplicate `dev-up` + heavy resolve is not worth it for one workflow's assertions. The `make bloommcp-smoke` target keeps this a one-line change if attribution ever proves confusing in practice.
- **Ordering is load-bearing**: the step MUST follow `make migrate-local`, because the storage-schema `GRANT USAGE ... TO bloom_agent` that the write path needs is applied by `migrate-local`'s repair block ([Makefile:257-260](Makefile#L257-L260)), not by `db push` alone. The regression guard asserts this ordering.

### Decision 4: Tier-0 import-clean check runs in a scrubbed subprocess, first

The issue's fourth assertion — `import bloom_mcp` is clean with no Supabase env, since the Tier-2 composition root constructs adapters at module load — cannot be checked in the same process that then configures a live env. The driver runs it as `subprocess.run([sys.executable, "-c", "import bloom_mcp; from bloom_mcp.tools import _ports"], env=<os.environ minus SUPABASE_URL/BLOOM_AGENT_KEY>)` and asserts exit 0, **before** configuring the live env.

### Decision 5: No bucket provisioning in the target — the migration owns it

The `SupabaseResultStore` writes to the `bloommcp-data` bucket ([bloommcp/src/bloom_mcp/supabase_client.py:43](bloommcp/src/bloom_mcp/supabase_client.py#L43)). That bucket is created by migration `20260605000000_create_bloommcp_data_bucket.sql` (`INSERT ... ON CONFLICT DO NOTHING`), which `make migrate-local` applies — so after the required preamble the bucket already exists. The earlier plan to run `make create-bucket BUCKET=bloommcp-data` is dropped: it is redundant *and* broken here — `scripts/create_bucket.py` reads `SUPABASE_KEY` (the service-role key), which is not in the smoke's exported env (`SUPABASE_URL` + `BLOOM_AGENT_KEY`), so the call would fail to authenticate rather than no-op. The driver surfaces a clear error if the bucket is somehow absent.

## Risks / Trade-offs

- **Flake from the live round-trip** (storage-api still warming, read-after-write lag) → the driver bounded-retries the manifest read-back with a **concrete, reviewable** budget (5 attempts, 1s apart, ≤5s ceiling), logging each attempt, so a genuine regression still fails fast rather than hanging or passing. The count is named (not "a few") so the guard and reviewers have a target.
- **Secret handling** — `BLOOM_AGENT_KEY` is a dev-only JWT minted by `make init`; the target sources it on an `@`-prefixed line, never echoes it or puts it on a command line, and lets `uv run` inherit it from the env. CI uses `make init`-generated local secrets (no repo secrets), same as `dev-stack-smoke`. GitHub logs are world-readable on public repos, so the no-echo rule is enforced, not advisory.
- **Host-side bloommcp resolve** (Decision 3) → appending the step still triggers a first-on-host `uv` resolve of the bloommcp closure; bounded and one-time per run, and far cheaper than a second `dev-up`.
- **Host/container drift** if `.env.dev` paths or `KONG_HTTP_PORT` change → the target derives the gateway URL and dir overrides explicitly from `.env.dev`; the facts mirror DEV_SETUP.md, and the regression guard ensures the job keeps invoking the target after migration.

## Migration Plan

Additive only. `bloommcp/_live_smoke.py` (untracked scratch) is deleted in favor of the maintained `bloommcp/scripts/live_persistence_smoke.py`. No schema, API, or data migration. Rollback = drop the job + target + script + guard test; nothing else depends on them.

## Open Questions

- Should the smoke also assert `SupabaseReader.load_experiment` round-trips a cleaned version, or is the workflow's implicit read sufficient? Default: rely on the workflow's read for now (the workflow loads `turface.csv` through the reader to run), keep the explicit assertions on the write/provenance path.
- Is `clustering/kmeans` the best single workflow, or should QC (deterministic, no seed) run too for a non-stochastic contrast? Default: one stochastic workflow covers the harder seed path; add QC only if a deterministic regression appears.
