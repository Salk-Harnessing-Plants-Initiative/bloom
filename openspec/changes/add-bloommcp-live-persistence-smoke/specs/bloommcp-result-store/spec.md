## ADDED Requirements

### Requirement: Live Supabase Persistence Smoke

A live smoke SHALL drive at least one workflow end-to-end through the real
`SupabaseResultStore` and `SupabaseReader` against the running dev stack (Supabase +
storage-api + MinIO) and assert the write-path guarantees the persistence layer provides:
a committed run lands in storage with a v3 manifest carrying resolved provenance, each
recorded content hash equals the bytes actually stored, `get_run("latest")` reads the
committed run back and advances on a second commit, and `import bloom_mcp` is clean with
no Supabase env. The smoke SHALL exit non-zero and name the failing check on any violated
guarantee, so a regression fails the job rather than passing silently.

#### Scenario: Committed run lands with a v3 manifest and resolved provenance

- **WHEN** the smoke drives a stochastic workflow (clustering/kmeans, which resolves
  `seed=42`) through the real `SupabaseResultStore` and reads the `manifest.json` back
  from storage via the real read path
- **THEN** the manifest's schema version equals 3 and its latest `VersionEntry` carries a
  non-null `seed` equal to 42, an `agent` equal to `bloom_agent`, a populated
  `environment`, and non-empty `output_sha256` and `output_keys` maps sharing one key-set

#### Scenario: Recorded hash equals the bytes actually stored

- **WHEN** the smoke downloads each object named in the latest entry's `output_keys` from
  the bucket and hashes the returned bytes
- **THEN** each `sha256(downloaded bytes)` equals the corresponding `output_sha256` value
  recorded in the manifest

#### Scenario: get_run("latest") reads back and advances on a second commit

- **WHEN** the smoke calls `get_run(experiment, tool_class, "latest")` after the first
  commit, then runs the workflow a second time
- **THEN** the first `get_run("latest")` resolves the committed run, and after the second
  run `latest` advances from `v1` to `v2`

#### Scenario: Import is clean with no Supabase env

- **WHEN** the smoke runs `import bloom_mcp` (including the Tier-2 `_ports` composition
  root that constructs adapters at module load) in a subprocess with `SUPABASE_URL` and
  `BLOOM_AGENT_KEY` removed from the environment, before configuring the live env
- **THEN** the import succeeds with no error, proving the Tier-0 lazy-validation contract
  holds for the real composition root

#### Scenario: A violated guarantee fails the smoke

- **WHEN** any asserted guarantee does not hold — for example a downloaded object's hash
  does not match the recorded `output_sha256`, the seed is null, or the workflow returns
  an error
- **THEN** the smoke routes the failure through its per-check summary and exits non-zero,
  naming the failing check, rather than passing or aborting with an unlabelled traceback

### Requirement: Persistence Smoke CI Gate

The live smoke SHALL be packaged as a single reusable `make bloommcp-smoke` target so the
local pre-merge step and the CI gate run identical assertions and cannot drift. CI SHALL
invoke `make bloommcp-smoke` only after the dev stack is up and migrated (`make dev-up`,
`make migrate-local`) — the storage-schema grants the bloommcp write path needs are
applied by `make migrate-local`. CI SHALL retain a regression-guard test asserting the
gate's presence and ordering so it cannot be silently deleted or hollowed out.

#### Scenario: CI gates the smoke via the shared target after migration

- **WHEN** the dev-stack CI job has brought the stack up and run `make migrate-local`
- **THEN** the same job runs `make bloommcp-smoke` after the migration step, and a
  persistence regression fails that job

#### Scenario: Gate presence and ordering are regression-guarded

- **WHEN** the `tests/unit/` suite parses `.github/workflows/pr-checks.yml`
- **THEN** it asserts (by step presence and relative order, never a fixed index) that a
  job runs `make migrate-local` before `make bloommcp-smoke` and retains an
  `if: always()` stack-teardown step — failing the PR if the gate is removed, reordered
  before migration, or stripped of cleanup
