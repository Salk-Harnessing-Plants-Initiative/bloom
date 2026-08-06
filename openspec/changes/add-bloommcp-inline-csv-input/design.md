## Context

`qc_clean` (`bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/qc_clean.py`) is the first
consumer to accept `csv_content`. Its current shape is fixed by the shipped code:

- `QCCleanParams.experiment: str` is `Field(...)` (required). The tool body calls
  `reader.load_experiment(params.experiment, version="raw")` to get an `ExperimentFrame`, then
  resolves roles independently via `resolve_columns(frame.df, sample_id_column=…,
  genotype_column=…, exclude_columns=…)` from `bloom_mcp.data_access.columns` — **it does not
  use `frame.trait_cols`/`frame.genotype_col`/etc. from the reader at all**, only `frame.df`,
  `frame.source`, and `frame.resolved_source`. This matters: the inline path only needs to
  produce a `df`, not a fully-populated `ExperimentFrame` with correct declared roles, to slot
  into `qc_clean` unchanged.
- `@as_mcp_tool(input_model=, output_model=, errors=)` validates the Pydantic input **before**
  the tool body runs (`contract/wrap.py`): a `ValidationError` raised inside a Pydantic
  `model_validator` becomes a structured `invalid_input` `BloomMCPError` for free — no new error
  code needed for the mutual-exclusivity rule.
- `Provenance.stamp(tool=, params=data.model_dump(), seed=…)` is stamped **before** the tool
  body runs, from the full input model dump — so for the `csv_content` path, `Provenance.params`
  will contain the raw CSV text. This is contract-time only: since the inline branch never calls
  `store.create_run`/`commit`, this provenance record is discarded in memory when the call
  returns and is never logged, persisted, or included in the response. Flagged here because it
  is not obvious from reading `qc_clean.py` alone — a reviewer should not assume `Provenance` is
  free of the raw content on this path, only that it goes nowhere.
- `resolve_columns(df, …)` (`bloom_mcp/data_access/columns.py`) is pure: no experiment identity,
  no adapter, just role-name matching + `sleap_roots_analyze.get_trait_columns` for trait
  detection. This is exactly what an inline frame needs and is already what every adapter
  (`SupabaseReader`, `LocalReader`, `FakeReader`) funnels through, directly or via
  `detect_columns`.
- `ResultStore.create_run(*, experiment, tool_class, provenance, …)` / `.commit(run, outputs)`
  (`result_store/ports.py`) is the only path that writes anything — the inline branch must never
  call either.

## Goals / Non-Goals

- **Goals:** a shared, reusable `_inline_input` helper that turns a CSV string into an
  `ExperimentFrame` with the same role/trait resolution every adapter uses; wire it into
  `qc_clean` as a mutually-exclusive alternative to `experiment`; prove `qc_clean` cleans an
  inline frame identically to a file-based one (equivalence oracle against the existing
  `turface_19` golden); prove nothing is persisted; return `input_sha256` for the caller's own
  record-keeping.
- **Non-Goals:** any other consumer tool (their own follow-up changes); versioned history or
  lineage chaining for the inline path (explicitly out of scope in the issue); returning the
  cleaned table inline (qc_clean does not inline the table on the registered-experiment path
  either — no reason to grow a second response shape here); a persistent-upload/registration
  alternative (a different, larger feature per the issue).

## Decisions

- **Decision: the shared helper builds a real `ExperimentFrame`, not a bespoke tuple.**
  `parse_inline_csv_frame(csv_content: str) -> ExperimentFrame` in the new
  `bloom_mcp/tools/_inline_input.py` parses via `pandas.read_csv(io.StringIO(csv_content))`,
  resolves roles/traits via the existing `resolve_columns(df)` (no overrides — an inline caller
  has no prior chance to name role overrides the way `qc_clean`'s own params do; `qc_clean`
  still applies its own `sample_id_column`/`genotype_column` overrides afterward, unchanged),
  and returns `ExperimentFrame(df=df, trait_cols=resolved.trait_cols,
  metadata_cols=resolved.metadata_cols, genotype_col=resolved.genotype,
  replicate_col=resolved.replicate, sample_id_col=resolved.sample_id, source="inline")`. A real
  `ExperimentFrame` (not a raw `DataFrame` or a dict) means any future consumer tool that *does*
  read `frame.genotype_col`/etc. directly (unlike `qc_clean`) gets a correctly-shaped value for
  free — the helper is not shaped around `qc_clean`'s particular indifference to those fields.
  - *Alternative considered:* have `qc_clean` call `pd.read_csv` itself and skip `resolve_columns`
    for the inline branch, since it re-resolves roles anyway. Rejected: it would leave the shared
    helper doing nothing but a size check, defeating the point of building it as the reusable
    surface for every subsequent tool.
