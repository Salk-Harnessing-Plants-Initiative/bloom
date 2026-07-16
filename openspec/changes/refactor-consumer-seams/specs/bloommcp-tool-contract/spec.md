## ADDED Requirements

### Requirement: RunLinks Base Model

The system SHALL provide a `RunLinks` Pydantic base model in `bloom_mcp.contract.models`,
re-exported from `bloom_mcp.contract` (included in `__all__`), carrying the four run-link
fields common to all consumer tool result models: `run_ref: str`, `version_dir: str`,
`manifest_path: str`, and `outputs: dict[str, str]`. Consumer tool result models (e.g.
`PCAAnalysisResult`, `RemoveOutliersResult`) SHALL inherit from `RunLinks` rather than
repeating these four fields verbatim.

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

#### Scenario: A missing or wrong-typed run-link field is rejected at construction

- **WHEN** a `RunLinks` subclass is constructed with `run_ref` omitted or with `outputs`
  set to a non-string-valued dict (e.g. `{"key": 42}`)
- **THEN** Pydantic raises a `ValidationError` identifying the offending field, and the
  model instance is not created
