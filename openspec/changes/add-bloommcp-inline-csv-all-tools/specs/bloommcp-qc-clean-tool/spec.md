## MODIFIED Requirements

> **Archive-order dependency.** This block is written against
> `add-bloommcp-inline-csv-input`'s text, which is merged to `staging` but **not yet
> archived** — so it does not appear in `openspec/specs/bloommcp-qc-clean-tool/spec.md` yet.
> This change MUST be archived **after** `add-bloommcp-inline-csv-input`. Archiving in the
> other order would let the predecessor's block replace this requirement wholesale and
> silently restore "the inline result's `next_step` is `None`", reverting this change's
> behavior with no validation error.

### Requirement: QC Clean Persists a Versioned Cleaned Run and Returns Links

When invoked with a registered `experiment`, the `qc_clean` tool SHALL persist its outputs as a
versioned run via the `ResultStore` port under tool class `qc`, carrying the contract-stamped
`Provenance` into the manifest, writing the cleaned trait CSV and the cleanup log, and SHALL
return the small in/out summary inline together with **links** to the persisted artifacts (the
`run_ref`, the `manifest_path`, and the per-output object keys) — never the cleaned table
inline. The persisted run SHALL be resolvable by the `ExperimentReader` as a **cleaned version**
so a later `pca_analysis` (`require_clean=True`) consumes it. When invoked with `csv_content`
instead, the tool SHALL NOT call `ResultStore.create_run`/`.commit` — no run is persisted, no
manifest entry is written, and the result's `run_ref`, `version_dir`, and `manifest_path` SHALL
be `None` with `outputs` empty; the caller receives the same small in/out summary with
`experiment=None`, `source="inline"`, and `input_sha256` populated from the exact
`csv_content` bytes.

