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

- [ ] 1.1 Add `bloommcp/tests/tools/test_inline_input.py`.
- [ ] 1.2 Valid CSV → `ExperimentFrame` with `source == "inline"` and roles matching a direct
      `resolve_columns` call on the same parsed `DataFrame`. Confirm RED (module doesn't exist).
- [ ] 1.3 Oversized content (construct a string whose UTF-8 encoding exceeds
      `MAX_INLINE_CSV_BYTES` by a small margin) → `BloomMCPError(invalid_input)` naming the byte
      count and limit; assert `pandas.read_csv` is never reached (patch/spy it, assert zero
      calls). Content exactly at the limit is accepted. Confirm RED.
- [ ] 1.4 **Byte-vs-character size guard**: construct CSV content using multi-byte UTF-8
      characters (e.g. CJK or accented genotype names) whose character count is well under
      `MAX_INLINE_CSV_BYTES` but whose UTF-8 *byte* count exceeds it; assert rejection — pins
      that the guard measures encoded bytes, not `len(csv_content)`. Confirm RED.
- [ ] 1.5 Malformed CSV (inconsistent field counts) → `BloomMCPError(invalid_input)`, not a raw
      `pandas.errors.ParserError`. Confirm RED.
- [ ] 1.6 Empty string and header-only (zero data rows) → `BloomMCPError(invalid_input)` stating
      no data rows were found. Confirm RED.
- [ ] 1.7 Content that parses to zero columns (e.g. a string of blank lines) →
      `BloomMCPError(invalid_input)` stating no columns were found. Confirm RED.
- [ ] 1.8 Content that raises `UnicodeDecodeError` during parsing → `BloomMCPError(invalid_input)`
      describing the decode failure, not a raw `UnicodeDecodeError`. Confirm RED.
- [ ] 1.9 **Leading UTF-8 BOM**: content whose first character is `﻿` immediately followed
      by an otherwise well-formed header → the returned frame's first column name has the BOM
      stripped (e.g. `"Barcode"`, not `"﻿Barcode"`). Confirm RED.
- [ ] 1.10 CRLF-joined rows parse identically to the LF equivalent (same shape, same values) —
      guards `pandas.read_csv(io.StringIO(...))` behaving the same as the file-based path's
      `pandas.read_csv(path)` for CRLF content. Confirm RED.
- [ ] 1.11 Non-ASCII content (accented / CJK genotype values) survives parsing intact — assert
      the parsed `DataFrame` cell values equal the original strings exactly (no mangling).
      Confirm RED.
- [ ] 1.12 Duplicate column header and whitespace-only header: pin pandas' actual behavior
      (auto-suffix vs. rejection) rather than leaving it unspecified — assert the helper does not
      crash and produces the same column-naming behavior a direct `pandas.read_csv` call on the
      same text would (no special-casing added; documents inherited behavior, consistent with
      the un-special-cased file-based path). Confirm RED.
- [ ] 1.13 `compute_input_sha256` matches an independently computed
      `hashlib.sha256(s.encode("utf-8")).hexdigest()` for several strings including an empty
      string and multi-byte UTF-8 content. Confirm RED.
- [ ] 1.14 **Touches no persistence port**: patch `bloom_mcp.tools._ports.store` (or the module's
      resolved store) and assert `create_run`/`commit` are never called during
      `parse_inline_csv_frame`. Confirm RED (trivially, since nothing exists yet) — this pins the
      guarantee structurally so a future edit to the helper cannot regress it silently.

## 2. GREEN — implement the shared helper

- [ ] 2.1 Add `bloommcp/src/bloom_mcp/tools/_inline_input.py`: `MAX_INLINE_CSV_BYTES = 5 *
      1024 * 1024`, `parse_inline_csv_frame(csv_content: str) -> ExperimentFrame`, and
      `compute_input_sha256(csv_content: str) -> str`.
- [ ] 2.2 `parse_inline_csv_frame`: strip at most one leading `"﻿"` from `csv_content`.
      Check `len(csv_content.encode("utf-8"))` against `MAX_INLINE_CSV_BYTES` **before**
      parsing; raise `BloomMCPError(code="invalid_input")` naming the byte count and the limit if
      over. Otherwise `pandas.read_csv(io.StringIO(csv_content))`, catching
      `pandas.errors.ParserError` / `pandas.errors.EmptyDataError` / `UnicodeDecodeError` and
      re-raising as `BloomMCPError(code="invalid_input")` with a caller-safe message (no raw
      pandas traceback). Reject zero-row and zero-column results the same way.
- [ ] 2.3 Resolve roles/traits via `resolve_columns(df)` (`bloom_mcp.data_access.columns`, no
      overrides) and build `ExperimentFrame(df=df, trait_cols=resolved.trait_cols,
      metadata_cols=resolved.metadata_cols, genotype_col=resolved.genotype,
      replicate_col=resolved.replicate, sample_id_col=resolved.sample_id, source="inline")`.
- [ ] 2.4 `compute_input_sha256`: `hashlib.sha256(csv_content.encode("utf-8")).hexdigest()` (over
      the original string, i.e. before any BOM-stripping — the hash reflects exactly what the
      caller sent).
- [ ] 2.5 Run §1's suite; debug to GREEN without weakening any guard (size, BOM, malformed-input
      mapping, no-persistence).

