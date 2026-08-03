## Why

`remove_outliers` (#378, shipped via PR #400) persists its trimmed table under the **same**
`tool_class="qc"` and the **same** `CLEANED_CSV_NAME` (`_cleaned.csv`) that `qc_clean` writes.
`ExperimentReader.load_experiment(..., version="latest")` resolves "latest cleaned" via
`experiment_utils._resolve_versioned_cleaned`, which is hardcoded to read only the `qc`-class
manifest's `latest` pointer — whichever `qc`-class run committed most recently, regardless of
which tool produced it. Every `commit()` advances that one manifest's `latest`, so:

1. `qc_clean` → v1 (158 rows) · latest = v1
2. `remove_outliers` → v2 (trimmed, 150 rows) · latest = v2 ✅ `require_clean` reads the trim
3. `qc_clean` again (e.g. to re-tune thresholds) → v3 (un-trimmed, 158) · latest = **v3**
4. `pca_analysis(require_clean=True)` → resolves **v3 = the un-trimmed frame**. The trim from
   step 2 is silently gone — no error, no warning.

Each run already records `based_on_version` in provenance, so the revert is auditable **after
the fact**, but nothing prevents or warns at read time. A bench scientist running
`qc_clean → remove_outliers → qc_clean → pca_analysis` loses the trim silently.

This was a known, deliberately deferred trade-off at ship time — `add-bloommcp-remove-outliers-tool`'s
design.md Decision 1 chose the shared-`qc`-class approach specifically because it composed into
`require_clean=True` "for free," and flagged the dedicated-class alternative as a documented,
reviewer-visible caveat rather than blocking on it (`design.md:96-102`). Reviewer `eberrigan`
re-verified on 2026-07-29 that the gap is still live in the shipped code (the module's own
docstring cites it as "tasks 7.2") and recommended implementing the dedicated-class alternative
rather than a warning-only interim, since a warning still leaves the underlying hazard live for
anyone who doesn't read it every time.

## What Changes

- **Give `remove_outliers` a dedicated `tool_class="outliers"`** (not `"qc"`), so its trimmed
  runs live in their own manifest catalog (`bloommcp_output/outliers_<stem>/manifest.json`),
  separate from `qc_clean`'s.
  - **Naming note (verified, not assumed):** the retired legacy `run_outlier_workflow`
    (pre-`devendor-bloommcp-analysis`) used `tool_class="outlier"` — **singular** — and any
    experiment that tool ever ran against still has a real `outlier_<stem>/manifest.json` in
    Storage today (`list_existing_analyses`'s `TOOL_CLASSES` keeps `"outlier"` listed
    specifically so those old runs stay readable — "do NOT prune retired classes"). This change
    uses `"outliers"` — **plural** — a genuinely unclaimed slot, so the new class cannot collide
    with or resurrect old vendored-detector history under the retired singular name.
- **Extend `experiment_utils._resolve_versioned_cleaned` to resolve `version="latest"` by
  preferring the `outliers` class's `latest` entry over the `qc` class's, whenever `outliers`
  has any committed version** — a fixed priority, not a recency comparison (an earlier draft of
  this fix tried comparing `VersionEntry.created_at` across the two manifests and picking
  whichever was more recent; walking the issue's own repro shows that does **not** fix it — the
  offending `qc_clean` re-run is, by construction, always committed *after* the trim it reverts,
  so "prefer whichever is newest" still picks it. See design.md for the full trace.)
- **Give `remove_outliers`'s own read of its trimming input a new, distinct
  `version="latest_qc"`** — resolved as "the `qc` class's latest, ignoring any `outliers`
  version" (exactly today's pre-fix behavior, kept for this one caller). This is what makes the
  fixed-priority rule above safe: without it, `remove_outliers`'s *own* next invocation would
  also prefer its own stale prior trim over a fresh `qc_clean`, permanently hiding new clean data
  behind an old trim. Every other `require_clean=True` consumer (`pca_analysis`, `umap_analysis`,
  `clustering`, `descriptive_stats`, `cross_experiment_correlations`) keeps calling with the
  default `version="latest"` and gains the trim-preferring behavior with no call-site change.
  `SupabaseReader` and `LocalReader` both call through the one shared `_resolve_versioned_cleaned`
  helper, so both adapters pick up the fix together. Explicit version pins (`version="v3"`) and
  the `version="raw"` tier are untouched — see Non-Goals.
- **Qualify the resolved label with the tool class only when `"latest"` actually resolves via
  `outliers`** (e.g. `"outliers_v2_cleaned"`), leaving the far more common qc-only case exactly as
  it returns today (`"v3_cleaned"`, unqualified) — deliberately asymmetric so this change has
  **zero observable effect** on any experiment that has never been trimmed (see design.md
  Decision 5 for why unconditional qualification was considered and rejected: it would silently
  change the persisted `based_on_version` format for every tool, on every experiment, and break
  an existing passing test).
- Update `remove_outliers.py`'s module docstring and the still-open
  `add-bloommcp-remove-outliers-tool` change's own proposal/design/tasks/spec text, which
  currently document the shared-class trade-off (including a spec **Scenario** that asserts the
  order-dependent revert as intended behavior) — that change was merged but never archived, so
  its spec is still the only written record of `remove_outliers`' persistence contract; leaving
  it uncorrected would ship a false record once it is eventually archived.
- Add a characterization test pinning
  `qc_clean → remove_outliers → qc_clean → require_clean=True` to resolve the **trimmed** frame
  (previously untested — the issue's own `test_qc_class_rerun_reverts_latest_cleaned_order_dependence`
  in `test_remove_outliers_tool.py:718-743` explicitly characterizes the *current, buggy*
  behavior and names itself "characterization, not a fix" — that test is deleted, not updated,
  since its premise no longer holds), plus unit tests on the new `"latest"` vs `"latest_qc"`
  branching in `_resolve_versioned_cleaned` (including the `ManifestSchemaError` boundary).

## Non-Goals

- **No warning/`superseded`-flag *substituting for* the structural fix.** The issue's "cheap
  interim" option (detect + warn, still silently revertible) is explicitly not pursued — matching
  the reviewer recommendation, a structural fix removes the hazard rather than requiring every
  caller to notice a flag. (A non-blocking staleness signal **in addition to** the unchanged
  fixed-priority resolution — logging when a trim's `based_on_version` no longer matches the
  current `qc` latest — is a different, non-contradictory idea and is left to the same follow-up
  issue as the audit script above, not implemented here.)
- **No change to explicit-version pinning across tool classes.** No shipped tool currently passes
  an explicit `version="v<N>"` for the cleaned tier (grep confirms the only two values used
  anywhere are the default `"latest"` and `qc_clean`'s own `version="raw"`), so resolving an
  explicit pin across two independently-numbered class sequences (`qc`'s `v3` vs `outliers`' own
  `v3` are unrelated numbers) is a real but currently-unreachable question, left for whenever a
  tool actually needs it.
- **No retroactive fix for runs already persisted under the old shared-`qc` scheme.** Any
  `remove_outliers` run committed under `tool_class="qc"` before this change ships keeps the
  pre-existing hazard (still auditable via `based_on_version`, as today); only runs made after
  this ships go to the protected `outliers` class. Filed as follow-up
  [#585](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/585) (a one-time
  read-only audit script flagging experiments where this already happened) — not part of this
  change.
- **No data migration.** Additive/behavioral only — a new manifest catalog going forward, plus a
  reader-resolution change. No existing manifest is rewritten.
- **No automatic recovery of a fresh `qc_clean` once a trim exists.** This is the central,
  disclosed trade-off of the chosen design (design.md Decision 4 / Open Questions): once an
  `outliers` version exists for an experiment, a later plain `qc_clean` re-run does not become
  "latest" for `require_clean=True` consumers on its own — only a fresh `remove_outliers` run
  makes the new clean reachable. The alternative (auto-falling-back to the newer plain clean when
  the trim looks stale) was considered and rejected because it re-admits a silent path back to
  un-trimmed data — see design.md for the full reasoning.

## Impact

- **Affected capability:** `bloommcp-experiment-read` (the `_resolve_versioned_cleaned` /
  "latest cleaned" resolution order is a spec'd requirement there — `Version selection resolves
  in the deployed order`).
- **Affected code:** `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/remove_outliers.py`
  (tool_class constant, `version="latest_qc"` read, docstring),
  `bloommcp/src/bloom_mcp/experiment_utils.py` (`_resolve_versioned_cleaned`,
  `load_experiment_data` docstring),
  `bloommcp/src/bloom_mcp/data_access/ports.py` (`ExperimentReader.load_experiment` docstring),
  `bloommcp/src/bloom_mcp/data_access/fake_reader.py` (`"latest_qc"` alias),
  `bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py` (`TOOL_CLASSES`),
  `bloommcp/src/bloom_mcp/manifest/__init__.py` (`CANONICAL_TOOL_CLASSES`),
  the parameter descriptions of `pca_analysis.py`, `umap_analysis.py`, `clustering.py`,
  `descriptive_stats.py`, and `cross_experiment_correlations.py` (one sentence each — design.md
  Decision 8), `bloommcp/tests/tools/test_remove_outliers_tool.py` (new characterization
  coverage; delete the now-invalidated
  `test_qc_class_rerun_reverts_latest_cleaned_order_dependence`),
  `bloommcp/tests/smoke/live_persistence_smoke.py` (`RO_TOOL_CLASS = "qc"` → `"outliers"`),
  `bloommcp/docs/local-validation.md` (its `remove_outliers` narrative currently documents the
  shared-`qc`-class assumption), and the still-open
  `openspec/changes/add-bloommcp-remove-outliers-tool/` proposal set (corrected, not archived,
  by this change).
- **No call-site or behavior change, docstring-only:** `qc_clean`, `pca_analysis`,
  `umap_analysis`, `clustering`, `descriptive_stats`, `cross_experiment_correlations` — all read
  through the same `ExperimentReader` port with the default `version="latest"` and gain the
  trim-preferring behavior automatically; five of them gain a one-sentence docstring note
  (Decision 8) but no code-path change.
- **Relationship to #419:** filed alongside this issue in the same epic (#554, "Trace &
  reproduce") and assigned together, but orthogonal — #419 is about *gating persistence* of an
  untrustworthy-fit trim before it commits; this change is about *which manifest class* a trim
  lands in once it does commit. Both touch `remove_outliers.py` but different, non-conflicting
  regions; no sequencing dependency either direction.
- This change is intended to fully close #420 — both Acceptance criteria (the sequence resolves
  the trimmed frame; a characterization test pins the chosen behavior) are met.
- Refs: #420 (this issue, closes), #400/#378 (`remove_outliers`), #419 (related, no dependency),
  #395 (backend-mixing sentinel, precedent for a Tier-2-reader-adjacent change on this branch's
  lineage).
