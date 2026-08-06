> **TDD note:** sections below are numbered in execution order — RED before its matching GREEN,
> for the helper first, then for `qc_clean`. Do not implement a section before confirming its
> preceding RED tests actually fail (no shortcuts, no reordering). Do not push a RED-only commit
> (CI gates the PR head) — commit RED+GREEN together per the plan below.
>
> **Commit plan** (repo convention per `git log`: a dedicated `docs(#NNN): draft ... proposal`
> commit before implementation, confirmed by git-workflow review):
> 0. `docs(#582): draft add-bloommcp-inline-csv-input proposal` — this `openspec/changes/
>    add-bloommcp-inline-csv-input/` directory, committed before any code.
> 1. `feat(#582): add shared inline-CSV parsing helper` — §1–2 (helper + its tests), green.
> 2. `feat(#582): wire csv_content into qc_clean` — §3–4 (qc_clean model/body/tests), green. The
>    `QCCleanParams`/`QCCleanResult` model changes and the tool-body branching land in this SAME
>    commit — never split a model change from its wiring (a prior change in this repo shipped a
>    broken intermediate schema doing that).
> 3. `docs(#582): document csv_content inline-input example` — §5 (docstrings + connecting-
>    claude-code.md).

## 1. RED — shared helper unit tests (write first; the module does not exist yet)

- [x] 1.1 Add `bloommcp/tests/tools/test_inline_input.py`.
- [x] 1.2 Valid CSV → `ExperimentFrame` with `source == "inline"` and roles matching a direct
      `resolve_columns` call on the same parsed `DataFrame`. Confirm RED (module doesn't exist).
- [x] 1.3 Oversized content (construct a string whose UTF-8 encoding exceeds
      `MAX_INLINE_CSV_BYTES` by a small margin) → `BloomMCPError(invalid_input)` naming the byte
      count and limit; assert `pandas.read_csv` is never reached (patch/spy it, assert zero
      calls). Content exactly at the limit is accepted. Confirm RED.
- [x] 1.4 **Byte-vs-character size guard**: construct CSV content using multi-byte UTF-8
      characters (e.g. CJK or accented genotype names) whose character count is well under
      `MAX_INLINE_CSV_BYTES` but whose UTF-8 *byte* count exceeds it; assert rejection — pins
      that the guard measures encoded bytes, not `len(csv_content)`. Confirm RED.
- [x] 1.5 Malformed CSV (inconsistent field counts) → `BloomMCPError(invalid_input)`, not a raw
      `pandas.errors.ParserError`. Confirm RED.
- [x] 1.6 Empty string and header-only (zero data rows) → `BloomMCPError(invalid_input)` stating
      no data rows were found. Confirm RED.
- [x] 1.7 Content that parses to zero columns (e.g. a string of blank lines) →
      `BloomMCPError(invalid_input)` stating no columns were found. Confirm RED.
- [x] 1.8 Content that raises `UnicodeDecodeError` during parsing → `BloomMCPError(invalid_input)`
      describing the decode failure, not a raw `UnicodeDecodeError`. Confirm RED.
- [x] 1.9 **Leading UTF-8 BOM**: content whose first character is `﻿` immediately followed
      by an otherwise well-formed header → the returned frame's first column name has the BOM
      stripped (e.g. `"Barcode"`, not `"﻿Barcode"`). Confirm RED.
- [x] 1.10 CRLF-joined rows parse identically to the LF equivalent (same shape, same values) —
      guards `pandas.read_csv(io.StringIO(...))` behaving the same as the file-based path's
      `pandas.read_csv(path)` for CRLF content. Confirm RED.
- [x] 1.11 Non-ASCII content (accented / CJK genotype values) survives parsing intact — assert
      the parsed `DataFrame` cell values equal the original strings exactly (no mangling).
      Confirm RED.
- [x] 1.12 Duplicate column header and whitespace-only header: pin pandas' actual behavior
      (auto-suffix vs. rejection) rather than leaving it unspecified — assert the helper does not
      crash and produces the same column-naming behavior a direct `pandas.read_csv` call on the
      same text would (no special-casing added; documents inherited behavior, consistent with
      the un-special-cased file-based path). Confirm RED.
- [x] 1.13 `compute_input_sha256` matches an independently computed
      `hashlib.sha256(s.encode("utf-8")).hexdigest()` for several strings including an empty
      string and multi-byte UTF-8 content. Confirm RED.
- [x] 1.14 **Touches no persistence port**: patch `bloom_mcp.tools._ports.store` (or the module's
      resolved store) and assert `create_run`/`commit` are never called during
      `parse_inline_csv_frame`. Confirm RED (trivially, since nothing exists yet) — this pins the
      guarantee structurally so a future edit to the helper cannot regress it silently.

## 2. GREEN — implement the shared helper

- [x] 2.1 Add `bloommcp/src/bloom_mcp/tools/_inline_input.py`: `MAX_INLINE_CSV_BYTES = 5 *
      1024 * 1024`, `parse_inline_csv_frame(csv_content: str) -> ExperimentFrame`, and
      `compute_input_sha256(csv_content: str) -> str`.
- [x] 2.2 `parse_inline_csv_frame`: strip at most one leading `"﻿"` from `csv_content`.
      Check `len(csv_content.encode("utf-8"))` against `MAX_INLINE_CSV_BYTES` **before**
      parsing; raise `BloomMCPError(code="invalid_input")` naming the byte count and the limit if
      over. Otherwise `pandas.read_csv(io.StringIO(csv_content))`, catching
      `pandas.errors.ParserError` / `pandas.errors.EmptyDataError` / `UnicodeDecodeError` and
      re-raising as `BloomMCPError(code="invalid_input")` with a caller-safe message (no raw
      pandas traceback). Reject zero-row and zero-column results the same way.
- [x] 2.3 Resolve roles/traits via `resolve_columns(df)` (`bloom_mcp.data_access.columns`, no
      overrides) and build `ExperimentFrame(df=df, trait_cols=resolved.trait_cols,
      metadata_cols=resolved.metadata_cols, genotype_col=resolved.genotype,
      replicate_col=resolved.replicate, sample_id_col=resolved.sample_id, source="inline")`.
- [x] 2.4 `compute_input_sha256`: `hashlib.sha256(csv_content.encode("utf-8")).hexdigest()` (over
      the original string, i.e. before any BOM-stripping — the hash reflects exactly what the
      caller sent).
- [x] 2.5 Run §1's suite; debug to GREEN without weakening any guard (size, BOM, malformed-input
      mapping, no-persistence). **18/18 passed on first implementation.**

