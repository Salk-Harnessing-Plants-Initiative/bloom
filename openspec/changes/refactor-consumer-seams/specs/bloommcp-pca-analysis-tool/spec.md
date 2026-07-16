## MODIFIED Requirements

### Requirement: PCA Analysis Persists a Versioned Run With Lineage and Returns Links

The `pca_analysis` tool SHALL persist its outputs as a versioned run via the `ResultStore` port
under tool class `pca`, carrying the contract-stamped `Provenance` into the manifest, recording the
cleaned-source version it consumed as `based_on_version` and content-addressing the consumed frame
via `source_csv` — captured using the shared `snapshot_frame` context manager from
`bloom_mcp.tools._consumer_utils`, which writes `frame.df` to a temporary CSV with `index=False`
and yields the path so the manifest's `input_sha256` pins the exact input bytes, not just the
mutable `v<N>_cleaned` label — writing the component loadings and the component scores **with
sample identity** (built via `_build_output_frame` from `bloom_mcp.tools._consumer_utils`, which
prepends `frame.metadata_cols` onto a pre-built scores `pd.DataFrame`, resets the index on both
sides before concatenation so positional alignment is preserved regardless of the frame's original
index) and the serialized `PCAResult`, and SHALL return the small variance summary inline together
with **links** to the persisted artifacts — never the loadings or score matrices inline. The
`PCAAnalysisResult` return model SHALL inherit `RunLinks` (from `bloom_mcp.contract`) for the
four run-link fields (`run_ref`, `version_dir`, `manifest_path`, `outputs`).

#### Scenario: Run is committed with provenance and cleaned-source lineage

- **WHEN** `pca_analysis` completes successfully
- **THEN** a `StoredRun` is recorded for `(experiment, "pca")` with a `run_ref`, a manifest path,
  the same `Provenance` (including `seed = None`) the contract stamped, and `based_on_version` equal
  to the consumed cleaned source version (e.g. `v3_cleaned`)
- **AND** the committed outputs include the loadings CSV, the component scores CSV, and the
  serialized `PCAResult` (`pca_result.json`)
- **AND** the tool passes the consumed cleaned frame as `source_csv` (via `snapshot_frame`), so the
  manifest's `input_sha256` content-addresses the exact input rather than resting on the mutable
  version label; the snapshot CSV has no index column (`index=False`)

#### Scenario: Persisted scores carry sample identity for traceability

- **WHEN** the tool writes the component-scores CSV
- **THEN** each score row is prefixed with the frame's `metadata_cols` (e.g. Barcode/Genotype/
  Replicate) via `_build_output_frame`, which resets the index on both the identity columns and
  the scores DataFrame before concatenation so positional alignment is guaranteed regardless of
  the frame's original index — a PC-score row maps back to its plant by a shared key rather than
  by fragile positional alignment against the cleaned version

#### Scenario: Result returns a summary and links, not the matrices

- **WHEN** the tool returns its result
- **THEN** `n_components`, the per-PC `explained_variance_ratio`, the `cumulative_variance_ratio`,
  the `eigenvalues`, `feature_names`, and the certified `n_samples` / `n_features` are inline
- **AND** the loadings and component-score matrices are referenced via `RunLinks`-inherited fields
  (`run_ref`, `version_dir`, `manifest_path`, `outputs`) to the persisted run rather than embedded
  inline
