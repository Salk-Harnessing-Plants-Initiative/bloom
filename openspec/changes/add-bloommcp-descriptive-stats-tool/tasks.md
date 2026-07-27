> **TDD note:** the RED steps below (§2–§3) are a *local* working-tree rhythm — confirm each
> fails, then make it pass. Do **not** push a RED-only commit: CI (`pr-checks.yml`,
> `cd bloommcp && uv run --frozen --extra test pytest tests/`) gates the PR head, and a committed
> failing/**uncollectable** test (importing `descriptive_stats` before it exists) is red. Commit
> RED+GREEN together.
>
> **Commit plan** (no dependency commit — `sleap-roots-analyze>=0.1.0a5` already carries
> `calculate_trait_statistics`). Every commit ends with
> `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`:
> 1. `docs(#488): openspec proposal — granular descriptive_stats tool` — the
>    `openspec/changes/…` artifacts (green; run `openspec validate --strict` locally).
> 2. `chore(#488): add turface_19 stats golden fixture` — the golden JSON + `fixtures/README.md`
>    entry (green; inert JSON, no lock change, deliberately orphaned for exactly one commit).
> 3. `feat(#488): add granular descriptive_stats MCP tool` — tool + I/O models + section
>    registration + **all** §2–§3 tests (green; RED+GREEN atomic).
> 4. `test(#488): add descriptive_stats live smoke leg + local-validation docs` — §6 (green via
>    the `dev-stack-smoke` job; kept separate — different CI job, different failure domain).
> Expect 1–2 reactive `fix(#488): address review` commits after `/review-pr`. Single PR →
> `staging`.

## 1. Pre-work — stats golden (prerequisite)

- [x] 1.1 Confirm the delegate is importable on the pinned lock: `uv run --frozen python -c "from
      sleap_roots_analyze import calculate_trait_statistics"` resolves against `>=0.1.0a5` (no pin
      change needed).
- [x] 1.2 Record the **stats golden** into `bloommcp/tests/fixtures/turface_19_stats_golden.json`:
      run `sleap_roots_analyze.clean_traits_for_analysis` on `turface_19_raw_data.csv` with the
      role columns `barcode_col="Barcode", genotype_col="geno", replicate_col="rep"` and the
      **canonical-default** thresholds (`max_zeros_per_trait=0.5, max_nans_per_trait=0.2,
      max_nans_per_sample=0.0, min_samples_per_trait=10` — same `_CANONICAL_*` constants
      `qc_clean`/`_qc_shared.py` uses), which should yield **158 samples, 19 kept trait columns**
      (all candidate traits survive the looser 0.2 threshold; only `Computation.Time.s` was
      excluded upstream as non-trait metadata). Then call
      `calculate_trait_statistics(cleaned_df, kept_cols)` directly (not through the tool) and
      record the full per-trait dict, `cleaning_params`, `cleaned_samples`, and
      `kept_trait_columns` in the golden JSON, plus a
      `_reproduced_by_sleap_roots_analyze_version` key. Sanity-check against the values already
      pinned in `design.md`/`spec.md` (e.g. `Shoot_Biomass_mg`: `mean=158.2860759493671,
      std=44.96525972299035, cv=0.28407590151754053, skewness=-0.44548624755816857`;
      `Root_Shoot_Ratio`: `skewness=6.782113998908719, kurtosis=65.62876947585153`) — these were
      independently computed against `sleap-roots-analyze==0.1.0a5` while drafting this proposal,
      so a mismatch on re-generation means the pinned analyze version or the cleaning recipe
      drifted, not a typo to silently "fix" in the golden. Document it in `tests/fixtures/README.md`
      next to `turface_19_outlier_golden.json`, explicitly noting the same 158-sample
      canonical-default clean is reused (not re-derived) so the two goldens' provenance stays
      legible together.
- [x] 1.3 Verify no kept trait in this fixture has `mean == 0` or zero variance (the golden won't
      exercise the non-finite-coercion path) — confirmed already (`stats_per_trait` has no
      inf/nan entries); the non-finite path is covered by a synthetic test instead (§3.9).

## 2. RED — golden stats through the tool (north star, write first)

- [x] 2.1 Add `bloommcp/tests/tools/test_descriptive_stats_tool.py`; wire `_ports.configure(...)`
      with a `FakeReader` serving the canonical-default cleaned turface_19 frame (158 samples, via
      `add_cleaned_version`, seeded from the cleaned snapshot behind task 1.2) and a
      `FakeResultStore`.
- [x] 2.2 Write the **golden-through-the-tool** test FIRST: invoke `descriptive_stats` with no
      `trait_columns` override; assert the `Shoot_Biomass_mg` entry in `stats_per_trait` matches
      the recorded golden (`n`, `mean`, `std`, `median`, `q25`, `q75`, `min`, `max`, `cv`,
      `skewness`, `kurtosis`, each within `abs=1e-9`), and the `Root_Shoot_Ratio` entry's
      `skewness`/`kurtosis` match within `abs=1e-6` (unmodified — not clipped/flagged). Confirm it
      fails (no tool yet).
- [x] 2.3 Assert every reported trait's `n == 158` — no silent sample loss via the delegate's
      per-trait `dropna()`. Confirm RED.
- [x] 2.4 Assert `n_traits_requested == n_traits_reported == 19`, `n_failed == 0`,
      `failed_traits == []`. Confirm RED.

## 3. RED — the remaining contract patterns + composition

- [x] 3.1 `tools/list` presence: a FastMCP `Client` lists `descriptive_stats` with an input schema.
- [x] 3.2 Schema round-trip: valid input/output validate; an invalid input (missing `experiment`)
      → `BloomMCPError` (`invalid_input`).
- [x] 3.3 Provenance + links: a successful call stamps `Provenance` with tool name, the selected
      trait columns, and `seed = None`; the persisted `StoredRun` for `(experiment, "stats")`
      carries the same provenance, `based_on_version == frame.source`, and includes `stats.csv`.
      Assert the result references it via a `resource_link` (object key + manifest path), and
      that reloading the committed `stats.csv` bytes parses with one row per certified trait.
- [x] 3.3b Source label + bounded-payload guard (matching `test_pca_analysis_tool.py`'s
      `test_consumes_cleaned_version_source` / size-guard pattern, both absent from this tool's
      initial plan): assert `result.source == "v1_cleaned"` and `!= "raw"` on a successful call; and
      `dumped = result.model_dump(); assert not any(isinstance(v, (list, dict)) and len(str(v)) >
      5000 for v in dumped.values())` so a future cap-logic refactor can't silently reintroduce a
      large inline blob.
- [x] 3.4 Delegation pinning (spy on `calculate_trait_statistics`): assert `descriptive_stats`
      calls it exactly once with the resolved trait columns, and never re-implements any statistic
      itself.
- [x] 3.5 Guardrail — un-cleaned input: a `FakeReader` that raises `CleanedVersionRequiredError` on
      `require_clean=True` → `BloomMCPError(code="tool_error")` with a "run qc_clean first"
      remedy, no persisted run. (Genuinely mirrors `pca_analysis`/`clustering`'s own handling of
      this exact case — both use `code="tool_error"`, confirmed by reading the source; do **not**
      use `assumption_violated`, which is `remove_outliers`'s choice for this guard, not
      `pca_analysis`'s.)
- [x] 3.6 Trait-selection validation (reusing `_validate_trait_subset(require_certified=True)`
      unchanged — assert it's the same helper `pca_analysis`/`clustering` import, not a
      reimplementation): an unknown column, a non-certified numeric column, a literal non-numeric
      identifier column (e.g. `trait_columns=["Barcode"]`), an empty list, and duplicate columns
      each → `BloomMCPError(invalid_input)` naming the offending column(s), no run persisted. An
      explicit valid subset narrows `stats_per_trait` to just those traits.
- [x] 3.7 Determinism: two identical calls produce identical `stats_per_trait` values (within
      `abs=1e-9`).
- [x] 3.8 50-trait cap: seed a `FakeReader` cleaned frame with 60 certified traits (synthetic,
      constant-free numeric columns); assert `stats_per_trait` has exactly 50 entries,
      `truncated_in_summary == True`, `omitted_traits` equals exactly the 10 trait names beyond the
      cap (in the resolved order), and the persisted `stats.csv` has 60 rows. A second frame with
      ≤50 traits asserts `truncated_in_summary == False`, `omitted_traits == []`, and the inline
      list is complete.
- [x] 3.9 Non-finite coercion (synthetic, not the golden — the golden's 19 real traits have no
      zero-mean/zero-variance trait): a `FakeReader` cleaned frame with one certified trait whose
      values are all equal to a positive constant (zero variance, non-zero mean → `skewness`/
      `kurtosis` are `nan` — reachable only via a hand-crafted frame that bypasses `qc_clean`'s own
      zero-variance filter, never through real `qc_clean` output) and one whose values sum to a
      mean of exactly 0 (`cv` is `inf` — genuinely reachable through real `qc_clean` output, since
      no cleanup step excludes a zero-mean trait); assert both fields are `None` in
      `stats_per_trait` **and** in the reloaded `stats.csv` (empty cell), never a raw `inf`/`nan`
      token, both trait names appear in `nonfinite_stat_traits`, and the call does not raise.
- [x] 3.9b Finiteness re-verification guard (mirrors `pca_analysis`'s own `np.isfinite` guard): a
      `FakeReader` cleaned frame where one certified trait carries a residual `NaN` (simulating a
      reader/`qc_clean`-invariant violation, since nothing but `qc_clean`'s own write-time guard
      normally prevents this) → `BloomMCPError(code="assumption_violated")`, no persisted run, and
      `calculate_trait_statistics` is never called (the finiteness check runs before delegating, so
      the delegate never silently returns an under-counted `n`).
- [x] 3.10 Delegate-failure defense-in-depth: monkeypatch `calculate_trait_statistics` to return
      `{"error": "No valid data"}` for one requested trait; assert that trait is excluded from
      `stats_per_trait`/`stats.csv`, `n_failed == 1`, `failed_traits == [<trait>]`, and the call
      still succeeds (no raised error, run still persisted for the remaining traits). Also cover the
      delegate omitting a requested trait from its result dict entirely (rather than returning an
      `"error"` key) — the `results.get(trait)` defense in task 4.4 should route this the same way,
      not raise a `KeyError`/`internal_error`.
- [x] 3.11 Composition (via the Supabase-adapter harness over `_InMemoryObjectStore`, mirroring
      `pca_analysis`'s/`remove_outliers`'s pattern): after `qc_clean` commits a cleaned version, a
      `descriptive_stats` run persists and its `stats.csv` is downloadable and parses with the
      certified trait count.
- [x] 3.12 Second run increments version: two `descriptive_stats` runs on the same experiment →
      `v<N>`, `v<N+1>`, both independently readable.

## 4. GREEN — implement the tool

- [x] 4.1 Add `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/descriptive_stats.py`:
      `DescriptiveStatsParams(BaseModel)` (`experiment: str`; optional `trait_columns:
      list[str] | None`; optional `user_label`) and a `TraitStatistics(BaseModel)` nested model
      (`trait: str`, `n: int`, `mean/std/median/q25/q75/min/max/cv/skewness/kurtosis:
      Optional[float]`) and a `DescriptiveStatsResult(RunLinks)` output model (`experiment`,
      `source`, `n_samples`, `n_traits_requested`, `n_traits_reported`, `n_failed`,
      `failed_traits: list[str]`, `stats_per_trait: list[TraitStatistics]`,
      `truncated_in_summary: bool`, `omitted_traits: list[str]`,
      `nonfinite_stat_traits: list[str]`).
- [x] 4.2 Implement `descriptive_stats(params, *, provenance)` wrapped by `@as_mcp_tool`
      (declares **only** `provenance` — no `random_state`): load the **cleaned** frame via
      `_ports.reader().load_experiment(name, require_clean=True)`. Map errors in the **tool body**:
      `except CleanedVersionRequiredError → raise BloomMCPError(code="tool_error", remedy="run
      qc_clean first")` — this genuinely mirrors `pca_analysis.py`/`clustering.py`'s own handling
      of this exact guard (both use `code="tool_error"`; do not use `assumption_violated`, which is
      `remove_outliers`'s choice for this case, not `pca_analysis`'s — verify against the actual
      source before implementing, not from memory).
- [x] 4.3 Resolve `trait_cols`: `frame.trait_cols` if `params.trait_columns is None`, else
      `_validate_trait_subset(frame, params.trait_columns, params.experiment,
      require_certified=True)` then use the caller's list. Then re-verify finiteness (mirrors
      `pca_analysis`'s own guard): `if not np.isfinite(frame.df[trait_cols].to_numpy(dtype=float)).all():
      raise BloomMCPError(code="assumption_violated", remedy="re-run qc_clean to produce a
      finite-valued cleaned version")` — before delegating, so a residual-NaN certified trait never
      silently under-counts `n` via the delegate's own per-trait `dropna()`.
- [x] 4.4 Delegate: `results = calculate_trait_statistics(frame.df, trait_cols)`. For each trait in
      `trait_cols` order: if `results.get(trait) is None` or `"error" in results[trait]`, append to
      `failed` (the `None` branch guards a trait the delegate's dict omits entirely, not just an
      explicit `"error"` key — defense-in-depth, not expected to be reachable given task 4.3's
      finiteness guard); else build a `TraitStatistics` row, applying the non-finite → `None`
      coercion (a small `_finite_or_none(x)` helper: `float(x) if math.isfinite(x) else None`) to
      every numeric field and recording the trait name in `nonfinite_stat_traits` if any field was
      coerced. No statistics math of its own.
- [x] 4.5 Cap the inline list: `stats_per_trait = rows[:_SUMMARY_TRAIT_CAP]` (`= 50`),
      `truncated_in_summary = len(rows) > _SUMMARY_TRAIT_CAP`,
      `omitted_traits = [r["trait"] for r in rows[_SUMMARY_TRAIT_CAP:]]`.
- [x] 4.6 Persist via `_ports.store().create_run(experiment=…, tool_class="stats",
      provenance=provenance.model_copy(update={"based_on_version": frame.source}),
      user_label=…, source_csv=<snapshot_frame(frame.df)>)`: write **all** `rows` (uncapped, same
      non-finite coercion) to `stats.csv` (columns `trait, n, mean, std, median, q25, q75, min,
      max, cv, skewness, kurtosis` — matches the legacy layout) → `commit(...)`.
- [x] 4.7 Register the tool: confirm from the actual current source that `sections/sleap_roots/
      analysis/*.py` tools have **no per-module `register()`** function — registration is
      centralized in `sections/sleap_roots/__init__.py`'s single `register(section, ...)` call
      (verified against `pca_analysis.py`, which has none) — so `descriptive_stats.py` needs no
      `register(mcp)` function of its own; skip straight to task 4.8's wiring.
- [x] 4.8 Wire into `bloommcp/src/bloom_mcp/sections/sleap_roots/__init__.py`: import
      `descriptive_stats` alongside the other analysis modules, add
      `descriptive_stats.descriptive_stats` to the `register(section, ...)` call, and update the
      module docstring's "6 granular ... consumers" line to 7 (name it in the list).
- [x] 4.9 Update `bloommcp/src/bloom_mcp/server.py`'s module docstring tool-list line (currently
      `qc_clean, qc_inspect, pca_analysis, remove_outliers, clustering, umap_analysis, + 5
      plotting tools`) to include `descriptive_stats`.
- [x] 4.10 Update the three files that hardcode the analysis section's tool-name list to include
      `descriptive_stats`: `bloommcp/tests/test_sections_scaffold.py`,
      `bloommcp/tests/test_devendor_invariants.py` (`test_expected_tool_surface`), and
      `bloommcp/tests/test_persistence_import_guard.py` (`_CONSUMERS`). The latter two are
      **already stale today** — both pre-date `umap_analysis` (#425) and neither list is checked
      for exhaustiveness against the live tool registry, so the omission doesn't fail CI on its
      own. While touching these files for `descriptive_stats`, also add the missing `umap_analysis`
      entry to both, closing that pre-existing gap rather than leaving it for a future PR to
      rediscover.
- [x] 4.11 Run the suite; debug to GREEN **without** weakening the golden.

## 5. Refactor & verify

- [x] 5.1 Refactor for clarity; confirm the server still boots (`uv run python -c "import
      bloom_mcp.server"` clean with the new tool registered) and no other Phase-1/vendored module
      is touched.
- [x] 5.2 `/pre-merge`: `black --check` + `ruff check` over `bloommcp/`; full bloom-mcp suite
      (`uv run --frozen --extra test pytest tests/`); `uv lock --check` (proves the lock was not
      accidentally touched) + `python scripts/check-uv-locks.py`; `import bloom_mcp.server` boot;
      `openspec validate add-bloommcp-descriptive-stats-tool --strict` — all green.
- [ ] 5.3 Validate on **Claude Desktop**: `descriptive_stats` is selectable after `qc_clean`,
      produces a per-trait summary + a `stats.csv` link, and a wide experiment (cylinder) visibly
      truncates with `truncated_in_summary=true`. **Not yet done** — no dev stack / Claude Desktop
      session available in this environment; tracked as the unchecked dogfood row added to
      `docs/local-validation.md`'s manual checklist.

## 6. Live persistence smoke leg + per-tool smoke test + local-validation docs

- [x] 6.0 Add `bloommcp/tests/smoke/test_descriptive_stats_smoke.py`, mirroring
      `test_pca_analysis_smoke.py`: `pytestmark = pytest.mark.live_smoke`; call
      `sleap_roots_qc_clean` then `sleap_roots_descriptive_stats` via the shared `call_tool`/
      `seeded_experiment` fixtures (parametrized over both `turface_19` and `cylinder` for free);
      assert `n_traits_reported > 0`, a resolvable `run_ref`/`manifest_path`, and — on the
      `cylinder` parametrization only — `truncated_in_summary is True` (exercising the 50-cap for
      real, not only via the synthetic §3.8 test). This is the per-tool smoke file every other
      granular tool already has and CI's `dev-stack-smoke` job collects
      (`pytest tests/smoke/ -m "live_smoke and not live_smoke_slow"`) — distinct from, and in
      addition to, the `live_persistence_smoke.py` leg below.
- [x] 6.1 Add a `descriptive_stats` leg to `bloommcp/tests/smoke/live_persistence_smoke.py`,
      through the **real** `SupabaseReader`/`SupabaseResultStore`: after the existing `qc_clean`
      leg commits a cleaned version, run `descriptive_stats(experiment=…)`, then assert the
      committed outputs include `stats.csv`, the manifest is `manifest_schema_version == 3`, and
      each recorded `output_sha256` matches the actual stored bytes. Assert **structural**
      invariants (one row per certified trait, `n_failed == 0`) rather than the exact unit-golden
      numeric values (the smoke's cleaned input uses the `qc_clean` leg's own threshold, which may
      differ from the unit golden's canonical-default clean).
- [x] 6.2 Add pure-logic unit tests for the new smoke helpers to
      `bloommcp/tests/scripts/test_live_persistence_smoke_logic.py`, matching the existing
      no-live-stack pattern.
- [x] 6.3 Add a `descriptive_stats` leg section to `bloommcp/docs/local-validation.md` (following
      the existing Leg 1–3 sections: what it asserts, how to run) and a Claude dogfood row (run
      `qc_clean` → `descriptive_stats`, capture the per-trait summary + the `truncated_in_summary`
      behavior on a wide experiment).
- [ ] 6.4 Re-run `make bloommcp-smoke` (all legs green) and the smoke helper unit tests. **Not yet
      done** — no running dev stack in this environment; the pure-logic unit tests (§6.2) are
      green, and the leg's code has been syntax-checked and reviewed, but the actual live-stack
      run against Supabase/MinIO is still outstanding.

## 7. Follow-ups (out of this change's scope — tracked, not done here)

- [ ] 7.1 `analyze_trait_variance` remains unexposed by any MCP tool — a future issue's scope
      (confirmed in the proposal, not silently folded in here).
- [ ] 7.2 A group-comparison workflow wrapping `perform_anova_by_genotype` stays a separate,
      not-yet-scoped future tool — not this one.
