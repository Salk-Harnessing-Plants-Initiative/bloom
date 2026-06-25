## Why

bloom-mcp Tiers 3/4 (`pca_analysis` / `clustering` tools, #308/#309) consume
`sleap-roots-analyze`'s **serializable result types** directly
(`from sleap_roots_analyze import PCAResult, HeritabilityResult, KMeansResult, GMMResult`).
Those types ship in the analyze `0.1.0a3` release. A `pip`/`uv` install resolves
**released** versions, not `main`, so until the pin floor is bumped to `>=0.1.0a3`
bloom-mcp would have to write a throwaway dict→type adapter against `0.1.0a2`'s
untyped `perform_*` returns. Bumping the floor + re-locking lets Tiers 3/4 consume the
upstream typed results and deletes the need for that interim adapter.

This is Tier 3 pre-work tracked by **#327**.

> **Blocked upstream:** `0.1.0a3` is **not yet on PyPI** (latest published is `0.1.0a2`);
> the release is tracked by **talmolab/sleap-roots-analyze#163** (OPEN). The `uv lock`
> step cannot resolve `>=0.1.0a3` until that release lands, so this change is **ready to
> merge but not executable** until then. See `design.md` for sequencing.

## What Changes

- `bloommcp/pyproject.toml`: bump `sleap-roots-analyze>=0.1.0a2` → `>=0.1.0a3`.
  `sleap-roots-contracts[pandas]>=0.1.0a1` is **unchanged** — `main` == `0.1.0a1` ==
  released; no new contracts release is needed.
- Re-lock: `uv lock` in `bloommcp/` (and the root workspace lock) so `uv.lock` resolves
  `0.1.0a3` and its transitive deps.
- Confirm `import bloom_mcp` and the existing test suite stay green; re-snapshot the
  `0.1.0a2` heritability characterization in `tests/test_oracle.py` **only if** the
  `0.1.0a3` library output drifts it (the BLAS-robust drift guards must still pass).

This is a non-breaking dependency floor bump; no application code or public API changes.

## Impact

- Affected specs: `bloommcp-packaging` (MODIFIED: *Additive Dependency Set* — floor
  `>=0.1.0a3`).
- Affected code: `bloommcp/pyproject.toml`, `bloommcp/uv.lock`, root `uv.lock`; possibly
  the characterization snapshot in `bloommcp/tests/test_oracle.py`.
- Unblocks: bloom-mcp Tier 3 (#308) / Tier 4 (#309) consuming upstream typed results.
- Depends on: talmolab/sleap-roots-analyze#163 (a3 release) and, for the spec delta to
  archive cleanly, the `add-bloommcp-package-baseline` change being archived first (it
  owns the *Additive Dependency Set* requirement). See `design.md`.
