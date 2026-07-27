## Why

Before the Phase-1 workflow retirement (`refactor(bloommcp): C7 — retire the Phase-1 workflow
tools`, #438), `run_descriptive_stats_workflow` (`tools/workflows/stats.py`) delegated entirely to
`sleap_roots_analyze.statistics.calculate_trait_statistics` — per-trait n, mean, std, median, q25,
q75, min, max, CV, skewness, kurtosis — and persisted a versioned `stats.csv`. It was retired along
with every other Phase-1 workflow because it duplicated a delegate the granular tools now wrap
directly; nothing has replaced it since. `calculate_trait_statistics` is still public today
(confirmed in `sleap_roots_analyze.__all__`, unchanged signature, already satisfied by the pinned
`sleap-roots-analyze>=0.1.0a5` — no dependency bump needed, same situation `remove_outliers` #378
found).

This change (#488) adds **`descriptive_stats`**: the sixth granular `sleap_roots_analyze`
consumer in `sections/sleap_roots/analysis/`, alongside `pca_analysis` / `clustering` /
`umap_analysis`. Unlike those, it has no method roster, no stochastic seed, and (per the issue's
own scope line) no plotting — it is the simplest of the granular consumers: read a cleaned
experiment, delegate the entire computation to one pure function, persist the full table, return a
bounded summary + links.

## Explicitly out of scope (per the issue and the old workflow's own docstring)

- `perform_anova_by_genotype` — the old workflow's docstring already earmarked this for a
  separate group-comparison workflow, never descriptive stats.
- `calculate_heritability_estimates`, `identify_high_heritability_traits`,
  `analyze_heritability_thresholds`, `diagnose_heritability_issues`,
  `compare_trait_heritabilities`, `extract_blup_table` — heritability-family, already
  `plot_heritability_bar`'s domain.
- `analyze_trait_variance` — confirmed not used by any current tool (`plot_variance_decomposition`
  delegates to `calculate_heritability_estimates`, not this). A separate, currently-unexposed
  upstream function; a future issue's scope, not this one's.
- Plots — the issue doesn't ask for any, and `calculate_trait_statistics` returns numbers, not
  figures. `remove_outliers`/`pca_analysis` added optional plots because their own follow-up
  issues (#426 etc.) asked for it; no analogous ask exists here.

## What Changes

- **ADD** a granular `descriptive_stats` MCP tool (Pydantic input/output models + a tool function
  wrapped by `@as_mcp_tool`) in `sections/sleap_roots/analysis/descriptive_stats.py`, that:
  - reads the **cleaned** experiment frame through the injected `ExperimentReader` port with
    **`require_clean=True`** — a **consumer**, matching `pca_analysis` / `clustering`'s shape (the
    issue cites `qc_clean`/`pca_analysis`/`remove_outliers`/`clustering` collectively for "shape",
    but `qc_clean` is the raw-frame *producer*; `descriptive_stats` computes summary statistics
    over already-certified traits, so it belongs with the **consumer** half of that list); a
    missing cleaned version maps to a structured `BloomMCPError(code="tool_error", remedy="run
    qc_clean first")`, genuinely mirroring `pca_analysis`/`clustering`'s own handling of this exact
    case (not `remove_outliers`'s `assumption_violated` — the two consumer siblings this tool is
    modeled on both use `tool_error` here), no run persisted;
  - restricts trait selection to the certified-clean set (`frame.trait_cols`) via the existing
    `_validate_trait_subset(..., require_certified=True)` helper — reused verbatim, no new
    validation logic — so `trait_columns` (optional; omit for all certified traits) can't summarize
    a NaN-bearing or non-numeric column;
  - **re-verifies the selection is fully finite before delegating** (mirrors `pca_analysis`'s own
    `np.isfinite` defense-in-depth guard): a certified trait carrying a residual NaN would
    otherwise make the delegate's own per-trait `dropna()` silently report `n < n_samples` with no
    signal — a reader/`qc_clean`-invariant violation, not something to silently under-report;
  - delegates **all** computation to `sleap_roots_analyze.calculate_trait_statistics(df,
    trait_cols)` in one call. The MCP contains **no statistics math** — no mean/std/quantile/
    skewness/kurtosis computation of its own;
  - **declares no `random_state`** — `calculate_trait_statistics` is a pure deterministic function
    (no RNG) — so provenance records `seed = None`, matching `qc_clean` / `pca_analysis`;
  - **persists the full per-trait table** (every selected trait, uncapped) as a versioned run via
    the `ResultStore` port under a new tool class `stats`, writing `stats.csv` in the same
    long-format layout the legacy workflow used (`trait, n, mean, std, median, q25, q75, min, max,
    cv, skewness, kurtosis`) so any script that consumed the old `stats.csv` shape still parses the
    new one; records `based_on_version` = the consumed cleaned source (mirrors `pca_analysis`);
  - **returns a bounded inline summary, not the full table**: `stats_per_trait` is capped to the
    first 50 traits (`_SUMMARY_TRAIT_CAP`, carried over verbatim from the legacy workflow) with a
    `truncated_in_summary` flag and an `omitted_traits: list[str]` naming exactly which traits
    were cut (so a caller chasing one specific trait doesn't have to download and parse `stats.csv`
    blind to find out whether it's in the missing tail) — necessary given the cylinder fixture's
    ~649–880 traits (#483); the full table is always in the persisted CSV, referenced via
    `resource_link`;
  - **defends against non-finite values reaching the JSON-RPC envelope**: `cv` is `np.inf` when a
    trait's mean is exactly 0 — genuinely reachable, since `qc_clean`'s zero-*fraction* threshold
    limits how many values are exactly 0, not whether the *mean* is 0, and no cleanup step rejects
    it. (`skewness`/`kurtosis` returning `nan` for a zero-variance trait is a related SciPy
    divide-by-zero case, but is **not** reachable through a genuinely `qc_clean`-produced cleaned
    frame — `clean_traits_for_analysis` unconditionally strips zero-variance traits as one of its
    own steps, so it is handled as defense-in-depth against a hand-crafted/adversarial cleaned
    frame, not a case real `qc_clean` output can produce; see design.md Decision 4.) The tool
    coerces `inf`/`-inf`/`nan` to `None` (`Optional[float]` fields) before constructing the output
    model and before writing `stats.csv`, rather than letting a bare `Infinity`/`NaN` token reach
    the wire, and **names which traits were coerced** in a `nonfinite_stat_traits: list[str]` field
    (mirroring `pca_analysis`'s `dropped_constant_traits` precedent) rather than leaving a bare
    blank cell a scientist skimming a wide CSV could miss (the persisted CSV's `float` columns
    already round-trip string `"inf"`/`"nan"` via pandas, so this only changes the *inline*
    JSON-RPC payload and the output-model validation, not what a re-read of the CSV sees);
  - keeps the delegate's own per-trait `{"error": "No valid data"}` branch as **defense-in-depth**
    (unreachable via a genuinely certified-clean selection today, since `qc_clean` guarantees zero
    NaN cells in kept trait columns — same "delegate returns rather than raises" caution
    `remove_outliers` and `qc_clean` both apply to their own guards): a trait the delegate reports
    as failed is excluded from `stats_per_trait` / `stats.csv` and counted in `n_failed` /
    `failed_traits`, never raised as a tool error;
  - returns `experiment`, `source`, `n_samples` (rows in the consumed cleaned frame),
    `n_traits_requested`, `n_traits_reported`, `n_failed`, `failed_traits`, `stats_per_trait`
    (≤50), `truncated_in_summary`, `omitted_traits`, `nonfinite_stat_traits`, plus the inherited
    `RunLinks` fields.
- **REGISTER** `descriptive_stats` in `sections/sleap_roots/__init__.py` (import + `register(...)`
  call) and update that module's docstring + `server.py`'s module docstring tool-list line (both
  currently enumerate the 6 analysis consumers + 5 plotting tools by name).
- **LEAVE** the vendored `bloom_mcp.data_cleanup`/`stats`-adjacent code and any remaining Phase-1
  surface untouched — there is none left for descriptive stats specifically (the workflow was
  fully retired by #438, not partially).
- Tests cover the standard contract patterns (`tools/list` presence, schema round-trip, provenance
  + links, structured errors, delegation pinning) plus an **independently-computed golden**: unlike
  PCA/clustering/heritability (whose goldens are "characterization snapshots" pinned against #120's
  own recorded metadata), `calculate_trait_statistics`'s output is off-the-shelf pandas/SciPy
  arithmetic — directly hand-verifiable, not merely a drift gate.
- **EXTEND** the live persistence smoke (`make bloommcp-smoke`) with a `descriptive_stats` leg
  through the real `SupabaseReader`/`SupabaseResultStore`, and document it in
  `bloommcp/docs/local-validation.md` alongside the existing Leg 1–3 sections + a Claude dogfood
  row.

## Impact

- **Affected specs:** `bloommcp-descriptive-stats-tool` (new capability). Builds on (does not
  modify) `bloommcp-tool-contract`, `bloommcp-experiment-read`, `bloommcp-result-store`, and the
  `bloommcp-qc-clean-tool` producer.
- **Affected code:**
  - new `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/descriptive_stats.py` (tool + I/O
    models);
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/__init__.py` (import + register + docstring);
  - `bloommcp/src/bloom_mcp/server.py` (module-docstring tool-list line only — no per-tool wiring,
    sections own registration);
  - `bloommcp/tests/test_sections_scaffold.py`, `bloommcp/tests/test_devendor_invariants.py`, and
    `bloommcp/tests/test_persistence_import_guard.py` — all three hardcode the analysis section's
    tool-name list and need `descriptive_stats` added. `test_devendor_invariants.py`'s
    `test_expected_tool_surface` and `test_persistence_import_guard.py`'s `_CONSUMERS` list are
    **already stale today** (both pre-date `umap_analysis`, #425, and neither is exhaustive against
    the live registry, so the omission doesn't fail CI) — this change is a convenient place to fix
    that pre-existing drift too, but the pre-existing gap is not this proposal's bug;
  - new `bloommcp/tests/tools/test_descriptive_stats_tool.py`;
  - new `bloommcp/tests/smoke/test_descriptive_stats_smoke.py` — the per-tool live-smoke test every
    other granular tool already has (`test_pca_analysis_smoke.py`, `test_clustering_smoke.py`,
    etc.), collected by CI's `dev-stack-smoke` job (`pytest tests/smoke/ -m "live_smoke and not
    live_smoke_slow"`) — a distinct mechanism from `live_persistence_smoke.py` below, and the one
    piece of test coverage every sibling tool has that this change must not skip;
  - new golden fixture `bloommcp/tests/fixtures/turface_19_stats_golden.json` (independently
    computed via `calculate_trait_statistics` on the **canonical-default** `qc_clean` output of the
    existing `turface_19_raw_data.csv` — 158 samples / 19 kept traits, the same pre-trim clean
    `remove_outliers`'s golden documents — no new raw fixture needed) + a `fixtures/README.md` entry;
  - `bloommcp/tests/smoke/live_persistence_smoke.py` + `tests/scripts/
    test_live_persistence_smoke_logic.py` — a `descriptive_stats` leg + its pure-helper unit tests;
  - `bloommcp/docs/local-validation.md` (a new leg section + a Claude dogfood row).
- **Dependencies:** `sleap_roots_analyze.calculate_trait_statistics`, already available in the
  pinned `>=0.1.0a5` — no pin change.
- **Branch/PR:** branches off `origin/staging`; PR targets `staging` (closes #488).
