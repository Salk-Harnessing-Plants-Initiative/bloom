## 1. Foundation: Protocol, error class, adapter rejection

- [ ] 1.1 Write failing tests in `test_ports.py`/`test_local_reader.py`/`test_fake_reader.py`:
      `LocalReader.load_experiment(name, source_id=7)` and `FakeReader.load_experiment(name, run_id="r1")`
      each currently raise a bare `TypeError` (unexpected keyword argument) — pin this as the
      "before" behavior with a test that currently passes against `pytest.raises(TypeError)`, then
      flip it to assert `SourcePinningUnsupportedError` once 1.2/1.3 land.
- [ ] 1.2 Add `SourcePinningUnsupportedError(ExperimentReadError)` to `ports.py`; add
      `source_id: Optional[int] = None, run_id: Optional[str] = None` to `ExperimentReader.load_experiment`'s
      Protocol declaration (signature + docstring only — no behavior on the Protocol itself).
- [ ] 1.3 Add the same two kwargs to `LocalReader.load_experiment` and `FakeReader.load_experiment`;
      each raises `SourcePinningUnsupportedError` immediately when either is non-`None`. Run 1.1's
      tests — both now pass against the new error type.
- [ ] 1.4 Write a test asserting `SupabaseReader.load_experiment(name, source_id=7, run_id="r1")`
      (both given) raises `AmbiguousSourceSelectionError` through the `ExperimentReader` Protocol
      type (not just the concrete class) — this already works today (PR #557); the test is new
      coverage confirming the Protocol-typed call path, not new logic.
- [ ] 1.5 Add multi-source seeding support to `FakeReader`'s test double (e.g. an
      `add_source(name, SourceInfo)` helper) and make `FakeReader` implement `SourceSelectable`
      (`list_sources`/`resolve_source`) purely in-memory. Write tests for single-source,
      multi-source, and zero-source seeding. This is a prerequisite for every tool-layer
      multi-source test below — do this before 3.x/4.x.

## 2. Discovery tool: core_list_experiment_sources

- [ ] 2.1 Write a failing test: `core_list_experiment_sources` does not exist yet / is not
      registered on the `core` FastMCP section.
- [ ] 2.2 Implement `sections/core/list_experiment_sources.py`: isinstance-gate on
      `SourceSelectable`; call `reader.list_sources(experiment)`; format as text matching
      `list_available_experiments`'s style (Decision 3 in design.md). Register it in
      `sections/core/__init__.py` alongside the other core tools.
