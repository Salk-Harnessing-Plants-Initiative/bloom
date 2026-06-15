## ADDED Requirements

### Requirement: Installable Package Layout

The `bloommcp` service SHALL be an installable `uv` package rooted at
`bloommcp/src/bloom_mcp/`, with the former `source/`, `tools/`, and `storage/` modules
(including `supabase_client`) importable under the `bloom_mcp.*` namespace. The package
SHALL declare a `[build-system]` and `src/` package discovery so `uv build` produces an
importable wheel. The restructure SHALL be additive: the booting MCP server's tool
surface and behavior SHALL remain unchanged.

#### Scenario: Built wheel imports under the new namespace

- **WHEN** the package is built with `uv build` and the resulting wheel is installed into
  a clean environment
- **THEN** `bloom_mcp`, `bloom_mcp.tools`, and `bloom_mcp.storage` import without error
  and expose the same tool surface as the pre-restructure prototype

#### Scenario: No stale prototype imports remain

- **WHEN** every module under `bloommcp/src/bloom_mcp/` is scanned for import statements
- **THEN** no import has a first dotted segment of `source`, `tools`, or `storage` —
  every intra-package import resolves under `bloom_mcp.*` (imports of `bloom_mcp.tools`
  / `bloom_mcp.storage` are not matches)

### Requirement: Container Entry Point Preserved

The bloommcp container SHALL build and launch the server against the `src/` layout. The
`Dockerfile` SHALL install the package so `bloom_mcp.*` resolves at runtime, and the
dev compose bind-mount SHALL still reflect local source edits (hot-reload preserved).

#### Scenario: Image builds and boots under both compose files

- **WHEN** the bloommcp image is built via its `Dockerfile` and started under
  `docker-compose.dev.yml` (bind-mounted) and `docker-compose.prod.yml`
- **THEN** the server process starts, resolves `bloom_mcp.*`, and `/health` returns OK
  in both

### Requirement: Lazy Supabase Environment Validation

The `bloom_mcp.supabase_client` module SHALL NOT validate `SUPABASE_URL` or
`BLOOM_AGENT_KEY` at import time. Validation SHALL be deferred to an explicit
`validate_env()` and to first Supabase access, so that `import bloom_mcp` and the
fakes-based unit tests succeed with neither variable set.

#### Scenario: Import succeeds with no Supabase env

- **WHEN** `import bloom_mcp` runs with both `SUPABASE_URL` and `BLOOM_AGENT_KEY` unset
- **THEN** the import succeeds and raises no `RuntimeError`

#### Scenario: First Supabase access validates and names the missing variable

- **WHEN** a Supabase client accessor is called with `SUPABASE_URL` set but
  `BLOOM_AGENT_KEY` unset (and the symmetric case, and both unset)
- **THEN** an error is raised at that call site naming exactly the missing variable, and
  no error is raised for a variable that is set

### Requirement: Server Boot Fail-Fast Preserved

The MCP server SHALL fail fast at startup when the Supabase environment is missing, via
an explicit `validate_env()` call before `mcp.run()` rather than an import-time side
effect, so a misconfigured deploy fails at container boot before serving requests. The
`/health` endpoint SHALL continue to report healthy on a correctly configured boot.

#### Scenario: Misconfigured deploy fails at boot

- **WHEN** the server starts with `SUPABASE_URL` or `BLOOM_AGENT_KEY` unset
- **THEN** `validate_env()` raises a clear error before the port is bound or requests are
  served

#### Scenario: Configured server boots healthy

- **WHEN** the server starts with the Supabase environment correctly set
- **THEN** it boots and `/health` returns OK

### Requirement: Additive Dependency Set

The `bloommcp` `pyproject.toml` SHALL declare a publication-ready project that adds
`sleap-roots-analyze>=0.1.0a2` and `sleap-roots-contracts[pandas]>=0.1.0a1` (the oracle
+ Phase-2 foundation) while retaining the analysis dependencies still imported directly
by the vendored modules. No dependency that is still imported by shipped code SHALL be
removed. Committed lockfiles (`bloommcp/uv.lock` + root) SHALL stay in sync with their
`pyproject.toml`.

#### Scenario: Build and import succeed in a clean environment

- **WHEN** the package is built with `uv` and imported in a clean environment, and the
  dev group is resolved
- **THEN** the build succeeds, `import bloom_mcp` works, and no runtime dependency is
  missing

#### Scenario: Lockfiles are in sync

- **WHEN** `uv lock --check` runs against the committed `bloommcp/uv.lock` and the root
  lock (and `scripts/check-uv-locks.py` runs)
- **THEN** each reports the lockfile in sync with its `pyproject.toml`

### Requirement: Supabase-Free Test Stack with Cross-Tier Oracle

The package SHALL provide a `bloommcp/tests/` layout using `pytest`, `hypothesis`,
`syrupy`, and the FastMCP `Client`, runnable with fakes and **no live Supabase**, and
this suite SHALL be executed by CI. The #120 turface_19 fixture and its independently
recorded golden values SHALL be committed under `bloommcp/tests/fixtures/` and asserted —
with explicit numeric tolerances, not auto-generated snapshots — by an oracle test that
the `sleap-roots-analyze` `perform_*` functions reproduce.

#### Scenario: Suite collects, runs without Supabase, and is gated by CI

- **WHEN** the CI bloommcp test job runs `uv run pytest` with no live Supabase and
  `SUPABASE_URL` / `BLOOM_AGENT_KEY` unset
- **THEN** the suite collects and the unit tests pass using fakes, and the job fails the
  PR if they do not

#### Scenario: Oracle reproduces independently recorded golden values

- **WHEN** the `sleap-roots-analyze` `perform_*` functions run on the committed
  turface_19 fixture
- **THEN** their outputs match the independently recorded #120 golden values in
  `bloommcp/tests/fixtures/` within the stated tolerance
