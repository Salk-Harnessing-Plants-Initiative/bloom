## 1. RED — regression tests proving the swallow, per tool (fakes only, no live Supabase)

Each test uses `FakeResultStore.fail_next_commit(experiment, tool_class)` (raises
`CommitFailedError` from the next `commit()`, named `test_commit_failure_surfaces_as_tool_error`
in each file) or `fail_next_read(experiment, tool_class)` (raises `ManifestReadError` from the
next `create_run()`, named `test_manifest_read_failure_surfaces_as_tool_error`), both already
used by `test_store_parity.py` — no new fake behavior needed. Confirm each test FAILS against
today's code (asserts `code == "tool_error"` and the store's own message text; today's
undeclared exception maps to `"internal_error"` with only a `ref:` id, so both assertions
miss).

- [x] 1.1 `qc_inspect` — `bloommcp/tests/tools/test_qc_inspect_tool.py`: arm against
      `(_EXPERIMENT, "qc_inspect")`. A commit failure (`fail_next_commit`) surfaces as
      `BloomMCPError(code="tool_error")` whose `message` contains `"commit failed for
      qc_inspect"`; a create_run manifest-read failure (`fail_next_read`) surfaces as
      `tool_error` whose message contains "manifest read failure".
- [x] 1.2 `qc_clean` — same two cases in `bloommcp/tests/tools/test_qc_clean_tool.py`,
      arm against `(_EXPERIMENT, "qc")`. **Must drive the tool through its registered-
      `experiment=` path, not its `csv_content=` inline-upload path** (`qc_clean.py`'s
      `csv_content` branch never calls `store.create_run`/`commit` at all — only the
      `experiment=` path does) — use the same `_run(experiment=...)`-style helper the
      existing test file already uses for its non-`csv_content` cases.
- [x] 1.3 `clustering` — same two cases in `bloommcp/tests/tools/test_clustering_tool.py`,
      arm against `(_EXPERIMENT, "clustering")`.
- [x] 1.4 `pca_analysis` — same two cases in `bloommcp/tests/tools/test_pca_analysis_tool.py`,
      arm against `(_EXPERIMENT, "pca")` (confirm the exact `_TOOL_CLASS` constant in
      `pca_analysis.py` before writing the fixture — do not assume the module name).
- [x] 1.5 `remove_outliers` — same two cases in
      `bloommcp/tests/tools/test_remove_outliers_tool.py`, arm against
      `(_EXPERIMENT, "outliers")` — confirmed via `experiment_utils.py`'s
      `OUTLIERS_TOOL_CLASS = "outliers"` constant (NOT `"qc""`, despite `qc_clean.py`'s own
      docstring loosely suggesting otherwise in an unrelated comment — that docstring is
      stale post-#420, verify directly against `remove_outliers.py`'s actual
      `store.create_run(tool_class=...)` call instead of trusting any comment).
