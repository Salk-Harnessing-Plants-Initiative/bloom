## Context

Confirmed by reading the shipped tool
(`bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/remove_outliers.py`):

- `_fit_is_trustworthy(goodness_of_fit)` is computed only at the very end of the function
  (line ~450), **after** the persistence block (`store.create_run` / `.commit()` at
  lines ~415-445) has already run. The flag reaches the caller in the returned
  `RemoveOutliersResult`, but by then the trim is already committed as the new latest
  `outliers`-class version.
- `goodness_of_fit` / `fit_is_trustworthy` are method-dependent: both are always `None` for
  `isolation_forest` (no chi-squared assumption), and only meaningful for `mahalanobis`.
- The defense-in-depth guard already in the tool (NaN-free / row-subset / non-empty, right
  before `store.create_run`) is a **structural** guard — it catches a malformed trim, not an
  untrustworthy-but-well-formed one. `fit_is_trustworthy` is a different, orthogonal signal:
  the trim can be perfectly well-formed and still rest on a threshold that doesn't mean what it
  claims to mean.
- Confirmed on **both** of the project's reference fixtures that mahalanobis' default fit is
  untrustworthy: `turface_19_outlier_golden.json` records `fit_quality="very_poor"`, and
  `cylinder_outlier_golden.json` records `fit_quality="poor"` (846 traits vs. 129 samples —
  expected given the dimensionality). Both are asserted in
  `test_remove_outliers_tool.py` (`test_goodness_of_fit_is_dict_with_fit_quality_and_optional_types`
  and the cylinder characterization test) as **successful, persisted** trims today — this
  proposal's gate flips both of those to raises.
- `BloomMCPError` (`contract/errors.py`) carries only `code` / `message` / `remedy` strings — no
  structured `details` payload — so any numeric context that should survive the raise must be
  interpolated into `message`, same as the tool's existing `assumption_violated` raises
  (`_RELAX_REMEDY` / `_STRUCTURAL_REMEDY` paths) already do.
- `remove_outliers` already raises `BloomMCPError(assumption_violated)` from the tool body for a
  different pre-commit condition (a degenerate trim — Decision 6 of
  `add-bloommcp-remove-outliers-tool/design.md`). This change adds a second, independent
  pre-commit gate of the same shape and error code, not a new mechanism.
- **Gating `poor` and `very_poor` identically is correct, not overcautious.** The primary
  justification: both already sit in the delegate's own pre-existing `_UNTRUSTWORTHY_FIT` set
  (unchanged by this proposal) — this is the delegate's own tiering, not a stricter line the MCP
  invents. A secondary, weaker candidate objection is that cylinder's `"poor"` rating (846
  traits vs. 129 samples) might just be a high-dimensionality artifact, distinct from
  turface_19's `"very_poor"` (a more fundamental non-Gaussian fit), such that dimensionality-
  driven "poor" fits might still produce usable trims worth persisting. The project's two
  fixtures are at least suggestively inconsistent with that objection: turface_19 has the *more*
  favorable dimensionality (20 traits, 158 samples) yet is rated `very_poor`, while
  dimensionality-stressed cylinder is only `poor` — the milder rating. This is anecdotal support
  from two confounded data points (different trait sets, different underlying assay biology,
  different sample sizes affecting KS-test power), not a controlled demonstration that
  dimensionality is irrelevant to fit severity in general — it should not be read as more than
  a data point against carving out a dimensionality-based exception, which the primary
  (delegate-tiering) justification above does not need anyway.
