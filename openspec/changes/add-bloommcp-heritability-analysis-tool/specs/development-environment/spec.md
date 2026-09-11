## MODIFIED Requirements

### Requirement: bloommcp Data Directory Writability

The three host directories `bloommcp` bind-mounts SHALL exist and be writable by the
`bloommcp` container's runtime user **before** `docker compose up` runs, on every fresh
clone — specifically `bloommcp/data/TRAITS_DIR`, `bloommcp/data/PLOTS_DIR`, and
`bloommcp/data/ANALYSIS_OUTPUT`. This SHALL NOT rely on Docker's default behavior for a missing bind-mount source
(creating it owned by the Docker daemon's user, typically root) — that default leaves the
non-root `bloommcp` container user unable to write into them, which silently breaks every
tool that writes to local disk (in fully-local storage-backend mode, every granular tool
does; no tool writes to `PLOTS_DIR` unconditionally any more).

The parenthetical used to say the `sleap_roots` plotting tools "always do, regardless of
`BLOOM_STORAGE_BACKEND`". That stopped being true in two steps: #466 moved
`plot_trait_histograms`/`plot_trait_boxplots`/`plot_correlation_matrix` onto `ResultStore`
persistence, and #462 retired the last two `PLOTS_DIR`-writing tools
(`plot_heritability_bar`, `plot_variance_decomposition`) into `heritability_analysis`, which
also persists through `ResultStore`. `PLOTS_DIR` is still provisioned, mounted, and served
(static mount, env validation, compose bind-mount) but nothing in `bloom_mcp` writes to it;
retiring that plumbing is a separate follow-up. The end-to-end "a plotting tool saves its PNG
to `PLOTS_DIR`" scenario that this requirement carried, and the `make bloommcp-plot-smoke`
step that proved it, went with the last tool that could satisfy it.

#### Scenario: Fresh clone provisions writable data directories

- **WHEN** `make dev-up` runs on a fresh clone where `bloommcp/data/` does not yet exist on
  the host
- **THEN** `bloommcp/data/{TRAITS_DIR,PLOTS_DIR,ANALYSIS_OUTPUT}` exist and are writable
  by the `bloommcp` container's runtime user before `docker compose up` starts the container
- **AND** no plotting or fully-local-backend analysis tool call fails with a permission error
  as a result of directory ownership

#### Scenario: A figure-producing tool succeeds end-to-end against the dev stack

- **WHEN** the dev stack is up and a figure-producing tool (e.g. `heritability_analysis` with
  `include_plots=true`, or `plot_trait_histograms`) is called through the MCP interface
- **THEN** it renders its figure(s) and persists them through the `ResultStore` port without a
  permission error, returning `resource_link`s into a versioned run — never a bare
  `PLOTS_DIR` URL

#### Scenario: A regression is caught by CI, not a developer

- **WHEN** the directory-provisioning step is skipped or broken
- **THEN** the CI check added by this change (task 2, location per design.md) fails, rather
  than the failure only surfacing when a developer or agent calls a plotting tool for the
  first time
