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

- **WHEN** `BLOOM_LOCAL_ROOT` is set and each of `BLOOM_PLOTS_DIR`, `BLOOM_EXPERIMENT_LOCAL_ROOT`,
  and `BLOOM_STORAGE_LOCAL_ROOT` is independently tested, explicitly set to a path that does not
  exist
- **THEN** validation still raises for each, rather than auto-creating the explicitly-named path

#### Scenario: The default (non-local) backend's directory requirements are unaffected

- **WHEN** `BLOOM_STORAGE_BACKEND` is unset or `supabase`, `BLOOM_LOCAL_ROOT` is set anyway (e.g.
  left over in a shell profile), and `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR`
  are unset
- **THEN** `validate_env()` still raises naming the missing variables, exactly as before this
  change — `BLOOM_LOCAL_ROOT` has no effect outside `BLOOM_STORAGE_BACKEND=local`

#### Scenario: The resolved PLOTS_DIR constant reflects the BLOOM_LOCAL_ROOT default

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` is set, and `BLOOM_PLOTS_DIR` is
  unset
- **THEN** the `experiment_utils.PLOTS_DIR` module-level constant itself equals
  `<BLOOM_LOCAL_ROOT>/plots` (not merely "validation succeeds") — the value every plot tool
  actually writes to via `_viz_shared.save_plot()`

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

#### Scenario: Fully-local boot fails fast when BLOOM_LOCAL_ROOT does not exist

- **WHEN** the server starts with `BLOOM_STORAGE_BACKEND=local` and `BLOOM_LOCAL_ROOT` set to a
  path that does not exist
- **THEN** boot fails fast naming `BLOOM_LOCAL_ROOT`, before the port is bound

#### Scenario: Fully-local boot fails fast when BLOOM_LOCAL_ROOT is a file, not a directory

- **WHEN** the server starts with `BLOOM_STORAGE_BACKEND=local` and `BLOOM_LOCAL_ROOT` set to an
  existing path that is a regular file rather than a directory
- **THEN** boot fails fast naming `BLOOM_LOCAL_ROOT`, with a message distinct from the
  does-not-exist case

#### Scenario: Fully-local boot fails fast when BLOOM_LOCAL_ROOT is not writable

- **WHEN** the server starts with `BLOOM_STORAGE_BACKEND=local` and `BLOOM_LOCAL_ROOT` set to an
  existing directory without write permission
- **THEN** boot fails fast naming `BLOOM_LOCAL_ROOT` — this check raises, unlike the legacy
  per-directory check's warn-only behavior for `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` /
  `BLOOM_PLOTS_DIR` (see design.md Decision 6)

#### Scenario: A BLOOM_LOCAL_ROOT-derived subfolder blocked by a non-directory file fails clearly

- **WHEN** `BLOOM_LOCAL_ROOT` is valid but `<BLOOM_LOCAL_ROOT>/plots` already exists as a regular
  file rather than a directory
- **THEN** boot raises a clear, caller-safe error rather than letting `mkdir`'s raw
  `FileExistsError` propagate
