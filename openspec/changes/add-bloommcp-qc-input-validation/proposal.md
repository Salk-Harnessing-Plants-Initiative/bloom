## Why

`qc_clean` (#338) is the **sole producer** of the analysis-ready `_cleaned.csv` that the
sleap-roots-analyze consumer tools read via `require_clean=True` — `remove_outliers` (#378),
`pca_analysis` (#308), and (next) `clustering` (#309). Today it cleans traits but does **not**
make that artifact *contract-valid* or *traceable*:

1. **Untraceable outliers / an opaque crash (surfaced by the PR #400 review).** When a cleaned
   frame carries no recognized sample-identifier column, `remove_outliers` crashes with a
   scrubbed `internal_error`: the delegate returns `report["outlier_barcodes"] = None` and the
   tool does `[str(b) for b in report.get("outlier_barcodes", [])]` → `TypeError`
   ([`remove_outliers_tool.py:665`]; delegate at
   `sleap_roots_analyze/outlier_removal.py:389-391`). Beyond the crash, an outlier report keyed
   by positional index is scientifically useless — you cannot tell *which* plants/scans were
   dropped, which defeats the platform's traceability purpose. #400 ships a small tool-local
   guard; this change removes the **root cause** by guaranteeing every cleaned frame carries a
   sample identifier.
2. **Numeric metadata analyzed as a trait.** bloommcp's `detect_columns`
   ([`experiment_utils.py:152`]) treats *any* numeric column as a trait, so `Computation.Time.s`
   (a processing artifact) sits in the turface trait set and flows into cleaning / PCA / outlier
   detection. `sleap-roots-analyze`'s `get_trait_columns` excludes it (case-insensitive `"time"`
   substring). We should use the upstream detector rather than bloommcp's looser heuristic.
3. **The contract dependency is present but unused.** bloommcp depends on `sleap-roots-contracts`
   and records its version in provenance ([`storage/code_versions.py:20`]) but **never calls the
   validator**. `sleap-roots-analyze`'s own `DataConfig.validate_input` docstring explicitly
   recommends `strict` mode "at untrusted/external input boundaries (e.g. **a Bloom export**)" —
   analyze considers *us* the boundary that should validate.

This change makes `qc_clean` the point where a raw trait table becomes a **contract-valid,
traceable, correctly-typed** analysis-ready table: it wires the ecosystem's input-contract
validation into `qc_clean`, **requires** a genotype **and** a sample identifier for
traceability, and **delegates trait detection** to `sleap-roots-analyze`. Like `qc_clean` and
`remove_outliers`, it is a granular QC-foundation improvement added **alongside** the roadmap
tiers, not a tier itself.

## What Changes

- **ADD** input-contract validation at `qc_clean`. `qc_clean` SHALL validate its input by
  delegating to `sleap_roots_analyze.validation.validate_entry_input(df, columns=…,
  mode="warn", additional_exclude=…)`, mapping bloommcp's resolved role columns to the
  delegate's canonical roles. The MCP holds **no** validation logic. Validation runs **before**
  any run is persisted. If `sleap-roots-contracts` is **absent**, validation degrades to a
  **logged no-op** (the wrapper already guarantees this — never an `ImportError`). `warn`-mode
  advisory findings do **not** abort the run.
- **ADD** a traceability requirement: `qc_clean` SHALL require a resolvable **genotype** column
  **and** a resolvable **sample-identifier** column. These guards are enforced at the
  **bloommcp** level (the resolved role must not be `None`), so traceability holds **even when
  `sleap-roots-contracts` is absent**. The contract runs in `warn` (which alone would not fail a
  missing `sample_id`) to surface everything else without escalating minor advisories to hard
  errors. `replicate` stays optional (auto-detect only).
- **ADD** a standalone column-resolution unit `data_access/columns.py`:
  ```python
  resolve_columns(df, *, sample_id_column=None, genotype_column=None, exclude_columns=None)
      -> ResolvedColumns(genotype, sample_id, replicate, trait_cols, excluded_cols)
  ```
  - **Role-name matching stays bloommcp domain knowledge** — the `SAMPLE_ID_PATTERNS` /
    `GENOTYPE_PATTERNS` / `REPLICATE_PATTERNS` lists move here from `experiment_utils`
    ([`experiment_utils.py:106-108`]). analyze can't do this — it takes *configured* names.
  - **Trait detection delegates to `sleap_roots_analyze.get_trait_columns`** — dropping the
    `Computation.Time.s` class, consistent for every consumer, retiring bloommcp's duplicate
    dtype heuristic.
  - The **reader** calls it with **no overrides** (populates `ExperimentFrame`); **`qc_clean`**
    calls it **with overrides** (final resolution). `load_experiment`'s signature gains **no new
    params** — overrides are a `qc_clean` concern, keeping the Tier-2 read port stable.
- **ADD** `qc_clean` params `sample_id_column`, `genotype_column` (default auto-detect), and
  `exclude_columns` (metadata deny-list). An override forces the named column as that role. The
  existing `trait_columns` allow-list ([`qc_clean_tool.py:89`]) still **wins** over
  `exclude_columns` when both are given.
- **ADD** "ask the user" via a **structured error, not FastMCP elicitation.** If a **required**
  role can't be resolved, `qc_clean` returns `BloomMCPError(assumption_violated)` whose message
  **lists the available columns** and whose remedy directs the agent to ask the user and re-call
  with the override; **no run is persisted**. This reuses the existing `BloomMCPError` envelope
  (same pattern as the "run qc_clean first" guard) and needs **zero** contract-layer changes —
  the params-only wrapper ([`contract/wrap.py:139-153`]) is untouched.
- **ADD** findings to the result and the manifest. The `QCCleanResult`
  ([`qc_clean_tool.py:127`]) gains the resolved `genotype_column` / `sample_id_column` /
  `replicate_column`, the `excluded_columns` list, and `validation_warnings: list[str]`. The run
  **manifest** gains an additive `input_validation` block:
  ```json
  "input_validation": {
    "mode": "warn",
    "contract_version": "0.1.0a1",
    "resolved_roles": {"genotype": "geno", "sample_id": "Barcode", "replicate": "rep"},
    "excluded_columns": ["Computation.Time.s"],
    "warnings": ["optional metadata column 'rep' contains NaN"]
  }
  ```
- **ADD** the traceability contract to the **agent-facing tool surface**: `qc_clean`'s function
  docstring (which MCP `tools/list` exposes as the tool description) and the new `QCCleanParams`
  field descriptions SHALL state that a genotype **and** a sample identifier are now **required**,
  and name the `sample_id_column` / `genotype_column` / `exclude_columns` overrides — so an agent
  driving the tool learns the contract without reading the spec.
- **MODIFY** the `bloommcp-experiment-read` reader contract: the adapter still **declares** roles
  (never re-inferred by callers), but declaration now goes through the shared `resolve_columns`
  (bloommcp role matching + upstream `get_trait_columns` trait detection), so numeric metadata
  like `Computation.Time.s` is **no longer** declared as a trait for any consumer.
- **LEAVE** the discovery/raw-read tools, `run_qc_workflow`, and the vendored
  `bloom_mcp.data_cleanup` in place — only `qc_clean` validates. This **adds** validation
  alongside the existing surface; nothing is removed.
- Tests cover the **oracle-first acceptance set**: contract-valid `turface_19` cleans and
  persists; missing `sample_id`/`genotype` (no override) → structured error listing columns +
  **no run**; overrides resolve the named column; `warn`-mode findings recorded in result +
  manifest with the run **still committed**; `get_trait_columns` **excludes `Computation.Time.s`**
  (re-recorded golden); `exclude_columns` trims a named column while explicit `trait_columns`
  still wins; **contracts-absent → graceful**; `resolve_columns` standalone + unit-tested, used
  by reader and `qc_clean`, `load_experiment` signature unchanged.

## Impact

- **Affected specs:**
  - `bloommcp-input-validation` (**new** capability): contract validation, required traceable
    roles, column resolution + upstream trait detection, override params + structured errors,
    findings surfaced in result + manifest.
  - `bloommcp-experiment-read` (**MODIFIED**): the `ExperimentReader Port` requirement — role
    declaration routes through the shared `resolve_columns`; trait detection delegates to
    `get_trait_columns` (numeric metadata excluded).
  - **Supersedes** two aspects of the still-in-flight `bloommcp-qc-clean-tool` (#338, unarchived):
    its scenario *"An undetected role column falls back to the delegate default"* (an unresolved
    **required** role — genotype or sample_id — now hard-errors instead of defaulting) and its
    `turface_19` oracle (`187 samples × 20 traits → n_traits_out == 18` becomes **19 detected /
    17 cleaned**, since `get_trait_columns` drops `Computation.Time.s`). Reconciled by archive
    order: if #338 archives first, this change adds a `MODIFIED` delta for `bloommcp-qc-clean-tool`
    correcting that scenario + the numbers; otherwise #338's in-flight spec + golden are edited in
    lockstep with the #338 owner (see `tasks.md §0`).
  - Builds on (does not modify) `bloommcp-tool-contract` and `bloommcp-result-store`.
- **Affected code:**
  - new `bloommcp/src/bloom_mcp/data_access/columns.py` (`resolve_columns` + `ResolvedColumns`
    + the moved pattern lists);
  - `bloommcp/src/bloom_mcp/experiment_utils.py` (pattern lists move to `data_access/columns.py`;
    `detect_columns` becomes a **thin shim** over `resolve_columns` — retirement deferred — so the
    `list_experiments` (`:134`) and other call sites (`:362`, `:380`) and per-commit revert stay
    stable);
  - `bloommcp/src/bloom_mcp/data_access/` reader adapters (`SupabaseReader` / `FakeReader`, and
    `LocalReader` if #390 has landed on the base) — populate `ExperimentFrame` via `resolve_columns`;
  - `bloommcp/src/bloom_mcp/tools/qc_clean_tool.py` (new params + result fields; validation call;
    required-role guards; `input_validation` manifest block; **updated docstring + `QCCleanParams`
    field descriptions** so the required-role contract reaches the MCP `tools/list` surface);
  - `bloommcp/tests/` — new `data_access/test_columns.py` (unit), extended
    `test_qc_clean_tool.py` (validation, guards, overrides, surfacing, **plus fixing the stale
    hardcoded 20/18 literals at L65/L84/L178/L481**), reader adapter tests;
  - `bloommcp/tests/fixtures/turface_19_qc_golden.json` — **re-recorded** (**20 → 19 detected**,
    **18 → 17 cleaned**; `Computation.Time.s` excluded) + `fixtures/README.md` counts updated +
    the `_reproduced_by_sleap_roots_analyze_version` key bumped `0.1.0a3 → 0.1.0a4`.
- **Dependencies:** `sleap_roots_analyze.validation.validate_entry_input` and
  `sleap_roots_analyze.get_trait_columns` are **already available** in the pinned `0.1.0a4`;
  `sleap-roots-contracts` `0.1.0a1` is already a dependency. **No pin change, no re-lock.**
- **Relationship to #378 / #400:** guaranteeing a `sample_id` on every cleaned frame
  **structurally prevents** #400's barcode-less `remove_outliers` crash on the `require_clean` path
  (the tool-local guard in #400 becomes defense-in-depth). This change re-records the `qc_clean`
  golden (`turface_19` **20 → 19 detected**, **18 → 17 cleaned**, since `get_trait_columns` drops
  `Computation.Time.s`). #400 merged before implementation, so its fixtures are on the base — but
  the `remove_outliers` golden is **verified UNAFFECTED**: its tests inject a fixed cleaned frame
  disjoint from the detection path, so `Computation.Time.s` never entered them and the full
  `test_remove_outliers_tool.py` suite passes untouched. **No `remove_outliers` re-record is needed**
  (the #403 checklist item is moot, not undone).
- **Behavior changes (heads-up):**
  1. **Required roles use exact-match name detection.** `qc_clean` now hard-requires a genotype
     **and** a sample identifier, matched by exact (case-insensitive) column name against
     `geno`/`genotype`/`accession`/`species_name` and
     `barcode`/`plant_qr_code`/`scan_id`/`plant_id`/`plant_name`. An experiment whose identifier is
     spelled differently (e.g. `Genotype_ID`, `sample_barcode`, `QR_code`) now returns a guided
     `assumption_violated` (lists the columns, names the override) where it previously fell back to
     the delegate default. The `sample_id_column` / `genotype_column` overrides are the escape
     hatch; growing the pattern lists with common variants is a possible follow-up.
  2. **The reader-detection change ripples to every consumer.** Because `detect_columns` is now a
     shim over `resolve_columns`, *all* reader consumers (`qc_inspect`, `pca_analysis`, `clustering`,
     `remove_outliers`, `correlation`, discovery) switch to `get_trait_columns` detection, and
     `metadata_cols` now includes numeric metadata like `Computation.Time.s` — which flows into
     e.g. pca `scores.csv` / clustering `labels.csv` identity columns. The full suite is green (only
     the qc golden + one pca layout assertion needed updating), but the blast radius extends past
     `qc_clean`; a future upstream `get_trait_columns` change would ripple everywhere at once.
- **Branch/PR:** branches off `origin/staging`; PR targets `staging` (link #403 + the roadmap).
  One OpenSpec change + PR.
