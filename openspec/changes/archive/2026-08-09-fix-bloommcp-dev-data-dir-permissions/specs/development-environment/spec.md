## MODIFIED Requirements

### Requirement: Fresh-Clone Stack Startup

`make dev-up` SHALL succeed on a fresh clone that has run `make init`, without the
developer having to hand-create any file that `make init` does not generate. Any
`env_file` a dev service references that is not committed and not produced by
`make init` (e.g. `web/.env`) SHALL be marked optional (`required: false`) so a
missing file does not abort `docker compose up` (issue #123). "Succeed" SHALL include the
`bloommcp` container's bind-mounted data directories (`TRAITS_DIR`, `PLOTS_DIR`,
`ANALYSIS_OUTPUT`) being writable by its runtime user immediately after bring-up — not just
that `docker compose up` itself exits 0 — per the "bloommcp Data Directory Writability"
requirement below.

#### Scenario: dev-up works without a web/.env

- **WHEN** a developer runs `make dev-up` on a fresh clone after `make init`, with
  no `web/.env` present
- **THEN** Compose does not error on the missing `web/.env` — the `bloom-web`
  `env_file` entry is marked `required: false`, and the variables it needs are
  supplied via the service's `environment`/`args` from the root `.env.dev`

## ADDED Requirements

### Requirement: bloommcp Data Directory Writability

The three host directories `bloommcp` bind-mounts SHALL exist and be writable by the
`bloommcp` container's runtime user **before** `docker compose up` runs, on every fresh
clone — specifically `bloommcp/data/TRAITS_DIR`, `bloommcp/data/PLOTS_DIR`, and
`bloommcp/data/ANALYSIS_OUTPUT`. This SHALL NOT rely on Docker's default behavior for a missing bind-mount source
(creating it owned by the Docker daemon's user, typically root) — that default leaves the
non-root `bloommcp` container user unable to write into them, which silently breaks every
tool that writes to local disk (the 5 `sleap_roots` plotting tools always do, regardless of
`BLOOM_STORAGE_BACKEND`; the QC/analysis tools do only in fully-local storage-backend mode).

#### Scenario: Fresh clone provisions writable data directories

- **WHEN** `make dev-up` runs on a fresh clone where `bloommcp/data/` does not yet exist on
  the host
- **THEN** `bloommcp/data/{TRAITS_DIR,PLOTS_DIR,ANALYSIS_OUTPUT}` exist and are writable
  by the `bloommcp` container's runtime user before `docker compose up` starts the container
- **AND** no plotting or fully-local-backend analysis tool call fails with a permission error
  as a result of directory ownership

#### Scenario: A plotting tool succeeds end-to-end against the dev stack

- **WHEN** the dev stack is up and a plotting tool (e.g. `plot_trait_histograms`) is called
  through the MCP interface
- **THEN** it renders and saves its PNG to `PLOTS_DIR` without a permission error and returns
  the expected "Plot saved: `<url>`" summary

#### Scenario: A regression is caught by CI, not a developer

- **WHEN** the directory-provisioning step is skipped or broken
- **THEN** the CI check added by this change (task 2, location per design.md) fails, rather
  than the failure only surfacing when a developer or agent calls a plotting tool for the
  first time