- [x] 1.6 `descriptive_stats` — same two cases in
      `bloommcp/tests/tools/test_descriptive_stats_tool.py`, arm against
      `(_EXPERIMENT, "stats")` (`descriptive_stats.py`'s `_TOOL_CLASS = "stats"`).
- [x] 1.7 `cross_experiment_correlations` — same two cases in
      `bloommcp/tests/tools/test_cross_experiment_correlations_tool.py`. **Corrected call
      site** (tasks.md previously misdescribed this as "the primary/first experiment's
      run" — wrong; found in review): the tool's only `ResultStore` interaction is a single
      `store.create_run(experiment=composite_experiment, tool_class=_TOOL_CLASS, ...)` /
      `store.commit(...)` pair (`cross_experiment_correlations.py:579-600`), where
      `composite_experiment = _composite_experiment_key(Path(experiment_1).stem,
      Path(experiment_2).stem)` (the length-prefixed encoding from that module's design.md
      D1 — NOT `experiment_1` or `experiment_2` directly) and `_TOOL_CLASS = "correlation"`
      (line 110). `experiment_1`/`experiment_2` are read via the **ExperimentReader** port
      (`_load_cleaned`), never `ResultStore` — `fail_next_read`/`fail_next_commit` cannot
      simulate a failure on either input experiment, only on the composite output key. Arm
      both fakes against `(_composite_experiment_key(...), "correlation")` — the existing
      test file already imports `_composite_experiment_key` and defines `_COMPOSITE_KEY =
      _composite_experiment_key("expA", "expB")` at module scope (line 57); reuse that
      constant rather than hand-deriving the key again.
- [x] 1.8 `umap_analysis` — same two cases in `bloommcp/tests/tools/test_umap_analysis_tool.py`,
      arm against `(_EXPERIMENT, "umap")` (confirm the exact `_TOOL_CLASS` constant first).
- [x] 1.9 Confirm every test above FAILS against today's code before starting section 2
      (`pytest -k "surfaces_as_tool_error"` across the 8 files, or run each new test
      individually).

## 2. GREEN — declare the reachable ResultStore errors, per tool (lands with section 1 in one commit)

Per design.md Decision 1: declare exactly `(ExperimentReadError, CommitFailedError,
ManifestReadError)` — not the full `ResultStoreError` base. `ManifestIncompatibleError` is
covered automatically (subclass of `ManifestReadError`).

- [x] 2.1 `qc_inspect.py` — add `from bloom_mcp.result_store import CommitFailedError,
      ManifestReadError`; change `errors=(ExperimentReadError,)` to
      `errors=(ExperimentReadError, CommitFailedError, ManifestReadError)` on the
      `@as_mcp_tool` decorator.
- [x] 2.2 `qc_clean.py` — same import + `errors=` edit.
- [x] 2.3 `clustering.py` — same import + `errors=` edit.
- [x] 2.4 `pca_analysis.py` — same import + `errors=` edit.
- [x] 2.5 `remove_outliers.py` — same import + `errors=` edit.
- [x] 2.6 `descriptive_stats.py` — same import + `errors=` edit.
- [x] 2.7 `cross_experiment_correlations.py` — same import + `errors=` edit.
- [x] 2.8 `umap_analysis.py` — same import + `errors=` edit.
- [x] 2.9 Run every section-1 test; confirm all now pass (GREEN).

## 3. Coverage gaps found in review (design.md Decision 4) — additional RED+GREEN pairs

- [x] 3.1 Add a contract-layer unit test (in `bloommcp/tests/contract/test_error_envelope.py`,
      alongside the existing declared/undeclared-exception tests) asserting
      `BloomMCPError.from_exception(ManifestIncompatibleError("simulated schema mismatch"),
      declared=(ExperimentReadError, CommitFailedError, ManifestReadError)).code ==
      "tool_error"` and that the message passes through — proves the `isinstance` subclass
      match mechanically, since `FakeResultStore.fail_next_read` can only simulate the
      generic `ManifestReadError`, never the schema-incompatible subtype (see that method's
      own docstring). This test does not need any tool or fake — call `from_exception`
      directly.
- [x] 3.2 Add one tool-boundary test (pick `qc_inspect`, the mechanism is identical across
      all 8) proving a `RunStateError` — e.g. by calling `store.commit(run, outputs)` twice
      on the same handle via a monkeypatched `_ports.store()` — still maps to
      `BloomMCPError(code="internal_error")`, not `tool_error`, after this change. This is
      the regression test that would catch an accidental `errors=(...,  ResultStoreError)`
      (the full base) instead of the intended narrow tuple.
- [x] 3.3 Add one test (any single tool) proving the safety property this whole change rests
      on: construct a `CommitFailedError`/`ManifestReadError` whose underlying triggering
      condition would, in a real deploy, embed something sensitive (simulate via
      monkeypatching `FakeResultStore.commit`/`create_run` to raise
      `CommitFailedError("commit failed for X/Y (transient — retry)")` — i.e. today's real,
      already-safe template) and assert the resulting `tool_error` message does NOT contain
      a planted path/host-shaped string (e.g. `/var/secrets` or an internal hostname) —
      mirroring `test_delegate_raise_is_structured_without_leaking`'s existing pattern but
      for the newly-declared types specifically, not just undeclared ones.
- [x] 3.4 Tighten the two existing `test_delegate_raise_is_structured_without_leaking`-style
      tests (`test_qc_inspect_tool.py`, `test_qc_clean_tool.py`) to also assert
      `exc.value.code == "internal_error"` (today they only assert the message excludes
      secrets, never pinning the error `code` itself) — closes the "no false-positive
      widening" gap found in review: without this, a future typo that widens `errors=`
      beyond the intended tuple would not be caught by either test.
- [x] 3.5 Tighten `ResultStoreError`'s docstring in `bloom_mcp/result_store/ports.py` to
      state the same explicit no-leak obligation `ExperimentReadError`'s docstring
      (`bloom_mcp/data_access/ports.py`) already states ("Adapters MUST NOT leak a
      filesystem path, bucket name, or storage traceback in the message") — documentation
      only, no behavior change; backs the claim `proposal.md`'s Why section makes that the
      write-side mirrors the read-side's contract.

## 4. Validate

- [x] 4.1 Run each of the 8 tools' full test files — no regression (existing
      `test_delegate_raise_is_structured_without_leaking`-style tests, and any other test
      that raises an *undeclared* exception through these tools, must still map to
      `internal_error` unchanged: `CommitFailedError`/`ManifestReadError` becoming declared
      must not accidentally widen what counts as "declared" beyond those two types).
- [x] 4.2 Run bloommcp's full test suite (`uv run pytest` from `bloommcp/`) — no regression
      elsewhere; specifically confirm `bloommcp/tests/test_local_mode.py`'s existing
      `BLOOM_STORAGE_URL`-unset local-mode tests (the ones proving #642's fix, around line
      730-774) still pass unmodified — this proposal does not touch that path, but it's the
      test that backs design.md's corrected narrative, worth confirming explicitly rather
      than assuming.
- [x] 4.3 `black`/`ruff` on the 8 changed tool files + changed test files +
      `result_store/ports.py`.
- [x] 4.4 `openspec validate fix-bloommcp-resultstore-error-swallowing --strict` passes.
