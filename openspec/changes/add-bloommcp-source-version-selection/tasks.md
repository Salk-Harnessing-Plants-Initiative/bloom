## 1. Foundation: Protocol, error class, adapter rejection

- [x] 1.1 Write failing tests in a **new** `bloommcp/tests/data_access/test_ports.py` (this file
      does not exist yet — do not confuse it with the unrelated
      `bloommcp/tests/result_store/test_ports.py`). (As implemented, this coverage landed entirely
      in the new `test_ports.py` rather than as separate additions to `test_local_reader.py`/
      `test_fake_reader.py` — a corrected plan, not a gap: those two adapters' rejection behavior is
      exercised via `test_ports.py`'s parametrized `LocalReader`/`FakeReader` cases instead.) Assert
      `"source_id" not in ExperimentReader.load_experiment`'s current
      signature (via `inspect.signature`), and that `LocalReader.load_experiment(name, source_id=7)`
      / `FakeReader.load_experiment(name, run_id="r1")` each currently raise a bare `TypeError`
      (unexpected keyword argument) — pin this as the "before" behavior, then flip the assertion to
      `SourcePinningUnsupportedError` once 1.2/1.3 land. Also assert both kwargs non-`None`
      simultaneously raises `SourcePinningUnsupportedError` (not `AmbiguousSourceSelectionError`) on
      these two adapters, since they never reach the ambiguity check `SupabaseReader` has.
- [x] 1.2 Add `SourcePinningUnsupportedError(ExperimentReadError)` to `ports.py`; add
      `source_id: Optional[int] = None, run_id: Optional[str] = None` to `ExperimentReader.load_experiment`'s
      Protocol declaration (signature + docstring only — no behavior on the Protocol itself).
      Re-export the new error from `data_access/__init__.py` alongside the other `ExperimentReadError`
      subclasses.
- [x] 1.3 Add the same two kwargs to `LocalReader.load_experiment` and `FakeReader.load_experiment`;
      each raises `SourcePinningUnsupportedError` immediately when either is non-`None`. Run 1.1's
      tests — all now pass against the new error type.
- [x] 1.4 Write a test asserting `SupabaseReader.load_experiment(name, source_id=7, run_id="r1")`
      (both given) raises `AmbiguousSourceSelectionError` through the `ExperimentReader` Protocol
      type (not just the concrete class) — this already works today (PR #557); the test is new
      coverage confirming the Protocol-typed call path, not new logic. (First-round PR review
      caught that this specific test was missing — `test_ports.py` only had a
      `FakeReader`/`SourcePinningUnsupportedError` case, a different error for a different adapter
      class. Added as
      `test_both_given_is_ambiguous_via_protocol_typed_handle_on_a_source_capable_adapter` in
      `test_ports.py`, using the shared `make_multi_source_fake_reader` double typed as
      `ExperimentReader`.) Also write a test for an
      explicit `source_id` that matches no known source, asserting `SourcePinNotFoundError` (already
      implemented in `SupabaseReader`; this is new spec/test coverage for a gap Tier 2 shipped
      without archiving).
- [x] 1.5 Extend the existing monkeypatched-`SupabaseReader`-boundary fixture helpers in
      `test_supabase_reader.py` (the same `fake_supabase_db`/`fake_supabase_storage` pattern already
      used for Tier-2 multi-source tests) with a small reusable seeding helper for "N sources on one
      experiment," for use by the tool-layer tests in sections 2-3 below. **Do not** make `FakeReader`
      implement `SourceSelectable` — `test_fake_reader_is_not_source_selectable` locks in that it
      must not, and this change does not touch that (design.md Decision 5).

## 2. Discovery tool: core_list_experiment_sources

- [x] 2.1 Write a failing test: `core_list_experiment_sources` does not exist yet / is not
      registered on the `core` FastMCP section.
- [x] 2.2 Implement `sections/core/list_experiment_sources.py`: isinstance-gate on
      `SourceSelectable`; call `reader.list_sources(experiment)`; format as text matching
      `list_available_experiments`'s style (Decision 3 in design.md). Register it in
      `sections/core/__init__.py` alongside the other core tools. (First-round PR review caught
      that this checkbox was marked done while the registration half was never actually written —
      the module existed and was unit-tested directly, but `sections/core/__init__.py` never
      imported or registered it, so the tool did not exist on the live MCP server. Fixed: it is now
      imported and passed to `register(...)` there.)
- [x] 2.3 Write tests: a multi-source experiment (seeded via 1.5's monkeypatched-`SupabaseReader`
      boundary) lists each source's fields; a single-/zero-source experiment gets the distinct "no
      meaningful choice" message; an unknown experiment name gets a not-found message, not a crash;
      a plain `FakeReader`/`LocalReader` (neither implements `SourceSelectable`) gets the "not
      applicable for this backend" message, not an exception.
- [x] 2.4 Decide (judgment call, not test-driven — `test_tool_name_lists_match_live_registry` in
      `test_devendor_invariants.py` only checks names already in `ALWAYS_INCLUDE_MCP_TOOLS` resolve
      to live tools, so it cannot confirm or refute this either way): default to **not** adding
      `core_list_experiment_sources` to `ALWAYS_INCLUDE_MCP_TOOLS` (`langchain/helpers/foundational_tools.py`)
      — it is an occasional discovery aid, not a foundational read path (design.md Decision 8).
- [x] 2.5 Update `bloommcp/tests/test_devendor_invariants.py::test_expected_tool_surface`'s
      hardcoded `core_*` tool enumeration to include `core_list_experiment_sources`, so the
      live-tool-surface guard stays meaningful instead of silently going stale. (First-round PR
      review caught that this checkbox was also marked done without being written — `expected` in
      `test_expected_tool_surface` was missing `"core_list_experiment_sources"`, so the one test
      built to catch exactly this class of registration drift stayed blind to the 2.2 gap either
      way. Fixed alongside 2.2.)

## 3. qc_clean: source pin + advisory note + provenance

- [x] 3.1 Write a failing test: `"source_id" not in QCCleanParams.model_fields` today (checking the
      field set directly, not assuming a raised validation error — `QCCleanParams` does not set
      `extra="forbid"`, so an unrecognized kwarg may be silently ignored rather than rejected).
- [x] 3.2 Add `source_id`/`run_id` fields to `QCCleanParams`; thread into the existing
      `reader.load_experiment(params.experiment, version="raw")` call at qc_clean.py:297. Write a
      test (using 1.5's multi-source fixture) proving an explicit pin actually changes which
      source's data is cleaned (assert on `frame.resolved_source.source_id` or an equivalent
      observable difference in the result) — not just that the call is schema-valid.
- [x] 3.3 Write a test: both `source_id` and `run_id` given raises a `BloomMCPError` derived from
      `AmbiguousSourceSelectionError` (through the existing `errors=(ExperimentReadError,)` mapping
      — no new mapping code expected to be needed; the test proves it). Add a second test for an
      explicit pin that matches no known source (`SourcePinNotFoundError` -> `BloomMCPError`).
- [x] 3.4 Add `source_note: Optional[str] = None` to `QCCleanResult`. Write tests (using 1.5's
      monkeypatched-`SupabaseReader` multi-source fixture) for: multi-source + no pin -> note names
      the resolved source and mentions `core_list_experiment_sources`; multi-source + explicit pin
      -> note is `None`; single-/zero-source -> note is `None`; `csv_content` (inline) path -> note
      is `None` regardless. Implement the note-population logic to make all four pass.
- [x] 3.5 Write a regression test: `qc_clean` invoked with neither `source_id` nor `run_id`, on a
      single-source experiment, produces byte-identical `QCCleanResult` fields (other than the new
      `source_note`, which must be `None`) to a pre-change golden fixture — proves the
      default-preserving guarantee, not just "it still works."
- [x] 3.6 Write a regression test locking in design.md Decision 7: after `qc_clean` commits with an
      explicit `source_id` pin, the persisted `StoredRun`/manifest's recorded source metadata
      (`source_id`/`source_name`, via `store.create_run(source=frame.resolved_source)`) equals the
      pin given — proves the pin is traceable from the committed run, not just used-and-forgotten.

## 4. qc_inspect: source pin

- [x] 4.1 Write a failing test: `"run_id" not in QCInspectParams.model_fields` today.
- [x] 4.2 Add `source_id`/`run_id` fields to `QCInspectParams`; thread into the raw-tier
      `load_experiment` call at qc_inspect.py:433. Write a test (mirroring 3.2) proving an explicit
      pin actually changes which source's data is inspected.
- [x] 4.3 Write tests mirroring 3.3 (ambiguous pin and pin-not-found -> `BloomMCPError`) and an
      omit-both regression test mirroring 3.5.

## 5. load_experiment_data: source pin (forces raw tier)

- [x] 5.1 Write a failing test: `_ports.load_frame(filename, source_id=7)` is rejected today
      (`load_frame` takes only `filename`).
- [x] 5.2 Add `source_id`/`run_id` kwargs to `_ports.load_frame`. **When either is non-`None`,
      force `version="raw"`** on the inner `_reader.load_experiment(...)` call (design.md Decision
      6) — a pin cannot apply to a cleaned read, and this tool has no other way to select the raw
      tier. Add the same two plain kwargs to the `load_experiment_data` tool function signature.
- [x] 5.3 Write tests: an explicit pin changes which source's summary is returned, **including for
      an experiment that already has a cleaned version** (proving the raw-tier forcing actually
      avoids the `AmbiguousSourceSelectionError` collision Decision 6 exists to prevent); both given
      returns the ambiguous-selection error message as the function's string result (existing
      `except ExperimentReadError` path — no new catch needed, test proves it); omitting both
      reproduces today's summary unchanged.

## 6. require_clean tools: version selector

- [x] 6.1 For each of `clustering`, `descriptive_stats`, `umap_analysis` (the 3 tools with no
      archived baseline spec — see 6.4 for `pca_analysis`, handled separately against its existing
      spec): write a failing/spy test asserting today's exact `load_experiment` call args
      (`load_experiment(params.experiment, require_clean=True)`, no `version` kwarg), then add the
      optional `version` field and re-run — the omitted-field case must still match the spy
      assertion exactly. Add a second test with an explicit version confirming it is passed
      through. (One task per tool; can run in parallel — no shared state.)
  - [x] 6.1.a clustering.py
  - [x] 6.1.b descriptive_stats.py
  - [x] 6.1.c umap_analysis.py
- [x] 6.2 `remove_outliers.py`: write a spy test asserting today's call is
      `load_experiment(params.experiment, require_clean=True, version="latest_qc")`. Add the
      optional `version` field with `version=params.version if params.version is not None else "latest_qc"`.
      Re-run the spy test (omitted case still resolves `"latest_qc"`), then add a second test
      confirming an explicit override is honored.
- [x] 6.3 `cross_experiment_correlations.py`: write a spy test on `reader.load_experiment` itself
      (**not** a wholesale mock of the `_load_cleaned` helper — `_load_cleaned` currently takes no
      `version` param at all, so mocking it out entirely cannot catch a bug where its new `version`
      param is accepted but never forwarded to the inner `load_experiment` call) asserting today's
      two calls (no `version` kwarg on either). Add `version_1`/`version_2` fields to the params
      model and a `version` parameter to `_load_cleaned`, each threaded only into its own
      experiment's `_load_cleaned(..., version=...)` -> `load_experiment(..., version=...)` call.
      Re-run the spy test (both omitted -> unchanged), then add tests for: only `version_1` given
      (only experiment 1's call changes), only `version_2` given (only experiment 2's call
      changes).
- [x] 6.4 `pca_analysis.py`: same shape as 6.1 (spy test on `load_experiment`, omit-preserves,
      explicit-honored), but this is a **MODIFIED** delta against the existing archived
      `bloommcp-pca-analysis-tool` spec, not the new `bloommcp-clean-version-selection` capability —
      keep its test file/PR description language consistent with "modifying an existing tool,"
      not "adding a new capability."

## 7. Spec + validation

- [x] 7.1 Run `openspec validate add-bloommcp-source-version-selection --strict` and resolve every
      issue. (This checkbox was marked done while strict validation was actually failing: the
      `bloommcp-experiment-read/spec.md` MODIFIED block had dropped a pre-existing base-spec
      scenario, "A resolvable-but-unreadable committed version is a caller-safe error, not a leaked
      exception" — a MODIFIED requirement replaces the whole block, so archiving would have
      silently lost that scenario. Not caught by the 4-pass PR review since none of its lenses run
      `openspec validate`; found and fixed during the review-response pass — see 9.10.)
- [x] 7.2 Confirm every scenario in the five spec delta files (`bloommcp-experiment-read`,
      `bloommcp-qc-clean-tool`, `bloommcp-pca-analysis-tool`, `bloommcp-source-selection`,
      `bloommcp-clean-version-selection`) has a corresponding test written above; add any missing
      test before marking this task done.

## 8. Pre-merge

- [x] 8.1 Run the repo's lint/format checks (ruff, black) on all touched Python files.
- [x] 8.2 Run the full `bloommcp` test suite; confirm no pre-existing test's assertions changed
      (only new tests added, per the default-preserving guarantee) — in particular confirm
      `test_fake_reader_is_not_source_selectable` still passes unmodified.
- [x] 8.3 Run `/pre-merge` end-to-end before opening the PR.
- [x] 8.4 (Follow-up, non-blocking) Recommend a manual check against a real multi-source
      experiment (`experiment_id=1` or `7206207`) on staging, since production-scale multi-source
      coverage is only 2/224 experiments and this proposal's automated tests are necessarily
      synthetic (design.md Risks).

## 9. PR #644 review fixes

Four independent review passes (code quality, testing, scientific rigor, behavioural correctness)
converged on the same blocking finding — see 2.2/2.5's updated notes above — plus several
"important" gaps below. All fixed in the same pass; see the PR's review-response commit(s) for
the full diff.

- [x] 9.1 **Blocking**: register `core_list_experiment_sources` in `sections/core/__init__.py`
      and add it to `test_expected_tool_surface`'s expected set (see 2.2/2.5).
- [x] 9.2 `qc_clean(csv_content=..., source_id=...)` now rejects the combination (a source pin only
      ever applies to the DB-backed raw tier, which `csv_content` bypasses entirely) instead of
      silently dropping the pin — consistent with the "reject, don't silently ignore" principle
      the mutual-exclusivity check already applies. Added
      `test_csv_content_with_source_id_is_rejected_not_silently_dropped` /
      `..._with_run_id_...` to `test_qc_clean_tool.py`.
- [x] 9.3 Removed `qc_clean`'s redundant second `list_sources` RPC call (it was re-querying sources
      to compute the advisory note after `load_experiment` had already resolved them internally —
      an always-on extra DB round-trip with a TOCTOU window against the read it followed). Added
      `ExperimentFrame.available_source_count`, stamped once from the same resolution
      `SupabaseReader.load_experiment` already performs (`_resolve_from_sources` factored out of
      `resolve_source` so both call sites share one `list_sources` call); `qc_clean`'s `source_note`
      now reads that field instead of calling `reader.list_sources` a second time.
