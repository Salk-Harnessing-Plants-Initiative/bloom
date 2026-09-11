## MODIFIED Requirements

> **Archive-order dependency.** This block is written against
> `add-bloommcp-inline-csv-input`'s text, which is merged to `staging` but **not yet archived**.
> This change MUST be archived **after** it, or the predecessor's narrower version of this
> requirement would replace the strengthened one below with no validation error.

### Requirement: Inline CSV Column-Count Guard Runs Before Parsing

A byte-size cap alone SHALL NOT be treated as sufficient to bound the CPU cost of parsing inline
content: a pathologically wide-but-short CSV (many narrow columns in one or few rows) can sit
comfortably under `MAX_INLINE_CSV_BYTES` while still costing seconds of CPU in `pandas.read_csv`'s
per-column overhead (measured: approximately 480,000 columns in a single row, 4.69 MB, cost
approximately 7.7 seconds of CPU) — a real, reproducible denial-of-service vector against the
shared container, which has no rate limiting in front of this path and no persistence step to
create natural backpressure.

**Measuring the header row alone is not sufficient, and left the guard fully bypassable.**
Reproduced: a 3-field header paired with 480,000-field *data* rows — 1.92 MB, under every
declared cap — was accepted after 16.03 seconds in `pandas.read_csv`, and accepted *silently*,
because `read_csv` does not require data rows to match the header's width and absorbs the surplus
into an implicit index rather than raising. The resulting frame had 3 columns, so even the
post-parse `df.shape[1]` backstop passed. Parse cost tracks the **widest row's** field count, not
the header's. The system SHALL therefore scan the header row **and the first data row** — before
`pandas.read_csv` is ever invoked — and SHALL reject content when either exceeds
`MAX_INLINE_CSV_COLUMNS` (2000), with a `BloomMCPError` (`invalid_input`) naming the offending
count and the limit.

Sampling one data row SHALL be treated as sufficient, on this basis: the silent, expensive
index-promotion path requires the divergence to be *consistent*, and the width `read_csv` locks in
comes from the first data row. A wide row appearing later is inconsistent with the first and is
rejected by the tokenizer itself with a `ParserError` in approximately 0.000 seconds (measured,
including against a following 200,000-field row at 0.002 seconds) — already mapped to
`invalid_input`. So a wide row beyond the scan window is either consistent with the first row and
caught by this guard, or inconsistent and caught cheaply by the parser.

**A first data row WIDER than the header SHALL be rejected** rather than resolved by
`pandas.read_csv`, as a correctness requirement and not only a cost one. Measured on a 3-name
header against 4-field rows: the default read promotes the first field to the index, so every
remaining value lands under the **wrong column name** (the barcodes became the index and the
genotypes became `Barcode`); `index_col=False` instead silently drops the trailing field. A
misaligned frame cleans and analyzes without complaint and reports confident nonsense, which for a
traceability-focused tool is worse than a refusal — so neither resolution is acceptable and the
content SHALL be refused.

**A first data row NARROWER than the header SHALL be accepted.** That direction carries no
misalignment risk: `pandas` NaN-pads the trailing columns and every value stays under its own name
(verified — a `a,b,c` header over a `1,2` row yields `1`/`2`/`NaN` on a plain `RangeIndex`, with
no warning). Rejecting it would turn a previously-valid inline CSV with a short trailing first row
into a hard error for no benefit, and would not close any bypass, per the sampling argument above.

