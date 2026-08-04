## ADDED Requirements

### Requirement: Shared Inline-CSV Parsing Helper

The system SHALL provide a shared helper, `parse_inline_csv_frame`, in
`bloom_mcp.tools._inline_input` that parses a caller-supplied CSV string directly into an
`ExperimentFrame` held only in memory. The helper SHALL strip at most one leading UTF-8 BOM
(`﻿`) from the content before parsing, since `csv_content` arrives as a `str` and Python's
`utf-8-sig` BOM-stripping only applies when decoding bytes — a caller pasting CSV text copied
from Excel or Windows Notepad routinely carries a literal BOM before the header row, which would
otherwise corrupt the first column's name and silently break role detection for that column. The
helper SHALL resolve column roles (genotype, sample-id, replicate) and trait columns through the
same `resolve_columns` unit (`bloom_mcp.data_access.columns`) every `ExperimentReader` adapter
uses, so an inline frame is resolved identically to an adapter-sourced one. The helper SHALL NOT
write to Storage, SHALL NOT call `ResultStore.create_run` or `.commit`, and SHALL NOT create any
manifest entry — the resulting `ExperimentFrame` SHALL never appear in `list_existing_analyses`
output. This helper is the shared surface every consumer tool's inline-content path imports; it
is not specific to any one tool.

#### Scenario: Valid CSV content parses into a frame with resolved roles

- **WHEN** `parse_inline_csv_frame` is called with a well-formed CSV string containing a
  genotype column, a sample-identifier column, and numeric trait columns
- **THEN** it returns an `ExperimentFrame` whose `df` holds the parsed rows, whose
  `genotype_col`/`sample_id_col`/`replicate_col` match what `resolve_columns` would resolve for
  the same `DataFrame`, whose `trait_cols` excludes non-numeric and role columns, and whose
  `source` is `"inline"`

#### Scenario: A leading UTF-8 BOM is stripped before parsing

- **WHEN** `parse_inline_csv_frame` is called with content whose first character is `﻿`
  (U+FEFF) immediately followed by an otherwise well-formed header row
- **THEN** the returned frame's first column name has the BOM character removed (e.g.
  `"Barcode"`, not `"﻿Barcode"`), so role detection for that column matches the same content
  without a BOM

#### Scenario: Parsing touches no persistence port

- **WHEN** `parse_inline_csv_frame` is called
- **THEN** no `ResultStore.create_run`/`.commit` call occurs and no object is written to Storage
  — verified by a spy/mock on the injected `ResultStore`, not merely the absence of a run in a
  fake store's records

### Requirement: Inline CSV Size Guard

The system SHALL reject inline CSV content whose UTF-8 encoded byte length exceeds
`MAX_INLINE_CSV_BYTES` (5 MiB) before attempting to parse it, raising a `BloomMCPError` with
code `invalid_input` whose message states the received byte count and the limit. This guard
SHALL run before `pandas.read_csv` is invoked, so an oversized payload cannot reach the parser.

#### Scenario: Oversized content is rejected before parsing

- **WHEN** `parse_inline_csv_frame` is called with a string whose UTF-8 encoding exceeds
  `MAX_INLINE_CSV_BYTES`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the byte count and the limit,
  and no `pandas.read_csv` call is attempted

#### Scenario: Content at or under the limit is accepted

- **WHEN** `parse_inline_csv_frame` is called with well-formed CSV content whose UTF-8 encoding
  is at or under `MAX_INLINE_CSV_BYTES`
- **THEN** parsing proceeds normally

### Requirement: Malformed Inline Content Is a Structured Error

The system SHALL map a CSV parsing failure (unparseable content, zero data rows, zero columns,
or a decode failure) to a `BloomMCPError` with code `invalid_input`, never a raw
`pandas`/`UnicodeDecodeError` traceback or an opaque `internal_error`.

#### Scenario: Unparseable content is rejected with a structured error

- **WHEN** `parse_inline_csv_frame` is called with a string that is not valid CSV (e.g.
  inconsistent field counts `pandas` cannot tokenize)
- **THEN** it raises `BloomMCPError(code="invalid_input")` describing the parsing failure, not a
  raw `pandas.errors.ParserError`

#### Scenario: Empty content is rejected

- **WHEN** `parse_inline_csv_frame` is called with an empty string or a string with a header row
  but zero data rows
- **THEN** it raises `BloomMCPError(code="invalid_input")` stating that no data rows were found

#### Scenario: Content with zero columns is rejected

- **WHEN** `parse_inline_csv_frame` is called with content that parses to a `DataFrame` with no
  columns (e.g. a string of blank lines)
- **THEN** it raises `BloomMCPError(code="invalid_input")` stating that no columns were found,
  rather than proceeding to role resolution against an empty column set

#### Scenario: Content that cannot be decoded is rejected

- **WHEN** `parse_inline_csv_frame` is called with content that raises `UnicodeDecodeError` when
  its bytes are re-encoded/processed during parsing
- **THEN** it raises `BloomMCPError(code="invalid_input")` describing the decode failure, not a
  raw `UnicodeDecodeError`

### Requirement: Inline Content Hash for Caller Record-Keeping

The system SHALL provide `compute_input_sha256(csv_content: str) -> str`, computing the SHA-256
hex digest over the exact UTF-8-encoded bytes of the caller-supplied content, independent of any
manifest- or `Provenance`-level hash. This value is returned to the caller so they can record
what they analyzed, even though nothing is stored server-side to check it against later.

#### Scenario: Hash matches an independent computation over the same bytes

- **WHEN** `compute_input_sha256` is called with a CSV string
- **THEN** the returned hex digest equals `hashlib.sha256(content.encode("utf-8")).hexdigest()`
  computed independently over the same string
