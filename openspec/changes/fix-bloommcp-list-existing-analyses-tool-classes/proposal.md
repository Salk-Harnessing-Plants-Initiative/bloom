## Why

[#669](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/669), filed
separately from and unrelated to #664/#660: `list_existing_analyses.py`'s `TOOL_CLASSES`
tuple — the domain its aggregation loop iterates to collect run history/errors per tool
class — is:

```python
TOOL_CLASSES = (
    QC_TOOL_CLASS,      # "qc"
    "stats",
    "dimred",
    "clustering",
    "outlier",
    OUTLIERS_TOOL_CLASS,  # "outliers"
    "viz",
    "correlation",
)
```

`manifest/__init__.py`'s `CANONICAL_TOOL_CLASSES` is the same list plus `"heritability"`,
`"anova"`. Neither contains `"pca"`, `"umap"`, or `"qc_inspect"` — the `tool_class` values
`pca_analysis`, `umap_analysis`, and `qc_inspect` actually persist their runs under
(confirmed at `pca_analysis.py:60`, `umap_analysis.py:77`, `qc_inspect.py:81`). `"dimred"` is
a retired legacy tool class (see `server.py`'s comment on the retired Phase-1
`run_*_workflow` tools), not an alias for either `pca_analysis` or `umap_analysis`.

**Practical consequence:** `list_existing_analyses`'s aggregation loop structurally never
calls `store.list_runs(experiment, "pca")` / `"umap"` / `"qc_inspect"` — so an agent has no
way to discover prior run history, or surface a `list_runs` failure, for any of these 3
tools via `list_existing_analyses`, regardless of how many times each has actually run.
`store.list_runs` itself takes an arbitrary `tool_class` string with no registry-backed
validation (`result_store/ports.py:227` and both implementations) — the 3 tools' runs are
persisted and independently readable today; `list_existing_analyses` simply never asks for
them.

No existing test (`bloommcp/tests/tools/test_list_existing_analyses_staleness.py`,
`bloommcp/tests/sections/core/`) exercises `list_existing_analyses` against a `"pca"`,
`"umap"`, or `"qc_inspect"` run — the gap isn't caught by current coverage either.

## What Changes

- Add `"pca"`, `"umap"`, and `"qc_inspect"` to `list_existing_analyses.TOOL_CLASSES`
  (`bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py`), so the aggregation
  loop calls `store.list_runs(experiment, tool_class)` for all 3 and both surfaces their run
  history in `analyses` and reports a `list_runs` failure for any of them in `errors`,
  exactly as it already does for `qc`/`stats`/`clustering`/`outliers`/`correlation`.
- Add the same 3 entries to `manifest.CANONICAL_TOOL_CLASSES`
  (`bloommcp/src/bloom_mcp/manifest/__init__.py`), so it remains a superset of
  `list_existing_analyses.TOOL_CLASSES` as its own top-of-file convention comment states.
- Add a regression test asserting `"pca"`, `"umap"`, and `"qc_inspect"` are registered in
  both `TOOL_CLASSES` and `CANONICAL_TOOL_CLASSES` (mirroring
  `test_remove_outliers_tool.py::test_outliers_class_registered_in_discovery_and_canonical_registries`'s
  existing pattern for `"outliers"`), plus one `test_discoverable_via_list_existing_analyses`
  end-to-end test **per tool** — added to `test_pca_analysis_tool.py`,
  `test_umap_analysis_tool.py`, and `test_qc_inspect_tool.py` respectively, each committing a
  real run through that tool's own `_run()`/`injected_ports` harness and asserting the
  response's `analyses` includes that tool's class — mirroring the identical pattern already
  established in `test_remove_outliers_tool.py::test_discoverable_via_list_existing_analyses`
  and `test_cross_experiment_correlations_tool.py::test_discoverable_via_list_existing_analyses`.
  (An earlier draft of this proposal planned a single manifest-fixture-based end-to-end test
  in `test_list_existing_analyses_staleness.py`; review found that fixture writes through the
  on-disk manifest backend while the aggregation loop under test reads through the injected
  `FakeResultStore`, an unrelated seam — that test could never observe the write regardless of
  whether the fix landed. The per-tool pattern above uses the same fake-store seam the
  aggregation loop actually reads, as proven by its two existing precedents.)
- Add one sentence to `manifest.CANONICAL_TOOL_CLASSES`'s own comment block stating the
  superset-of-`list_existing_analyses.TOOL_CLASSES` invariant explicitly — today that
  invariant exists only in this proposal's prose, not in the code comment itself. Add a
  parallel note to `list_existing_analyses.TOOL_CLASSES`'s comment acknowledging that
  `"pca"`/`"umap"`/`"qc_inspect"` are added as plain re-typed literals, not imported
  single-sourced constants like `QC_TOOL_CLASS`/`OUTLIERS_TOOL_CLASS` — each producer's
  `_TOOL_CLASS` constant is private/unexported, so this file can't import it today.

## Non-Goals

- No change to `_TOOL_CLASS_TO_PUBLIC_NAME`, redaction, or error-labeling behavior in
  `list_existing_analyses.py` — that surface is #664's scope
  (`fix-bloommcp-error-redaction-followups`, PR #671), not yet merged as of this change and
  independent of it. **Cross-PR tracking is bidirectional, not one-directional (bloom#673
  review):**
  - If #671 merges first: rebasing/merging this change (#669/PR #673) needs no action on
    `_TOOL_CLASS_TO_PUBLIC_NAME` itself, but whoever does that merge should extend that
    lookup with `"pca"` → `pca_analysis`, `"umap"` → `umap_analysis`,
    `"qc_inspect"` → `qc_inspect` as a small follow-up in the same PR — otherwise a
    `list_runs` failure for these 3 newly-discoverable classes will be labeled by its raw
    `tool_class` string instead of its public tool name, inconsistent with every other live
    entry in that lookup.
  - If #669/PR #673 merges first (the more likely order given current CI state on each PR):
    whoever later merges #671 must perform that same `_TOOL_CLASS_TO_PUBLIC_NAME` extension
    as part of that PR, since `TOOL_CLASSES` will already include `"pca"`/`"umap"`/
    `"qc_inspect"` by then — #671's diff as originally scoped predates this change and would
    not include them. A comment has been left on PR #671 flagging this so the note isn't
    lost to whichever PR merges second.
- No change to `pca_analysis.py`/`umap_analysis.py`/`qc_inspect.py` themselves — each
  already commits its runs under the correct `tool_class` today; the gap is purely in what
  `list_existing_analyses`/`CANONICAL_TOOL_CLASSES` iterate.
- No pruning of the existing retired entries (`"dimred"`, `"outlier"`, `"viz"`) — both
  registries' own comments say historical runs under those classes must still read back;
  out of scope and unrelated to this gap.
- No change to `store.list_runs`/`ResultStore` port validation — it already accepts an
  arbitrary `tool_class` string; nothing there needs to change for this fix.

## Impact

- **Affected specs:** `bloommcp-list-existing-analyses-tool` (ADDED — this change is the
  first to add a spec delta for this capability on this branch; #664/PR #671 also targets
  this capability name from a different, not-yet-merged branch — the two deltas will
  co-exist independently until whichever archives first).
- **Affected code:** `bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py` (the
  `TOOL_CLASSES` tuple + its comment), `bloommcp/src/bloom_mcp/manifest/__init__.py` (the
  `CANONICAL_TOOL_CLASSES` tuple + its comment).
- **Affected tests:** `bloommcp/tests/tools/test_list_existing_analyses_staleness.py` (one
  new registration test covering all 3 classes); `test_pca_analysis_tool.py`,
  `test_umap_analysis_tool.py`, `test_qc_inspect_tool.py` (one new
  `test_discoverable_via_list_existing_analyses` test each).
- **Dependencies:** none.
- **Branch/PR:** branches off `origin/staging`
  (`egao28/bloommcp-list-existing-analyses-tool-classes-669`); PR targets `staging`.
  Recommend `Fixes #669` in the PR body.