- [x] 9.4 `qc_inspect` and `load_experiment_data` previously gave zero ambiguity signal when
      multiple sources exist and none is pinned — only `qc_clean` had `source_note`. Added the same
      advisory (reading `available_source_count`/`resolved_source`, no extra RPC per 9.3) to both:
      `QCInspectResult.source_note` and a `"  Note: ..."` line in `load_experiment_data`'s text
      summary (threaded through `_ports.load_frame`'s `config` dict as `resolved_source_id`/
      `available_source_count`, preserving its existing 4-tuple return contract).
- [x] 9.5 Consolidated the `_MultiSourceFakeReader` test double, previously duplicated
      near-verbatim across `test_qc_clean_tool.py`, `test_qc_inspect_tool.py`, and `test_ports.py`,
      into a single `make_multi_source_fake_reader` factory fixture in the root `tests/conftest.py`
      (mirroring the existing `seed_multi_source_experiment` factory-fixture pattern there, since
      this package has no top-level `tests/__init__.py` for a plain cross-module import). Added a
      `resolve_when_unpinned` flag so `test_ports.py`'s raw-tier-forcing scenario (which needs an
      unpinned call to leave the cleaned-version resolution untouched) and `qc_clean`/`qc_inspect`'s
      scenarios (which need an unpinned call to resolve "latest" so `source_note` populates) share
      one class.
