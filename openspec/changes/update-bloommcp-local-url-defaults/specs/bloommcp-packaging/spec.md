## ADDED Requirements

### Requirement: Local-Mode Self-Served Plots URL

The system SHALL default `experiment_utils.PLOTS_URL` (resolved once at import time, mirroring
`PLOTS_DIR`'s own `_resolve_plots_dir()` treatment) — when `BLOOM_PLOTS_URL` is unset and the
single `BLOOM_LOCAL_ROOT` variable itself supplies a default (i.e. `_fully_local_root()` is not
`None`) — to `bloom_mcp.storage_backend.self_serve_base_url()` plus `/plots`, instead of the
empty string. This resolver SHALL reuse the existing `_fully_local_root()` gate so that
`is_local_backend()` (and therefore `BLOOM_STORAGE_BACKEND`) is read only when `BLOOM_LOCAL_ROOT`
is itself set, preserving the package's side-effect-free import contract in every deployment that
has not opted into `BLOOM_LOCAL_ROOT`. `bloom_mcp.server.build_app()` SHALL mount
`starlette.staticfiles.StaticFiles` at `/plots`, serving `experiment_utils.PLOTS_DIR`, whenever
`BLOOM_STORAGE_BACKEND=local` (regardless of which tier resolved `PLOTS_DIR`), so a plot URL built
from either the default or an explicit `BLOOM_PLOTS_URL` pointing at this same address actually
resolves standalone. The `/plots` mount is unauthenticated, matching `/output`'s and `/health`'s
precedent. Outside the `BLOOM_LOCAL_ROOT`-derived tier (an explicitly-set `BLOOM_PLOTS_DIR` with
no `BLOOM_LOCAL_ROOT`, or the default Supabase backend), `BLOOM_PLOTS_URL` remains exactly as
required as before this change.

#### Scenario: Unset BLOOM_PLOTS_URL defaults under the BLOOM_LOCAL_ROOT tier

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` is set to an existing writable
  directory, and `BLOOM_PLOTS_URL` is unset
- **THEN** `experiment_utils.PLOTS_URL` equals `self_serve_base_url() + "/plots"` (e.g.
  `http://localhost:8811/plots` with no `BLOOMMCP_PUBLIC_URL` set) rather than the empty string

#### Scenario: An explicit BLOOM_PLOTS_URL still wins over the default

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` is set, and
  `BLOOM_PLOTS_URL=http://elsewhere:9000/plots` is also set
- **THEN** `experiment_utils.PLOTS_URL` equals `http://elsewhere:9000/plots`, unaffected by
  `self_serve_base_url()`

#### Scenario: The default does not apply outside the BLOOM_LOCAL_ROOT tier

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` is unset, `BLOOM_PLOTS_DIR` is set
  explicitly, and `BLOOM_PLOTS_URL` is unset
- **THEN** `experiment_utils.PLOTS_URL` equals the empty string, exactly as before this change —
  the granular explicit-override tier is unaffected

#### Scenario: Import stays side-effect-free when BLOOM_LOCAL_ROOT is unset

- **WHEN** `import bloom_mcp.server` runs in a fresh interpreter with `BLOOM_LOCAL_ROOT` unset
  (regardless of `BLOOM_STORAGE_BACKEND` or `BLOOM_PLOTS_URL`)
- **THEN** the import succeeds without reading `BLOOM_STORAGE_BACKEND` or calling
  `is_local_backend()` — `_resolve_plots_url()`'s `BLOOM_LOCAL_ROOT` gate short-circuits before
  either happens

#### Scenario: The /plots mount is present only in local mode

- **WHEN** `bloom_mcp.server.build_app()` is called with `BLOOM_STORAGE_BACKEND` unset or
  `supabase`
- **THEN** the resulting app has no route mounted at `/plots`

#### Scenario: The /plots mount actually serves a generated plot

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `PLOTS_DIR` resolves to a directory containing a real
  file `histogram_x.png`, and a test client issues `GET /plots/histogram_x.png` against
  `build_app()`'s returned app
- **THEN** the response is `200` with that file's bytes

## MODIFIED Requirements

### Requirement: Lazy Environment Validation

No `bloom_mcp` module SHALL validate runtime environment at import time. Both the
`bloom_mcp.supabase_client` Supabase credentials (`SUPABASE_URL`, `BLOOM_AGENT_KEY`) and the
`bloom_mcp.experiment_utils` data directories (`BLOOM_TRAITS_DIR`, `BLOOM_OUTPUT_DIR`,
`BLOOM_PLOTS_DIR`, `BLOOM_PLOTS_URL`) SHALL be validated only by an explicit `validate_env()`
(and, for Supabase, at first access), so that `import bloom_mcp` and the fakes-based unit tests
succeed with **no** runtime environment set. When `BLOOM_STORAGE_BACKEND=local` and the single
`BLOOM_LOCAL_ROOT` variable is set, `BLOOM_TRAITS_DIR`, `BLOOM_OUTPUT_DIR`, `BLOOM_PLOTS_DIR`,
**and `BLOOM_PLOTS_URL`** SHALL each become individually optional: `validate_env()` SHALL
instead require only that `BLOOM_LOCAL_ROOT` itself exists and is a writable directory, creating
the `input/`, `output/`, and `plots/` subfolders under it as needed (`BLOOM_PLOTS_URL` carries no
directory to create — it resolves to the self-served default described in "Local-Mode
Self-Served Plots URL"). Any of the four variables that IS explicitly set SHALL keep today's
stricter contract (the three directory variables must already exist; an explicitly-set
`BLOOM_PLOTS_URL` is used verbatim) regardless of `BLOOM_LOCAL_ROOT`. In every other combination
— a variable unset with no `BLOOM_LOCAL_ROOT`, or the default (non-local) backend — validation is
unchanged.

#### Scenario: Import succeeds with no runtime env

- **WHEN** `import bloom_mcp.server` runs in a fresh interpreter with none of `SUPABASE_URL`,
  `BLOOM_AGENT_KEY`, or the `BLOOM_*_DIR` / `BLOOM_PLOTS_URL` variables set
- **THEN** the import succeeds and raises no `RuntimeError`

#### Scenario: First Supabase access validates and names the missing variable

- **WHEN** a Supabase client accessor is called with `SUPABASE_URL` set but `BLOOM_AGENT_KEY`
  unset (and the symmetric case, and both unset)
- **THEN** an error is raised at that call site naming exactly the missing variable, and no error
  is raised for a variable that is set

#### Scenario: Data-directory validation defers to validate_env

- **WHEN** `bloom_mcp.experiment_utils.validate_env()` is called with any of the `BLOOM_*_DIR` /
  `BLOOM_PLOTS_URL` variables unset, and this is not the `BLOOM_STORAGE_BACKEND=local` +
  `BLOOM_LOCAL_ROOT` combination
- **THEN** it raises a `RuntimeError` naming the missing variable(s), while merely importing the
  module did not

#### Scenario: BLOOM_LOCAL_ROOT makes all four variables optional

- **WHEN** `validate_env()` runs with `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` set to an
  existing writable directory, and `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` /
  `BLOOM_PLOTS_URL` all unset
- **THEN** validation succeeds; the `input/`, `output/`, and `plots/` subfolders are created
  under `BLOOM_LOCAL_ROOT` if they do not already exist; and `experiment_utils.PLOTS_URL`
  resolves to the self-served default rather than the empty string

#### Scenario: An explicit override still requires pre-existence

- **WHEN** `BLOOM_LOCAL_ROOT` is set and each of `BLOOM_PLOTS_DIR`, `BLOOM_EXPERIMENT_LOCAL_ROOT`,
  and `BLOOM_STORAGE_LOCAL_ROOT` is independently tested, explicitly set to a path that does not
  exist
- **THEN** validation still raises for each, rather than auto-creating the explicitly-named path

#### Scenario: The default (non-local) backend's directory requirements are unaffected

- **WHEN** `BLOOM_STORAGE_BACKEND` is unset or `supabase`, `BLOOM_LOCAL_ROOT` is set anyway (e.g.
  left over in a shell profile), and `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` /
  `BLOOM_PLOTS_URL` are unset
- **THEN** `validate_env()` still raises naming the missing variables, exactly as before this
  change — `BLOOM_LOCAL_ROOT` has no effect outside `BLOOM_STORAGE_BACKEND=local`

#### Scenario: The resolved PLOTS_DIR constant reflects the BLOOM_LOCAL_ROOT default

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` is set, and `BLOOM_PLOTS_DIR` is
  unset
- **THEN** the `experiment_utils.PLOTS_DIR` module-level constant itself equals
  `<BLOOM_LOCAL_ROOT>/plots` (not merely "validation succeeds") — the value every plot tool
  actually writes to via `_viz_shared.save_plot()`

### Requirement: Server Boot Fail-Fast Preserved

The MCP server SHALL fail fast at startup when its runtime environment is missing, via explicit
`validate_env()` calls before `mcp.run()` rather than an import-time side effect, so a
misconfigured deploy fails at container boot before serving requests. The exact set of required
variables is **backend-aware**: on the default (Supabase) backend it includes the Supabase
credentials and the data directories (`BLOOM_*_DIR`, `BLOOM_PLOTS_URL`); in fully-local mode
(`BLOOM_STORAGE_BACKEND=local`) the Supabase credentials are not required and the local input
root is validated instead (see this capability's "Backend-Aware Boot Gate" requirement), while
the data-directory / plots validation runs in both modes — **except** that
when `BLOOM_LOCAL_ROOT` is also set, an unset `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` /
`BLOOM_PLOTS_DIR` / `BLOOM_PLOTS_URL` is no longer itself a missing-variable failure (see "Lazy
Environment Validation"); boot instead fails fast only if `BLOOM_LOCAL_ROOT` is missing or
unwritable, or if an explicitly-set variable's path does not exist. The `/health` endpoint SHALL
continue to report healthy on a correctly configured boot.

#### Scenario: Misconfigured Supabase-backend deploy fails at boot

- **WHEN** the server starts on the default (Supabase) backend with `SUPABASE_URL` /
  `BLOOM_AGENT_KEY` **or** any `BLOOM_*_DIR` / `BLOOM_PLOTS_URL` variable unset
- **THEN** a validator raises a clear error naming the missing variable before the port is bound
  or requests are served

#### Scenario: Configured server boots healthy

- **WHEN** the server starts with the Supabase environment correctly set
- **THEN** it boots and `/health` returns OK

#### Scenario: Fully-local boot with BLOOM_LOCAL_ROOT needs neither Supabase credentials nor the four directory/URL variables

- **WHEN** the server boots with `BLOOM_STORAGE_BACKEND=local`, `BLOOM_LOCAL_ROOT` set to an
  existing writable directory, `SUPABASE_URL` / `BLOOM_AGENT_KEY` unset, and `BLOOM_TRAITS_DIR` /
  `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` / `BLOOM_PLOTS_URL` all unset
- **THEN** boot succeeds — this is the full 2-variable (`BLOOM_STORAGE_BACKEND` +
  `BLOOM_LOCAL_ROOT`) quick-start from `storage-backends.md`

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
  `BLOOM_PLOTS_DIR` (see design.md Decision 6 of `add-bloommcp-local-root`)

#### Scenario: A BLOOM_LOCAL_ROOT-derived subfolder blocked by a non-directory file fails clearly

- **WHEN** `BLOOM_LOCAL_ROOT` is valid but `<BLOOM_LOCAL_ROOT>/plots` already exists as a regular
  file rather than a directory
- **THEN** boot raises a clear, caller-safe error rather than letting `mkdir`'s raw
  `FileExistsError` propagate
