## MODIFIED Requirements

### Requirement: Lazy Environment Validation

No `bloom_mcp` module SHALL validate runtime environment at import time. Both the
`bloom_mcp.supabase_client` Supabase credentials (`SUPABASE_URL`,
`BLOOM_AGENT_KEY`) and the `bloom_mcp.experiment_utils` data directories
(`BLOOM_TRAITS_DIR`, `BLOOM_OUTPUT_DIR`, `BLOOM_PLOTS_DIR`, `BLOOM_PLOTS_URL`)
SHALL be validated only by an explicit `validate_env()` (and, for Supabase, at
first access), so that `import bloom_mcp` and the fakes-based unit tests succeed
with **no** runtime environment set. When `BLOOM_STORAGE_BACKEND=local` and the
single `BLOOM_LOCAL_ROOT` variable is set, `BLOOM_TRAITS_DIR`, `BLOOM_OUTPUT_DIR`,
and `BLOOM_PLOTS_DIR` SHALL each become individually optional: `validate_env()`
SHALL instead require only that `BLOOM_LOCAL_ROOT` itself exists and is a writable
directory, and SHALL create the `input/`, `output/`, and `plots/` subfolders under
it as needed. Any of the three variables that IS explicitly set SHALL keep today's
stricter contract (must already exist as a directory) regardless of
`BLOOM_LOCAL_ROOT`. In every other combination — a variable unset with no
`BLOOM_LOCAL_ROOT`, or the default (non-local) backend — validation is unchanged.

#### Scenario: Import succeeds with no runtime env

- **WHEN** `import bloom_mcp.server` runs in a fresh interpreter with none of
  `SUPABASE_URL`, `BLOOM_AGENT_KEY`, or the `BLOOM_*_DIR` / `BLOOM_PLOTS_URL`
  variables set
- **THEN** the import succeeds and raises no `RuntimeError`

#### Scenario: First Supabase access validates and names the missing variable

- **WHEN** a Supabase client accessor is called with `SUPABASE_URL` set but
  `BLOOM_AGENT_KEY` unset (and the symmetric case, and both unset)
- **THEN** an error is raised at that call site naming exactly the missing variable, and
  no error is raised for a variable that is set

#### Scenario: Data-directory validation defers to validate_env

- **WHEN** `bloom_mcp.experiment_utils.validate_env()` is called with any of the
  `BLOOM_*_DIR` / `BLOOM_PLOTS_URL` variables unset
- **THEN** it raises a `RuntimeError` naming the missing variable(s), while merely
  importing the module did not

#### Scenario: BLOOM_LOCAL_ROOT makes the three directory variables optional

- **WHEN** `validate_env()` runs with `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` set to an
  existing writable directory, and `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` all
  unset
- **THEN** validation succeeds, and the `input/`, `output/`, and `plots/` subfolders are created
  under `BLOOM_LOCAL_ROOT` if they do not already exist

#### Scenario: An explicit override still requires pre-existence

- **WHEN** `BLOOM_LOCAL_ROOT` is set and `BLOOM_PLOTS_DIR` (or `BLOOM_EXPERIMENT_LOCAL_ROOT` /
  `BLOOM_STORAGE_LOCAL_ROOT`) is also explicitly set to a path that does not exist
- **THEN** validation still raises rather than auto-creating the explicitly-named path

### Requirement: Server Boot Fail-Fast Preserved

The MCP server SHALL fail fast at startup when its runtime environment is missing, via
explicit `validate_env()` calls (Supabase credentials and data directories) before
`mcp.run()` rather than an import-time side effect, so a misconfigured deploy fails at
container boot before serving requests. The `/health` endpoint SHALL continue to report
healthy on a correctly configured boot. When `BLOOM_STORAGE_BACKEND=local` and
`BLOOM_LOCAL_ROOT` is set, an unset `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` /
`BLOOM_PLOTS_DIR` is no longer itself a missing-variable failure (see `Lazy Environment
Validation`) — boot instead fails fast only if `BLOOM_LOCAL_ROOT` is missing or
unwritable, or if an explicitly-set variable's path does not exist.

#### Scenario: Misconfigured deploy fails at boot

- **WHEN** the server starts with `SUPABASE_URL` / `BLOOM_AGENT_KEY` **or** any
  `BLOOM_*_DIR` / `BLOOM_PLOTS_URL` variable unset, and this is not the
  `BLOOM_STORAGE_BACKEND=local` + `BLOOM_LOCAL_ROOT` combination
- **THEN** a validator raises a clear error naming the missing variable before the port
  is bound or requests are served

#### Scenario: Configured server boots healthy

- **WHEN** the server starts with the Supabase environment correctly set
- **THEN** it boots and `/health` returns OK

#### Scenario: Fully-local boot with only BLOOM_LOCAL_ROOT set still fails fast on a bad root

- **WHEN** the server starts with `BLOOM_STORAGE_BACKEND=local` and `BLOOM_LOCAL_ROOT` set to a
  path that does not exist or is not writable
- **THEN** boot fails fast naming `BLOOM_LOCAL_ROOT`, before the port is bound