- [x] 9.6 `SourcePinningUnsupportedError` messages (`LocalReader`/`FakeReader`) no longer name the
      internal adapter class — reworded to match `list_experiment_sources`'s own care not to leak
      backend implementation detail into agent-facing error text.
- [x] 9.7 `clustering.py`'s new `version` field used `Optional[str]` while every other field in
      that file uses `X | None` (the file's own established style) — the only inconsistency of its
      kind in the diff. Fixed; `descriptive_stats.py`'s `Optional[str]` was left as-is since that
      whole file consistently uses `Optional[...]`, not `X | None` — not an inconsistency.
- [x] 9.8 Added the still-missing Protocol-typed `AmbiguousSourceSelectionError` test task 1.4
      claimed: `test_both_given_is_ambiguous_via_protocol_typed_handle_on_a_source_capable_adapter`
      in `test_ports.py` (see 1.4's updated note above).
- [x] 9.9 Reconciled stale `tasks.md` checkboxes against what was actually shipped: 1.1 (coverage
      landed in `test_ports.py`, not as separate `test_local_reader.py`/`test_fake_reader.py`
      additions), 2.2/2.5 (marked done while never implemented — see above), and 6.1.a/6.1.b/6.1.c
      (left unchecked as sub-items despite the parent task and its tests being complete).
- [x] 9.10 Also caught while reconciling: 7.1 was checked despite `openspec validate --strict`
      actually failing — the `bloommcp-experiment-read/spec.md` MODIFIED block had dropped a
      pre-existing base-spec scenario (a MODIFIED requirement replaces the whole block; omitting a
      scenario the base spec still has is a strict-mode error, not a style nit). Restored it, plus
      added the new scenarios/requirement text this pass's actual changes need: the
      `available_source_count` field (`bloommcp-experiment-read`), qc_clean's csv_content+pin
      rejection (`bloommcp-qc-clean-tool`), and qc_inspect/load_experiment_data's advisory note
      (`bloommcp-source-selection`). Re-ran `openspec validate --strict` clean afterward.