- `add-bloommcp-remove-outliers-tool` (the capability's originating change) is still unarchived
  (`openspec list` shows 50/60 tasks) — there is no `openspec/specs/bloommcp-remove-outliers-tool/`
  base spec to formally `MODIFIED` against. `fix-bloommcp-remove-outliers-tool-class` (#420) hit
  the exact same situation and resolved it by **not** attempting a formal delta against this
  capability — it added an inline "Note (superseded by #420...)" to the pending change's own
  `spec.md` instead. This change follows that precedent (task 1.9) rather than inventing a new
  convention.

## Goals / Non-Goals

- **Goal:** gate persistence on an untrustworthy mahalanobis fit; the remedy names
  `method="isolation_forest"` with a concrete `contamination` starting point.
- **Goal:** zero behavior change on the `isolation_forest` path (`fit_is_trustworthy is None`,
  never gated) and on an acceptable-or-better mahalanobis fit (`fit_is_trustworthy is True`).
- **Goal:** nothing silently lost — the raise's message embeds the counts and fit quality the
  caller would otherwise have received inline, so a caller reading the error is no worse
  informed than one reading a successful (today's) result, just told it wasn't persisted.
- **Non-Goal:** changing the tool's declared default `method` away from `"mahalanobis"` — see
  Decision 4 (settled).
- **Non-Goal:** an explicit opt-in bypass (e.g. `force_untrustworthy_fit`) — see Decision 5
  (Open Question).
- **Non-Goal:** anything about `#420`'s `qc`/`outliers` tool-class resolution or `#585`'s
  staleness audit — orthogonal, unaffected by this change.
- **Non-Goal:** a retroactive audit of already-persisted untrustworthy-fit trims committed before
  this ships (same shape as `#585`'s explicitly-scoped-out one-time audit for the tool-class
  fix; a candidate follow-up, not this change's job).

## Decisions

- **Decision 1 (central): raise immediately after the delegate call, before the existing
  structural guard, before any `plots=`/figure handling, and before any `ResultStore`
  interaction.** Compute `fit_is_trustworthy = _fit_is_trustworthy(report.get("goodness_of_fit"))`
  right after `remove_outlier_samples` returns (alongside where `n_input`/`n_outliers`/`n_output`
  are already extracted today), and when it is `False`, raise
  `BloomMCPError(code="assumption_violated", message=f"... n_input_samples={n_input},
  n_outliers={n_outliers}, n_output_samples={n_output}, fit_quality={fit_quality!r},
  outlier_barcodes={sorted_barcodes!r} ...", remedy="Re-run with method='isolation_forest' and
  contamination=0.1 (the delegate's own default); it has no chi-squared assumption.")` before
  reaching the structural guard, plot-key validation, figure generation, or `store.create_run`.
  No run, no figures, are created on this path — `include_plots` is moot here since the function
  returns (raises) before that block. **The message embeds `outlier_barcodes`, not just counts** —
  the review pass for this proposal flagged that a caller who only wanted to *inspect* the
  flagged set (not persist it) would otherwise get nothing at all once gated; embedding the
  sorted barcode list alongside the counts keeps that inspection path open even though nothing is
  committed. The suggested `contamination=0.1` matches the delegate's own default (the review
  pass also flagged that this proposal's earlier draft suggested an invented `0.05`, inconsistent
  with the delegate it's steering the caller toward).
- **Decision 2: gate condition is exactly `fit_is_trustworthy is False`, never `is None`.**
  `isolation_forest` always reports `fit_is_trustworthy is None` and is therefore structurally
  exempt from this gate — no method check needed in the gate itself, the derived flag already
  encodes it.
- **Decision 3: re-point the existing "successful default trim" tests/goldens at
  `method="isolation_forest"`; add a dedicated gate-firing test for mahalanobis defaults.** Both
  `turface_19_outlier_golden.json` and `cylinder_outlier_golden.json` characterize a mahalanobis
  trim that is untrustworthy by this change's own definition, so both existing "the tool
  persists a trim successfully" tests built on them now hit the gate instead. Concretely:
  - Add tests asserting mahalanobis-default runs on both fixtures raise
    `assumption_violated`, name `isolation_forest` in the remedy, and commit no run (verify via
    the fake `ResultStore`'s recorded runs, or an equivalent no-new-version assertion).
  - Compute new isolation_forest golden values (flagged count, retained count, sorted barcodes)
    against the shipped delegate during implementation — not invented here — and use them as the
    new "successful persisted trim" characterization for both fixtures, same shape as the
    existing goldens (an explicit characterization pin, not a ground-truth outlier claim).
  - Update `bloommcp/tests/smoke/test_remove_outliers_smoke.py`'s live-persistence assertions to
    drive `method="isolation_forest"` (the mahalanobis-default path used there today would now
    raise against real Supabase-backed data with the same fit problem).
- **Decision 4 (settled: `method` stays `"mahalanobis"` — not left open).** An earlier draft of
  this design left "should the declared default also change away from `mahalanobis`, given both
  tested reference fixtures show an untrustworthy fit under it" as an open question deferred to
  reviewer sign-off — a 5-lens PR review flagged that framing itself as a problem (3 of 5
  reviewers independently raised it, and "recommend X, settle at review" reads as a
  self-recommendation once nobody with authority over the default has actually settled it).
  Deciding here instead: **keep `"mahalanobis"`.** Reasoning:
  1. Changing a Pydantic field's default is an independent, strictly larger API change than a
     persistence-safety fix — it silently changes behavior for *every* caller that omits
     `method`, including ones whose data has a perfectly trustworthy mahalanobis fit (this
     proposal's two known-bad fixtures are not evidence about data nobody has characterized yet).
  2. The gate this proposal ships makes the *practical* consequence of the current default
     small and self-correcting regardless of which way this decision goes: a caller who omits
     `method` on untrustworthy-fit data gets an immediate, actionable error naming the better
     method — not a silently-corrupted result. The default's "badness" was precisely the
     silent-persistence hazard #419 exists to close; with that closed, an unhelpful-but-safe
     default is a smaller residual problem than the schema/compatibility churn of changing it.
  3. Reversible either way, and orthogonal to review sign-off on *this* change: if reference data
     accumulates showing mahalanobis is untrustworthy on most real inputs (not just these two
     characterized fixtures), that is its own, independently-decidable follow-up with its own
     evidence base — not something to bundle into a persistence-gate fix under review-cycle time
     pressure.
- **Decision 5 (open — flagged for reviewer sign-off, not adopted by default): should a caller
  be able to opt in to persisting an untrustworthy-fit trim anyway** (e.g. `force_untrustworthy_fit:
  bool = False`), for a scientist who has already read `fit_is_trustworthy=False` and
  deliberately wants it (e.g., to compare methods, or because domain knowledge says the
  chi-squared test is overly conservative for this dataset)? Recommend **not** adding it in this
  change: there is no existing bypass-flag precedent elsewhere in the bloommcp tool surface, and
  a boolean escape hatch re-opens exactly the hazard the issue names — "an agent that doesn't
  read the flag" — just moved from `fit_is_trustworthy` to a second flag an agent could set
  reflexively. If a reviewer has a concrete workflow that needs it, it is a small, additive
  follow-up on top of this change (the gate check becomes `fit_is_trustworthy is False and not
  params.force_untrustworthy_fit`).
- **Decision 6: the fit gate takes precedence over `plots=` key validation.** The tool's
  existing `_make_figures` validates a caller-supplied `plots` subset against the delegate's
  real figure keys, raising `invalid_input` on an unknown key — but only when `include_plots=True`,
  and only by first calling `plot_outlier_analysis`. Because Decision 1 places the fit gate
  *before* that call, a request combining an untrustworthy mahalanobis fit with an unknown
  `plots` key now surfaces the fit gate's `assumption_violated`, not the plot-key `invalid_input`.
  This is deliberate, not an oversight: validating figure options for a run that is about to be
  rejected regardless is wasted work, and the fit check is the more fundamental gate. Two
  existing tests (`test_unknown_plot_key_is_invalid_input_with_no_run` and
  `test_unknown_plot_key_failure_closes_all_figures`) assert the plot-key `invalid_input` shape
  against the turface_19 mahalanobis-default fixture specifically — cylinder has no plot-key
  test today, so only these two are affected — since that validation logic is method-agnostic,
  they move to `method="isolation_forest"` (never gated) rather than being weakened or dropped
  (task 1.2 in `tasks.md`).

## Risks / Trade-offs

- **Sizable test/golden ripple for a persistence-safety fix — larger than a first read suggests.**
  Both of the project's only two characterized reference fixtures hit the gate under mahalanobis
  defaults, and at least ~18 currently-passing unit tests in `test_remove_outliers_tool.py`
  exercise mahalanobis defaults against them (not merely the two golden/characterization tests) —
  spanning report/round-trip, provenance, versioning/composition, figure-generation-and-cleanup
  (which would become vacuously true, asserting "no leak" without ever creating a figure to leak),
  and plot-key-validation tests (Decision 6). Each needs individual triage (repoint to
  `isolation_forest`, or restructure to assert the gate itself), not a blanket repoint. This also
  touches the **live** persistence path: `bloommcp/tests/smoke/live_persistence_smoke.py` (the
  actual `make bloommcp-smoke` driver, wired into CI) and `bloommcp/docs/local-validation.md`'s
  runbook both currently demonstrate a mahalanobis-default trim persisting against real
  Supabase-backed turface_19 data. That smoke path cleans at a different threshold than the unit
  golden (`max_nans_per_trait=0.1` → 187 samples, vs. the golden's canonical-default 0.2 → 158),
  so whether it actually trips the same untrustworthy-fit gate must be **verified empirically
  during implementation**, not assumed either way (task 1.6).
- **This is not incrementally landable.** The gate change and the golden/test repointing are
  inseparable — merging Decision 1 alone turns ~18 tests red — so this ships as one commit; there
  is no intermediate green state to split across.
- **A legitimate "I want the poor-fit trim anyway" workflow has no path to *persist* it** under
  this change except re-running with `isolation_forest` (Decision 5, deferred). Inspection is
  still possible (Decision 1 embeds `outlier_barcodes` in the raise), so this trade-off is
  narrower than it first appears — the caller loses *canonicalization*, not *visibility*.
- **`isolation_forest`, the prescribed escape valve, has no goodness-of-fit self-diagnostic of
  its own** (`goodness_of_fit`/`fit_is_trustworthy` are always `None` for it). This change trades
  a threshold that at least self-reports when its assumption fails for one with no equivalent
  feedback loop — a caller who follows the remedy gets a result either way, with nothing to warn
  them if `contamination` is badly mistuned for their data. Not fixed here (would mean asking the
  delegate for a diagnostic it doesn't have); named explicitly so it isn't mistaken for a solved
  problem.
- **`BloomMCPError` has no structured `details` field**, so the numeric context (and now
  `outlier_barcodes`) is interpolated into a message string — more information-dense than this
  tool's existing `assumption_violated` messages, but consistent with how they already embed
  dynamic values (e.g. `_STRUCTURAL_REMEDY`'s exception text).
- **A body-raised `BloomMCPError` is not logged server-side** (`contract/wrap.py` only logs on
  the `from_exception`/`from_output_validation` paths, not a direct `raise` in the tool body) —
  matching this tool's existing sibling `assumption_violated` raises, not a new regression, but
  worth naming: since nothing is persisted on the gated path, the *only* durable trace of a
  rejected trim is whatever the calling agent's own transcript retains. No server-side audit
  trail exists for a gated attempt.
- **A malformed or keyless `goodness_of_fit` dict is silently treated as trustworthy.**
  `_fit_is_trustworthy` returns `True` — not `None` — when `"fit_quality"` is absent from an
  otherwise-dict `goodness_of_fit`: `dict.get("fit_quality")` yields `None`, and `None not in
  _UNTRUSTWORTHY_FIT` evaluates `True` (membership-test, not an identity/type check), so a
  keyless dict reads as "fit is fine" and is **not** gated — a corrected, PR-review-caught
  restatement of an earlier draft of this note, which wrongly said the result was `None`.
  (`None` is the *actual* result only when `goodness_of_fit` itself is not a dict at all — e.g.
  the `isolation_forest` case — a different, already-handled branch.) Pre-existing helper
  behavior this change doesn't alter. The real delegate is not observed to return a
  fit_quality-less dict, so this is a theoretical robustness gap, not a reachable one today;
  called out so it isn't rediscovered as a surprise later.
- **After this ships, the tool's *declared default* (`method="mahalanobis"`) raises on 100% of
  the project's currently-characterized reference data.** Decision 4 settles on keeping the
  default rather than changing it (see that decision's reasoning) — this is a known, accepted
  end state, not an oversight: an unhelpful default that fails safely (a clear, actionable error)
  is preferred here over a schema-visible default change with its own independent blast radius.
- **No retroactive protection.** An `outliers`-class run already persisted from an
  untrustworthy-fit mahalanobis trim before this ships stays exactly as it is — this change only
  stops new ones. Filed as [#593](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/593)
  (same shape as `#585`'s audit for the tool-class fix), not this change's scope.

## Migration Plan

Additive/behavioral only — no schema or manifest change, no dependency bump. Rollback = remove
the gate branch (the two new lines computing-and-checking `fit_is_trustworthy` before
persistence); any `isolation_forest`-repointed tests would need reverting alongside if the
golden/test changes from Decision 3 also need to unwind.

## Open Questions

- **Decision 4 is now settled** (see Decisions) — kept here only as a changelog note: a 5-lens
  PR review flagged the prior "recommend X, settle at review" framing as itself a problem
  (self-recommendation, not actual sign-off), so this design now states and justifies the kept
  default directly instead of deferring it further.
- **Decision 5** — add an opt-in bypass (`force_untrustworthy_fit`) for a caller that wants an
  untrustworthy-fit trim persisted anyway? Recommend not adding it; still open for reviewer
  input if a concrete workflow surfaces the need (unlike Decision 4, no reviewer has raised this
  one as needing to be settled before merge).