## 3. RED — qc_clean wiring (write first; today's model accepts only `experiment`)

- [ ] 3.1 In `bloommcp/tests/tools/test_qc_clean_tool.py`, add mutual-exclusivity tests: both
      `experiment` and `csv_content` set → `BloomMCPError(invalid_input)` before the tool body
      runs (assert the injected reader's `load_experiment` is never called); neither set → same.
      Confirm RED (today's model accepts the `experiment`-only shape with no validator).
- [ ] 3.2 **Equivalence oracle** (north star): load `turface_19_raw_data.csv`'s text via
      `Path.read_text(encoding="utf-8")`, call `qc_clean(QCCleanParams(csv_content=text,
      max_nans_per_trait=_MNT))`, and assert `n_samples_out`, `n_traits_out`, `removed_traits`,
      `genotype_column`, `sample_id_column` all equal the existing file-based oracle's result for
      the same thresholds. Confirm RED.
- [ ] 3.3 Never-persisted guarantee: spy/mock the injected `ResultStore` (e.g.
      `unittest.mock.create_autospec` or a `FakeResultStore` subclass that fails the test if
      `create_run`/`commit` is called) and assert it is never invoked on the `csv_content` path.
      Confirm RED.
- [ ] 3.4 Response shape: `csv_content` call → `result.experiment is None`, `result.source ==
      "inline"`, `result.input_sha256 == compute_input_sha256(text)`, `result.run_ref is None`,
      `result.version_dir is None`, `result.manifest_path is None`, `result.outputs == {}`.
      Confirm RED.
- [ ] 3.5 Reader-bypass: spy on the injected `ExperimentReader` and assert `load_experiment` is
      never called on the `csv_content` path. Confirm RED.
- [ ] 3.6 **`next_step` suppression**: run an inline call whose thresholds drop at least one
      sample (the condition that populates `next_step` on the `experiment` path) and assert
      `result.next_step is None` — no `qc_inspect` nudge, no `None` interpolated into a message.
      Confirm RED.
- [ ] 3.7 **Override interaction**: `csv_content` combined with `sample_id_column` /
      `genotype_column` / `exclude_columns` / `trait_columns` overrides produces the same
      override-driven role resolution and cleaning as the equivalent `experiment` call (mirror
      the existing `experiment`-path override tests already in this file, substituting
      `csv_content=` for `experiment=`). Confirm RED.
- [ ] 3.8 **Inline-branch error-message wording**: an unknown `sample_id_column` override, or a
      roleless inline CSV, on the `csv_content` path raises an error whose message reads e.g.
      "the supplied csv_content" rather than interpolating the literal string `"None"` where
      `params.experiment!r}` is normally used. Confirm RED against today's unconditional
      `{params.experiment!r}` interpolation.
- [ ] 3.9 Existing `experiment`-only tests in `test_qc_clean_tool.py` continue to pass unmodified
      (confirms the required→optional `Field` change is behavior-preserving for the existing call
      shape). Run them now and record they are green before touching the model.

## 4. GREEN — implement qc_clean wiring

- [ ] 4.1 `QCCleanParams`: change `experiment` to `Optional[str] = Field(default=None, ...)`;
      add `csv_content: Optional[str] = Field(default=None, description="Raw CSV text for a
      one-off analysis with no persistence. Mutually exclusive with experiment; exactly one is
      required. No run is persisted — no run_ref, no lineage into a later based_on_version, and
      no list_existing_analyses entry.")` (this description is the single canonical statement of
      the "no history" caveat — docs point here rather than restating it); add a
      `model_validator(mode="after")` raising `ValueError` unless exactly one of the two is set.
- [ ] 4.2 `QCCleanResult`: change `experiment: str` to `Optional[str]`; change `run_ref: str`,
      `version_dir: str`, `manifest_path: str` to `Optional[str] = None`; add
      `input_sha256: Optional[str] = None`.
- [ ] 4.3 In `qc_clean(params, *, provenance)`: branch on `params.csv_content is not None` —
      inline branch calls `parse_inline_csv_frame(params.csv_content)` for `frame` and
      `compute_input_sha256(params.csv_content)` for the hash, and skips
      `_ports.reader().load_experiment(...)` entirely; experiment branch is unchanged
      (`reader.load_experiment(params.experiment, version="raw")`). Every downstream step
      (role-override validation, `resolve_columns`, trait validation, input-contract validation,
      `clean_traits_for_analysis`, no-NaN guard) runs identically on `frame.df` regardless of
      branch — **no duplicated cleaning logic**.
- [ ] 4.4 At the persistence step: only call `store.create_run(...)` / `.commit(...)` when
      `params.csv_content is None`; on the inline branch, skip straight to constructing
      `QCCleanResult` with `run_ref=None`, `version_dir=None`, `manifest_path=None`,
      `outputs={}`, `output_links={}`, `experiment=None`, `source="inline"`,
      `input_sha256=<computed hash>`, `next_step=None` (unconditionally — never compute the
      `qc_inspect` nudge on this branch, regardless of `n_samples_dropped`).
- [ ] 4.5 Adjust every reference to `params.experiment` used in error messages on the inline
      branch (e.g. the role-overrides unknown-column message, the genotype-blank message, the
      missing-role message) to read sensibly with no experiment name — use a fixed placeholder
      phrase (e.g. `"the supplied csv_content"`) rather than `None` interpolated into an
      f-string.
- [ ] 4.6 Run §3's suite; debug to GREEN without weakening the equivalence oracle, the
      never-persisted guarantee, or the `next_step` suppression.

## 5. Docs

- [ ] 5.1 Update `qc_clean.py`'s module docstring (currently states unconditionally that the tool
      "reads the raw frame via the ExperimentReader port" and "persists a versioned run") and the
      `qc_clean` function's own docstring (currently `"""Clean ``experiment`` via analyze's
      ``clean_traits_for_analysis`` and persist it."""`, which is literally what `tools/list`
      surfaces to Claude Code) to describe both paths — this is more load-bearing than any
      markdown doc since it is what the calling agent reads.
- [ ] 5.2 Add a short section to `bloommcp/docs/connecting-claude-code.md` stating plainly that
      an inline `csv_content` call never touches Bloom's shared Storage/DB — the fact this
      particular doc's own access-scope thesis makes most relevant to state — plus a minimal
      `qc_clean(csv_content="...")` example, and a pointer to `QCCleanParams.csv_content`'s field
      description for the full "no history" caveat rather than restating that list a second time
      in prose.

## 6. Refactor & verify

- [ ] 6.1 Refactor for clarity; confirm the `experiment`-only path's behavior, tests, and
      registered tool schema for that call shape are unchanged except `experiment` moving from
      required to optional in the schema.
- [ ] 6.2 `/pre-merge`: lint (`black --check` + `ruff check`) + the exact CI suite command
      `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not
      live_smoke" -v --tb=short` (matches `pr-checks.yml`'s `python-audit` job exactly — not a
      bare `pytest tests/`, which would also attempt integration/live-smoke tests that need a
      live stack) + `uv run --frozen` import + `python scripts/check-uv-locks.py` (no drift — no
      new dependency) + `openspec validate add-bloommcp-inline-csv-input --strict`.
- [ ] 6.3 Validate on Claude Code (the actual target client for this feature, per the issue):
      connect to a local/dev bloommcp, call `qc_clean` with `csv_content` set to a small local
      CSV's text, confirm the summary response and `input_sha256`, and confirm no
      `list_existing_analyses` entry appears for it afterward.

## 7. Follow-ups (out of this change's spec deltas)

- [ ] 7.1 File (or confirm filed) follow-up issues for the remaining consumer-tool rollout per
      #582: `pca_analysis`, `clustering`, `remove_outliers`, `descriptive_stats`,
      `cross_experiment_correlations`, `umap_analysis` — each imports
      `bloom_mcp.tools._inline_input` and gets its own thorough test pass, per the issue's
      "tested individually" rollout rule.