- [ ] 2.3 Write tests: multi-source experiment lists each source's fields; single-/zero-source
      experiment gets the distinct "no meaningful choice" message; a `LocalReader`/`FakeReader`
      lacking `SourceSelectable` gets the "not applicable for this backend" message, not an
      exception. (Uses 1.5's `FakeReader` seeding.)
- [ ] 2.4 Add `core_list_experiment_sources` to any static tool-name registries that enumerate the
      live tool set (check `langchain/helpers/foundational_tools.py`'s
      `test_tool_name_lists_match_live_registry` invariant test — decide deliberately whether this
      tool belongs in `ALWAYS_INCLUDE_MCP_TOOLS`; default to **not** always-included, since it is
      an occasional discovery aid, not a foundational read path).

## 3. qc_clean: source pin + advisory note

- [ ] 3.1 Write a failing test: `QCCleanParams(experiment=..., source_id=7)` is rejected today
      (unknown field) — pins the "before" state.
- [ ] 3.2 Add `source_id`/`run_id` fields to `QCCleanParams`; thread into the existing
      `reader.load_experiment(params.experiment, version="raw")` call at qc_clean.py:297. Test 3.1
      now passes as a valid call.
- [ ] 3.3 Write a test: both `source_id` and `run_id` given raises a `BloomMCPError` derived from
      `AmbiguousSourceSelectionError` (through the existing `errors=(ExperimentReadError,)` mapping
      — no new mapping code expected to be needed; the test proves it).
- [ ] 3.4 Add `source_note: Optional[str] = None` to `QCCleanResult`. Write tests (using 1.5's
      `FakeReader` seeding) for: multi-source + no pin -> note names the resolved source and
      mentions `core_list_experiment_sources`; multi-source + explicit pin -> note is `None`;
      single-/zero-source -> note is `None`; `csv_content` (inline) path -> note is `None`
      regardless. Implement the note-population logic to make all four pass.
- [ ] 3.5 Write a regression test: `qc_clean` invoked with neither `source_id` nor `run_id`, on a
      single-source experiment, produces byte-identical `QCCleanResult` fields (other than the new
      `source_note`, which must be `None`) to a pre-change golden fixture — proves the
      default-preserving guarantee, not just "it still works."

## 4. qc_inspect: source pin

- [ ] 4.1 Write a failing test: `QCInspectParams(experiment=..., run_id="r1")` is rejected today.
- [ ] 4.2 Add `source_id`/`run_id` fields to `QCInspectParams`; thread into the raw-tier
      `load_experiment` call at qc_inspect.py:433.
- [ ] 4.3 Write tests mirroring 3.3 (ambiguous pin -> `BloomMCPError`) and an omit-both regression
      test mirroring 3.5.

## 5. load_experiment_data: source pin

- [ ] 5.1 Write a failing test: `_ports.load_frame(filename, source_id=7)` is rejected today
      (`load_frame` takes only `filename`).
- [ ] 5.2 Add `source_id`/`run_id` kwargs to `_ports.load_frame`, threaded into
      `_reader.load_experiment(filename, source_id=source_id, run_id=run_id)`. Add the same two
      plain kwargs to the `load_experiment_data` tool function signature.
- [ ] 5.3 Write tests: an explicit pin changes which source's summary is returned; both given
      returns the ambiguous-selection error message as the function's string result (existing
      `except ExperimentReadError` path — no new catch needed, test proves it); omitting both
      reproduces today's summary unchanged.

## 6. The 6 require_clean tools: version selector

- [ ] 6.1 For each of `clustering`, `descriptive_stats`, `pca_analysis`, `umap_analysis`: write a
      failing/spy test asserting today's exact `load_experiment` call args
      (`load_experiment(params.experiment, require_clean=True)`, no `version` kwarg), then add the
      optional `version` field and re-run — the omitted-field case must still match the spy
      assertion exactly. Add a second test with an explicit version confirming it is passed
      through. (One task per tool; can run in parallel — no shared state.)
  - [ ] 6.1.a clustering.py
  - [ ] 6.1.b descriptive_stats.py
  - [ ] 6.1.c pca_analysis.py
  - [ ] 6.1.d umap_analysis.py
- [ ] 6.2 `remove_outliers.py`: write a spy test asserting today's call is
      `load_experiment(params.experiment, require_clean=True, version="latest_qc")`. Add the
      optional `version` field with `version=params.version if params.version is not None else "latest_qc"`.
      Re-run the spy test (omitted case still resolves `"latest_qc"`), then add a second test
      confirming an explicit override is honored.
- [ ] 6.3 `cross_experiment_correlations.py`: write a spy test on `_load_cleaned` asserting today's
      two calls (no `version` kwarg on either). Add `version_1`/`version_2` fields, each threaded
      only into its own experiment's `_load_cleaned(..., version=...)` call. Re-run the spy test
      (both omitted -> unchanged), then add tests for: only `version_1` given (only experiment 1's
      call changes), only `version_2` given (only experiment 2's call changes).

## 7. Spec + validation

- [ ] 7.1 Run `openspec validate add-bloommcp-source-version-selection --strict` and resolve every
      issue.
- [ ] 7.2 Confirm every scenario in the four spec delta files has a corresponding test written
      above; add any missing test before marking this task done.

## 8. Pre-merge

- [ ] 8.1 Run the repo's lint/format checks (ruff, black) on all touched Python files.
- [ ] 8.2 Run the full `bloommcp` test suite; confirm no pre-existing test's assertions changed
      (only new tests added, per the default-preserving guarantee).
- [ ] 8.3 Run `/pre-merge` end-to-end before opening the PR.
