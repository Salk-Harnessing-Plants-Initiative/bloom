## Context

`qc_clean` (#338) is the single producer of the analysis-ready `_cleaned.csv` consumed by the
sleap-roots-analyze tools (`remove_outliers` #378, `pca_analysis` #308, `clustering` #309) via
the reader's `require_clean=True` seam. Three gaps make that artifact weaker than the platform
needs:

- **No input-contract validation.** bloommcp depends on `sleap-roots-contracts` and records its
  version in provenance ([`storage/code_versions.py:20`]) but never calls the validator.
- **No traceability guarantee.** A cleaned frame can lack a sample identifier; downstream
  `remove_outliers` then keys its report by positional index (scientifically useless) or crashes
  outright (the PR #400 `TypeError` on `outlier_barcodes = None`).
- **Loose trait detection.** bloommcp's `detect_columns` ([`experiment_utils.py:152`]) treats any
  numeric column as a trait, so processing metadata like `Computation.Time.s` is analyzed as a
  biological trait. `sleap-roots-analyze.get_trait_columns` already excludes it.

How the code works today (so the change is well-placed):

- **The reader owns column detection.** `detect_columns` populates `ExperimentFrame`
  (`trait_cols`, `metadata_cols`, `genotype_col`, `replicate_col`, `sample_id_col`, `source` —
  [`data_access/ports.py:36-50`]). Analysis tools **consume** the frame; they never detect
  columns.
- **`qc_clean` delegates trait cleaning** to `clean_traits_for_analysis`, passing bloommcp's
  detected roles.
- **The contract layer is params-only.** `@as_mcp_tool` presents FastMCP a single-`params`
  signature and injects only `random_state` / `provenance` — there is **no `Context` /
  elicitation plumbing** ([`contract/wrap.py:139-153`]).

What the ecosystem already provides (delegate, don't reinvent):

- **`sleap_roots_analyze.validation.validate_entry_input(df, *, columns, mode,
  additional_exclude=None, logger=None) -> None`** — canonicalizes a *copy* (renames the
  caller's role columns → canonical roles, casts dtypes, drops metadata), runs the contract, and
  **degrades to a logged no-op if `sleap-roots-contracts` isn't installed** (never `ImportError`).
  `warn` hard-fails only on universal structural errors (missing/blank `genotype`, no numeric
  trait, bad role dtype); everything else — incl. missing `sample_id` — is advisory. `strict`
  hard-fails on any violation.
- **`sleap_roots_analyze.get_trait_columns(df, barcode_col='Barcode', genotype_col='geno',
  replicate_col='rep', additional_exclude=None) -> List[str]`** — the canonical trait detector:
  excludes roles + `additional_exclude` + `"Plot"` + metadata-substring columns
  (`date, time, age_days, batch, experiment, qc_, outlier, …`), returns the numeric traits.
- **`sleap_roots_contracts.validate_analysis_input`** (`0.1.0a1`) — makes `genotype` **required**
  and `sample_id` **recommended** (a warning unless `strict`); deliberately name-agnostic
  (consumers map their own names to canonical roles first).

## Goals / Non-Goals

**Goals**

- Every `qc_clean` `_cleaned.csv` is contract-valid (validated against `sleap-roots-contracts`)
  and **traceable** (carries a genotype and a sample identifier).
- Trait detection is consistent for every consumer and excludes numeric metadata, by delegating
  to `get_trait_columns`.
- The traceability guarantee holds even when `sleap-roots-contracts` is not installed.
- No dependency change; no contract-layer (`Context`/elicitation) plumbing.

**Non-Goals (explicitly deferred)**

- `image_path` role and image linking; `group_by` per-timepoint analysis (pipeline concerns).
- A `validate_input="off"` escape hatch — the traceability requirement is deliberately
  non-optional.
- Validation in the reader or the discovery/raw-read tools (only `qc_clean` validates).
- Any upstream change to `validate_entry_input` — it already handles arbitrary names via
  `columns=`. A shared role-alias registry in `sleap-roots-contracts` is a possible future item
  only if a second consumer duplicates bloommcp's role detection; not warranted now.
- FastMCP `ctx.elicit()` — stays a documented future option.

## Decisions

- **D1 — Enforce at `qc_clean`, not the reader.** `qc_clean` is the sole producer of the
  analysis-ready `_cleaned.csv`; validating at the producer guarantees the artifact for every
  downstream consumer. *Rejected:* validating in `ExperimentReader.load_experiment` — it would
  validate on every internal composition read (noisy) and touch the shipped Tier-2 read
  capability's contract.

- **D2 — Delegate validation to `validate_entry_input`, not the raw contracts validator.** The
  wrapper gives canonicalization (arbitrary names → canonical roles) and graceful degradation for
  free, and keeps the logic upstream. *Rejected:* calling `validate_analysis_input` directly
  (we'd re-implement canonicalization + the contracts-absent guard).

- **D3 — Require genotype + sample_id via bloommcp-level guards; run the contract in `warn`.**
  The traceability requirement (a sample identifier must exist) is a **bloommcp policy**, enforced
  by checking the resolved role is not `None`, so it holds **even if `sleap-roots-contracts` is
  absent**. The contract call runs in `warn` (which alone would not fail a missing `sample_id`)
  to surface everything else without escalating minor advisories to hard errors. `replicate`
  stays optional. *Rejected:* full `strict` (also hard-fails unexpected-non-numeric columns and
  NaN-in-metadata — too aggressive for exploratory data); pure `warn` (would not guarantee
  traceability).

- **D4 — New `data_access/columns.py`; role matching stays in bloommcp, trait detection delegates
  upstream.** `resolve_columns(df, *, sample_id_column=None, genotype_column=None,
  exclude_columns=None) -> ResolvedColumns(...)`.
  - Role-name matching stays bloommcp domain knowledge — move `SAMPLE_ID_PATTERNS` /
    `GENOTYPE_PATTERNS` / `REPLICATE_PATTERNS` here from `experiment_utils`. analyze can't do it
    (it takes *configured* names).
  - Trait detection delegates to `get_trait_columns` (drops `Computation.Time.s`; consistent for
    every consumer; retires bloommcp's duplicate heuristic).
  - The **reader** calls it with **no overrides** (populates `ExperimentFrame`); **`qc_clean`**
    calls it **with overrides** (final resolution). `load_experiment` gains **no new params** —
    overrides are a `qc_clean` concern, keeping the read port stable.
  - *Rejected:* extending `detect_columns` in place (leaves reader logic + detection + versioning
    tangled); adding override params to the reader port (they don't belong on the shared read
    boundary).

- **D5 — "Ask the user" via structured errors, not FastMCP elicitation.** New params
  `sample_id_column`, `genotype_column` (default auto), plus `exclude_columns` (the existing
  `trait_columns` allow-list wins when both are given). If a **required** role can't be resolved,
  return `BloomMCPError(assumption_violated)` whose message lists the available columns and whose
  remedy directs the agent to ask the user and re-call with the override. Reuses the existing
  envelope; **zero** contract-layer changes. *Rejected:* `ctx.elicit()` — requires plumbing
  `Context` through the params-only contract layer (a Tier-1 foundational change) and depends on
  client elicitation support that can't be assumed.

- **D6 — Surface findings in the result and the manifest.** `QCCleanResult` gains resolved
  `genotype_column` / `sample_id_column` / `replicate_column`, `excluded_columns`, and
  `validation_warnings: list[str]`; the manifest gains an additive `input_validation` block
  (`mode`, `contract_version`, `resolved_roles`, `excluded_columns`, `warnings`). *Rejected:*
  log-only (warnings vanish, not reproducible); manifest-only (agent/scientist doesn't see them
  live).

## Risks / Trade-offs

- **Golden churn — two distinct counts.** Because `get_trait_columns` drops `Computation.Time.s`,
  the `turface_19` **detected** trait set goes `20 → 19`, and — since `Computation.Time.s` is
  NaN-free and survived cleaning today — the **cleaned** set goes `18 → 17` (cleaning still
  additionally drops the two NaN-heavy traits). Do **not** conflate the two: `n_traits_in == 19`,
  `n_traits_out == 17`. The qc golden is a pure structural snapshot (integer counts + trait-name
  lists, no floats), so it carries **no** numpy/scipy version-drift risk. The `remove_outliers`
  golden *does* shift too (mahalanobis over the 19/17 set), but that fixture is **not on this
  change's base** — its re-record is decoupled to a follow-up (see Migration Plan).
- **A newly-required role could break an experiment that previously "cleaned".** An input with no
  detectable sample identifier now errors instead of silently producing an untraceable run. This
  is intended: an untraceable cleaned frame is the root cause of the #400 crash. The structured
  error lists the available columns and the `sample_id_column` override, so the user recovers in
  one re-call. This deliberately **supersedes** the in-flight `bloommcp-qc-clean-tool` scenario
  *"An undetected role column falls back to the delegate default"* for the **required** roles
  (see the proposal Impact + `tasks.md §0`).
- **`ColumnRoles` shape coupling — `.barcode`, not `.sample_id`.** `validate_entry_input`'s
  `columns=` argument wants an object satisfying the upstream `ColumnRoles` **Protocol**
  (`sleap_roots_analyze.validation.input_contract.ColumnRoles`) — it is **not constructible**
  (a `Protocol`) and **not** re-exported top-level, and its required attributes are
  **`.genotype` / `.barcode` / `.replicate`**. bloommcp's resolved **`sample_id`** role must be
  mapped onto **`.barcode`** on the duck-typed object it passes; a `.sample_id` attribute would
  silently misfire. Pin this in tasks (§1.2) and test the Protocol conformance (§2.7). If the
  upstream type is unavailable, D2's wrapper still degrades gracefully.

## Migration Plan

1. **Precondition for the qc-clean-tool reconciliation (B2):** if #338 (`add-bloommcp-qc-clean-tool`)
   has **archived** by implementation time, add a `MODIFIED` delta here for `bloommcp-qc-clean-tool`
   correcting the role-fallback scenario + the `20/18 → 19/17` oracle; if it is still **in-flight**,
   edit its change's spec + golden in lockstep with the #338 owner. Either way the two changes must
   not merge into a contradiction.
2. Implement this change off `origin/staging` (TDD per `tasks.md`), keeping `detect_columns` as a
   thin shim over `resolve_columns`.
3. Re-record **only** `turface_19_qc_golden.json` here (`20 → 19` detected, `18 → 17` cleaned;
   bump `_reproduced_by_sleap_roots_analyze_version` to `0.1.0a4`); update the `fixtures/README.md`
   counts. The `remove_outliers` golden re-record is **decoupled** to a follow-up once #400 merges
   (its fixture is not on this base).
4. Rollback: revert the change commit(s); the reader falls back through the `detect_columns` shim
   and `qc_clean` drops the new params/guards. No persisted-data migration — the change only affects
   how *new* runs are produced.

## Open Questions (resolved in this revision)

- **Manifest `contract_version` source → RESOLVED: provenance-recorded.** Record the resolved
  `sleap-roots-contracts` version from `code_versions.py` (`0.1.0a1` today) for reproducibility,
  not a live read. The `input_validation` test asserts against the provenance-recorded value.
- **`detect_columns` fate → RESOLVED: keep a thin shim.** `detect_columns` becomes a thin wrapper
  over `resolve_columns` (retirement deferred to a follow-up) so the `list_experiments`
  (`experiment_utils.py:134`) and other call sites (`:362`, `:380`) and single-commit revert stay
  stable; the shim adds no dtype heuristic of its own.
