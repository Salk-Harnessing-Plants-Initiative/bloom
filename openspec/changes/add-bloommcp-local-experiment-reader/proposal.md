## Why

[#389](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/389) added a
local-filesystem **output** backend (`LocalStorageBackend`,
`BLOOM_STORAGE_BACKEND=local`), so bloommcp can now write versioned analysis
artifacts to disk instead of Supabase Storage. But the **input side is
asymmetric**, so a fully-local (Supabase-free) run is still impossible:

- **Boot hard-requires Supabase.** `server.main()` calls `validate_supabase_env()`
  unconditionally before the data-env check
  ([server.py](../../../bloommcp/src/bloom_mcp/server.py)), so even with
  `BLOOM_STORAGE_BACKEND=local` the server **cannot boot** without `SUPABASE_URL` /
  `BLOOM_AGENT_KEY`. This is the exact boot-gate finding flagged in the #389 review,
  and why #389's docs state "`local` is not a fully-offline mode."
- **No local reader adapter.** The `ExperimentReader` port
  ([ports.py](../../../bloommcp/src/bloom_mcp/data_access/ports.py)) has only
  `SupabaseReader` and the in-memory `FakeReader`. `SupabaseReader`'s raw-input read
  from local `BLOOM_TRAITS_DIR` is **deprecated** — it emits a `DeprecationWarning`
  ([supabase_reader.py:28-31,59-60](../../../bloommcp/src/bloom_mcp/data_access/supabase_reader.py#L28-L31))
  because the Tier 2 consolidation plans to **retire** `BLOOM_TRAITS_DIR` in favor of
  Supabase `bloommcp_input/`. So bloommcp is gaining a local _output_ path while
  removing its local _input_ path.
- **Stragglers bypass the port.** `correlation_tools.py` reads raw CSVs directly from
  `TRAITS_DIR` ([correlation_tools.py](../../../bloommcp/src/bloom_mcp/tools/correlation_tools.py)),
  and `start_run` reads `TRAITS_DIR / filename` for the source-CSV
  ([\_ports.py:84](../../../bloommcp/src/bloom_mcp/tools/_ports.py#L84)), so even a
  correct adapter would not be honored consistently.

This change is the **input-side twin of #389**: a first-class opt-in **`LocalReader`
adapter behind the `ExperimentReader` port** plus a **backend-aware boot gate**, so
bloommcp runs **fully local with no Supabase** — driven by Claude Code / Claude
Desktop as the MCP client, for **dev / power-user use** (roadmap "Mode B"). It is the
_cheap_ half of local mode — adapter + boot only — and does **not** include packaging
for non-technical users (see Non-Goals).

## What Changes

- Introduce a **`LocalReader`** adapter implementing the existing `ExperimentReader`
  port: `load_experiment(name, version, require_clean)` → `ExperimentFrame` and
  `list_experiments()`, reading experiment CSVs from a configurable local dir and
  resolving cleaned/versioned outputs from the local output store. It declares column
  roles via the shared `detect_columns` oracle (same source of truth as the other
  adapters), returns the **same `ExperimentFrame` contract** as `SupabaseReader` (so
  consumers are unchanged), reaches **no Supabase** (no `supabase_client` import, no
  PostgREST/table read), and emits **no deprecation warning** — the local input path is
  _promoted_, not retired.
- **Promote, don't retire, the local input path.** This reverses the Tier 2 "retire
  `BLOOM_TRAITS_DIR` outright" direction in favor of a proper opt-in adapter — symmetric
  to how #389 handled output. `SupabaseReader`'s raw-read deprecation is re-pointed at the
  `LocalReader` adapter (use `local` mode for local inputs) rather than at removal.
- **Single fully-local switch.** Reuse `BLOOM_STORAGE_BACKEND=local` (the switch #389
  introduced for output) to _also_ select `LocalReader` for input at the composition
  root ([\_ports.py](../../../bloommcp/src/bloom_mcp/tools/_ports.py),
  [server.py](../../../bloommcp/src/bloom_mcp/server.py)). Supabase remains the default;
  behavior is byte-for-byte unchanged when unset. The local input root is
  `BLOOM_EXPERIMENT_LOCAL_ROOT` when set, else `BLOOM_TRAITS_DIR` (already required and
  mounted in dev) — mirroring #389's `BLOOM_STORAGE_LOCAL_ROOT` → `BLOOM_OUTPUT_DIR`
  fallback.
- **Backend-aware boot gate.** When fully-local, `server.main()` MUST NOT call
  `validate_supabase_env()`; it validates the local input root instead (the local output
  root is already validated by #389's `validate_storage_backend`). On the default
  (Supabase) path, Supabase credentials remain required and boot behavior is unchanged.
  This directly closes the #389-review boot-gate finding.
- **Route the stragglers through the port.** `correlation_tools.py`'s cross-experiment
  raw-CSV reads and `start_run`'s source-CSV read go through the injected
  `ExperimentReader` so the active adapter (local or Supabase) is honored consistently.
  This overlaps roadmap cleanup **C** and fulfills the "routed through the port in the
  follow-up" promise the current spec makes — by **promoting** the path, not removing it.
- **Docs.** Update `bloommcp/docs/storage-backends.md` to document that
  `BLOOM_STORAGE_BACKEND=local` is now a **fully-local (offline) dev mode** (input +
  output), with `BLOOM_EXPERIMENT_LOCAL_ROOT`, the boot gate, and an explicit statement
  that this is a **dev / power-user** path — not a normal-user packaged distribution.
- **Tests.** `LocalReader` unit tests against fixtures; a `FakeReader`/`SupabaseReader`
  parity check for observable behavior; and a **fully-local end-to-end test** — `import
bloom_mcp`, server boot, and a full `qc_clean → pca_analysis` run — all with **no live
  Supabase** (`SUPABASE_URL` / `BLOOM_AGENT_KEY` unset).

## Impact

- **Affected specs:**
  - `bloommcp-experiment-read` — ADD `LocalReader` adapter, the fully-local reader
    selection, and cross-experiment reads routed through the port; MODIFY the
    `SupabaseReader` deprecation stance (re-pointed from removal to promotion) and the
    `ExperimentReader Port` consumer scenario (stragglers routed now, not "in a later
    follow-up that removes the path").
  - `bloommcp-packaging` — MODIFY `Server Boot Fail-Fast Preserved` to be backend-aware;
    ADD a `Backend-Aware Boot Gate` requirement (fully-local boots + runs with no Supabase).
  - Extends the `BLOOM_STORAGE_BACKEND` switch owned by the in-flight `bloommcp-storage-backend`
    capability (#389); the two coexist (input-select + boot-gate here; object-storage select there).
- **Affected code:**
  - New `bloommcp/src/bloom_mcp/data_access/local_reader.py` — `LocalReader` (Supabase-free).
  - `bloommcp/src/bloom_mcp/data_access/__init__.py` — export `LocalReader`.
  - `bloommcp/src/bloom_mcp/data_access/supabase_reader.py` — re-point the deprecation message.
  - `bloommcp/src/bloom_mcp/server.py` (`main`) — backend-aware boot gate + wire `LocalReader`
    at the composition root when fully-local.
  - `bloommcp/src/bloom_mcp/experiment_utils.py` — a small `BLOOM_EXPERIMENT_LOCAL_ROOT`
    resolver + boot-time validation of the local input root (mirrors `_resolve_local_root`).
  - `bloommcp/src/bloom_mcp/tools/correlation_tools.py` and
    `bloommcp/src/bloom_mcp/tools/_ports.py` (`start_run`) — read through the injected reader.
  - `docker-compose.dev.yml` — document the input var alongside #389's storage vars (commented; default off).
  - Docs: `bloommcp/docs/storage-backends.md`.
  - Tests under `bloommcp/tests/data_access/` and a fully-local end-to-end test.

## Scope / Non-Goals

- **No packaging/distribution for non-technical users** — no `.mcpb` Claude Desktop
  bundle, no installer UX. Separate, larger product decision.
- **No per-user identity / real RLS** (separate deferred `ResultStore` item).
- **No change to the deployed default** — Supabase stays the default; prod/staging are
  untouched; `local` is opt-in only.
- **Does not replace #388** — #388 serves the deployed web product (Mode A); this is the
  local/dev path (Mode B). Different audiences; they coexist.
- **PostgREST/table reads stay out of scope** — `get_postgrest_client` / `read_input_csv`
  are the database, not the experiment-read port (unchanged since #389). The fully-local
  `qc_clean → pca_analysis` path is verified to make no live table call; any residual
  table dependency on that path is surfaced by the end-to-end test rather than assumed away.
