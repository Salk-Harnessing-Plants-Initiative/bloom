## ADDED Requirements

### Requirement: Backend-Agnostic Experiment Identifier Wording

Every LLM-facing tool-schema `description=`, discovery-tool docstring, and
path-traversal validation message that documents the `experiment` (or
`experiment_1`/`experiment_2`) input SHALL describe it as an experiment identifier,
never a "CSV filename," so the text stays accurate both under the deployed
filename-shaped identifier and under a future backend that resolves a different
identifier shape (e.g. `str(experiment_id)`). Path-traversal validation guards
(`tools/_qc_shared._validate_experiment_name`,
`sections/sleap_roots/analysis/_viz_shared.validate_filename`) SHALL keep their exact
accept/reject behavior — only their message text changes.

#### Scenario: Tool schema description does not claim a CSV filename

- **WHEN** the input schema for any of `qc_clean`, `qc_inspect`, `remove_outliers`,
  `clustering`, `pca_analysis`, `umap_analysis`, `descriptive_stats`, or
  `cross_experiment_correlations` is inspected
- **THEN** its `experiment` (or `experiment_1`/`experiment_2`) field's description
  identifies the input as an experiment identifier and does not contain the phrase
  "CSV filename"

#### Scenario: Path-traversal rejection still rejects, with corrected wording

- **WHEN** `_validate_experiment_name` or `validate_filename` is called with a path
  traversal payload (a path separator, `..`, or an absolute path)
- **THEN** the call still rejects the input exactly as before, and the error text
  no longer says "bare CSV filename"

#### Scenario: The dotted-identifier storage-key constraint survives the reword

- **WHEN** `cross_experiment_correlations`'s `experiment_1`/`experiment_2` field
  descriptions are inspected after the reword
- **THEN** they still state that an identifier containing more than one `.` character is
  rejected (required by this tool's composite storage-key encoding, enforced by
  `_reject_dotted_stem`) — the reword changes "CSV filename" to "experiment identifier"
  without dropping this constraint, and drops filename-specific vocabulary
  ("stem"/"extension") in favor of the character-count rule so the description stays
  accurate for a non-filename-shaped identifier too

#### Scenario: Discovery-tool and consumer docstrings do not claim a CSV filename

- **WHEN** the docstrings of `load_experiment_data`, `list_available_experiments`,
  `summarize_trait`, and the five plotting tools (`plot_correlation_matrix`,
  `plot_heritability_bar`, `plot_trait_boxplots`, `plot_trait_histograms`,
  `plot_variance_decomposition`) are inspected, along with
  `list_available_experiments`'s hardcoded "how to analyze an experiment" response text
- **THEN** none describes the input as a "CSV filename" or "CSV file"; each identifies it
  as an experiment identifier

### Requirement: Consistent Experiment Parameter Naming

`list_existing_analyses`'s parameter SHALL be named `experiment`, matching every other
tool's Pydantic-declared experiment parameter, and its JSON response SHALL key the
echoed value the same way.

#### Scenario: list_existing_analyses accepts and echoes `experiment`

- **WHEN** `list_existing_analyses` is called with an `experiment` argument
- **THEN** the call succeeds and the returned JSON's echoed-input key is `"experiment"`,
  not `"experiment_filename"`
