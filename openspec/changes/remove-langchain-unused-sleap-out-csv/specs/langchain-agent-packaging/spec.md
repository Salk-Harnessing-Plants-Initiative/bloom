## ADDED Requirements

### Requirement: Deployed image excludes unused vendored data

The `langchain-agent` Docker image SHALL NOT include any data directory that no
in-repo code path reads. Specifically, `langchain/SLEAP_OUT_CSV/` SHALL NOT exist in
the repository or the built image.

#### Scenario: No dead CSV data in the built image

- **WHEN** the `langchain-agent` image is built from `langchain/Dockerfile`
- **THEN** the image SHALL NOT contain a `SLEAP_OUT_CSV/` directory under `/app`

#### Scenario: No in-repo reader references the removed directory

- **WHEN** `langchain/` is scanned for file reads (`open(`, `read_csv`, path joins)
  targeting `SLEAP_OUT_CSV`
- **THEN** no match SHALL be found

### Requirement: Illustrative prompt examples name only existing repo data

Illustrative filenames used in router training examples and system-prompt context
blocks within `langchain/` SHALL name datasets that exist elsewhere in the
repository (e.g. as real fixture or reference filenames), not filenames that exist
only in data that has been deleted.

#### Scenario: Router few-shot example names a current dataset

- **WHEN** `langchain/prompts/router.py`'s `ROUTER_FEW_SHOTS` few-shot examples are
  inspected
- **THEN** any CSV filename they mention SHALL also appear as a real filename
  reference elsewhere in the repository

#### Scenario: CONTEXT_MCP block names a current dataset

- **WHEN** `langchain/tools/context_tools.py`'s `CONTEXT_MCP` block is inspected
- **THEN** any CSV filename it mentions SHALL also appear as a real filename
  reference elsewhere in the repository
