## MODIFIED Requirements

### Requirement: UMAP on a cleaned experiment

The system SHALL provide an `umap_analysis` tool that, **when invoked with a registered
`experiment`,** reads a cleaned experiment (via `require_clean=True`), delegates all UMAP
computation to `sleap_roots_analyze.perform_umap_analysis`, and wraps the result into the
upstream `UMAPResult` type. The tool SHALL own no UMAP math of its own on either input path.

**When invoked with `csv_content`** the reader and its `require_clean` resolution do not apply;
see "UMAP Analysis Accepts Inline Content With No Persistence". Delegation, degeneracy handling,
and the non-finite-embedding guard are unchanged on both paths.

#### Scenario: Successful embedding on a cleaned experiment

- **WHEN** `umap_analysis` is called with a valid `experiment` that has a cleaned version
  and a certified-clean trait selection
- **THEN** it returns an embedding summary whose `n_samples` equals the certified-clean row
  count, whose `feature_names` matches the selected trait columns, and whose embedding
  values are all finite

#### Scenario: Missing cleaned version

- **WHEN** `umap_analysis` is called with an `experiment` that has no cleaned version
- **THEN** it raises a `tool_error` naming the missing cleaned version, with a remedy to run
  `qc_clean` first

#### Scenario: Delegation is pinned to the upstream entry point

- **WHEN** `umap_analysis` runs successfully on either input path
- **THEN** `sleap_roots_analyze.perform_umap_analysis` is called exactly once, with the
  selected trait columns and the resolved seed, and the tool performs no UMAP
  computation of its own

#### Scenario: Degenerate or invalid delegate input

- **WHEN** the selected trait set is degenerate (too few samples, no
  non-constant trait, or otherwise rejected by `perform_umap_analysis`)
- **THEN** `umap_analysis` raises a structured `assumption_violated` error describing the
  degeneracy, without leaking the delegate's raw exception text

#### Scenario: Non-finite embedding is never persisted or leaked past the JSON boundary

- **WHEN** the delegate returns an embedding containing a non-finite value (NaN or ±inf) —
  whether from a degenerate fit or an unstable UMAP initialization
- **THEN** `umap_analysis` detects this before persistence begins and raises a structured
  `assumption_violated` error; no run is committed and no unstructured `ValueError` from
  `UMAPResult.to_json()`'s `allow_nan=False` boundary escapes as an unhandled error

## ADDED Requirements

### Requirement: UMAP Analysis Accepts Inline Content With No Persistence

`umap_analysis` SHALL accept `csv_content` as the mutually exclusive alternative to `experiment`
(exactly one required). On the inline path it SHALL skip the `ExperimentReader` port entirely,
run the same delegate with the same parameters and the same resolved seed, and return the same
embedding summary — while creating no run, committing nothing, and returning `run_ref` /
`version_dir` / `manifest_path` as `None` with `outputs` and `output_links` empty.

Seed handling SHALL be unchanged and the resolved seed SHALL be reported in the response, so an
inline run remains reproducible **by the caller** even though no provenance record is persisted —
the caller has no run to recover it from, which makes reporting it more important here, not less.

`version`, `user_label`, `include_plots=true`, and every plot-companion parameter SHALL be
rejected when combined with `csv_content`. `input_sha256` SHALL be populated on the inline path.
A non-finite value in a selected trait column SHALL raise `invalid_input` with a remedy naming
`qc_clean(csv_content=..., return_cleaned_csv=true)`, rather than the registered path's
`assumption_violated` error.

#### Scenario: Inline UMAP reproduces the registered path's embedding

- **WHEN** `umap_analysis` is called with the text of a cleaned fixture as `csv_content` and with
  that fixture as a registered cleaned experiment, using identical parameters and the same
  explicit `seed`
- **THEN** the embedding coordinates and summary are identical, differing only in `experiment`,
  `source`, `input_sha256`, and the persistence-linked fields

#### Scenario: The resolved seed reaches both the delegate and the response

- **WHEN** `umap_analysis` is called with `csv_content` and no explicit `seed`
- **THEN** the response reports the concrete resolved seed, and a spy confirms the delegate
  received that same value — matching what the registered path passes

#### Scenario: Inline UMAP persists nothing

- **WHEN** `umap_analysis` is called with `csv_content`
- **THEN** a `ResultStore` spy records zero `create_run` and zero `commit` calls, and an
  `ExperimentReader` spy records zero `load_experiment` calls

#### Scenario: Plots are rejected on the inline path

- **WHEN** `umap_analysis` is called with `csv_content` and `include_plots=true`, or with any
  plot-companion parameter
- **THEN** it raises `BloomMCPError(code="invalid_input")`, and a spy on `Figure.savefig` records
  zero calls
