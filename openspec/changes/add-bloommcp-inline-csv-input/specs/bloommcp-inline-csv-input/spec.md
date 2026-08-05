## ADDED Requirements

### Requirement: Shared Inline-CSV Parsing Helper

The system SHALL provide a shared helper, `parse_inline_csv_frame`, in
`bloom_mcp.tools._inline_input` that parses a caller-supplied CSV string directly into an
`ExperimentFrame` held only in memory. The helper SHALL strip every leading UTF-8 BOM (`﻿`,
one or more) from the content before parsing, since `csv_content` arrives as a `str` and
Python's `utf-8-sig` BOM-stripping only applies when decoding bytes — a caller pasting CSV text
copied from Excel or Windows Notepad routinely carries a literal BOM before the header row
(occasionally more than one, from a double-encoded or re-saved file), which would otherwise
corrupt the first column's name and silently break role detection for that column. The helper
SHALL resolve column roles (genotype, sample-id, replicate) and trait columns through the same
`resolve_columns` unit (`bloom_mcp.data_access.columns`) every `ExperimentReader` adapter uses,
so an inline frame is resolved identically to an adapter-sourced one. The helper SHALL NOT write
to Storage, SHALL NOT call `ResultStore.create_run` or `.commit`, and SHALL NOT create any
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

#### Scenario: One or more leading UTF-8 BOMs are stripped before parsing

- **WHEN** `parse_inline_csv_frame` is called with content whose first character(s) are one or
  more `﻿` (U+FEFF) immediately followed by an otherwise well-formed header row
- **THEN** the returned frame's first column name has every leading BOM character removed
  (e.g. `"Barcode"`, not `"﻿Barcode"` or `"﻿﻿﻿Barcode"`), so role detection for that
  column matches the same content without a BOM

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

### Requirement: Inline CSV Column-Count Guard Runs Before Parsing

A byte-size cap alone SHALL NOT be treated as sufficient to bound the CPU cost of parsing inline
content: a pathologically wide-but-short CSV (many narrow columns in one or few rows) can sit
comfortably under `MAX_INLINE_CSV_BYTES` while still costing seconds of CPU in `pandas.read_csv`'s
per-column overhead (measured: approximately 480,000 columns in a single row, 4.69 MB, cost
approximately 7.7 seconds of CPU) — a real, reproducible denial-of-service vector against the
shared container, which has no rate limiting in front of this path and no persistence step to
create natural backpressure. The system SHALL therefore estimate the column count from the
content's header row alone — before `pandas.read_csv` is ever invoked — and reject content whose
estimated count exceeds `MAX_INLINE_CSV_COLUMNS` (2000) with a `BloomMCPError`
(`invalid_input`) naming the estimated count and the limit.

This header-row estimate SHALL correctly resolve the row's true extent even when a header cell
contains a literal newline inside quotes (valid CSV) — a naive single-line split (e.g.
`csv_content.split("\n", 1)[0]`) cuts such a row short and undercounts, which was found to let
the exact denial-of-service payload above bypass this guard entirely when one header cell carried
an embedded newline (reproduced: the estimate reported "1 column," and `pandas.read_csv` ran
anyway, costing several seconds of CPU before a post-parse check caught it — defeating the
guard's purpose). The estimate SHALL instead read the header row via a multi-line-aware
tokenizer (the same mechanism by which iterating a real file correctly handles a quoted field
spanning multiple physical lines), bounded to a fixed maximum number of bytes scanned so that an
*unterminated* quote cannot force scanning the entire payload in search of a closing quote that
never comes — content whose header row cannot be resolved within that bound SHALL be rejected
outright with a `BloomMCPError` (`invalid_input`), rather than guessed at.

A post-parse `df.shape[1]` check SHALL be retained as an exact backstop for any residual
divergence between the header-row estimate and `pandas.read_csv`'s own tokenization, but SHALL
NOT be the primary guard, since by the time it runs the expensive parse has already completed.

#### Scenario: A wide-but-short CSV is rejected before pandas.read_csv runs

- **WHEN** `parse_inline_csv_frame` is called with content whose header row implies more than
  `MAX_INLINE_CSV_COLUMNS` columns, sized well under `MAX_INLINE_CSV_BYTES`
- **THEN** it raises `BloomMCPError(code="invalid_input")`, and `pandas.read_csv` is never
  called — verified by a spy/mock asserting zero calls
- **AND** the reproduction of the reported denial-of-service shape (~480,000 columns, ~4.69 MB)
  is rejected in well under one second, not after paying the multi-second parse cost a
  post-parse-only check would incur

#### Scenario: An embedded newline in a header cell cannot bypass the guard

- **WHEN** `parse_inline_csv_frame` is called with content shaped like the denial-of-service
  payload above, except one header cell is a quoted value containing a literal newline —
  crafted so that a naive single-line split would undercount the row's true width
- **THEN** the guard still resolves the row's true column count (or rejects via the scan-bound
  below, whichever applies), `pandas.read_csv` is never called, and rejection happens in well
  under one second — not after the multi-second parse cost a naive single-line estimate would
  have let through