## 3. RED — qc_clean wiring (write first; today's model accepts only `experiment`)

- [x] 3.1 In `bloommcp/tests/tools/test_qc_clean_tool.py`, add mutual-exclusivity tests: both
      `experiment` and `csv_content` set → `BloomMCPError(invalid_input)` before the tool body
      runs (assert the injected reader's `load_experiment` is never called); neither set → same.
      Confirm RED (today's model accepts the `experiment`-only shape with no validator).
- [x] 3.2 **Equivalence oracle** (north star): load `turface_19_raw_data.csv`'s text via
      `Path.read_text(encoding="utf-8")`, call `qc_clean(QCCleanParams(csv_content=text,
      max_nans_per_trait=_MNT))`, and assert `n_samples_out`, `n_traits_out`, `removed_traits`,
      `genotype_column`, `sample_id_column` all equal the existing file-based oracle's result for
      the same thresholds. Confirm RED.
- [x] 3.3 Never-persisted guarantee: spy/mock the injected `ResultStore` (e.g.
      `unittest.mock.create_autospec` or a `FakeResultStore` subclass that fails the test if
      `create_run`/`commit` is called) and assert it is never invoked on the `csv_content` path.
      Confirm RED.
- [x] 3.4 Response shape: `csv_content` call → `result.experiment is None`, `result.source ==
      "inline"`, `result.input_sha256 == compute_input_sha256(text)`, `result.run_ref is None`,
      `result.version_dir is None`, `result.manifest_path is None`, `result.outputs == {}`.
      Confirm RED.
- [x] 3.5 Reader-bypass: spy on the injected `ExperimentReader` and assert `load_experiment` is
      never called on the `csv_content` path. Confirm RED.
- [x] 3.6 **`next_step` suppression**: run an inline call whose thresholds drop at least one
      sample (the condition that populates `next_step` on the `experiment` path) and assert
      `result.next_step is None` — no `qc_inspect` nudge, no `None` interpolated into a message.
      Confirm RED.
- [x] 3.7 **Override interaction**: `csv_content` combined with `sample_id_column` /
      `genotype_column` / `exclude_columns` / `trait_columns` overrides produces the same
      override-driven role resolution and cleaning as the equivalent `experiment` call (mirror
      the existing `experiment`-path override tests already in this file, substituting
      `csv_content=` for `experiment=`). Confirm RED.
- [x] 3.8 **Inline-branch error-message wording**: an unknown `sample_id_column` override, or a
      roleless inline CSV, on the `csv_content` path raises an error whose message reads e.g.
      "the supplied csv_content" rather than interpolating the literal string `"None"` where
      `params.experiment!r}` is normally used. Confirm RED against today's unconditional
      `{params.experiment!r}` interpolation.
- [x] 3.9 Existing `experiment`-only tests in `test_qc_clean_tool.py` continue to pass unmodified
      (confirms the required→optional `Field` change is behavior-preserving for the existing call
      shape). Run them now and record they are green before touching the model. **All 43
      pre-existing tests confirmed green before the model change.**

## 4. GREEN — implement qc_clean wiring

- [x] 4.1 `QCCleanParams`: change `experiment` to `Optional[str] = Field(default=None, ...)`;
      add `csv_content: Optional[str] = Field(default=None, description="Raw CSV text for a
      one-off analysis with no persistence. Mutually exclusive with experiment; exactly one is
      required. No run is persisted — no run_ref, no lineage into a later based_on_version, and
      no list_existing_analyses entry.")` (this description is the single canonical statement of
      the "no history" caveat — docs point here rather than restating it); add a
      `model_validator(mode="after")` raising `ValueError` unless exactly one of the two is set.
- [x] 4.2 `QCCleanResult`: change `experiment: str` to `Optional[str]`; change `run_ref: str`,
      `version_dir: str`, `manifest_path: str` to `Optional[str] = None`; add
      `input_sha256: Optional[str] = None`.
- [x] 4.3 In `qc_clean(params, *, provenance)`: branch on `params.csv_content is not None` —
      inline branch calls `parse_inline_csv_frame(params.csv_content)` for `frame` and
      `compute_input_sha256(params.csv_content)` for the hash, and skips
      `_ports.reader().load_experiment(...)` entirely; experiment branch is unchanged
      (`reader.load_experiment(params.experiment, version="raw")`). Every downstream step
      (role-override validation, `resolve_columns`, trait validation, input-contract validation,
      `clean_traits_for_analysis`, no-NaN guard) runs identically on `frame.df` regardless of
      branch — **no duplicated cleaning logic**.
- [x] 4.4 At the persistence step: only call `store.create_run(...)` / `.commit(...)` when
      `params.csv_content is None`; on the inline branch, skip straight to constructing
      `QCCleanResult` with `run_ref=None`, `version_dir=None`, `manifest_path=None`,
      `outputs={}`, `output_links={}`, `experiment=None`, `source="inline"`,
      `input_sha256=<computed hash>`, `next_step=None` (unconditionally — never compute the
      `qc_inspect` nudge on this branch, regardless of `n_samples_dropped`). **Superseded note:**
      an earlier draft of this task said `QCCleanResult` had no `output_links` field "as of this
      change" — that was true only before `origin/staging` picked up #595's `output_links`/
      `OutputLink` mid-PR; §7.1 below records the actual reconciliation (`output_links={}` inline,
      `stored.output_links` when persisted). A future rollout tool's author should treat
      `output_links` as a real field to set on both branches, not skip it.
- [x] 4.5 Adjust every reference to `params.experiment` used in error messages on the inline
      branch (e.g. the role-overrides unknown-column message, the genotype-blank message, the
      missing-role message) to read sensibly with no experiment name — use a fixed placeholder
      phrase (e.g. `"the supplied csv_content"`) rather than `None` interpolated into an
      f-string.
- [x] 4.6 Run §3's suite; debug to GREEN without weakening the equivalence oracle, the
      never-persisted guarantee, or the `next_step` suppression. **53/53 passed on first
      implementation (43 pre-existing + 10 new).**

## 5. Docs

- [x] 5.1 Update `qc_clean.py`'s module docstring (currently states unconditionally that the tool
      "reads the raw frame via the ExperimentReader port" and "persists a versioned run") and the
      `qc_clean` function's own docstring (currently `"""Clean ``experiment`` via analyze's
      ``clean_traits_for_analysis`` and persist it."""`, which is literally what `tools/list`
      surfaces to Claude Code) to describe both paths — this is more load-bearing than any
      markdown doc since it is what the calling agent reads.
- [x] 5.2 Add a short section to `bloommcp/docs/connecting-claude-code.md` stating plainly that
      an inline `csv_content` call never touches Bloom's shared Storage/DB — the fact this
      particular doc's own access-scope thesis makes most relevant to state — plus a minimal
      `qc_clean(csv_content="...")` example, and a pointer to `QCCleanParams.csv_content`'s field
      description for the full "no history" caveat rather than restating that list a second time
      in prose.

## 6. Refactor & verify

- [x] 6.1 Refactor for clarity; confirm the `experiment`-only path's behavior, tests, and
      registered tool schema for that call shape are unchanged except `experiment` moving from
      required to optional in the schema. Verified live via `tools/list`: `sleap_roots_qc_clean`'s
      input schema exposes both `experiment` and `csv_content`.
- [x] 6.2 `/pre-merge`: lint (`black --check` + `ruff check`, pinned to the repo's `.pre-commit-
      config.yaml` versions: black 26.3.1, ruff 0.9.9 — both clean after one `black` reformat of
      the two test files) + the exact CI suite command `cd bloommcp && uv run --frozen --extra
      test pytest tests/ -m "not integration and not live_smoke"` (green, full suite, both before
      and after the black reformat) + `uv run --frozen` import (server boots, tools/list
      confirmed) + `python scripts/check-uv-locks.py` (no drift across all 5 services) +
      `openspec validate add-bloommcp-inline-csv-input --strict` (valid).
- [ ] 6.3 Validate on Claude Code (the actual target client for this feature, per the issue):
      connect to a local/dev bloommcp, call `qc_clean` with `csv_content` set to a small local
      CSV's text, confirm the summary response and `input_sha256`, and confirm no
      `list_existing_analyses` entry appears for it afterward. **Not done in this session** — no
      live Claude Code ↔ dev-bloommcp connection available here; left for a human reviewer/the PR
      author to confirm before merge.

## 7. Post-PR review fixes, round 1 (two independent review passes on PR #608)

- [x] 7.1 Merge conflict: `origin/staging` picked up the signed-URL-download change (`OutputLink`/
      `output_links` on `QCCleanResult`) while this PR was open — merged and reconciled both sets
      of changes (kept `input_sha256` alongside `output_links`; both branches of `qc_clean` now
      set `output_links` appropriately: `stored.output_links` when persisted, `{}` inline).
- [x] 7.2 Mutual-exclusivity error message was silently discarded by the contract layer's generic
      `from_input_validation` mapping (verified empirically) — moved the check from a
      `model_validator` into `qc_clean`'s body (matching the existing B-4 pattern), so the
      specific message actually reaches the caller.
- [x] 7.3 **Blocking: wide-CSV CPU-cost DoS.** A column-count guard checked only after
      `pandas.read_csv` had already parsed the content — reproduced the reported case (~480,000
      columns, 4.69 MB, under the byte cap, ~7.7s CPU) and confirmed a post-parse-only check does
      not prevent it. Fixed with a pre-parse, `csv.reader`-based header-line column estimate that
      rejects before the expensive parse ever runs (verified: same repro now rejected in ~0.002s);
      the post-parse `df.shape[1]` check is kept only as an exact backstop. **This fix itself had
      a bypass — see §8.1.**
- [x] 7.4 Uncaught `UnicodeEncodeError` in the `.encode("utf-8")` calls (byte-size guard and
      `compute_input_sha256`) — both now wrapped, mapping to `invalid_input`.
- [x] 7.5 `test_zero_columns_is_rejected` didn't exercise the code path its name claimed (a real
      `pandas.read_csv(io.StringIO(...))` call cannot return a 0-column frame without first
      raising `EmptyDataError`) — fixed to mock the return value directly.
- [x] 7.6 Equivalence oracle strengthened to check every resolved-roles/shape field the result
      exposes (`replicate_column`, `excluded_columns`, `kept_trait_columns`,
      `cleaned_nan_cells_remaining`), not a partial subset. **Still overclaimed — see §8.3.**
- [x] 7.7 Added regression tests pinning "csv_content never appears in a log record" (success path
      and forced `internal_error` path) — previously disclosed as a risk but untested. **The
      success-path test was vacuous — see §8.2.**
- [x] 7.8 Cosmetic: `_INLINE_EXPERIMENT_LABEL` shortened to `"csv_content"` (reads cleanly through
      `!r`; the prior `"the supplied csv_content"` read awkwardly once quoted).
- [x] 7.9 OpenSpec: moved the mutual-exclusivity requirement to `## ADDED Requirements` (it is a
      wholly new requirement, not a modification of an existing one) and corrected its text to
      describe body-level enforcement instead of a model validator.
- [x] 7.10 Full suite re-verified green (1033 passed) after all fixes; `black`/`ruff` clean;
      `check-uv-locks.py` clean; server boots; `openspec validate --strict` passes.

## 8. Post-PR review fixes, round 2 (re-review of the round-1 fix commit)

- [x] 8.1 **Blocking: the round-1 DoS fix (§7.3) itself had a bypass via an embedded newline in a
      header cell — reproduced directly, ~5.5s CPU wasted before the post-parse backstop caught
      it.** `_estimate_header_columns`'s naive `csv_content.split("\n", 1)[0]` cuts a row short the
      moment any field contains a literal newline inside quotes (valid CSV): a header whose first
      cell is `"h0\nrest_of_h0"` made the estimate say "1 column" for a real ~480,000-column row,
      letting `pandas.read_csv` run anyway. Fixed by feeding `csv.reader` a bounded line iterator
      (`_bounded_lines`) instead of a pre-split string — `csv.reader` then handles a multi-line
      quoted field correctly the same way iterating a real file does (pulling more lines until the
      quote closes), while `_bounded_lines` caps total bytes scanned (256 KiB) so an unterminated
      quote can't force scanning the whole payload instead. Verified: the exact repro now rejects
      via the pre-parse guard again (not the post-parse backstop).
- [x] 8.2 **Important: `test_csv_content_never_appears_in_logs_on_success` was vacuous.**
      `run_input_validation` (`bloom_mcp/data_access/columns.py`) sets `logger.propagate = False`
      on `bloom_mcp.input_validation` for the call's duration, restoring it in `finally` — this
      makes `caplog` structurally blind to that logger regardless of content (verified
      empirically: even `caplog.at_level(level, logger=name)` captures nothing from a
      `propagate=False` logger, since `caplog`'s capture relies on propagation to root, which
      `propagate=False` specifically suppresses). The test passed for the trivial reason that
      nothing was captured, not because the marker was checked against real output. Fixed by
      attaching a handler directly to the relevant loggers (`_capture_all_logs`), bypassing
      `caplog` entirely — a directly-attached handler fires regardless of `propagate`.
- [x] 8.3 Equivalence-oracle docstring overclaim ("every resolved-roles/shape field") — added the
      two fields that were actually missing: `validation_warnings` and `input_nan_summary` (the
      fields most likely to expose a real inline-vs-file NaN/dtype divergence).
- [x] 8.4 `proposal.md` still described the mutual-exclusivity rule as "enforced by a Pydantic
      model validator" — stale after §7.2 moved it to a body-level check; `spec.md` had already
      been corrected but `proposal.md` hadn't, so the two contradicted each other. Fixed.
- [x] 8.5 `design.md`/`tasks.md` §4.4 still said `QCCleanResult` has no `output_links` field "as
      of this change, nothing to set here" — stale after the §7.1 staging-merge reconciliation
      that added it. Fixed, with a note that a future rollout tool's author should treat
      `output_links` as a real field to set, not skip.
- [x] 8.6 `_estimate_header_columns`'s `except StopIteration: return 0` branch, previously flagged
      as unreachable dead code (a 1-element list passed to `csv.reader` never raises
      `StopIteration`) — the §8.1 rewrite genuinely changed this: `_bounded_lines("")` yields zero
      lines, so `next(csv.reader(...))` on truly empty `csv_content` now does raise
      `StopIteration` for real. No longer dead; already covered functionally by the existing
      `test_empty_string_is_rejected` end-to-end test.
- [x] 8.7 tasks.md numbering: this file's own §7 items were numbered 8.1–8.10, colliding with the
      old §8 "Follow-ups" (also 8.1) — renumbered §7 to 7.1–7.10 and this round to §8, pushing
      Follow-ups to §9.

## 9. Follow-ups (out of this change's spec deltas)

- [ ] 9.1 File (or confirm filed) follow-up issues for the remaining consumer-tool rollout per
      #582: `pca_analysis`, `clustering`, `remove_outliers`, `descriptive_stats`,
      `cross_experiment_correlations`, `umap_analysis` — each imports
      `bloom_mcp.tools._inline_input` and gets its own thorough test pass, per the issue's
      "tested individually" rollout rule. **Not filed in this session** — left for the issue owner
      to triage after this first slice lands.