- **Decision: `source="inline"` reuses `QCCleanResult.source`; no new boolean field.** The
  existing `source: str` field already communicates provenance tier (`"raw"`, `"v3_cleaned"`,
  …), so `"inline"` slots in as one more value rather than adding a redundant
  `persisted: bool`. A caller (or a test) can branch on `result.source == "inline"`.
- **Decision: `experiment` becomes `Optional[str] = None` on `QCCleanResult` too, not a
  sentinel string.** `None` is honest — there is no experiment identifier for ephemeral input —
  and avoids a magic string (`"<inline>"`) a caller could mistake for a real identifier.
- **Decision: `run_ref`, `version_dir`, `manifest_path` become `Optional[str] = None`;
  `outputs` stays `dict[str, str]` defaulting to `{}`.** All three are meaningless when nothing
  is persisted. `output_links: dict[str, OutputLink]` gets the same treatment: `{}` on the
  inline path, `stored.output_links` on the persisted path — added when `origin/staging` (and
  therefore this branch, after merging it) picked up `output_links`/`OutputLink` mid-PR via the
  signed-URL-download change (#595/#581); an earlier draft of this document incorrectly said
  `QCCleanResult` had no such field, written from a read taken on a different branch before that
  merge — corrected here and reconciled in the implementation (see tasks.md §7.1).
- **Decision: exactly-one-of enforced in `qc_clean`'s tool body, not a Pydantic
  `model_validator`.** A `model_validator(mode="after")` was the first implementation, but a
  `ValueError` raised there is remapped by `BloomMCPError.from_input_validation` into a generic
  `"Input did not match the tool's schema (<root>: value_error)"` — the validator's own message
  text is discarded, not merely a value (verified empirically: `qc_clean({"experiment": ...,
  "csv_content": ...})` returned exactly that generic string, with no mention of either field).
  Reworked to raise `BloomMCPError` directly as the first lines of `qc_clean`'s body, the same
  pattern the existing genotype/sample_id-collision check (B-4) in this file already uses — this
  is what actually gets the specific, actionable message to the caller. Trade-off: a bare
  `QCCleanParams(experiment=..., csv_content=...)` construction that is never passed to
  `qc_clean` no longer raises at construction time (only calling `qc_clean` with it does) —
  accepted, since this matches how B-4's own check already behaves and no code in this repo
  constructs `QCCleanParams` for a purpose other than calling `qc_clean` with it.
- **Decision: `MAX_INLINE_CSV_BYTES = 5 * 1024 * 1024` (5 MiB), checked against the UTF-8
  encoded byte length of `csv_content` before parsing — but NOT sufficient on its own.**
  bloommcp runs in a shared container; an unbounded inline string is a caller-controlled
  allocation with no upload step to rate-limit it first. 5 MiB comfortably covers real
  phenotyping tables (`turface_19`: 187×20, ~30 KB; the `cylinder` fixture: 129×846, ~0.83 MB) —
  an earlier draft of this section claimed a "10,000-row × 200-column" table would also fit in "a
  few MB," which was simply wrong arithmetic (that shape is closer to 20 MB as text); corrected
  here rather than left standing. **A byte cap alone does not bound CPU cost**, which is the more
  important finding: a pathologically wide-but-short CSV (many narrow columns) can sit
  comfortably under this cap while still costing real CPU in `pandas.read_csv`'s per-column
  overhead. Measured directly: ~480,000 columns in a single row, 4.69 MB (under the cap), cost
  ~7.7s of CPU — a real, reproducible denial-of-service vector, since bloommcp has no rate
  limiting in front of this path (FastMCP ships a `RateLimitingMiddleware` but it is not wired
  into `server.py`) and this path has no persistence step to create natural backpressure. See the
  `MAX_INLINE_CSV_COLUMNS` decision below for the actual fix. The byte cap remains useful for what
  it does bound (aggregate payload size / in-memory frame size), just not CPU cost in isolation.
- **Decision: `MAX_INLINE_CSV_COLUMNS = 2000`, enforced via a cheap PRE-PARSE header-row
  estimate — not a post-parse `df.shape[1]` check.** A first draft checked column count only
  *after* `pandas.read_csv` returned, which does not prevent the CPU-cost DoS above: the expensive
  parse has already run by the time a post-parse check can fire. Verified the fix against the
  exact reported shape (~480,000 columns, 4.69 MB): rejection now takes ~0.002s instead of ~7.7s.
  The post-parse `df.shape[1] > MAX_INLINE_CSV_COLUMNS` check is retained only as an exact
  backstop for any residual divergence, not as the primary guard. 2000 is generous headroom over
  any real phenotyping table (`turface_19` has 20 trait columns; `cylinder` has 846) while still
  bounding the pathological case.
  - **Revision: the first implementation of the pre-parse estimate (`csv_content.split("\n",
    1)[0]`, then `csv.reader` on that one line) itself had a bypass, found in a second review
    round and reproduced directly.** A naive line-split cuts the header row short the moment any
    field contains a literal newline inside quotes (valid CSV) — a header whose first cell was a
    quoted value with one embedded newline made the estimate say "1 column" for a real
    ~480,000-column row, letting `pandas.read_csv` run anyway (~5.5s of CPU measured) before the
    post-parse backstop finally caught it, defeating the whole point. Fixed by feeding
    `csv.reader` a *bounded line iterator* (`_bounded_lines`) instead of a pre-split string:
    `csv.reader` then handles a multi-line quoted field correctly the same way iterating a real
    file does — pulling more lines from the iterator until the field's closing quote is found and
    the row is complete — recovering the header row's true extent instead of a fragment. The
    bound (`_MAX_HEADER_SCAN_BYTES = 256 KiB`) caps how much `_bounded_lines` will read before
    giving up and rejecting outright: without it, an *unterminated* quote would force scanning
    the entire payload looking for a closing quote that never comes, reintroducing the exact
    CPU-cost problem this guard exists to avoid, one level deeper. 256 KiB is generous headroom
    over any legitimate header row (even 2000 columns with unusually long names) while still
    being cheap to scan. Verified both directions: the exact bypass repro is rejected fast again
    (via either the corrected column count or the scan cap, whichever resolves first — both are
    safe outcomes), a header using the embedded-newline trick *past* ~2500 legitimate-looking
    columns (small enough to stay under the scan cap) is still counted correctly through the
    embedded newline rather than merely failing safe, and a genuinely small, legitimate header
    cell containing an embedded newline is accepted and counted correctly (not just tolerated by
    accident).
- **Decision: `input_sha256` is computed by the helper over the exact UTF-8-encoded
  `csv_content` string** (`hashlib.sha256(csv_content.encode("utf-8")).hexdigest()`) —
  independent of, and not reusing, `Provenance.stamp`'s own `input_sha256` field (that field is
  populated by `ResultStore` at commit from a file path per `contract-pinning`/manifest
  conventions, which the inline path never reaches). The two `input_sha256` computations happen
  to share a name but serve different tiers (manifest-level vs. this-response-only) — worth
  reviewer attention so nobody conflates them as the same code path.
