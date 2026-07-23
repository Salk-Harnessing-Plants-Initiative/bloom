## ADDED Requirements

### Requirement: Data Mount Directory Naming Matches Its Env Var

Every `bloommcp` bind-mounted data directory SHALL be named for its purpose, matching the
convention already used by its own env var — not named after a tool or file format. Concretely,
the directory backing `BLOOM_TRAITS_DIR` SHALL be `TRAITS_DIR` (not `SLEAP_OUT_CSV`), consistent
with `PLOTS_DIR`/`BLOOM_PLOTS_DIR` and `ANALYSIS_OUTPUT`/`BLOOM_OUTPUT_DIR`, which already match.
This SHALL hold identically across `docker-compose.dev.yml` and `docker-compose.prod.yml`.

#### Scenario: Dev and prod compose files agree on the directory name

- **WHEN** `tests/unit/test_bloommcp_data_mount_rename.py::test_traits_dir_name_matches_env_var_in_both_compose_files`
  runs
- **THEN** it parses both `docker-compose.dev.yml` and `docker-compose.prod.yml` and asserts the
  `bloommcp` service's `BLOOM_TRAITS_DIR` env value and its bind-mount both resolve to
  `/app/data/TRAITS_DIR` (host side `./bloommcp/data/TRAITS_DIR`), and that no `bloommcp` data
  directory anywhere in either file is named after a tool or file format

#### Scenario: Renaming the directory does not change reader behavior

- **WHEN** the bind-mounted directory is renamed
- **THEN** `SupabaseReader`'s raw-input fallback tier, `qc_inspect`'s direct `TRAITS_DIR` read, the
  `phenotyping_segmentation` section's `compute_min`/`compute_median`/`compute_mode` tools, and
  `start_run`'s input-provenance hashing continue to resolve inputs through the `BLOOM_TRAITS_DIR`
  env var unchanged — only the physical folder name changes, not the resolution logic

### Requirement: Prod/Staging Data Mounts Remain Necessary

The three `bloommcp` bind-mounts in `docker-compose.prod.yml` — `TRAITS_DIR` (env
`BLOOM_TRAITS_DIR`), `ANALYSIS_OUTPUT` (env `BLOOM_OUTPUT_DIR`), `PLOTS_DIR` (env
`BLOOM_PLOTS_DIR`) — SHALL remain mounted because each backs at least one reachable code path on
the deployed Supabase-backend surface: `TRAITS_DIR` backs `SupabaseReader`'s raw-input fallback
tier (required on **every** `qc_clean` call, not only an experiment's first, since `qc_clean`
always re-derives from raw input), `qc_inspect`'s direct `TRAITS_DIR` read, and `start_run`'s
input-provenance hashing; `TRAITS_DIR` and `ANALYSIS_OUTPUT` both back the
`phenotyping_segmentation` section's `compute_min`/`compute_median`/`compute_mode` tools, which
are registered and reachable on the combined `/mcp` surface; `PLOTS_DIR` backs every plotting tool
regardless of storage backend. None SHALL be unmounted without first retiring or rewiring the
consumer(s) that depend on it — see the open follow-up tracked by #476 (retiring the
`supabase_reader.py`/`qc_inspect` `BLOOM_TRAITS_DIR` bypasses), which would need to land and be
re-evaluated against this requirement before any future removal.

#### Scenario: A CI shape test fails if a necessary mount is removed

- **WHEN** `tests/unit/test_bloommcp_data_mount_rename.py::test_prod_compose_keeps_all_three_bloommcp_data_mounts`
  runs
- **THEN** it parses `docker-compose.prod.yml` and asserts the `bloommcp` service's `volumes` list
  still contains bind-mounts for `TRAITS_DIR`, `ANALYSIS_OUTPUT`, and `PLOTS_DIR`, failing CI if any
  is removed without a corresponding spec change

#### Scenario: A genuinely dead mount is removed only alongside its consumer and this requirement

- **WHEN** a follow-up change (e.g. #476) retires the `phenotyping_segmentation` demo tools' use of
  a directory, or migrates `SupabaseReader`'s/`qc_inspect`'s raw-input reads off local disk
  entirely
- **THEN** that change updates this requirement (removing the retired directory from the
  enumerated list) and the corresponding CI shape test in the same change — not silently, and not
  in a change that only touches `docker-compose.prod.yml`

### Requirement: Deploy-Time Directory Rename Is Migrated, Not Orphaned

Because `bloommcp/data/` is gitignored, the deploy workflow's `git reset --hard` never touches it,
so renaming a bind-mount's source directory in `docker-compose.prod.yml` SHALL be paired with an
idempotent host-side migration step in `.github/workflows/deploy.yml` (`deploy-production` and
`deploy-staging` jobs), run before `docker compose ... up`, that renames a pre-existing
`bloommcp/data/SLEAP_OUT_CSV` to `bloommcp/data/TRAITS_DIR` in place when the old path exists and
the new one does not. Without this, a real production/staging host's already-populated raw-input
directory is silently orphaned — Docker auto-creates an empty `TRAITS_DIR`, `bloommcp` boots
green (directory-existence checks pass on an empty directory), and every subsequent `qc_clean`
call fails, since `qc_clean` always re-derives from raw input rather than only on an experiment's
first run.

This migration step SHALL run **before** the `deploy-health-check` capability's bloommcp
data-directory preflight (`scripts/ensure_bloommcp_data_dirs.sh`, added by
`add-bloommcp-prod-staging-data-dir-preflight`) in both jobs — that preflight's `mkdir -p` would
otherwise auto-create an empty `TRAITS_DIR` first, causing this migration's own
already-migrated-or-fresh-host guard to (correctly, per its own logic) skip renaming the real
populated legacy directory, silently orphaning it exactly as if no migration step existed at all.

#### Scenario: Pre-existing host directory is migrated in place

- **GIVEN** a production or staging host has a populated `bloommcp/data/SLEAP_OUT_CSV/` from a
  prior deploy, and no `bloommcp/data/TRAITS_DIR/` yet
- **WHEN** the deploy workflow runs this change
- **THEN** the migration step renames `SLEAP_OUT_CSV` to `TRAITS_DIR` in place, preserving its
  contents, before `docker compose up` starts the `bloommcp` container

#### Scenario: Already-migrated host is a no-op

- **GIVEN** a host where `bloommcp/data/TRAITS_DIR/` already exists (a prior deploy of this same
  change already migrated it)
- **WHEN** the deploy workflow runs again
- **THEN** the migration step detects the new path already exists and makes no change (idempotent,
  safe to re-run on every deploy)

#### Scenario: A genuinely fresh host is unaffected

- **GIVEN** a host with neither `SLEAP_OUT_CSV` nor `TRAITS_DIR` present
- **WHEN** the deploy workflow runs
- **THEN** the migration step is a no-op, and the `deploy-health-check` capability's data-dir
  preflight (running immediately after) provisions an empty, writable `TRAITS_DIR`

#### Scenario: Migration runs before the data-dir preflight in both deploy jobs

- **GIVEN** both the migration step (this requirement) and the data-dir preflight (from
  `add-bloommcp-prod-staging-data-dir-preflight`) are present in `deploy-production` and
  `deploy-staging`
- **WHEN** `tests/unit/test_deploy_data_dir_preflight_ordering.py::test_deploy_jobs_provision_data_dirs_before_compose_up`
  runs
- **THEN** it asserts the migration step's index precedes the data-dir preflight step's index in
  both jobs' `steps` list
