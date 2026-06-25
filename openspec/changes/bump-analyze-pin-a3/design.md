## Context

#327 is Tier 3 pre-work: bump the `sleap-roots-analyze` floor from `>=0.1.0a2` to
`>=0.1.0a3` and re-lock, so Tiers 3/4 (#308/#309) can import the serializable result
types (`PCAResult`, `HeritabilityResult`, `KMeansResult`, `GMMResult`) from the upstream
release instead of writing a throwaway dict→type adapter against `0.1.0a2`.

The work is mechanically trivial (one floor edit + `uv lock`), but two dependencies and
one drift risk make it worth recording before implementation.

## Goals / Non-Goals

- Goals: raise the floor to `>=0.1.0a3`, re-lock both lockfiles, keep `import bloom_mcp`
  and the test suite green.
- Non-Goals: consuming the typed results (that is Tiers 3/4, #308/#309); bumping
  `sleap-roots-contracts` (no release needed); any application/API change.

## Decisions

- **Decision: keep `sleap-roots-contracts[pandas]>=0.1.0a1`.** `main` == `0.1.0a1` ==
  released; the typed results live in *analyze*, not contracts. No contracts release is
  in scope.
- **Decision: re-snapshot the oracle characterization only on observed drift.**
  `tests/test_oracle.py` pins a *characterization snapshot* of `0.1.0a2`'s
  `heritability_mean` on the turface_19 fixture — a no-drift assertion against recorded
  library output, not an independently validated value. If `0.1.0a3` changes that output,
  re-record the snapshot and stamp `_heritability_source` = `0.1.0a3`; the
  `heritability_method` and discrete high-H² count remain the BLAS/optimizer-robust
  guards. If it does not drift, leave it untouched (smallest diff).
- **Decision: spec home is `bloommcp-packaging` → *Additive Dependency Set*.** That
  requirement names the exact floor (`>=0.1.0a2`), so a MODIFIED delta is the precise fit.

## Risks / Trade-offs

- **Upstream not released → cannot lock.** `0.1.0a3` is not on PyPI; `uv lock` will fail
  to resolve `>=0.1.0a3`. → Gate implementation on talmolab/sleap-roots-analyze#163
  (task 0.1). The proposal can be approved now; only steps 1.x are blocked.
- **Characterization drift on bump.** A new alpha may change the recorded H² mean. →
  Handled by task 2.3 (re-snapshot with provenance note), not by loosening tolerances.
- **Transitive churn.** `uv lock` may pull newer transitive versions. → Bounded by the
  test suite + `pip-audit` in CI; keep the lock diff reviewable.

## Migration Plan

1. Wait for a3 on PyPI (#163) and for `add-bloommcp-package-baseline` to archive (so the
   *Additive Dependency Set* requirement exists in `openspec/specs/` for the MODIFIED
   delta to apply at archive time).
2. Bump floor, `uv lock` (bloommcp + root), run suite, re-snapshot if needed.
3. PR to `staging` per the issue. No rollback complexity — revert is a one-line floor
   restore + re-lock.

## Open Questions

- Will `add-bloommcp-package-baseline` archive before a3 ships? If a3 lands first, this
  change is still implementable (code-wise); only the spec delta's clean archive waits on
  the baseline. The MODIFIED delta is authored against that requirement's current text.