This scan SHALL correctly resolve a row's true extent even when a cell contains a literal newline
inside quotes (valid CSV) — a naive single-line split (e.g. `csv_content.split("\n", 1)[0]`)
cuts such a row short and undercounts, which was found to let the exact denial-of-service payload
above bypass this guard entirely when one header cell carried an embedded newline (reproduced: the
estimate reported "1 column," and `pandas.read_csv` ran anyway, costing several seconds of CPU
before a post-parse check caught it — defeating the guard's purpose). The scan SHALL instead read
rows via a multi-line-aware tokenizer (the same mechanism by which iterating a real file correctly
handles a quoted field spanning multiple physical lines), bounded to a fixed maximum number of
bytes so that an *unterminated* quote cannot force scanning the entire payload in search of a
closing quote that never comes. Content whose leading rows cannot be resolved within that bound
SHALL be rejected outright with a `BloomMCPError` (`invalid_input`), rather than guessed at. This
bound SHALL be understood as load-bearing beyond the unterminated-quote case: a single
legitimately-terminated but enormous row also exhausts it, and is refused in milliseconds rather
than measured and then refused.

The size checks SHALL run before this scan, so that content which is simply too large is reported
as too large rather than as an unreadable leading row. Encoding a payload already known to be
within the cap is bounded work, so nothing is lost by ordering them that way.

A post-parse `df.shape[1]` check SHALL be retained as an exact backstop for any residual
divergence between the pre-parse scan and `pandas.read_csv`'s own tokenization, but SHALL NOT be
the primary guard, since by the time it runs the expensive parse has already completed — and, as
the bypass above showed, in the implicit-index case it does not fire at all.

#### Scenario: A wide-but-short CSV is rejected before pandas.read_csv runs

- **WHEN** `parse_inline_csv_frame` is called with content whose header row implies more than
  `MAX_INLINE_CSV_COLUMNS` columns, sized well under `MAX_INLINE_CSV_BYTES`
- **THEN** it raises `BloomMCPError(code="invalid_input")`, and `pandas.read_csv` is never
  called — verified by a spy/mock asserting zero calls
- **AND** the reproduction of the reported denial-of-service shape (~480,000 columns, ~4.69 MB)
  is rejected in well under one second, not after paying the multi-second parse cost a
  post-parse-only check would incur

#### Scenario: Wide data rows behind a narrow header are rejected before parsing

- **WHEN** `parse_inline_csv_frame` is called with a 3-field header paired with data rows of
  approximately 480,000 fields, sized under `MAX_INLINE_CSV_BYTES`
- **THEN** it raises `BloomMCPError(code="invalid_input")`, `pandas.read_csv` is never called,
  and rejection happens in well under one second — where the header-only guard accepted this
  same payload after approximately 16 seconds and returned a 3-column frame

#### Scenario: A wide data row that fits inside the scan bound is still rejected

- **WHEN** `parse_inline_csv_frame` is called with a narrow header and data rows of a few
  thousand fields — small enough that the bounded scan reads them without exhausting its budget
- **THEN** the data-row column check itself rejects the content, naming the field count, and
  `pandas.read_csv` is never called

#### Scenario: A data row wider than the header is rejected rather than silently realigned

- **WHEN** `parse_inline_csv_frame` is called with a 3-name header and a first data row of 4
  fields
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming both counts, and
  `pandas.read_csv` is never called — rather than producing a frame whose values sit under the
  wrong column names

#### Scenario: A data row narrower than the header is accepted and NaN-padded

- **WHEN** `parse_inline_csv_frame` is called with a 3-name header whose first data row has 2
  fields
- **THEN** the content is accepted, every supplied value appears under its own column name, the
  missing trailing value is `NaN`, and the frame carries a plain `RangeIndex`

#### Scenario: A narrow first row does not reopen the expensive path

- **WHEN** `parse_inline_csv_frame` is called with a narrow first data row followed by a row of
  approximately 200,000 fields
- **THEN** it raises `BloomMCPError(code="invalid_input")` in well under one second — the
  tokenizer rejects the inconsistency rather than promoting an index

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

#### Scenario: A single row too wide to measure cheaply is refused

- **WHEN** `parse_inline_csv_frame` is called with content under `MAX_INLINE_CSV_BYTES` whose
  single data row exceeds the scan bound
- **THEN** it is rejected, naming the scan bound — a row that cannot be measured without reading
  the very payload the bound exists to avoid reading is refused rather than waved through

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

## ADDED Requirements

### Requirement: Exactly One of experiment or csv_content

Every tool accepting inline content SHALL require **exactly one** of `experiment` (or, for
`load_experiment_data`, `filename`) and `csv_content`, raising `BloomMCPError`
(`invalid_input`) when both or neither is given. This check SHALL run **before** any reader
call, before any parse, and before the registered-only-parameter check below — so a malformed
call fails without touching Storage, the DB, or `pandas`, and a call that is wrong in two ways
reports the input conflict first rather than a parameter conflict that is moot.

The check SHALL be performed in the tool body path, not a Pydantic `@model_validator`, because
a validator's raised `ValueError` is remapped by the contract layer into a generic
`"(<root>: value_error)"` message that discards the author's own message text.

#### Scenario: Both inputs supplied is rejected before any read or parse

- **WHEN** any tool that accepts inline content is called with both its registered-experiment
  parameter and `csv_content` supplied
- **THEN** it raises `BloomMCPError(code="invalid_input")` stating that exactly one is required,
  **AND** neither `ExperimentReader.load_experiment` nor `pandas.read_csv` is called — verified
  by spies asserting zero calls on both

#### Scenario: Neither input supplied is rejected

- **WHEN** any tool that accepts inline content is called with neither its registered-experiment
  parameter nor `csv_content`
- **THEN** it raises `BloomMCPError(code="invalid_input")` stating that exactly one is required

#### Scenario: The input conflict is reported before a parameter conflict

- **WHEN** a tool is called with both inputs supplied **and** a registered-only parameter
  (e.g. `version`)
- **THEN** the error names the input conflict, not the parameter — the two checks have a
  defined order rather than an incidental one

### Requirement: One Resolver, One Message Vocabulary

The system SHALL provide a single shared resolver in `bloom_mcp.tools._inline_input` —
`resolve_inline_or_experiment` — that every tool accepting inline content uses to obtain its
frame, so the exactly-one-of rule, the registered-only-parameter rejection, the parse, and the
`input_sha256` computation have exactly one implementation across every tool.

On the inline path the resolver SHALL return a frame produced by `parse_inline_csv_frame` — so
every size, row-count, column-count, BOM, encoding, and malformed-content guard applies
unchanged and **no tool can bypass them by calling `pandas.read_csv` itself** — together with
the `compute_input_sha256` digest of the exact caller-supplied bytes. On the registered path it
SHALL return the frame from the tool's own supplied reader call with a `None` digest, so each
tool's `require_clean`, version pinning, and read-error mapping stay in that tool.

The resolver SHALL expose a `label` for use wherever a tool interpolates an experiment
identifier into an error message: the experiment name on the registered path, and the literal
`csv_content` on the inline path, so **no message ever interpolates `None` as an identifier** —
including messages raised deep inside a tool, such as `remove_outliers`' fit-quality gate.

Because `load_experiment_data` pairs `csv_content` with `filename` rather than `experiment`,
and `cross_experiment_correlations` resolves per side, the resolver SHALL take the registered
parameter's name so those tools reuse one vocabulary rather than forking it. Message equality
across tools is therefore required **modulo that parameter name**.

#### Scenario: The mutual-exclusivity message is identical across every tool

- **WHEN** the both-supplied and neither-supplied calls are made against every tool that accepts
  inline content
- **THEN** each tool's message and remedy are identical to every other tool's after substituting
  that tool's registered parameter name, proving they share one implementation rather than ten
  drifted copies

#### Scenario: Error messages name csv_content rather than None

- **WHEN** an inline call fails any check whose message would normally interpolate the
  experiment identifier — a column override naming an absent column, a trait-subset rejection,
  `remove_outliers`' fit-quality gate, or `summarize_trait`'s missing-genotype error
- **THEN** the message contains `'csv_content'` and does not contain `'None'` as the identifier

#### Scenario: No tool parses caller content outside the shared helper

- **WHEN** each tool that accepts inline content is called with content whose header implies more
  than `MAX_INLINE_CSV_COLUMNS` columns, or whose byte length exceeds `MAX_INLINE_CSV_BYTES`
- **THEN** each is rejected with `invalid_input` naming the relevant limit, and a spy on
  `pandas.read_csv` records zero calls for the column case — proving the pre-parse guards are
  reachable from every entry point rather than assumed

### Requirement: Registered-Only Parameters Are Rejected, Never Ignored

A parameter that can only be honored against a registered experiment SHALL be **rejected** when
combined with inline content, never silently ignored — a caller who supplied a pin and received
a successful result must not be left believing the pin took effect. Rejection SHALL raise
`BloomMCPError` (`invalid_input`) naming the offending parameter and stating why it cannot
apply, with a remedy offering both "omit it" and "supply the registered experiment instead".

#### Scenario: A registered-only parameter with inline content is rejected

- **WHEN** a tool is called with `csv_content` and any parameter from its registered-only roster
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming that parameter, and no analysis
  is performed

#### Scenario: Registered-only parameters still work on the registered path

- **WHEN** each of those parameters is supplied together with a registered experiment and no
  `csv_content`
- **THEN** the call behaves exactly as it did before this change, with the pin honored

### Requirement: The Registered-Only Parameter Roster

The registered-only parameters SHALL be, per tool:

| Tool | Registered-only parameters |
| --- | --- |
| `qc_clean` | `source_id`, `run_id`, `user_label` |
| `qc_inspect` | `source_id`, `run_id`, `user_label` |
| `remove_outliers` | `version`, `user_label`, `include_plots`, `plots` |
| `pca_analysis` | `version`, `user_label`, `include_plots`, `plots`, `plot_font_family`, `plot_font_size`, `plot_alpha` |
| `umap_analysis` | `version`, `user_label`, `include_plots`, `plots`, `plot_font_family`, `plot_font_size`, `plot_cmap`, `plot_point_size` |
| `clustering` | `version`, `user_label`, `include_plots`, `plots` |
| `descriptive_stats` | `version`, `user_label` |
| `cross_experiment_correlations` | `version_1`, `version_2` (each rejected only when **its own side** is inline), `user_label` |
| `load_experiment_data` | `source_id`, `run_id` |
| `summarize_trait` | *(none — it has no version, source, or label parameters)* |

`user_label` is registered-only on every persisting tool because it names a version directory
the inline path never creates. The plot-companion parameters (`plots`, `plot_font_*`,
`plot_cmap`, `plot_point_size`, `plot_alpha`) are registered-only for the same reason
`include_plots` is: they configure figures the inline path does not produce. Documenting them as
"ignored" would contradict this capability's reject-don't-ignore rule.

`qc_inspect` has **no `include_plots` parameter** — its figures are unconditional — so it has
nothing to reject; see "qc_inspect's Inline Path Produces No Figures and No Run".

#### Scenario: A version pin with inline content is rejected

- **WHEN** any of `pca_analysis`, `umap_analysis`, `clustering`, `descriptive_stats`, or
  `remove_outliers` is called with both `csv_content` and `version`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming `version`

#### Scenario: `version="latest"` is rejected too, not treated as a harmless default

- **WHEN** `remove_outliers` is called with `csv_content` and `version="latest"`
- **THEN** it is rejected — the registered path coerces `None`/`"latest"` to `"latest_qc"`, so an
  innocuous-looking `"latest"` is a real pin request the inline path cannot honor

#### Scenario: A user_label with inline content is rejected

- **WHEN** any persisting tool is called with both `csv_content` and `user_label`
- **THEN** it raises `BloomMCPError(code="invalid_input")` explaining that `user_label` names a
  version directory and the inline path creates none

#### Scenario: A plot-companion parameter with inline content is rejected

- **WHEN** any plot-capable tool is called with `csv_content` and any of `plots`,
  `plot_font_family`, `plot_font_size`, `plot_cmap`, `plot_point_size`, or `plot_alpha` — with
  `include_plots` omitted
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming that parameter, rather than
  accepting it as a silently ignored no-op

#### Scenario: A source pin with inline content is rejected

- **WHEN** `qc_clean`, `qc_inspect`, or `load_experiment_data` is called with inline content and
  either `source_id` or `run_id`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the parameter

### Requirement: Inline Analysis Never Persists, on Every Tool

Every tool's inline path SHALL make no call to `ResultStore.create_run` or `ResultStore.commit`,
SHALL write no object to Storage, SHALL create no manifest entry, and SHALL never appear in
`list_existing_analyses` output.

For every tool whose result model carries run links, the response SHALL carry `run_ref`,
`version_dir`, and `manifest_path` as `None` with `outputs` and `output_links` empty (see the
`bloommcp-tool-contract` delta widening `RunLinks`). `load_experiment_data` and `summarize_trait`
carry no run-link fields and persist nothing on either path; their inline paths SHALL likewise
create no run.

This SHALL be verified **per tool** by a spy or mock on the injected `ResultStore` whose
`create_run` and `commit` raise if called — not merely by observing that no run appears in a fake
store's records, and not by asserting it once on one tool and generalizing to the others.

Additionally, and more directly: `Provenance.to_version_entry` copies `params` — which carries
the caller's `csv_content` verbatim — into a manifest `VersionEntry`. The tests SHALL assert
**positively**, per tool, that no record held by the fake `ResultStore` contains a marker string
placed in the inline content. The `create_run`/`commit` spies are an indirect proxy for this;
the marker assertion is the actual egress path, and it is what catches a
`cross_experiment_correlations` implementation that forgets the "either side inline ⇒ fully
ephemeral" rule.

#### Scenario: Each tool's inline path touches no persistence port

- **WHEN** each of `qc_clean`, `qc_inspect`, `remove_outliers`, `pca_analysis`, `umap_analysis`,
  `clustering`, `descriptive_stats`, `cross_experiment_correlations`, `load_experiment_data`, and
  `summarize_trait` is called with inline content and otherwise valid parameters
- **THEN** for each tool independently, a `ResultStore` spy records zero `create_run` calls and
  zero `commit` calls

#### Scenario: Each run-link-carrying tool returns null run links inline

- **WHEN** each of the eight tools whose result model inherits or declares run links is called
  with inline content
- **THEN** for each independently, `run_ref`, `version_dir`, and `manifest_path` are `None` and
  `outputs` and `output_links` are empty

#### Scenario: No caller content reaches a manifest entry

- **WHEN** each tool is called with inline content carrying a distinctive marker in a data cell
- **THEN** for each tool independently, no record held by the fake `ResultStore` — manifest
  `VersionEntry.params` included — contains the marker

#### Scenario: Each tool's inline path bypasses the reader port

- **WHEN** each of those tools is called with inline content
- **THEN** for each tool independently, an `ExperimentReader` spy records zero
  `load_experiment` calls

#### Scenario: An inline analysis is invisible to list_existing_analyses

- **WHEN** `list_existing_analyses` is called for a registered experiment carrying committed runs
  and its payload is snapshotted; then one inline call is made on each persisting tool; then the
  module-global response cache is cleared and `list_existing_analyses` is called again
- **THEN** the two payloads are identical. The cache clear is required, not optional: the tool
  memoizes for 30 seconds with no invalidation hook, so a naive before/after comparison would
  pass off the cache and assert nothing

### Requirement: Inline Content Is Never Logged, on Every Tool

The caller-supplied `csv_content` SHALL never reach a log record, stdout, stderr, or any error
envelope returned to the caller, for any tool that accepts it. `Provenance.stamp` carries the raw
text in `params=data.model_dump()` in memory for the call's duration; that text SHALL NOT escape
the process by any of those channels.

Verification SHALL be **per tool** and SHALL reach past the Python `logging` module, because
three channels bypass a `logging` handler entirely:

- the MCP server's lowlevel dispatcher logs the whole JSON-RPC message — `csv_content`
  included — at `DEBUG`. The effective level is `INFO` today, so this does not fire; it is one
  log-level change away, which is exactly what an operator does during an incident;
- the upstream analysis delegates call bare `print()` in several places, which reaches stdout,
  not `logging`;
- `warnings.warn` reaches stderr.

Destination makes this load-bearing rather than theoretical: this repository is public, and the
deploy workflow echoes `docker compose logs` for all services into the GitHub Actions job log
when a deploy fails — container stdout from a failed deploy is a publicly readable artifact.

Tests SHALL therefore capture stdout and stderr alongside a handler attached directly to every
logger in the call graph (`caplog` is structurally blind here — `run_input_validation` sets
`propagate = False`), and SHALL place a marker in a **column name** as well as a data cell, since
caller-supplied column names reach exception text and thence the contract layer's
`logger.error(..., exc_info=exc)`.

#### Scenario: Content does not appear in logs, stdout, or stderr on the success path

- **WHEN** each tool that accepts inline content is called with content carrying distinctive
  markers in both a data cell and a column name, and the call succeeds
- **THEN** for each tool independently, neither marker appears in any captured log record, in
  captured stdout, or in captured stderr

#### Scenario: Content does not appear in logs or the error envelope on an internal error

- **WHEN** each contract-wrapped tool that accepts inline content is called with marker-carrying
  content and its delegate is made to raise an undeclared exception
- **THEN** for each tool independently, the raised `BloomMCPError` has code `internal_error`, and
  neither marker appears in its message, its remedy, any captured log record, stdout, or stderr

#### Scenario: Content does not leak even at DEBUG log level

- **WHEN** one representative inline call is made with the root logger set to `DEBUG`
- **THEN** the marker appears in no captured record — pinning the JSON-RPC dispatch path rather
  than relying on the default level to keep it quiet

#### Scenario: A schema-level rejection does not echo the content

- **WHEN** a tool is called with a `csv_content` value that fails Pydantic validation
- **THEN** the resulting envelope names the offending field's location only, never its value

### Requirement: Inline Content Is Caller-Asserted Analysis-Ready, With the Invariant Re-Checked Locally

Inline content SHALL NOT be treated as certified-clean, because certification lives in the
manifest and the inline path has none. A consumer tool that would otherwise read with
`require_clean=True` SHALL instead accept the caller's assertion and re-establish locally the
invariants that `require_clean` supplied — **matching each tool's own existing failure policy,
not a uniform one imposed across tools**:

- Tools that today treat a non-finite certified trait as an all-or-nothing failure
  (`pca_analysis`, `umap_analysis`, `clustering`, `cross_experiment_correlations`) SHALL raise on
  the inline path too, but with code `invalid_input` — **not** `assumption_violated`, which
  attributes the fault to a mis-reporting reader this path does not use — naming the offending
  columns, with a remedy directing the caller to
  `qc_clean(csv_content=..., return_cleaned_csv=true)`. Their registered-path error, message, and
  remedy SHALL be unchanged.
- `descriptive_stats` SHALL keep its existing per-trait policy on both paths: a non-finite trait
  is routed to `failed_traits` rather than failing the call, deliberately so one bad trait does
  not block hundreds of healthy ones. It SHALL NOT gain an all-or-nothing guard.
- `remove_outliers` has no finiteness check today, because `require_clean` made one unreachable.
  It SHALL gain one **scoped to the inline path**, raising `invalid_input` with the same remedy.
  The registered path SHALL be unchanged.

Where a tool validates a caller-supplied `trait_columns` subset against the frame's trait set,
the **set of accepted columns SHALL be unchanged** on the inline path (empty lists and duplicates
still rejected; membership in the frame's resolved trait columns still required), but the error
wording SHALL NOT claim the columns are or are not "certified-clean traits of `<experiment>`",
since no certification was made and no experiment exists.

#### Scenario: Non-finite inline content is a caller error, not an assumption violation

- **WHEN** `pca_analysis`, `umap_analysis`, `clustering`, or `cross_experiment_correlations` is
  called with `csv_content` whose selected trait columns contain a NaN or an infinity
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the offending columns, with a
  remedy naming `qc_clean` with `return_cleaned_csv`
- **AND** the same content read through a registered experiment still raises the existing
  `assumption_violated` error with its original message and remedy

#### Scenario: descriptive_stats keeps its per-trait policy on both paths

- **WHEN** `descriptive_stats` is called with `csv_content` containing one non-finite trait
  alongside several finite ones
- **THEN** the finite traits are reported and the non-finite one appears in `failed_traits` — the
  call does not raise, exactly as on the registered path

#### Scenario: remove_outliers gains an inline-only finiteness guard

- **WHEN** `remove_outliers` is called with `csv_content` whose selected trait columns contain a
  non-finite value
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the columns
- **AND** its registered path is unchanged — it has no finiteness check there and gains none

#### Scenario: Trait-subset validation accepts an identical column set on both paths

- **WHEN** the same table is analyzed once as a registered cleaned experiment and once as
  `csv_content`, each with the same `trait_columns` selection
- **THEN** the set of accepted columns is identical, and an empty list, a duplicated column, and
  a column outside the frame's trait set are each rejected on both paths

#### Scenario: Inline trait-subset errors do not claim a certification that was never made

- **WHEN** an inline call names a `trait_columns` entry that is not in the frame's resolved trait
  set
- **THEN** the error message describes the columns as not being detected trait columns of
  `csv_content`, and does not describe them as not being "certified-clean traits" of an
  experiment

#### Scenario: A frame missing a required role column fails with a usable message

- **WHEN** a tool requiring a genotype or sample-identifier role is called with `csv_content`
  whose frame resolves neither
- **THEN** it raises `BloomMCPError(code="invalid_input")` listing the available columns and
  naming `csv_content`, not `'None'`, as the source

### Requirement: Plot Generation Is Rejected on the Inline Path

A tool offering `include_plots` SHALL reject it when combined with inline content.
`remove_outliers`, `pca_analysis`, `umap_analysis`, and `clustering` SHALL each raise
`BloomMCPError` (`invalid_input`) whose remedy names the registered-experiment path. The request
SHALL NOT be silently downgraded to `include_plots=false`.

No figure SHALL be produced on the inline path by any tool. The load-bearing assertion is a spy
on figure saving, **not** an assertion that the shared plots directory is unchanged: these tools
write figures into the run's staging directory, never into the shared plots directory, so the
directory assertion cannot fail and proves nothing. It is retained only as a defense-in-depth
backstop.

The shared plots directory is not an acceptable destination for inline-derived figures: it is a
single directory shared by the whole container, mounted into more than one service and served as
unauthenticated static files from the public ingress, with deterministically derived filenames.
Writing content the caller explicitly chose not to register into that location would contradict
the guarantee this capability exists to provide.

#### Scenario: include_plots with inline content is rejected

- **WHEN** any of `remove_outliers`, `pca_analysis`, `umap_analysis`, or `clustering` is called
  with both `csv_content` and `include_plots=true`
- **THEN** it raises `BloomMCPError(code="invalid_input")` with a remedy naming the
  registered-experiment path, and no analysis result is returned

#### Scenario: No figure is saved by any inline call

- **WHEN** any inline call is made to any plot-capable tool, with `include_plots` omitted
- **THEN** a spy on `Figure.savefig` (patched to raise) records zero calls, and the shared plots
  directory's contents are unchanged

#### Scenario: Plots still work unchanged on the registered path

- **WHEN** a plot-capable tool is called with a registered experiment and `include_plots=true`
- **THEN** the plots are generated and persisted as run artifacts exactly as before this change

### Requirement: Inline Content Has Row and Cost Guards Beyond the Byte Cap

Inline content SHALL be bounded by a row cap and by per-tool cost caps, not by the byte cap alone.
The existing `MAX_INLINE_CSV_BYTES` (5 MiB) and `MAX_INLINE_CSV_COLUMNS` (2000) guards were
sized for `qc_clean`, whose work is linear in the payload. Several tools this capability now
serves are super-linear, and measurement shows the byte cap does not bound them: a compliant
5 MiB payload parses in ~0.03 s into **313,171 rows**, and `clustering(method="hierarchical")`
is O(n²) in both time and memory (measured: n=6,000 → 1.7 s and +809 MiB resident;
n=12,000 → 7.2 s and +2.38 GiB). At the row count the byte cap admits, the condensed distance
matrix alone would be hundreds of gibibytes. Separately,
`cross_experiment_correlations` costs a measured ~326 µs per trait pair and defaults to *all*
trait columns on both sides, so two inline sides at the column cap imply 4,000,000 pairs —
roughly 22 minutes of single-threaded CPU for one request.

Nothing sits in front of this path: no rate limiting, no request-body cap at the proxy, no
per-tool timeout, and no container memory limit — so an out-of-memory event is resolved by the
host, which may kill an unrelated service. The system SHALL therefore enforce:

- `MAX_INLINE_CSV_ROWS` (20,000) in `parse_inline_csv_frame`, applied to every inline parse.
  20,000 is roughly a hundred times the largest real experiment fixture in this repository.
- A stricter inline sample cap for `clustering(method="hierarchical")` specifically, checked
  before the delegate is called, since its cost is quadratic where the other methods' is not.
- A cap on `len(trait_cols_1) * len(trait_cols_2)` in `cross_experiment_correlations` when either
  side is inline, checked after trait resolution and before the delegate is called, set high
  enough that the existing registered-path oracle remains unaffected.

Each guard SHALL raise `BloomMCPError` (`invalid_input`) naming the measured value and the limit,
with a remedy offering both a cheaper option and registering the data as an experiment. Each
SHALL fire **before** the expensive work, not after. The registered path SHALL be unaffected by
all three.

#### Scenario: A row count above the cap is rejected at parse time

- **WHEN** `parse_inline_csv_frame` is called with content whose parsed frame exceeds
  `MAX_INLINE_CSV_ROWS`, sized under `MAX_INLINE_CSV_BYTES`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the row count and the limit;
  content at exactly the limit is accepted

#### Scenario: Hierarchical clustering rejects an oversized inline frame before fitting

- **WHEN** `clustering` is called with `csv_content` and `method="hierarchical"` on a frame above
  the inline hierarchical sample cap
- **THEN** it raises `BloomMCPError(code="invalid_input")`, a spy on the delegate records zero
  calls, and the same frame with `method="kmeans"` is accepted

#### Scenario: An unbounded max_clusters is rejected by the schema

- **WHEN** `clustering` is called with `max_clusters` above its declared upper bound
- **THEN** the schema rejects it, on both the registered and the inline path — today the field
  has a lower bound but no upper one, and it multiplies the quadratic silhouette search

#### Scenario: An oversized trait-pair product is rejected before correlating

- **WHEN** `cross_experiment_correlations` is called with either side inline and a resolved
  `len(trait_cols_1) * len(trait_cols_2)` above the cap
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming both counts and their product,
  and a spy on the correlation delegate records zero calls

#### Scenario: The guards do not constrain the registered path

- **WHEN** the registered-path equivalence oracles for every affected tool are run, including the
  widest existing fixture
- **THEN** all pass unchanged — none of the three guards fires on a registered read

### Requirement: The Inline Path Has a Runtime Kill Switch

The system SHALL read an environment variable (`BLOOMMCP_INLINE_CSV_ENABLED`, default enabled)
once inside `resolve_inline_or_experiment`. When disabled, any call supplying `csv_content` SHALL
be rejected with `BloomMCPError` (`invalid_input`) whose remedy names the registered-experiment
path, and every registered path SHALL be untouched.

This exists because bloommcp has no feature flags today and the deploy pipeline's automatic
rollback covers only a *failed* deploy — a successfully deployed but misbehaving build is
reverted only by a new commit through a full multi-image rebuild. This change enables ten tools
at once; one variable and a container restart is a proportionate off switch.

#### Scenario: Disabling the flag rejects every inline call

- **WHEN** `BLOOMMCP_INLINE_CSV_ENABLED` is set to a false value and any tool is called with
  `csv_content`
- **THEN** it raises `BloomMCPError(code="invalid_input")` naming the registered-experiment path
  as the remedy

#### Scenario: Disabling the flag leaves the registered path untouched

- **WHEN** the flag is disabled and every tool is called with a registered experiment
- **THEN** each behaves exactly as it does with the flag enabled

### Requirement: load_experiment_data Accepts Inline Content

`load_experiment_data` SHALL accept `csv_content` as the mutually exclusive alternative to
`filename` and return the same formatted summary — sample count, genotype count, replicate
count, trait columns, and missing-data preview — computed from the supplied content. Because
this tool returns a formatted string rather than a structured model, the summary SHALL include an
explicit line carrying the `input_sha256` and an explicit statement that the content was not
registered and no run was recorded.

This tool is **not** contract-wrapped: it is a plain function that returns error strings rather
than raising `BloomMCPError`, and its Google-style `Args:` docstring is itself the parameter
schema the agent reads. Its inline errors SHALL therefore be returned as strings in that same
style, matching its existing convention rather than importing the contract layer's.

Because it is unwrapped, an unhandled exception inside it propagates without the contract layer's
redaction. Its inline path SHALL therefore guard its own failures explicitly rather than relying
on an envelope that does not exist for it.

#### Scenario: Inline summary matches the registered summary

- **WHEN** `load_experiment_data` is called with a fixture's raw text as `csv_content`, and
  separately with that fixture as a registered `filename`
- **THEN** the reported sample count, genotype count, replicate count, and trait columns are
  identical

#### Scenario: The inline summary states its content was not registered

- **WHEN** `load_experiment_data` is called with `csv_content`
- **THEN** the returned string contains the `input_sha256` hex digest and a statement that the
  content was not registered

#### Scenario: Inline errors are returned as strings, not raised

- **WHEN** `load_experiment_data` is called with both `filename` and `csv_content`, or with
  unparseable `csv_content`
- **THEN** it returns an error string in its existing style and raises nothing, matching how it
  already reports a conflicting `source_id`/`run_id` pair

### Requirement: summarize_trait Accepts Inline Content

`summarize_trait` SHALL accept `csv_content` as the mutually exclusive alternative to
`experiment` and return the same per-accession summary for the named trait, with `experiment` set
to `None` and `input_sha256` populated. A trait name absent from the supplied content, and a
frame with no resolvable accession/genotype column, SHALL each raise `BloomMCPError`
(`invalid_input`) naming `csv_content` rather than a non-existent experiment.

#### Scenario: Inline trait summary matches the registered path

- **WHEN** `summarize_trait` is called with a fixture's text as `csv_content` and with that
  fixture as a registered `experiment`, for the same trait
- **THEN** the per-accession summaries are identical, and the inline result's `experiment` is
  `None` with `input_sha256` equal to an independently computed digest

#### Scenario: An unknown trait names csv_content in the error

- **WHEN** `summarize_trait` is called with `csv_content` and a `trait` that is not a column in
  the supplied content
- **THEN** it raises `BloomMCPError(code="invalid_input")` whose message names `csv_content` and
  does not interpolate `None` as an experiment identifier

#### Scenario: A missing genotype column names csv_content in the error

- **WHEN** `summarize_trait` is called with `csv_content` whose frame resolves no
  accession/genotype column
- **THEN** the error message names `csv_content`, not `'None'`

### Requirement: Every Registered-Experiment Path Is Byte-Identical to Before

Adding the inline path SHALL NOT change any tool's behavior when called with a registered
experiment only. Each tool's registered path SHALL produce the same result, the same persisted
artifacts, the same provenance, and the same error text as before this change.

Two schema changes accompany this and SHALL be stated rather than discovered: each tool's
registered-experiment parameter widens from required to optional-with-a-body-check, and each
tool's **result** model widens its identity and run-link fields to optional (see the
`bloommcp-tool-contract` delta). Both are visible in `tools/list`.

#### Scenario: Existing golden oracles still pass unchanged

- **WHEN** every existing registered-experiment test and golden oracle for the affected tools is
  run against the changed code
- **THEN** all pass without modification to their expected values

#### Scenario: A missing registered experiment still fails, with a better message

- **WHEN** a tool is called with neither its registered-experiment parameter nor `csv_content`
- **THEN** it raises `invalid_input` from the tool body naming both inputs, rather than the
  contract layer's generic schema-mismatch text — a strictly more actionable error for the same
  invalid call

#### Scenario: Refactoring qc_clean onto the shared resolver preserves its behavior

- **WHEN** `qc_clean` is refactored to obtain its frame through `resolve_inline_or_experiment`,
  in a commit that touches no test file
- **THEN** its complete existing test suite passes unmodified — the refactor's entire claim
