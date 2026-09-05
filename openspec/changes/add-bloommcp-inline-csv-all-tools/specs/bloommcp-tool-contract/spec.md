## MODIFIED Requirements

### Requirement: RunLinks Base Model

The system SHALL provide a `RunLinks` Pydantic base model in `bloom_mcp.contract.models`,
re-exported from `bloom_mcp.contract` (included in `__all__`), carrying the four run-link
fields common to all consumer tool result models: `run_ref: Optional[str]`,
`version_dir: Optional[str]`, `manifest_path: Optional[str]`, and `outputs: dict[str, str]`.
Consumer tool result models (e.g. `PCAAnalysisResult`, `RemoveOutliersResult`) SHALL inherit
from `RunLinks` rather than repeating these four fields verbatim.

**BREAKING (output schema).** The three run-link fields widen from required `str` to
`Optional[str]` defaulting to `None`, and `outputs` defaults to an empty mapping. This is
required by the ephemeral inline-content input path (`bloommcp-inline-csv-input`): a tool
invoked with `csv_content` creates no run, so there is no run reference, no version directory,
and no manifest to name. Returning a placeholder string would be worse than `None` — it would
name an object that does not exist.

The widening SHALL NOT be read as permission for a *persisting* call to omit them. On any path
that commits a run, all three SHALL be populated. Because Pydantic can no longer enforce that
at construction, each consumer tool SHALL carry a test asserting its registered path returns
non-`None` run links and non-empty `outputs` — replacing, rather than merely losing, the
validation this widening removes.

#### Scenario: RunLinks is importable from the contract package

- **WHEN** `from bloom_mcp.contract import RunLinks` is executed in a clean import context
- **THEN** the import succeeds and `RunLinks` is a Pydantic `BaseModel` subclass whose
  field set includes exactly `run_ref`, `version_dir`, `manifest_path`, and `outputs`
- **AND** `"RunLinks"` is present in `bloom_mcp.contract.__all__`

#### Scenario: Consumer result models inherit RunLinks fields without redeclaring them

- **WHEN** a `PCAAnalysisResult` instance is constructed with `run_ref`, `version_dir`,
  `manifest_path`, and `outputs` supplied alongside its tool-specific fields (e.g.
  `experiment`, `n_samples`, `n_components`)
- **THEN** the model validates successfully, the four run-link attributes are accessible on
  the instance, and none of the four field names appear in `PCAAnalysisResult.__fields__`
  directly — they are inherited from `RunLinks`

#### Scenario: RunLinks fields survive round-trip serialization

- **WHEN** a `PCAAnalysisResult` instance (a concrete `RunLinks` subclass) is serialized
  with `.model_dump()` and reconstructed with `PCAAnalysisResult.model_validate()`
- **THEN** the `run_ref`, `version_dir`, `manifest_path`, and `outputs` values are identical
  before and after the round-trip alongside the tool-specific fields

#### Scenario: A wrong-typed run-link field is still rejected at construction

- **WHEN** a `RunLinks` subclass is constructed with `run_ref` set to a non-string, non-`None`
  value (e.g. `42`), or with `outputs` set to a non-string-valued dict (e.g. `{"key": 42}`)
- **THEN** Pydantic raises a `ValidationError` identifying the offending field, and the
  model instance is not created

#### Scenario: An ephemeral result carries null run links

- **WHEN** a `RunLinks` subclass is constructed with `run_ref`, `version_dir`, and
  `manifest_path` all `None` and `outputs` omitted
- **THEN** the model validates, `outputs` and `output_links` are empty mappings, and the three
  run-link attributes are `None` — the shape an inline-content call returns

#### Scenario: A persisting call still returns populated run links

- **WHEN** each consumer tool is invoked with a registered `experiment` and commits a run
- **THEN** its result's `run_ref`, `version_dir`, and `manifest_path` are all non-`None` and
  `outputs` is non-empty — asserted per tool, since Pydantic no longer enforces it
