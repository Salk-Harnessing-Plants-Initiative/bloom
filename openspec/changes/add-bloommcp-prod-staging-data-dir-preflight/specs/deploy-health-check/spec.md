## ADDED Requirements

### Requirement: Production and staging deploys MUST provision writable bloommcp data directories before `docker compose up`

The `deploy-production` and `deploy-staging` jobs MUST pre-create and make writable the three
bind-mounted `bloommcp` data directories (`bloommcp/data/{SLEAP_OUT_CSV,PLOTS_DIR,
ANALYSIS_OUTPUT}`) before their `docker compose up` command runs, so Docker never auto-creates
them owned by the daemon user (which the non-root `bloommcp` container user cannot then write
into). Because these directories persist across deploys on a long-lived host, this requirement
also covers a directory left in an incorrect state by something other than this preflight.

#### Scenario: Production and staging deploys provision data directories before compose up

- **GIVEN** the `deploy-production` or `deploy-staging` job has pulled the latest code onto
  the Salk deploy host
- **WHEN** the workflow reaches the "Deploy production/staging stack" step
- **THEN** a preceding step MUST have already run the data-directory preflight
  (`scripts/ensure_bloommcp_data_dirs.sh`) against that host's checkout
- **AND** the preflight MUST fail the job loudly (non-zero exit) before `docker compose up`
  runs if any of the three directories cannot be made writable

#### Scenario: Preflight surfaces a clear remedy when a directory is already incorrectly owned

- **GIVEN** one of the three directories already exists on the deploy host but is owned by
  neither the invoking deploy user nor an owner the preflight can `chmod` (e.g., left
  root-owned from a `docker compose up` that ran before this preflight existed)
- **WHEN** the preflight step runs
- **THEN** it MUST print an actionable remedy (a `sudo chown`/`sudo rm -rf` command naming the
  affected path) to the job log
- **AND** it MUST exit non-zero, aborting the job before `docker compose up` runs — it MUST
  NOT attempt privilege escalation itself or silently continue

#### Scenario: Preflight is idempotent on an already-correctly-provisioned host

- **GIVEN** a host where `bloommcp/data/{SLEAP_OUT_CSV,PLOTS_DIR,ANALYSIS_OUTPUT}` already
  exist and are writable by the container's runtime user
- **WHEN** the preflight step runs again on a subsequent deploy
- **THEN** it MUST succeed as a no-op (create-if-missing, `chmod` an already-correct
  directory) without failing

### Requirement: CI's compose-health-check MUST provision writable bloommcp data directories before `docker compose up`

The `compose-health-check` job in `pr-checks.yml` MUST pre-create and make writable the same
three `bloommcp` data directories before its `docker compose up` step, even though — unlike the
deploy jobs above — every run starts from a fresh, throwaway GitHub-hosted runner with no
persistent-host ownership risk to guard against.

#### Scenario: compose-health-check provisions data directories before compose up

- **GIVEN** CI's `compose-health-check` job has checked out the repository on a fresh runner
  (`bloommcp/data/` does not yet exist — it is gitignored and untracked)
- **WHEN** the job reaches its
  `docker compose $COMPOSE_FILES -f docker-compose.ci-cache.yml --env-file .env.ci up -d
--build` step
- **THEN** a preceding step MUST have already created and `chmod`-ed the three `bloommcp`
  data directories
- **AND** this MUST hold even though no current step in the job would otherwise surface a
  permission failure on them (no `bloommcp` Docker healthcheck; `BLOOM_STORAGE_BACKEND`
  defaults to `supabase`, not `local`)
