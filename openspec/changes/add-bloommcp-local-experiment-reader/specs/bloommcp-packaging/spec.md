## ADDED Requirements

### Requirement: Backend-Aware Boot Gate

When fully-local mode is active (`BLOOM_STORAGE_BACKEND=local`), server boot SHALL NOT require the Supabase credentials: `server.main()` SHALL NOT call `validate_supabase_env()`. Instead it SHALL validate the local input root (`BLOOM_EXPERIMENT_LOCAL_ROOT` when set, otherwise `BLOOM_TRAITS_DIR`) — exists and is a readable directory; the local output root is already validated by the storage-backend boot validation (`validate_storage_backend`). On the default (Supabase) backend, `validate_supabase_env()` SHALL run exactly as before. The data-directory and storage-backend validation SHALL run in both modes. This makes a fully-local run possible with no live Supabase and closes the boot-gate finding raised in the #389 review.

#### Scenario: Fully-local boot needs no Supabase credentials

- **WHEN** the server boots with `BLOOM_STORAGE_BACKEND=local`, `SUPABASE_URL` / `BLOOM_AGENT_KEY` unset, and the local input and output roots configured and valid
- **THEN** boot succeeds without raising for the missing Supabase credentials, and `validate_supabase_env()` is not called

#### Scenario: Fully-local boot validates the local input root

- **WHEN** the server boots with `BLOOM_STORAGE_BACKEND=local` and the resolved local input root missing or not a readable directory
- **THEN** a validator raises a clear error naming the local input root before the port is bound or requests are served

#### Scenario: Default backend still requires Supabase at boot

- **WHEN** the server boots with `BLOOM_STORAGE_BACKEND` unset or `supabase` and `SUPABASE_URL` / `BLOOM_AGENT_KEY` unset
- **THEN** boot fails fast naming the missing Supabase variable, exactly as before this change

#### Scenario: Fully-local import, boot, and run make no live Supabase call

- **WHEN** `import bloom_mcp`, server boot through `main()`'s validators, and a full `qc_clean → pca_analysis` run execute with `BLOOM_STORAGE_BACKEND=local` and `SUPABASE_URL` / `BLOOM_AGENT_KEY` unset (local input + output roots on temp dirs)
- **THEN** all complete successfully with no live Supabase access, producing real output files under the local output root

## MODIFIED Requirements

### Requirement: Server Boot Fail-Fast Preserved

The MCP server SHALL fail fast at startup when its runtime environment is missing, via explicit `validate_env()` calls before `mcp.run()` rather than an import-time side effect, so a misconfigured deploy fails at container boot before serving requests. The exact set of required variables is **backend-aware**: on the default (Supabase) backend it includes the Supabase credentials and the data directories; in fully-local mode (`BLOOM_STORAGE_BACKEND=local`) the Supabase credentials are not required and the local input root is validated instead (see Backend-Aware Boot Gate). The `/health` endpoint SHALL continue to report healthy on a correctly configured boot.

#### Scenario: Misconfigured Supabase-backend deploy fails at boot

- **WHEN** the server starts on the default (Supabase) backend with `SUPABASE_URL` / `BLOOM_AGENT_KEY` **or** any `BLOOM_*_DIR` / `BLOOM_PLOTS_URL` variable unset
- **THEN** a validator raises a clear error naming the missing variable before the port is bound or requests are served

#### Scenario: Configured server boots healthy

- **WHEN** the server starts with the Supabase environment correctly set
- **THEN** it boots and `/health` returns OK