#### Scenario: A legitimate embedded newline in a header cell is counted correctly, not just tolerated

- **WHEN** `parse_inline_csv_frame` is called with a small, well-formed header whose one cell
  contains a literal newline inside quotes, and a column count safely under
  `MAX_INLINE_CSV_COLUMNS` — including the case where legitimate-looking columns precede the
  newline-bearing cell
- **THEN** the content is accepted, with that cell's value (embedded newline included) preserved
  intact as the column name — proving the estimate genuinely counts through the embedded newline
  rather than merely failing safe when it cannot

#### Scenario: An unterminated quote in the header is rejected without scanning the whole payload

- **WHEN** `parse_inline_csv_frame` is called with content whose header row opens a quote that
  never closes, followed by a large amount of filler content
- **THEN** it raises `BloomMCPError(code="invalid_input")` once a fixed maximum scan size is
  exceeded, rather than scanning arbitrarily far (up to the entire payload) looking for a closing
  quote that does not exist — rejection happens quickly regardless of how much filler follows

#### Scenario: A header value containing a quoted comma is not falsely rejected

- **WHEN** `parse_inline_csv_frame` is called with exactly `MAX_INLINE_CSV_COLUMNS` real columns,
  one of whose header names contains a comma inside quotes (which a naive `str.count(",")`
  estimate would overcount by one, pushing it over the limit)
- **THEN** the column-count estimate — computed via `csv.reader` on the header line, not a naive
  comma count — correctly counts the quoted field as one column, and the content is accepted

#### Scenario: Column count at the limit is accepted

- **WHEN** `parse_inline_csv_frame` is called with well-formed content whose real column count is
  exactly `MAX_INLINE_CSV_COLUMNS`
- **THEN** parsing proceeds normally

### Requirement: Malformed Inline Content Is a Structured Error

The system SHALL map a CSV parsing failure (unparseable content, zero data rows, zero columns, an
encode failure, or a decode failure) to a `BloomMCPError` with code `invalid_input`, never a raw
`pandas`/`UnicodeEncodeError`/`UnicodeDecodeError` traceback or an opaque `internal_error`. The
UTF-8 encode step (used both for the byte-size guard and, independently, by
`compute_input_sha256`) SHALL be wrapped explicitly: a lone UTF-16 surrogate in `csv_content`
(possible via a lossy upstream decode) raises `UnicodeEncodeError` on `.encode("utf-8")`, which
happens before `pandas.read_csv` is ever called and would otherwise propagate as an opaque
`internal_error` if left unguarded.

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

#### Scenario: A lone surrogate cannot be encoded and is rejected

- **WHEN** `parse_inline_csv_frame` is called with `csv_content` containing a lone UTF-16
  surrogate character, which raises `UnicodeEncodeError` on `.encode("utf-8")`
- **THEN** it raises `BloomMCPError(code="invalid_input")` describing the encode failure, not a
  raw `UnicodeEncodeError`, and before `pandas.read_csv` is ever called

### Requirement: Inline Content Hash for Caller Record-Keeping

The system SHALL provide `compute_input_sha256(csv_content: str) -> str`, computing the SHA-256
hex digest over the exact UTF-8-encoded bytes of the caller-supplied content, independent of any
manifest- or `Provenance`-level hash. This value is returned to the caller so they can record
what they analyzed, even though nothing is stored server-side to check it against later. Since
this function is a public entry point in its own right — not guaranteed to run only after
`parse_inline_csv_frame` has already validated the same string — it SHALL independently guard its
own `.encode("utf-8")` call, raising `BloomMCPError` (`invalid_input`) rather than a raw
`UnicodeEncodeError` on a lone surrogate.

#### Scenario: Hash matches an independent computation over the same bytes

- **WHEN** `compute_input_sha256` is called with a CSV string
- **THEN** the returned hex digest equals `hashlib.sha256(content.encode("utf-8")).hexdigest()`
  computed independently over the same string

#### Scenario: compute_input_sha256 rejects a lone surrogate with a structured error

- **WHEN** `compute_input_sha256` is called with a string containing a lone UTF-16 surrogate
- **THEN** it raises `BloomMCPError(code="invalid_input")`, not a raw `UnicodeEncodeError`