- **Decision: malformed CSV (unparseable, zero rows, zero columns, encode failure, decode
  failure) raises `BloomMCPError(code="invalid_input", …)` from the helper, not a bare
  `pandas`/`Unicode` exception.** `qc_clean` is declared with `errors=(ExperimentReadError,)`
  today; a raw `pandas.errors.ParserError` would fall through to the contract's opaque
  `internal_error` if raised from inside the tool body without translation, which is the exact
  anti-pattern `qc_clean`'s own file-based error handling already avoids for its other structural
  failures. The helper does the mapping itself so `qc_clean` does not need `errors=` extended.
  **A gap found during review:** the `.encode("utf-8")` calls (the byte-size guard, and
  independently `compute_input_sha256`) ran outside any `try/except`. A lone UTF-16 surrogate in
  `csv_content` (reachable via a lossy upstream decode) raises `UnicodeEncodeError` there — before
  `pandas.read_csv` is ever called — and would otherwise propagate as an opaque `internal_error`,
  contradicting this module's own guarantee. Both call sites now wrap the encode step explicitly.
  **A second finding, not acted on (kept as documented, intentional dead code):** three
  independent review passes found that `df.shape[1] == 0` (checked after a successful parse) and
  the `except UnicodeDecodeError` clause around `pandas.read_csv` are not reachable through any
  real `csv_content` string — a `pandas.read_csv(io.StringIO(...))` call on an already-decoded
  `str` cannot itself raise `UnicodeDecodeError`, and every blank/whitespace/comma-only input that
  might otherwise produce a zero-column frame raises `EmptyDataError` first instead. Both are kept
  as defense-in-depth against a future pandas behavior change rather than removed, since the
  ongoing cost of two short branches is lower than the risk of removing a guard whose
  unreachability isn't provable across every pandas version/configuration; the corresponding
  tests were adjusted to mock `pandas.read_csv`'s return value directly so they honestly test the
  branch they claim to, rather than asserting on a string that cannot actually reach it.
  **A third branch flagged in the same review round, `_estimate_header_columns`'s `except
  StopIteration: return 0`, is a different story: the DoS-bypass rewrite below made it genuinely
  reachable** (`_bounded_lines("")` yields zero lines, so `next(csv.reader(...))` on empty
  `csv_content` now does raise `StopIteration`) — not dead code kept for defense-in-depth, but a
  real path exercised by the existing `test_empty_string_is_rejected` end-to-end test.
- **Decision: strip every leading UTF-8 BOM (`﻿`, one or more) from `csv_content` before
  parsing — not just the first one.** `csv_content` is a `str`, so Python's `encoding="utf-8-sig"`
  BOM-stripping (which only applies when *decoding bytes*) never runs. A caller pasting CSV text
  copied out of Excel or Windows Notepad routinely carries a literal `﻿` before the header row;
  left in place, the first column name becomes `"﻿Barcode"` instead of `"Barcode"`, silently
  breaking `resolve_columns`'s sample-id/genotype pattern matching for that one column. A first
  draft stripped only a single leading BOM (`if csv_content.startswith(_BOM): ...`) — reviewed and
  found to leave a mangled column name when a double-encoded or re-saved file carries more than
  one (e.g. `"﻿﻿﻿Barcode"` → stripping one still leaves `"﻿﻿Barcode"`). Fixed to
  `csv_content.lstrip(_BOM)`, which strips every leading occurrence.
- **Decision: suppress `qc_clean`'s `qc_inspect` nudge (`next_step`) entirely on the inline
  path.** `qc_clean.py`'s existing `next_step` advisory (built when `n_samples_dropped > 0`)
  interpolates `params.experiment!r}` into a "Run qc_inspect on {experiment!r}…" message. On the
  inline path `params.experiment is None`, so the message would literally read "Run qc_inspect
  on None" — and the advice would still be wrong even with the string fixed, because
  `qc_inspect` has no `csv_content` parameter (out of scope per #582's per-tool rollout) and
  cannot act on ephemeral input at all. Rather than reword the message for a tool that can't
  help, `qc_clean` SHALL set `next_step=None` unconditionally on the inline path, regardless of
  `n_samples_dropped`. Found during review, not obvious from reading `qc_clean.py` alone since
  the nudge is a late, easy-to-miss addition near the end of the function.
- **Decision: `_INLINE_EXPERIMENT_LABEL = "csv_content"`, not a full sentence.** Every error
  message that would normally interpolate `{params.experiment!r}` uses this placeholder in the
  same spot for the inline path, so it is always rendered through `!r`. An earlier draft used
  `"the supplied csv_content"`, which reads awkwardly once quoted (`'the supplied csv_content'`)
  — a bare `"csv_content"` reads cleanly as `'csv_content'` in the same messages a real
  experiment name (`'turface_19_raw.csv'`) would otherwise occupy.
- **Deferred, not implemented (reviewer suggestions, non-blocking):** (1) extracting the
  `if is_inline: ... else: ...` persistence/result-shaping block in `qc_clean` into standalone
  helper functions, ahead of any second consumer tool actually needing the same shape — premature
  given only one caller exists; revisit once a second tool's rollout PR shows what's genuinely
  shared vs. per-tool. (2) An explicit advisory string field (beyond `source="inline"`,
  `run_ref=None`, and the `csv_content` field description) stating non-persistence in the
  result itself — the existing signals already communicate this both structurally and in the
  schema description; a redundant free-text field adds surface area without a concrete gap it
  closes. Both are one-line additions to make later if a second tool's needs prove them out.

## Risks / Trade-offs

- **`Provenance.params` carries the raw CSV text in memory for the duration of the call.** See
  Context above — accepted because it is never persisted or logged on this path, but a future
  refactor that adds logging around `Provenance` (e.g. an audit trail) must exclude `csv_content`
  explicitly or this stops being true. Traced the current error-logging path (`contract/wrap.py`
  → `contract/errors.py`) during review: a `BloomMCPError` raised directly (as the helper and
  `qc_clean`'s own validation do) is re-raised unmapped and never logged; only the `internal_error`
  path calls `logger.error(...)`, and that call does not dump local variables — so today's code
  path genuinely never writes `csv_content` to a log. **Now pinned by regression tests**
  (`test_csv_content_never_appears_in_logs_on_success` /
  `..._on_internal_error` in `test_qc_clean_tool.py`), added after multiple review passes flagged
  the invariant as real but untested — both a successful call and a forced `internal_error` are
  exercised with a unique marker embedded in the content, asserting it never appears in
  `caplog.text` or in the returned error's `message`/`remedy`.
- **The 5 MiB byte cap and the 2000-column cap are both judgment calls, not measured limits.**
  Easy to change (module constants) if real usage says otherwise. The column cap in particular
  was added specifically in response to a demonstrated CPU-cost DoS (see the
  `MAX_INLINE_CSV_COLUMNS` decision above) rather than derived from a load test — 2000 is simply
  generous headroom over any real phenotyping table seen in this repo's fixtures.
- **No streaming / chunked parsing.** `pandas.read_csv` on a `StringIO` loads the full frame at
  once, same as every other adapter — consistent with existing behavior, not a new limitation.
- **`QCCleanResult`'s `experiment`/`run_ref`/`version_dir`/`manifest_path` widen from required
  `str` to `Optional[str]` for *every* caller, not only inline ones.** This is a real (if minor)
  JSON-schema contract change to an already-shipped, registered MCP tool: any existing caller
  that assumed these fields are always a non-null string now sees a schema that allows `null`,
  even though a registered-`experiment` call still always populates them with a real string in
  practice. Accepted because it is additive (widening, not narrowing or removing a field) and
  because MCP/JSON-schema consumers are expected to tolerate `Optional` fields — flagged
  explicitly here so a reviewer evaluates it as a deliberate trade-off, not an oversight.

## Migration Plan

Additive only — one new optional parameter (mutually exclusive with the existing required one,
which becomes optional but behaves identically when supplied), one new internal helper module,
no schema/manifest/dependency change. Existing `qc_clean(experiment=...)` calls are unaffected.
Rollback = revert the parameter addition; the helper module is otherwise unused and safe to
remove with it.

## Open Questions

- **`MAX_INLINE_CSV_BYTES` (5 MiB) and `MAX_INLINE_CSV_COLUMNS` (2000) values** — both are
  judgment calls (see Risks above), not derived from a measured production constraint; flag if
  either is wrong for real Claude Code usage patterns. (The column cap was resolved from
  "deferred unless requested" to "implemented" during review, once a concrete CPU-cost DoS was
  demonstrated against the byte cap alone — see the `MAX_INLINE_CSV_COLUMNS` decision above.)
- **Row-count cap independent of column count?** Not proposed — a pathologically tall-but-narrow
  CSV is still bounded by `MAX_INLINE_CSV_BYTES` (many rows of a few columns each still
  accumulates bytes linearly, unlike the columns case, which showed CPU cost decoupled from byte
  size). Revisit only if a similar decoupled-cost repro is demonstrated for rows.
