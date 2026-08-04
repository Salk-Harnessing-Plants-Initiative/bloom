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
  is persisted. `output_links` already defaults to `{}` (existing field) — unchanged.
- **Decision: exactly-one-of via a Pydantic `model_validator(mode="after")` on
  `QCCleanParams`, not two `Optional` fields left to the tool body to sort out.** Raising
  `ValueError` there is—per the tool-contract's existing "Invalid input is rejected before the
  tool body runs" guarantee—automatically mapped to `invalid_input` before `qc_clean`'s body
  executes. No new error code, no new contract-layer change.
- **Decision: `MAX_INLINE_CSV_BYTES = 5 * 1024 * 1024` (5 MiB), checked against the UTF-8
  encoded byte length of `csv_content` before parsing.** bloommcp runs in a shared container
  (see `project.md`'s data-dir/MinIO constraints); an unbounded inline string is a caller-
  controlled allocation with no upload step to rate-limit it first. 5 MiB is generous headroom
  over any realistic phenotyping table (the `turface_19` fixture — 187 samples × 20 traits — is
  ~30 KB; a 10,000-row × 200-column table is still only a few MB as text) while bounding
  worst-case memory (pandas' in-memory representation is a multiple of the raw text size). Over
  the cap raises `BloomMCPError(code="invalid_input", …)` naming the byte count and the limit.
  **Open question for reviewers:** this number is a judgment call, not derived from a measured
  constraint — flag if 5 MiB is wrong for real Claude Code usage patterns.
- **Decision: `input_sha256` is computed by the helper over the exact UTF-8-encoded
  `csv_content` string** (`hashlib.sha256(csv_content.encode("utf-8")).hexdigest()`) —
  independent of, and not reusing, `Provenance.stamp`'s own `input_sha256` field (that field is
  populated by `ResultStore` at commit from a file path per `contract-pinning`/manifest
  conventions, which the inline path never reaches). The two `input_sha256` computations happen
  to share a name but serve different tiers (manifest-level vs. this-response-only) — worth
  reviewer attention so nobody conflates them as the same code path.
- **Decision: malformed CSV (unparseable, zero rows, zero columns, decode failure) raises
  `BloomMCPError(code="invalid_input", …)` from the helper, not a bare `pandas`/`Unicode`
  exception.** `qc_clean` is declared with `errors=(ExperimentReadError,)` today; a raw
  `pandas.errors.ParserError` would fall through to the contract's opaque `internal_error` if
  raised from inside the tool body without translation, which is the exact anti-pattern
  `qc_clean`'s own file-based error handling already avoids for its other structural failures.
  The helper does the mapping itself so `qc_clean` does not need `errors=` extended.
- **Decision: strip a single leading UTF-8 BOM (`﻿`) from `csv_content` before parsing.**
  `csv_content` is a `str`, so Python's `encoding="utf-8-sig"` BOM-stripping (which only applies
  when *decoding bytes*) never runs. A caller pasting CSV text copied out of Excel or Windows
  Notepad routinely carries a literal `﻿` before the header row; left in place, the first
  column name becomes `"﻿Barcode"` instead of `"Barcode"`, silently breaking
  `resolve_columns`'s sample-id/genotype pattern matching for that one column — exactly the kind
  of parsing edge case this helper exists to guard. The helper SHALL strip at most one leading
  `﻿` before handing the string to `pandas.read_csv`, mirroring what `utf-8-sig` decoding
  would have done had the content arrived as bytes.
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

## Risks / Trade-offs

- **`Provenance.params` carries the raw CSV text in memory for the duration of the call.** See
  Context above — accepted because it is never persisted or logged on this path, but a future
  refactor that adds logging around `Provenance` (e.g. an audit trail) must exclude `csv_content`
  explicitly or this stops being true. Not fixed here since no such logging exists today. Traced
  the current error-logging path (`contract/wrap.py` → `contract/errors.py`) during review: a
  `BloomMCPError` raised directly (as the helper and `qc_clean`'s own validation do) is re-raised
  unmapped and never logged; only the `internal_error` path calls `logger.error(...)`, and that
  call does not dump local variables — so today's code path genuinely never writes `csv_content`
  to a log. No test currently pins this invariant; a cheap follow-up would assert no log record
  emitted during a `qc_clean(csv_content=...)` call contains the input text, but it is not added
  here since there is no logging call on this path to regress against yet.
- **The 5 MiB cap is a first guess, not a measured limit.** Easy to change (one module
  constant) if real usage says otherwise; flagged as an open decision above.
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

- **`MAX_INLINE_CSV_BYTES` value** — proposed 5 MiB (above); confirm before implementation or
  adjust.
- **Row-count cap in addition to a byte cap?** A byte cap already bounds memory; the issue does
  not ask for a separate row limit and none is proposed here, but a reviewer may want one for a
  pathologically wide-but-short file. Deferred unless requested.
