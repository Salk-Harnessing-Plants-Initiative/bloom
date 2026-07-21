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
- **Stragglers bypass the port.** The cross-experiment correlation tools read raw CSVs
  from `TRAITS_DIR` — not in `correlation_tools.py` itself but one layer down, in
  `cross_experiment_correlations.load_and_align_experiments`, which takes filesystem
  **paths** fed by a hardcoded `EXPERIMENTS` dict
  ([correlation_tools.py](../../../bloommcp/src/bloom_mcp/tools/correlation_tools.py)),
  and the legacy workflow tools' `start_run` reads `TRAITS_DIR / filename` for the
  source-CSV ([\_ports.py:84](../../../bloommcp/src/bloom_mcp/tools/_ports.py#L84)). So
  even a correct adapter would not be honored consistently.

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
  roles via the shared `detect_columns` oracle **with the same `pd.read_csv`
  configuration as the deployed raw path** (so identical bytes yield identical
  trait/metadata roles), returns the **same `ExperimentFrame` contract** as
  `SupabaseReader` (so consumers are unchanged), reaches **no Supabase** (no
  `supabase_client` import, no PostgREST/table read), rejects any `name` that resolves
  outside its configured root, and emits **no deprecation warning** — the local input
  path is _promoted_, not retired.
- **Promote, don't retire, the local input path.** This reverses the Tier 2 "retire
  `BLOOM_TRAITS_DIR` outright" direction in favor of a proper opt-in adapter — symmetric
  to how #389 handled output. `SupabaseReader`'s raw-read deprecation is re-pointed at the
  `LocalReader` adapter (use `local` mode for local inputs) rather than at removal.
- **Single fully-local switch.** Reuse `BLOOM_STORAGE_BACKEND=local` (the switch #389
  introduced for output) to _also_ select `LocalReader` for input at the composition
  root ([\_ports.py](../../../bloommcp/src/bloom_mcp/tools/_ports.py),
  [server.py](../../../bloommcp/src/bloom_mcp/server.py)), via a new **public**
  backend-name accessor on `storage_backend.py` (today only a private
  `_selected_backend_name()` exists). The reader and store backends SHALL be coupled —
  `LocalReader` is wired only when the active storage backend is also `local`, so a
  reader/store split (local raw input but Supabase cleaned reads) cannot arise. Supabase
  remains the default; behavior is byte-for-byte unchanged when unset. The local input
  root is `BLOOM_EXPERIMENT_LOCAL_ROOT` when set, else `BLOOM_TRAITS_DIR` (already
  required and mounted in dev) — mirroring #389's `BLOOM_STORAGE_LOCAL_ROOT` →
  `BLOOM_OUTPUT_DIR` fallback.
- **Backend-aware boot gate.** When fully-local, `server.main()` MUST NOT call
  `validate_supabase_env()`; it validates the local input root instead (the local output
  root is already validated by #389's `validate_storage_backend`, which also fails fast
  on an invalid `BLOOM_STORAGE_BACKEND` value). The data-directory / plots validation
  (`BLOOM_*_DIR`, `BLOOM_PLOTS_URL`) still runs in both modes. On the default (Supabase)
  path, Supabase credentials remain required and boot behavior is unchanged. This
  directly closes the #389-review boot-gate finding.
- **Route the stragglers through the port.** The cross-experiment correlation reads are
  routed through the injected `ExperimentReader` using `version="raw"` (the port already
  supports it) to preserve today's raw-only semantics — this requires
  `cross_experiment_correlations.load_and_align_experiments` to accept **frames** instead
  of paths, and the hardcoded `EXPERIMENTS` dict / local `list_experiments` to resolve
  through the port. `list_available_experiments` already routes through
  `_ports.reader().list_experiments()`, so it needs no change (AC #4's mention of it is
  satisfied by the existing wiring). `start_run`'s source-CSV read is routed through the
  reader's optional `raw_source_path` capability (the on-disk input at the active
  adapter's root), so the 5 legacy workflow tools that use `start_run` keep a non-empty
  `input_sha256` at the correct root — provenance is preserved, and only a genuinely
  path-less adapter degrades to `None`. This overlaps roadmap cleanup **C** and fulfills the "routed through the port in
  the follow-up" promise the current spec makes — by **promoting** the path, not removing it.
- **Docs.** Update `bloommcp/docs/storage-backends.md` to document that
  `BLOOM_STORAGE_BACKEND=local` is now a **fully-local (offline) dev mode** (input +
  output), with `BLOOM_EXPERIMENT_LOCAL_ROOT`, the boot gate, and an explicit statement
  that this is a **dev / power-user** path — not a normal-user packaged distribution;
  replace the "not a fully-offline mode" caveat.
- **Tests.** `LocalReader` unit tests against fixtures (incl. path-escape rejection and a
  dtype-ambiguous raw CSV read against the same bytes as `SupabaseReader`); a
  `FakeReader`/`SupabaseReader`/`LocalReader` parity check with a named oracle; an
  import-purity subprocess test; and a **fully-local end-to-end test** — `import
bloom_mcp`, server boot, and a full `qc_clean → pca_analysis` run — with `SUPABASE_URL`
  / `BLOOM_AGENT_KEY` unset **and a hard network guard** (`supabase.create_client`
  monkeypatched to raise), asserting real files on disk and **no live Supabase**.

## Impact

- **Affected specs:**
  - `bloommcp-experiment-read` — ADD `LocalReader` adapter, the fully-local reader
    selection (reader/store coupling), and cross-experiment reads routed through the port;
    MODIFY the `SupabaseReader` deprecation stance (re-pointed from removal to promotion)
    and the `ExperimentReader Port` consumer scenario (stragglers routed now, not "in a
    later follow-up that removes the path").
  - `bloommcp-packaging` — MODIFY `Server Boot Fail-Fast Preserved` to be backend-aware;
    ADD a `Backend-Aware Boot Gate` requirement (fully-local boots + runs with no Supabase).
  - **Cross-capability dependency:** this extends the `BLOOM_STORAGE_BACKEND` switch owned
    by the in-flight `bloommcp-storage-backend` capability (#389). That capability is
    **merged in code but not yet archived** (no `openspec/specs/bloommcp-storage-backend/`
    exists), so this change cannot MODIFY it. **Ordering requirement:** #389 MUST archive
    before (or with) this change, and a follow-up MUST reconcile #389's "governs only the
    five object-storage helpers" sentence, which this change makes false. Tracked in the
    tasks / Migration Plan — not silently relied upon.
- **Affected code:**
  - New `bloommcp/src/bloom_mcp/data_access/local_reader.py` — `LocalReader` (Supabase-free).
  - `bloommcp/src/bloom_mcp/data_access/__init__.py` — export `LocalReader`.
  - `bloommcp/src/bloom_mcp/data_access/supabase_reader.py` — re-point the deprecation message.
  - `bloommcp/src/bloom_mcp/storage_backend.py` — add a **public** `selected_backend_name()`
    / `is_local_backend()` accessor for the composition root and boot gate.
  - `bloommcp/src/bloom_mcp/server.py` (`main`) — backend-aware boot gate + wire `LocalReader`
    at the composition root when fully-local (coupled to the store backend).
  - `bloommcp/src/bloom_mcp/experiment_utils.py` — a `BLOOM_EXPERIMENT_LOCAL_ROOT` →
    `BLOOM_TRAITS_DIR` resolver + boot-time validation of the local input root (mirrors
    `_resolve_local_root`).
  - `bloommcp/src/bloom_mcp/tools/correlation_tools.py` **and**
    `bloommcp/src/bloom_mcp/cross_experiment_correlations.py` (`load_and_align_experiments`
    grows a frame-accepting entry point) — read through the injected reader.
  - `bloommcp/src/bloom_mcp/tools/_ports.py` (`start_run`) — snapshot the reader's frame for
    the source-CSV hash.
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
  local/dev path (Mode B). #388 builds on the same `supabase_client` seam (#389) and is
  orthogonal to the reader port, so no interface collision. Different audiences; they coexist.
- **PostgREST/table reads stay out of scope** — `get_postgrest_client` / `read_input_csv`
  are the database, not the experiment-read port (unchanged since #389). This is
  consistent with the fully-local run being Supabase-free: the `qc_clean → pca_analysis`
  path persists via `SupabaseResultStore`, which touches **only** the five object-storage
  helpers (routed through the active backend, local under #389) and makes **no** live
  table call — a stated invariant confirmed by the end-to-end network guard, not an
  assumption deferred to a follow-up.
