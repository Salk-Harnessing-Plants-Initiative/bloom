## MODIFIED Requirements

### Requirement: bloommcp Data Directory Writability

The three host directories `bloommcp` bind-mounts SHALL exist and be writable by the
`bloommcp` container's runtime user **before** `docker compose up` runs, on every fresh
clone — specifically `bloommcp/data/TRAITS_DIR`, `bloommcp/data/PLOTS_DIR`, and
`bloommcp/data/ANALYSIS_OUTPUT`. This SHALL NOT rely on Docker's default behavior for a missing bind-mount source
(creating it owned by the Docker daemon's user, typically root) — that default leaves the
non-root `bloommcp` container user unable to write into them, which silently breaks every
tool that writes to local disk (the 3 surviving `sleap_roots` plotting tools always do,
regardless of `BLOOM_STORAGE_BACKEND`; the QC/analysis tools do only in fully-local
storage-backend mode).

`heritability_analysis` — which replaced the retired `plot_heritability_bar` /
`plot_variance_decomposition` — is deliberately **not** covered by the "always writes to local
disk" clause despite rendering figures: it persists them through the `ResultStore` port like
every other granular consumer, so in a Supabase-backed configuration it never touches
`PLOTS_DIR`. "Plot tool" and "writes to `PLOTS_DIR`" are no longer equivalent.

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
