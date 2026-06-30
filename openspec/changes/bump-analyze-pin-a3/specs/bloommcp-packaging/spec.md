## MODIFIED Requirements

### Requirement: Additive Dependency Set

The `bloommcp` `pyproject.toml` SHALL declare a publication-ready project that adds
`sleap-roots-analyze>=0.1.0a3` and `sleap-roots-contracts[pandas]>=0.1.0a1` (the oracle
+ Phase-2 foundation) while retaining the analysis dependencies still imported directly
by the vendored modules. The `sleap-roots-analyze` floor SHALL be `>=0.1.0a3` so that the
serializable result types (`PCAResult`, `HeritabilityResult`, `KMeansResult`,
`GMMResult`) resolve from the released package for downstream tiers. No dependency that is
still imported by shipped code SHALL be removed. The committed `bloommcp/uv.lock` SHALL
stay in sync with `bloommcp/pyproject.toml`, verified by `scripts/check-uv-locks.py`,
which checks the `langchain`, `bloommcp`, and `services/video-worker` service locks (the
root lock is independent of this package and is not a checked service).

#### Scenario: Build and import succeed in a clean environment

- **WHEN** the package is built with `uv` and imported in a clean environment, and the
  dev group is resolved
- **THEN** the build succeeds, `import bloom_mcp` works, and no runtime dependency is
  missing

#### Scenario: Lockfiles are in sync

- **WHEN** `uv lock --check` runs against the committed `bloommcp/uv.lock`, and
  `scripts/check-uv-locks.py` runs across the `langchain`, `bloommcp`, and
  `services/video-worker` service locks (the root lock is independent and not a checked
  service)
- **THEN** each reports the lockfile in sync with its `pyproject.toml`

#### Scenario: Released analyze result types resolve

- **WHEN** the locked `sleap-roots-analyze` is installed in a clean environment
- **THEN** the resolved version is `>=0.1.0a3` and
  `from sleap_roots_analyze import PCAResult, HeritabilityResult, KMeansResult, GMMResult`
  succeeds
