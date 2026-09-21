## ADDED Requirements

### Requirement: qc_inspect Accepts Inline Content

`qc_inspect` SHALL accept `csv_content` as the mutually exclusive alternative to `experiment`,
parse it into an in-memory frame, and return the same missingness summary, per-trait
diagnostics, and threshold recommendation it returns for a registered experiment — computed by
the same delegate, with no run persisted, `source` set to `"inline"`, `experiment` set to
`None`, and `input_sha256` populated. `source_id`, `run_id`, and `user_label` SHALL be rejected.

The experiment-name validation `qc_inspect` performs before reading SHALL be skipped on the
inline path: there is no name to validate, and applying it to `None` would fail with an opaque
internal error rather than a usable one.

#### Scenario: Inline inspection returns the same diagnostics as the registered path

- **WHEN** `qc_inspect` is called with the raw text of a fixture experiment as `csv_content`, and
  separately with that same fixture registered as `experiment`, with identical thresholds
- **THEN** the missingness summary, per-trait diagnostics, and threshold recommendation are
  identical, differing only in `experiment`, `source`, `input_sha256`, and the persistence-linked
  fields

#### Scenario: Inline inspection persists nothing

- **WHEN** `qc_inspect` is called with `csv_content`
- **THEN** a `ResultStore` spy records zero `create_run` and zero `commit` calls, an
  `ExperimentReader` spy records zero `load_experiment` calls, and `run_ref` / `version_dir` /
  `manifest_path` are `None` with `outputs` and `output_links` empty

#### Scenario: The experiment-name validator is not reached on the inline path

- **WHEN** `qc_inspect` is called with `csv_content` and no `experiment`
- **THEN** it succeeds — it does not raise `internal_error` from validating a `None` name

### Requirement: qc_inspect's Inline Path Produces No Figures and No Run

`qc_inspect`'s inline path SHALL render no figure and SHALL create no run or staging directory.
Unlike every other plot-capable tool it has **no `include_plots` parameter**: it
renders its figures unconditionally and writes them, together with its NaN-sample CSV and
recommendation JSON, into the run's staging directory. Those artifacts are its persisted
deliverables, and its result advertises links to them rather than inline blobs.

The inline path SHALL therefore render **no figure at all** and create no staging directory,
returning the summary, the per-trait diagnostics, and the recommendation — the parts that fit in
a response — with empty `outputs` and `output_links`. This is a real reduction in what the tool
delivers, and it SHALL be stated in the tool's own docstring and in `csv_content`'s field
description so the agent learns it from `tools/list` rather than by comparing two responses.

Silently rendering figures into the shared plots directory instead is rejected for the reason
given in "Plot Generation Is Rejected on the Inline Path": that directory is shared, mounted into
more than one service, and served unauthenticated from the public ingress.

#### Scenario: Inline inspection renders no figure

- **WHEN** `qc_inspect` is called with `csv_content`
- **THEN** a spy on `Figure.savefig` (patched to raise) records zero calls, no staging directory
  is created, and the response carries the full summary, per-trait diagnostics, and
  recommendation with `outputs` and `output_links` empty

#### Scenario: The registered path still renders and persists its figures

- **WHEN** `qc_inspect` is called with a registered `experiment`
- **THEN** its figures, NaN-sample CSV, and recommendation JSON are written and committed exactly
  as before this change

#### Scenario: The reduced output is documented where the agent reads it

- **WHEN** the generated input schema for `qc_inspect` is inspected
- **THEN** `csv_content`'s description states that the inline path returns no figures
