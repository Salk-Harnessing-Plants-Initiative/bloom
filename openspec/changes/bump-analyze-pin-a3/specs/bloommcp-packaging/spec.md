## MODIFIED Requirements

### Requirement: Additive Dependency Set

The `bloommcp` `pyproject.toml` SHALL declare a publication-ready project that adds
`sleap-roots-analyze>=0.1.0a3` and `sleap-roots-contracts[pandas]>=0.1.0a1` (the oracle
+ Phase-2 foundation) while retaining the analysis dependencies still imported directly
by the vendored modules. The `sleap-roots-analyze` floor SHALL be `>=0.1.0a3` so that the
serializable result types (`PCAResult`, `HeritabilityResult`, `KMeansResult`,
`GMMResult`) resolve from the released package for downstream tiers. No dependency that is
still imported by shipped code SHALL be removed. Committed lockfiles (`bloommcp/uv.lock` +
root) SHALL stay in sync with their `pyproject.toml`.

#### Scenario: Build and import succeed in a clean environment

- **WHEN** the package is built with `uv` and imported in a clean environment, and the
  dev group is resolved
- **THEN** the build succeeds, `import bloom_mcp` works, and no runtime dependency is
  missing

#### Scenario: Lockfiles are in sync

- **WHEN** `uv lock --check` runs against the committed `bloommcp/uv.lock` and the root
  lock (and `scripts/check-uv-locks.py` runs)
- **THEN** each reports the lockfile in sync with its `pyproject.toml`

#### Scenario: Released analyze result types resolve

- **WHEN** the locked `sleap-roots-analyze` is installed in a clean environment
- **THEN** the resolved version is `>=0.1.0a3` and
  `from sleap_roots_analyze import PCAResult, HeritabilityResult, KMeansResult, GMMResult`
  succeeds