On the `csv_content` path the cleaned table itself SHALL be returned only when the caller opts
in via `return_cleaned_csv` (see "QC Clean Optionally Returns the Cleaned Table on the Inline
Path"). Absent that opt-in the inline response shape is unchanged: a summary, never the table.

#### Scenario: Run is committed with provenance

- **WHEN** `qc_clean` completes successfully with a registered `experiment`
- **THEN** a `StoredRun` is recorded for `(experiment, "qc")` with a `run_ref`, a manifest
  path, and the same `Provenance` (including `seed = None`) the contract stamped
- **AND** the committed outputs include the cleaned CSV and the cleanup log

#### Scenario: Result returns links and a summary, not the table

- **WHEN** the tool returns its result for a registered `experiment`
- **THEN** `n_samples_in` / `n_samples_out` / `n_traits_in` / `n_traits_out` and the separate
  `sample_retention` / `trait_retention` ratios are inline
- **AND** the cleaned CSV and cleanup log are referenced via links (object keys + manifest
  path) to the persisted run rather than embedded inline

#### Scenario: Cleaned run composes into the PCA tool

- **WHEN** a downstream tool loads the experiment with `require_clean=True` after `qc_clean`
  has committed a run
- **THEN** the reader resolves the `qc_clean` cleaned version rather than the raw input

#### Scenario: Inline call never persists a run

- **WHEN** `qc_clean` completes successfully with `csv_content`
- **THEN** no `ResultStore.create_run`/`.commit` call occurred (verified by a spy/mock, not
  merely the absence of a run in a fake store's records), and the result's `run_ref`,
  `version_dir`, and `manifest_path` are `None` with `outputs == {}`

#### Scenario: Inline result reports the summary and the input hash, not an experiment identity

- **WHEN** `qc_clean` completes successfully with `csv_content`
- **THEN** the result's `experiment` is `None`, `source == "inline"`, and `input_sha256` equals
  the SHA-256 hex digest of the exact UTF-8-encoded `csv_content` string supplied

#### Scenario: Inline result nudges toward an inline qc_inspect when samples were dropped

- **WHEN** `qc_clean` completes successfully with `csv_content` and the cleanup dropped one or
  more samples (the condition that populates `next_step` on the `experiment` path)
- **THEN** the result's `next_step` names `qc_inspect`, states that the **same `csv_content`**
  should be supplied, and carries the `input_sha256` so the caller can confirm they are
  inspecting the same bytes
- **AND** it interpolates no experiment identity — the message contains no `'None'` identifier

> Supersedes the predecessor's scenario "Inline result never nudges toward qc_inspect", whose
> premise — "`qc_inspect` has no `csv_content` parameter and cannot act on ephemeral input" —
> is retired by this change. That scenario is deliberately absent from this block rather than
> merely contradicted by a new one.

#### Scenario: Inline cleaning matches the file-based oracle for identical content

- **WHEN** `qc_clean` is invoked with `csv_content` equal to the text of the `turface_19` raw
  fixture, using the same thresholds as the existing file-based oracle
  (`turface_19_qc_golden.json`)
- **THEN** the resulting `n_samples_in`/`n_samples_out`, `n_traits_in`/`n_traits_out`,
  `kept_trait_columns`, `removed_traits`, `genotype_column`, `sample_id_column`,
  `replicate_column`, `excluded_columns`, and `cleaned_nan_cells_remaining` are all identical to
  the file-based oracle's result — the ephemeral and persisted paths clean the same input
  identically, checked field-for-field rather than on a partial subset

## ADDED Requirements

### Requirement: QC Clean Optionally Returns the Cleaned Table on the Inline Path

`qc_clean` SHALL accept `return_cleaned_csv: bool` (default `false`), valid **only** together
with `csv_content`. Supplying it with `experiment` SHALL be rejected with `BloomMCPError`
(`invalid_input`), because the registered path already returns the cleaned table as a persisted,
linkable artifact.

When `return_cleaned_csv` is true, the response SHALL carry `cleaned_csv` — the cleaned table
serialized as CSV text, with no index column and an explicit `\n` line terminator so the digest
is platform-independent — and `cleaned_csv_sha256`, its SHA-256 digest. Both SHALL be `None`
when the parameter is false or absent. The default SHALL be `false`, so an agent that does not
ask for the table never receives a large string in its context.

This is **not** persistence: the text is placed in the response and nowhere else — no file, no
Storage object, no manifest entry, no run. It exists so a caller can chain client-side, passing
the returned text as the `csv_content` of a subsequent `pca_analysis`, `clustering`,
`umap_analysis`, `descriptive_stats`, or `remove_outliers` call. That chaining is the caller's
own; the server records no lineage between the two calls, and `based_on_version` remains
unavailable on the inline path.

For that chaining to be sound, the serialized table SHALL re-resolve to the same analysis shape
it was cleaned into: parsing `cleaned_csv` back and running the shared column resolution SHALL
yield a trait set equal to the result's `kept_trait_columns` and the same genotype, sample-id,
and replicate roles, and the tool SHALL verify this before returning rather than assume it.

The reason is not, as an earlier draft of this requirement claimed, that removed trait columns
survive into the serialized table carrying NaN. Measured against the real fixture, they do not:
`clean_traits_for_analysis` physically drops them (23 columns in, 21 out) and leaves no NaN cell
anywhere in the frame. The reason is that a consumer re-derives its trait set by running the
detection heuristic over the *re-parsed* text, so the agreement between what a producer certifies
and what a consumer detects rests on two independently-evolving pieces of logic happening to
coincide — and on dtypes surviving a text round trip unchanged. That is a coincidence worth
checking, not a guarantee worth assuming, particularly as further consumer tools adopt this path.

The serialized cleaned CSV SHALL be checked against `MAX_INLINE_CSV_BYTES` before being placed
in the response. If it exceeds that cap the call SHALL raise `BloomMCPError` (`invalid_input`)
naming the size and the limit, with a remedy directing the caller to register the data as an
experiment — rather than returning a multi-megabyte string through the MCP transport.

#### Scenario: The returned cleaned CSV is exactly the cleaned table

- **WHEN** `qc_clean` is called with `csv_content` and `return_cleaned_csv=true`
- **THEN** `cleaned_csv` parses back to a table whose columns and rows match the cleaned table the
  registered path would have persisted for the same input and thresholds, with no index column
  added, **AND** `cleaned_csv_sha256` equals an independently computed SHA-256 of the returned
  text's UTF-8 bytes

#### Scenario: The returned text is platform-independent

- **WHEN** `qc_clean` returns a `cleaned_csv`
- **THEN** it contains no carriage return, and `cleaned_csv_sha256` is stable across repeated
  calls on the same input — the serializer pins its line terminator rather than inheriting the
  platform's

#### Scenario: The returned table re-resolves to the same analysis shape

- **WHEN** `cleaned_csv` is parsed back and run through the shared column resolution
- **THEN** the resolved trait set equals the result's `kept_trait_columns`, and the resolved
  genotype, sample-id, and replicate columns match the result's — so a downstream tool selecting
  "all detected traits" selects exactly what `qc_clean` certified

#### Scenario: The returned cleaned CSV is accepted by a downstream tool's inline path

- **WHEN** `qc_clean(csv_content=<raw fixture text>, return_cleaned_csv=true)` succeeds and its
  `cleaned_csv` is passed verbatim as `csv_content` to `pca_analysis`
- **THEN** `pca_analysis` succeeds — the cleaned text satisfies the finiteness check its inline
  path enforces — proving the client-side chaining this parameter exists to enable works end to
  end

#### Scenario: return_cleaned_csv is rejected with a registered experiment

- **WHEN** `qc_clean` is called with `experiment` and `return_cleaned_csv=true`
- **THEN** it raises `BloomMCPError(code="invalid_input")` explaining that the registered path
  already persists the cleaned table, with a remedy naming the run's output links

#### Scenario: Omitting return_cleaned_csv returns no table

- **WHEN** `qc_clean` is called with `csv_content` and `return_cleaned_csv` omitted or false
- **THEN** `cleaned_csv` and `cleaned_csv_sha256` are both `None`, and the rest of the response is
  unchanged from the inline path's existing behavior

#### Scenario: An oversized cleaned table is rejected rather than returned

- **WHEN** `qc_clean` is called with `return_cleaned_csv=true` and the serialized cleaned table
  exceeds `MAX_INLINE_CSV_BYTES`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the serialized size and the
  limit, and no partial or truncated table is returned

#### Scenario: Persistence is still never touched when the table is returned

- **WHEN** `qc_clean` is called with `csv_content` and `return_cleaned_csv=true`
- **THEN** a `ResultStore` spy records zero `create_run` and zero `commit` calls, and `run_ref`,
  `version_dir`, and `manifest_path` are all `None`

#### Scenario: The recommended inline inspection call actually works

- **WHEN** the `qc_inspect` call described by an inline `next_step` is executed with the same
  `csv_content`
- **THEN** it succeeds and returns the missingness diagnostics for that content — the
  recommendation is verified against the real tool, not merely asserted to name it
