## Why

[#552](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/552) — every
bloom-mcp tool schema `Field(description=...)`, discovery-tool docstring, and
path-traversal validation error that describes the `experiment` input tells the calling
LLM to pass a **CSV filename**. That is wrong under the default `supabase` backend once
`data-access-roadmap.md` Tier 2
([#551](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/551), successor
to Tier 1 [#546](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/546))
lands: `SupabaseReader.load_experiment` will resolve an experiment-id-shaped string
(`str(experiment_id)`), not a filename, and an LLM that follows the tool's own
description will pass the wrong shape and get a confusing "not found" instead of a clear
path to the right input.

Two distinct categories, confirmed by reading the current code (not the roadmap doc's own
"five tool schemas" undercount):

1. **Enforced path-traversal guards** (`tools/_qc_shared.py:73`,
   `sections/sleap_roots/analysis/_viz_shared.py:98`) — these only reject path
   separators/`..`/absolute paths; they never checked for a `.csv` suffix. A numeric
   identifier like `"42"` already passes both today. So this is a wrong error **message**,
   not an actual functional block — the traversal guard itself (preventing escape from
   `TRAITS_DIR`) is correct and untouched.
2. **Tool-schema `description=` text / docstrings** across 8 consumer/producer tools (9
   sites — `cross_experiment_correlations.py` has two), 5 plot tools' shared docstring
   pattern, 2 other discovery/consumer docstrings, and 1 discovery-tool param name
   (`list_existing_analyses.py`'s `experiment_filename`) — what the calling LLM actually
   reads to decide what to pass.

A fresh grep against current `staging` (not just the issue's point-in-time list) turns up
two more locations added since the issue was filed by
[#489](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/550)
(`cross_experiment_correlations.py`'s `experiment_1`/`experiment_2` descriptions) and one
the issue's list omitted (`list_available_experiments.py`'s docstring + its hardcoded
`"use its filename"` response line) — the same kind of drift the issue itself calls out
in the roadmap doc's stale count. All are in scope here.

## What Changes

Reword every listed location's LLM-facing text from "CSV filename" to a backend-agnostic
**"experiment identifier"** (matching what `ExperimentReader.load_experiment(name, ...)`
accepts today and will keep accepting post-Tier-2), without asserting *how* the identifier
resolves (it resolves to a filename today; it will resolve to `str(experiment_id)` once
Tier 2 ships) — so this wording stays true on both sides of that migration and does not
need a second pass when Tier 2 lands:

- **Path-traversal guard messages** (behavior unchanged, message text only):
  - `bloommcp/src/bloom_mcp/tools/_qc_shared.py:73` —
    `_validate_experiment_name`'s raised message + `remedy`.
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/_viz_shared.py:98` —
    `validate_filename`'s returned string (plus its docstring's first line, line 72).
- **Tool-schema `description=` text** (9 files, `Field(...)` on the Pydantic input model):
  `clustering.py:75`, `descriptive_stats.py:110`, `pca_analysis.py:82`,
  `umap_analysis.py:100`, `qc_clean.py:97`, `qc_inspect.py:90`, `remove_outliers.py:99`,
  `cross_experiment_correlations.py:142,154` (both `experiment_1`/`experiment_2`).
- **Docstrings** (LLM-facing `Args:` lines, not schema text):
  `sections/core/load_experiment_data.py:20`, `sections/core/list_available_experiments.py`
  (module + function docstrings, plus the hardcoded `"use its filename"` response line —
  additional location beyond the issue's list, found via fresh grep),
  `experiment_utils.py:459`, `sections/phenotyping_segmentation/summarize_trait.py:17`,
  and the 5 plot tools' `filename:` docstring line (`plot_correlation_matrix.py:25`,
  `plot_heritability_bar.py:29`, `plot_trait_boxplots.py:32`,
  `plot_trait_histograms.py:31`, `plot_variance_decomposition.py:28`).
- **Param rename**: `sections/core/list_existing_analyses.py`'s `experiment_filename`
  parameter → `experiment` (matching the other 9 tools' param name — the name itself is
  more load-bearing to an LLM than its description, per the issue). Cascades into the
  function's JSON response key (`"experiment_filename"` → `"experiment"`) and
  `tests/tools/test_qc_tools_discovery.py:102`'s assertion on that key.
- **Tests**: `tests/tools/test_viz_tools.py:411,426` — update the two assertions pinned to
  `"bare CSV filename"` to the new wording; both are regression guards on error-message
  *content*, not behavior, so the assertions move with the text.

No functional behavior change anywhere in this list — every path-traversal guard's
accept/reject decision is byte-for-byte unchanged; only what it says, and one
non-Pydantic function's parameter name, change.

## Explicitly out of scope / Non-Goals

- **`bloommcp/docs/storage-backends.md`'s `supabase`-mode description** — the issue asks
  to describe it as "DB-direct trait reads, not bucket CSVs," but Tier 2
  ([#551](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/551)) has not
  merged (open PR [#557](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/557),
  issue still OPEN) — the deployed `supabase` backend still reads raw inputs from local
  `BLOOM_TRAITS_DIR` / cleaned outputs from Supabase Storage, exactly as the doc says
  today. Rewriting it now to describe DB-direct reads would describe behavior that does
  not exist yet — the same class of inaccuracy this issue is fixing elsewhere. Deferred to
  land alongside or after #551/#546 (see `design.md`).
- **Renaming the other `filename`-named params** (the 5 plot tools, `load_experiment_data`,
  and the unrelated `phenotyping_segmentation` demo tools `compute_min`/`compute_mode`/
  `compute_median`) — the issue only calls out `list_existing_analyses.py`'s
  `experiment_filename` for a rename ("consider renaming... the param name itself"); a
  bare `filename` param name is a smaller, more ambiguous signal and renaming it is a
  separate, broader tool-schema decision this issue doesn't ask for.
- **Retiring dead CSV-from-bucket/local-disk raw-tier code, or dropping
  `BLOOM_TRAITS_DIR` from boot validation** — that is
  [#476](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/476)'s scope
  (gated on Tier 2), not this change's.
  [#477](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/477) — the
  adjacent `SLEAP_OUT_CSV`/bind-mount ask the roadmap's Tier 3 row also names — is
  already **CLOSED** (completed via PR #495, 2026-07-22; verified live via `gh issue
  view 477`), so only #476 remains open and gated on Tier 2 here.
- **`bloommcp/src/bloom_mcp/manifest/analysis_dir.py`'s `experiment_filename`
  constructor param/attribute** (and its `supabase_store.py` call sites) — internal
  storage-layer plumbing, never an LLM-facing schema or docstring; not in the issue's
  list and out of scope here.
- **`bloommcp/docs/data-access-roadmap.md`'s own "CSV filename" mentions** (its Tier 3
  table row, and the Reconciliation log's "LLM-facing tool text says 'CSV filename'"
  entry) — that table row bundles more unfinished Tier-3 work than this change's narrow
  slice (dead-code retirement, the `storage-backends.md` rewrite, `BLOOM_TRAITS_DIR` boot
  validation all remain undone), so editing just the wording clause would leave the row's
  overall `⬜ not started` status just as misleading in a different way. Left as a
  tracked follow-up (`tasks.md` §7.3) rather than partially edited here; `tasks.md`
  §6's verify-grep is deliberately widened to include `bloommcp/docs` so these expected
  hits stay visible instead of silently missed.

## Impact

- **Affected specs:** `bloommcp-experiment-identifier-wording` (new capability, ADDED).
  Builds on (does not modify) `bloommcp-experiment-read`, `bloommcp-tool-contract`.
- **Affected code:** the 20 files listed under "What Changes" above (2 validation guards,
  9 tool-schema descriptions across 8 files, 10 docstring sites across 9 files, 1 param
  rename). Recounted directly against a fresh `rg` over `bloommcp/src` — see
  `tasks.md` §6.1's verify step for the authoritative list.
- **Affected tests:** `bloommcp/tests/tools/test_viz_tools.py` (2 assertions),
  `bloommcp/tests/tools/test_qc_tools_discovery.py` (1 assertion on the renamed response
  key). Confirmed via full-tree grep that no other test asserts on `experiment_filename`
  or pins the old wording.
- **Dependencies:** none — no `sleap-roots-analyze`/`sleap-roots-contracts` pin change.
- **Branch/PR:** branches off `origin/staging`; PR targets `staging`. **Do not use a
  closing keyword** (`closes #552`/`fixes #552`) in the PR description: issue #552's own
  Acceptance section has a `storage-backends.md` bullet this change deliberately defers
  (see Non-Goals above and `design.md`'s Decisions), so merging this PR does not fully
  satisfy #552. Recommend the PR body read `Addresses #552` and that #552 stay open,
  re-scoped to track only the deferred `storage-backends.md` follow-up (`tasks.md` §7.1),
  until Tier 2 (#551/#546) ships and that follow-up lands.
