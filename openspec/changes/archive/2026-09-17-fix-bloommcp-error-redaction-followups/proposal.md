## Why

[#664](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/664) bundles three
lower-priority follow-ups explicitly scoped out of #660
(`fix-bloommcp-resultstore-error-swallowing`)'s own review, worth closing rather than
silently relying on an unstated invariant or leaving coverage gaps in place:

1. `list_existing_analyses.py`'s per-`tool_class` error-aggregation loop
   (`errors.append(f"{tool_class}: {exc}")`) skips the `safe_error_text` redaction its own
   sibling branch (`trim_staleness`, further down in the same function) and
   `get_download_links.py`'s equivalent handling both apply. Safe today only because `list_runs()` routes through
   `_guarded_manifest_read`, whose exception messages are pre-redacted by construction — an
   _implicit_ invariant at this call site, not an explicit, tested one (already called out
   as a follow-up in #660's `design.md` Decision 3).
2. Only 3 of the 8 write-and-link analysis tools (`qc_inspect`, `qc_clean`,
   `remove_outliers`) have a test pinning `code == "internal_error"` for an
   undeclared-exception fallthrough. The other 5 (`clustering`, `pca_analysis`,
   `descriptive_stats`, `cross_experiment_correlations`, `umap_analysis`) either exercise a
   _different_ code path (a degenerate-fit delegate raise translated to
   `assumption_violated` by the tool's own `except` clause) or, for `descriptive_stats`,
   have no leak test of any kind — its delegate call has no `except` clause at all.
3. The same aggregation loop labels a failing tool with its internal `tool_class` string
   (e.g. `"stats"`) rather than the public tool name an agent actually invoked (e.g.
   `"descriptive_stats"`) — mildly confusing when a scientist correlates the error against
   the call they made.

Two related items surfaced during investigation are explicitly **not** part of this change
(see Non-Goals): item 4 of #664 (`CommitFailedError` wording vs. `from_exception`'s shared
remedy) is an accepted trade-off per #660's `design.md` Decision 2, and a newly-discovered
gap — `list_existing_analyses`'s `TOOL_CLASSES` tuple never enumerating `"pca"`/`"umap"`/
`"qc_inspect"` — is unrelated to redaction/leak-testing and has been filed separately as
[#669](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/669).

## What Changes

- Wrap the per-`tool_class` `errors.append(...)` call in `list_existing_analyses.py` with
  `safe_error_text(exc)`, matching the pattern its `trim_staleness` sibling branch and
  `get_download_links.py` already use.
- Add a `tool_class` → public-tool-name lookup, scoped to the tool classes
  `list_existing_analyses.TOOL_CLASSES` actually iterates today, and use it when building
  that same aggregated error string, so a failure surfaces as (for example)
  `"descriptive_stats: ..."` rather than `"stats: ..."`. A `tool_class` with no known public
  mapping (the three legacy/retired entries — `"dimred"`, `"outlier"`, `"viz"`) falls back to
  the raw `tool_class` string rather than raising or dropping the error entry.
- Add a new leak-scrub test per tool for `clustering`, `pca_analysis`, and `umap_analysis`
  (monkeypatching their delegate call to raise an exception type _outside_ that tool's own
  degenerate-fit `except` clause, so the failure falls through undeclared to
  `internal_error`), tighten `cross_experiment_correlations`'s existing
  `test_no_error_leaks_backend_internals` to also assert `code == "internal_error"` (it
  already exercises the right path, just never asserted the code), and add a brand-new
  leak-scrub test for `descriptive_stats` (which has neither an `except` clause around its
  delegate call nor any leak test today), following the same pattern already established
  for `qc_inspect`/`qc_clean`/`remove_outliers` in #660.
- Update `test_list_existing_analyses_staleness.py`'s existing
  `test_trim_is_stale_and_an_unrelated_tool_class_error_both_survive_together` test, whose
  assertion (`e.startswith("qc: ")`) hard-codes the pre-fix raw-`tool_class` naming this
  change replaces with the public tool name (`"qc_clean: "`).

## Non-Goals

- **`CommitFailedError`'s structural-bug wording contradicting `from_exception`'s shared
  retry remedy** (#664 item 4). Documented as an accepted trade-off in #660's `design.md`
  Decision 2 (`from_exception`'s remedy is intentionally one fixed string for every declared
  exception type). Fixing it means either varying the remedy by exception type or rewording
  `CommitFailedError`'s message — both out of scope for this narrowly-scoped follow-up.
- **Threading the public tool name into `CommitFailedError`'s own message** (constructed
  deep in `ResultStore.commit`, e.g. `"commit failed for stats/..."`). This is actually the
  more commonly agent-visible instance of the tool_class-leak pattern #664 item 3 describes,
  but #664's own wording scopes item 3 to "`list_existing_analyses.py` and similar
  aggregation sites" — and `list_existing_analyses.py`'s loop is the only site of that shape
  in the codebase. Reworking `CommitFailedError`'s construction would touch `ResultStore`
  internals (which reason in terms of `tool_class`, not tool names) and require updating the
  literal `"commit failed for <tool_class>"` assertions already present in all 8 tools'
  existing test suites — a materially larger, more invasive change than this issue asks for.
- **`list_existing_analyses.TOOL_CLASSES` never enumerating `"pca"`/`"umap"`/
  `"qc_inspect"`**, so that tool's aggregation loop structurally cannot surface run
  history/errors for those 3 tools regardless of this change's redaction fix. Filed
  separately as #669; not implemented here.
- No change to `contract/wrap.py`/`contract/errors.py`'s mapping mechanics, `storage_backend.py`,
  or any of the 8 tools' `except`-clause classification logic (which delegate exception types
  map to `assumption_violated` vs. fall through to `internal_error`) — items 2's new/tightened
  tests are coverage-closing, not behavior-changing; each is expected to pass against the
  current implementation with no production-code change beyond item 1/3's `list_existing_analyses.py`
  edits.

## Impact

- **Affected specs:** `bloommcp-list-existing-analyses-tool` (ADDED — new capability covering
  this loop's redaction and tool-naming contract, which no existing spec owns today).
- **Affected code:** `bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py` (the
  aggregation loop; one new lookup table).
- **Affected tests:** `bloommcp/tests/tools/test_list_existing_analyses_staleness.py`
  (one updated assertion, two new tests); `test_clustering_tool.py`, `test_pca_analysis_tool.py`,
  `test_umap_analysis_tool.py` (one new test each); `test_cross_experiment_correlations_tool.py`
  (one tightened assertion); `test_descriptive_stats_tool.py` (one new test).
- **Dependencies:** none.
- **Branch/PR:** branches off `origin/staging` (`egao28/bloommcp-error-redaction-followups-664`);
  PR targets `staging`. Recommend `Fixes #664` in the PR body, noting items 4 and the
  `TOOL_CLASSES` gap (#669) are explicitly out of scope per the above.
