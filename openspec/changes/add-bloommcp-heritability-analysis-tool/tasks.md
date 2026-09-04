> **TDD note:** the RED steps below are a *local* working-tree rhythm — confirm each fails, then
> make it pass. Do **not** push a RED-only commit. The gate on the PR head is `python-audit`'s
> `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke" -v --tb=short`
> (quoted exactly, because every greenness claim below depends on which markers it deselects — e.g.
> §7.7's `test_oracle.py` heritability tests are `integration`-marked and do not run in CI at all).
> A committed failing or **uncollectable** test is red. Commit RED+GREEN together.
>
> **Commit plan.** Every commit ends with
> `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
> §1's verifications run **before** C1, because C1 ships design.md's D2/D8 claims as fact.
>
> | # | Message | CI after |
> | --- | --- | --- |
> | C1 | `docs(#462): openspec proposal — heritability_analysis tool, retire the 2 plot tools` | 🟢 (no openspec job in CI; validate locally) |
> | C2 | `chore(#462): add turface_19 per-trait heritability golden` | 🟢 inert JSON; nothing enumerates `tests/fixtures/`; no lock touch |
> | C3 | `feat(#462): support paginated plotter returns in _plots.generate_figures` | 🟢 additive; §5 only |
> | C4 | `feat(#462): add heritability_analysis, an H²-as-data tool with optional plots` | 🟢 both retired tools still live; §2–§4 + §6 |
> | C5 | `feat(#462)!: retire plot_heritability_bar and plot_variance_decomposition` | 🟢 §7 — every presence assertion **and every module-level import** of the two modules disappears here |
> | C6 | `test(#462): heritability_analysis live smoke + local-validation docs` | 🟢 §8.1/§8.4 — `live_smoke`, deselected by `python-audit` |
> | C7 | `test(#462): heritability_analysis leg in live_persistence_smoke` | 🟡 §8.2/§8.3 — **actually runs** in `dev-stack-smoke` via `make bloommcp-smoke`; split out so a red bisects to one commit |
>
> **C4/C5 split rationale** (the previous single-commit plan was too large): the three roster
> guards are all *subset* or *set-membership* checks — `test_sections_scaffold.py` loops
> `assert f"sleap_roots_{tool}" in tools` over `expected`; `test_devendor_invariants.py` asserts
> `live & relevant == expected` where `relevant = expected | not_expected`, so a name in neither
> set is invisible; `test_list_existing_analyses_staleness.py` needs only `TOOL_CLASSES` and
> `_TOOL_CLASS_TO_PUBLIC_NAME` to land together. So **adding** is green while both retired tools
> still exist, and only the **deletion** reds them. C4 and C5 are a rollback pair — revert
> together, or squash-merge so the breaking marker lands once on `staging`.
>
> **Rebase note — three open PRs touch these files.** Land this change **last**.
> - **#726** (`egao28/bloommcp-plot-guards-721`) edits `_plots.py` **and both retired modules**.
>   Mitigation: land **C3 as its own small PR first**, so #726 rebases onto a ~10-line addition
>   instead of the reverse. When merging: keep #726's `FIGURE_REGISTRY_LOCK` + allocate-then-raise
>   `plt.get_fignums()` diff, apply this change's list expansion to `fn()`'s return *inside* it,
>   and keep record-all-pages-then-style (design D6). Its edits to the two doomed modules become
>   `CONFLICT (modify/delete)` — resolve by **deleting**, never `--theirs`. Its lock docstring
>   enumerating "the 5 legacy `plot_*` tools" must be corrected by whoever lands second.
> - **#724** (`egao28/bloommcp-plot-snapshot-tests-713`) imports **both retired modules at module
>   level** in `tests/tools/test_viz_snapshot.py` and `scripts/gen_plot_snapshots_golden.py`, and
>   commits baseline PNGs for both. If it lands first and §7.14 is skipped, `python-audit` fails at
>   **collection** — the whole bloommcp suite. See §7.14.
> - **#683** (`egao28/bloommcp-converge-viz-tools-466`) rewrites `test_viz_tools.py` and edits
>   `_viz_shared.py`, `sections/sleap_roots/__init__.py`, `test_sections_scaffold.py`,
>   `test_devendor_invariants.py`, `tests/smoke/conftest.py`, `list_existing_analyses.py`. It is
>   doing the #466 consolidation this proposal lists as out of scope — rebase onto it.
>
> **Housekeeping:** the working tree carries six unrelated modified `test_data/*.csv` files — keep
> them out of every commit here. Pre-commit's `prettier` hook matches `*.json` and **will**
> reformat the new golden; run `pre-commit run -a` after generating it, before committing.

## 1. Pre-work — verify the assumptions design.md asserts (do these BEFORE C1)

- [x] 1.1 Confirm every delegate is importable on the pinned lock:
      `uv run --frozen python -c "from sleap_roots_analyze import HeritabilityResult,
      TraitHeritability, create_heritability_plot, create_variance_decomposition_plot;
      from sleap_roots_analyze.statistics import calculate_heritability_estimates,
      compare_trait_heritabilities"` resolves against `>=0.1.0a5` (no pin change needed).
- [x] 1.2 Record `bloommcp/tests/fixtures/turface_19_heritability_golden.json`: clean
      `turface_19_raw_data.csv` with `clean_traits_for_analysis` at the **canonical-default**
      thresholds (`max_zeros_per_trait=0.5, max_nans_per_trait=0.2, max_nans_per_sample=0.0,
      min_samples_per_trait=10` — the `_CANONICAL_*` constants `qc_clean`/`_qc_shared.py` use),
      yielding **158 samples / 19 kept traits** (the identical pre-trim clean
      `turface_19_stats_golden.json` and `turface_19_outlier_golden.json` document — reuse it, do
      not re-derive a different one). Then call `calculate_heritability_estimates(cleaned_df,
      kept_cols, genotype_col="geno", replicate_col="rep")` **directly, not through the tool**, and
      record: the full per-trait dict (`heritability`, `var_genetic`, `var_residual`,
      `n_genotypes`, `n_observations`, `model_type`), `method_used_for_all_traits`, `mean_h2`,
      `n_above_0.5`, the `cleaning_params` / `cleaned_samples` / `kept_trait_columns` keys the
      sibling goldens carry, a `_reproduced_by_sleap_roots_analyze_version` key, and a `_source`
      key labeling it a **characterization snapshot** of that version on this fixture.
      Sanity-check against the values recorded while drafting this proposal: **19/19 scored, 0
      failed**, `method="mixed_model"`, `mean_h2 ≈ 0.4780923914803235`, `n_above_0.5 = 8`. A
      mismatch means the pinned analyze version or the cleaning recipe drifted — do not silently
      "fix" the golden.
- [x] 1.3 Add the golden's entry to `bloommcp/tests/fixtures/README.md` next to
      `turface_19_stats_golden.json`, noting (a) the 158-sample canonical-default clean is
      **reused, not re-derived**, and (b) that it is a drift gate, not an independently validated
      H² — mirroring the caveat already recorded for `turface_19_pca_golden.json`'s
      `heritability_mean`. Record that `Lower.Root.Area.mm2` sits at H² ≈ 7.7e-09, which is why the
      tests need an absolute tolerance floor (§2.2).
- [ ] 1.4 **Not done — no dev stack in the implementing environment.** Verify no legacy runs
      exist under tool class `"heritability"` for any experiment this
      tool will touch (`store.list_runs(experiment, "heritability")` against the dev stack, per
      seeded fixture). Design D8 asserts a fresh lineage — this confirms it rather than assuming.
      **If no dev stack is available**, leave this unchecked and downgrade D8's wording to a stated
      assumption before shipping C1, rather than letting §1 block §2.
- [x] 1.5 Confirm from the **actual current source** (not memory) that `pca_analysis.py` and
      `clustering.py` both use `code="tool_error"` for the missing-cleaned-version guard, and that
      `remove_outliers.py` uses `assumption_violated` for its own — D2 depends on it.

## 2. RED — the golden through the tool (north star, write first)

- [x] 2.1 Add `bloommcp/tests/tools/test_heritability_analysis_tool.py`; wire `_ports.configure(...)`
      with a `FakeReader` serving the canonical-default cleaned turface_19 frame (158 samples,
      19 traits, `genotype_col="geno"`, `replicate_col="rep"`, via `add_cleaned_version`) and a
      `FakeResultStore`. Reuse the existing fakes — do not fork new ones.
- [x] 2.2 Write the **golden-through-the-tool** test FIRST: invoke `heritability_analysis` with no
      `trait_columns` override and the default `threshold=0.5`; assert every recorded trait's `h2`
      matches the golden with `pytest.approx(golden, rel=1e-5, abs=1e-6)` — `rel` kept in sync with
      `tests/test_oracle.py::_H2_TOL` (currently `1e-5`), with a comment recording that
      `Lower.Root.Area.mm2` sits at ~7.7e-09, which is why the absolute floor is required and why a
      bare `rel=1e-6` (an earlier draft's value) would be both tighter than the repo's own
      heritability tolerance and meaningless at that magnitude. Assert `method` matches the
      golden's recorded method and `n_above_threshold` equals the recorded discrete count — the
      optimizer-robust guard `test_oracle.py` uses for the same reason. Confirm RED.
- [x] 2.3 Assert `var_genetic` / `var_residual` / `n_genotypes` / `n_observations` / `model_type`
      for at least two named traits match the golden — the keys the variance-decomposition figure
      consumes, i.e. `bloommcp-packaging`'s "no silent zero-fill" obligation on the happy path.
      Confirm RED.
- [x] 2.4 Assert `n_traits_requested == n_traits_reported == 19`, `n_failed == 0`,
      `failed_traits == []`, `nonfinite_traits == []`, and `mean_h2` matches the golden at the same
      tolerance as 2.2. Confirm RED.
- [x] 2.5 Determinism: two identical calls in one process produce **bit-identical** `per_trait`
      `h2` values (verified reproducible; scope the docstring to same-process, same-input — this is
      not a cross-environment claim, which is what 2.2's tolerance is for). Confirm RED.

## 3. RED — the standard contract patterns + guards

- [x] 3.1 `tools/list` presence: a FastMCP `Client` lists `sleap_roots_heritability_analysis` with
      a non-null input schema, and its description names both retired tools plus the `plots` key
      that reproduces each figure (the migration path, per the spec's description scenario).
      **The absence half of this test lands in C5, not C4** — see 7.5b.
- [x] 3.2 Schema round-trip: valid input/output validate; missing `experiment` →
      `BloomMCPError(invalid_input)`; `threshold` of `1.5` and `-0.1` → `invalid_input`. Also
      assert PNG output keys survive a `model_dump_json()` / `model_validate()` round-trip when
      `include_plots=True` (mirrors `test_pca_analysis_tool.py`).
- [x] 3.3 Provenance + links: a successful call stamps `Provenance` with the tool name, the selected
      trait columns, and `seed = None`; the persisted `StoredRun` for `(experiment,
      "heritability")` carries the same provenance, `based_on_version == frame.source`, and its
      outputs include `heritability.csv` + `heritability_result.json`. Assert both are referenced
      by `resource_link`, that the committed CSV bytes parse with one row per scored trait, and
      that the committed JSON parses (proving `to_json()`'s `allow_nan=False` held).
- [x] 3.3b Source label + bounded payload: `result.source == "v1_cleaned"` and `!= "raw"`; and
      `dumped = result.model_dump(); assert not any(isinstance(v, (list, dict)) and len(str(v)) >
      5000 for v in dumped.values())`.
- [x] 3.3c Persisted-JSON content (not just parseability): assert its top-level key set is exactly
      `{method, threshold, per_trait, failed_traits, error}` (verified — `mean_h2` /
      `n_above_threshold` are `@property` and are **not** serialized), that `per_trait` is
      **uncapped** on a >50-trait frame rather than mirroring the 50-item inline list, that each
      entry carries all eight `TraitHeritability` fields, and that `threshold` equals the
      caller-supplied value.
- [x] 3.4 Delegation pinning (spy on `calculate_heritability_estimates`): called **exactly once**
      with the resolved trait columns and the frame's genotype/replicate roles — including with
      `include_plots=True` and **both** catalog keys (the consistency guarantee, §4.4). Assert the
      module contains no H² arithmetic of its own (no `mixedlm` / `cov_re` / `scale` reference).
- [x] 3.5 Un-cleaned input: a `FakeReader` raising `CleanedVersionRequiredError` on
      `require_clean=True` → `BloomMCPError(code="tool_error")` with a "run qc_clean first" remedy,
      no persisted run. (Matches `pca_analysis`/`clustering` per 1.5; **not** `assumption_violated`.)
- [x] 3.5b `version` selector (#626 pattern): the field exists on the input model; omitting it
      makes the reader call identical to today (no `version` kwarg); an explicit value is passed
      through to `load_experiment` and recorded as `based_on_version`.
- [x] 3.6 Trait-selection validation, reusing `_validate_trait_subset(require_certified=True)`
      unchanged (assert it is the same helper `pca_analysis`/`clustering` import, not a
      reimplementation): unknown column, non-certified numeric column, non-numeric identifier
      column, empty list, and duplicates each → `BloomMCPError(invalid_input)` naming the offending
      column(s), no run persisted. A valid explicit subset narrows `per_trait` **and** the delegate
      call's `trait_cols`.
- [x] 3.7 Genotype/replicate roles (D3): (a) `replicate_col=None` with a present `genotype_col` →
      the call **succeeds**, the delegate receives `replicate_col=None`, H² values are returned —
      the deliberate loosening, and (per D3) the only path a `SupabaseReader`-backed experiment
      has; (b) `genotype_col=None` → `BloomMCPError(assumption_violated)` naming the resolved
      roles, no run persisted; (c) (a)'s H² values equal the same frame's values with
      `replicate_col="rep"` supplied — pinning upstream's "replicate values never enter the model"
      claim rather than trusting the docstring. Build the role-less frames by dropping the `rep`
      (resp. `geno`) column from the seeded cleaned frame so `detect_columns` returns `None`.
- [x] 3.8 Run-level delegate error: monkeypatch the delegate to return `{"error": "Missing required
      columns: [...]"}` → `BloomMCPError`, no run persisted, the raw delegate string not echoed
      verbatim into the envelope.
- [x] 3.8b Declared-error mapping + no-leak (mirrors `test_pca_analysis_tool.py`'s block, absent
      from an earlier draft of this plan): (a) `store.fail_next_commit(exp, "heritability")` →
      `tool_error` naming a commit failure, no run persisted; (b) `store.fail_next_read(...)` →
      `tool_error` naming a manifest read failure; (c) `store.commit` raising `RunStateError` →
      `internal_error` (proves the `errors=` tuple was not widened to the `ResultStoreError` base);
      (d) monkeypatch `calculate_heritability_estimates` — then, separately,
      `compare_trait_heritabilities`, `create_heritability_plot`, and
      `create_variance_decomposition_plot` — to raise
      `RuntimeError("secret path /var/secrets/key and host db.internal")` → the `BloomMCPError`'s
      message and remedy contain neither `"/var"` nor `"db.internal"`, and
      `store.list_runs(exp, "heritability") == []`.
- [x] 3.9 Per-trait failure routing: monkeypatch the delegate to (a) return `{"error": ...}` for one
      trait, (b) **omit** a requested trait from its result dict entirely — each → that trait in
      `failed_traits`, `n_failed == 1`, remaining traits reported normally, run still persisted, no
      raise. (b) is the case `from_heritability_dict` cannot see; see 6.7.
- [x] 3.9b All traits fail: the delegate returns `{"error": ...}` for every requested trait → the
      call succeeds, a run **is** persisted, `n_traits_reported == 0`, `per_trait == []`,
      `failed_traits` names all of them, `heritability.csv` has a header and zero data rows,
      `heritability_result.json` parses, and **`mean_h2` is `None`, not `0.0`** (verified:
      `HeritabilityResult.mean_h2` returns `0.0` for an empty `per_trait`, which an agent would
      read as "heritability is zero").
- [x] 3.10 Non-finite routing (D5): monkeypatch the delegate to return, for separate traits, a
      non-finite `heritability`, `var_genetic`, and `var_residual` → each lands in `failed_traits`
      **and** `nonfinite_traits`, is absent from `per_trait` and the reloaded `heritability.csv`,
      `mean_h2` is finite, `heritability_result.json` is written and parses, and the call succeeds.
      Assert the delegate's returned dict object is **not mutated**. Docstring must record that
      this path is monkeypatch-only: `max(0, min(1, nan))` evaluates to `1`, so the delegate's own
      clamp cannot emit a non-finite `heritability` (D5's correction).
- [x] 3.10b Missing-key routing (D5(a) — the zero-fill guard `bloommcp-packaging` requires):
      monkeypatch the delegate to return a per-trait entry carrying `heritability` but **missing**
      `var_genetic` (then, separately, `var_residual`, then `n_genotypes`) → on the **default**
      `include_plots=False` path the trait is routed to `failed_traits` and never emitted with a
      `0.0` component in `per_trait`, in the reloaded `heritability.csv`, or in
      `heritability_result.json`. Pin the reason in the docstring: `from_heritability_dict` does
      `float(entry.get("var_genetic", 0.0))`, so the tool must validate key **presence**, not only
      finiteness, and D7's plot-path guard does not run here.
- [x] 3.11 50-trait cap: a `FakeReader` cleaned frame with 60 certified traits (synthetic,
      non-constant, ≥2 genotypes — measured ~0.25 s for 60 fits, so this is not accidentally slow);
      assert `per_trait` has exactly 50 entries, `truncated_in_summary is True`, `omitted_traits`
      equals exactly the 10 names beyond the cap in resolved order, and the persisted
      `heritability.csv` has 60 rows. A second frame with ≤50 traits asserts
      `truncated_in_summary is False`, `omitted_traits == []`, and a complete inline list.
- [x] 3.12 Composition + versioning (Supabase-adapter harness over `_InMemoryObjectStore`,
      mirroring `pca_analysis`): after `qc_clean` commits a cleaned version, a
      `heritability_analysis` run persists and its `heritability.csv` downloads and parses; two
      successive runs land at `v<N>` / `v<N+1>`, both independently readable.
- [x] 3.13 Discovery: `list_existing_analyses` reports a committed `heritability` run and, on a
      `list_runs` failure for that class, names the public tool `heritability_analysis`
      (bloom#664/#669 pattern).
- [x] 3.14 Cylinder-scale unit oracle (mirroring `test_descriptive_stats_tool.py`'s cylinder test —
      19 traits never exercises the scale-specific risks): seed the canonical-default cleaned
      cylinder frame (846 certified traits, `Geno`/`Rep` — verified detected) via
      `fake_supabase_storage`; assert `n_traits_reported == 846`, `n_failed == 0`,
      `len(per_trait) == 50`, `truncated_in_summary is True`, `len(omitted_traits) == 796`, and the
      real persisted `heritability.csv` has 846 rows in resolved trait order. With
      `plots=["create_heritability_plot"]`, assert `outputs` holds exactly
      `create_heritability_plot_page1.png` … `_page17.png` and `plt.get_fignums() == []` afterward.
      Budget ~11 s measured; mark `integration` if that is too slow for `python-audit` rather than
      dropping it. This is the only pre-PR coverage of the pagination path at real scale.
- [x] 3.15 `user_label` (slugged into the version dir; omitted-label case), and the one retained
      property-style invariant declared in the proposal's out-of-scope section: over several valid
      certified trait subsets, `n_traits_requested == n_traits_reported + n_failed` always holds.

## 4. RED — the folded-in plots

- [x] 4.1 Default path: `include_plots` omitted → no figure generated, `outputs` contains only
      `heritability.csv` + `heritability_result.json`, and `compare_trait_heritabilities` is never
      called.
- [x] 4.1b Import-cleanliness on the default path (the spec requires it; `test_pca_analysis_tool.py`
      has the pattern): with `monkeypatch.setitem(sys.modules, "matplotlib", None)`, a default
      `include_plots=False` call still succeeds.
- [x] 4.2 `plots` with `include_plots=False` is silently ignored (no error, no figure).
- [x] 4.3 Key validation before commit (reusing `validate_plot_keys` verbatim): `plots=[]`, an
      unknown key, and a duplicated key each → `BloomMCPError(invalid_input)` naming the offending
      value, with **no run committed** — assert via `store.list_runs(experiment, "heritability")
      == []` (the codebase idiom; `FakeResultStore` has no `commits` attribute). Assert
      `plt.get_fignums() == []` afterward.
- [x] 4.4 Plot/number consistency (the issue's headline oracle): with `include_plots=True` and both
      keys, spy on `create_heritability_plot` and `create_variance_decomposition_plot` and assert
      the heritability values they receive are the same values in `per_trait` and
      `heritability.csv` — and, per 3.4, that one delegate call produced them. Assert the dict
      handed to the plotters is the **scrubbed copy** (D5), not the raw delegate return.
- [x] 4.4b Documented ordering divergence (D1): on a >50-trait frame, assert the inline `per_trait`
      order is the resolved `trait_cols` order while `create_heritability_plot` receives values it
      sorts H²-descending — i.e. the inline top-50 and `_page1.png`'s 50 bars are legitimately
      different trait sets — and that the tool's description says so.
- [x] 4.5 Threshold forwarding (D4): with `threshold=0.7`, assert `create_heritability_plot`
      receives `threshold=0.7` **and** `create_variance_decomposition_plot` receives
      `threshold=0.7` (not its own `0.3` default), and that `passed_threshold` /
      `n_above_threshold` reflect `0.7`.
- [x] 4.6 Laziness + delegation of the comparison table: `plots=["create_heritability_plot"]` alone
      → `compare_trait_heritabilities` not called. `plots=["create_variance_decomposition_plot"]`
      alone → called exactly once, with the same frame, trait list, and (scrubbed) h2 dict the
      numbers came from, and the frame handed to the plotter **is** its return (after the
      documented NaN-row drop) — not a re-derived table.
- [x] 4.7 Variance-component guard (D7): a comparison frame where a **scored** trait has NaN
      `var_genetic`/`var_residual` → `BloomMCPError(assumption_violated)` naming the trait, no run
      persisted, no figure rendered. A frame where an **unscored** (NaN-`heritability`) trait is
      present → that row is dropped and the figure renders normally.
- [x] 4.7b Empty comparison frame: every trait fails and `create_variance_decomposition_plot` is
      requested → the run succeeds with no decomposition entry in `outputs` and the result names
      the unscored traits (the spec's "skips the figure and names the reason" scenario — a
      user-visible silent omission otherwise).
- [x] 4.8 Persistence + cleanup: with `include_plots=True`, each requested figure lands as a `.png`
      entry in the same run's `outputs`, and the staged bytes start with `b"\x89PNG"` (including
      across the paginated `_pageN.png` set). Every figure is closed on **all four** exit paths —
      success, an invalid plot key, a plotter raising mid-generation, and `commit` raising —
      asserted via `plt.get_fignums() == []`.
- [x] 4.9 Pagination end-to-end: a cleaned frame with >50 certified traits and
      `plots=["create_heritability_plot"]` → `outputs` contains
      `create_heritability_plot_page1.png` … `_pageN.png` (one per returned page), every page
      closed, and no `outputs` entry holds a non-`Figure`.

## 5. RED+GREEN — `_plots.generate_figures` learns `list[Figure]` (lands first, as its own PR)

- [x] 5.1 RED in `bloommcp/tests/tools/test_plots_helpers.py`: a resolved call returning a
      `list[Figure]` expands into `<key>_page1` … `<key>_pageN` (1-indexed) in the caller's
      `figures` dict; a resolved call returning a single `Figure` keeps **byte-identical** key
      naming (regression guard for `pca_analysis`/`umap_analysis`/`clustering`); an empty list
      produces no phantom entry; `close_figures` closes every page. Assert the branch is selected
      by `isinstance(result, list)`, **not** a duck-typed `__iter__` check — the existing helper
      tests pass string sentinels (`lambda: "fig_a"`) that an iterable check would shred into
      per-character pages.
- [x] 5.2 Implement the expansion inside `generate_figures`'s per-key loop. **Record every page
      into `figures` before styling any page** (a second pass over the just-added keys) — not a
      per-page record→style interleave. Design D6: with an interleave, a styling failure on page 3
      of 17 leaves pages 4–17 allocated by `fn()`, unrecorded, and unreachable by
      `close_figures`. Add a test that pins this: make `apply_font_style` raise on the 2nd page of a
      3-page return and assert all 3 are closed.
- [x] 5.3 Update `generate_figures`'s type hints and docstring to state the `Figure | list[Figure]`
      contract, naming `create_heritability_plot`'s `traits_per_page` pagination as the motivating
      case and `_viz_shared.save_plot_or_plots` as the precedent.
- [x] 5.4 Re-run the existing `pca_analysis` / `umap_analysis` / `clustering` plot tests unchanged —
      the backstop that this shared-module change is additive.

## 6. GREEN — implement `heritability_analysis`

- [x] 6.1 Add `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/heritability_analysis.py`:
      `HeritabilityAnalysisParams` (`experiment: str`; `version: str | None`;
      `trait_columns: list[str] | None`; `threshold: float = 0.5` with `ge=0.0, le=1.0`;
      `include_plots: bool = False`; `plots: list[str] | None`; `user_label: str | None`), a
      `TraitH2` row model (`trait`, `h2`, `passed_threshold`, `var_genetic`, `var_residual`,
      `n_genotypes`, `n_observations`, `model_type`), and `HeritabilityAnalysisResult(RunLinks)`
      (`experiment`, `source`, `n_samples`, `genotype_col`, `replicate_col`, `method`, `threshold`,
      `n_traits_requested`, `n_traits_reported`, `n_failed`, `failed_traits`, `nonfinite_traits`,
      `mean_h2: float | None`, `n_above_threshold`, `per_trait`, `truncated_in_summary`,
      `omitted_traits`).
      The **tool description** (not just the module docstring) must name the two retired tools and
      the `plots` key replacing each, and state the D1 ordering divergence — §3.1 asserts both.
      The module docstring states the delegation boundary, the `require_clean` rationale, the
      single-delegate-call guarantee, and D3's replicate loosening.
- [x] 6.2 Implement `heritability_analysis(params, *, provenance)` under `@as_mcp_tool` (declares
      **only** `provenance` — no `random_state`), `errors=(ExperimentReadError, CommitFailedError,
      ManifestReadError)`. Load via `_ports.reader().load_experiment(name, require_clean=True,
      **version_kwargs)`; map `CleanedVersionRequiredError` → `BloomMCPError(code="tool_error",
      remedy="run qc_clean first")` in the tool body (per 1.5).
- [x] 6.3 Reject a falsy `frame.genotype_col` with `BloomMCPError(code="assumption_violated")`
      naming the resolved roles; pass `frame.replicate_col` through unchanged (`None` included).
- [x] 6.4 Resolve `trait_cols`: `frame.trait_cols` when `params.trait_columns is None`, else
      `_validate_trait_subset(frame, params.trait_columns, params.experiment,
      require_certified=True)` then the caller's list.
- [x] 6.5 Delegate **once**: `h2_raw = calculate_heritability_estimates(frame.df, trait_cols,
      genotype_col=..., replicate_col=...)`. A run-level `{"error": <str>}` → `BloomMCPError` with
      a fixed, actionable message (do not echo the delegate string verbatim).
- [x] 6.6 Scrub into a **copy** (D5): for each per-trait entry, if any of `heritability`,
      `var_genetic`, `var_residual`, `n_genotypes`, `n_observations`, `model_type` is **absent**,
      or if any of the three numeric ones is **non-finite**, replace that entry in the copy with
      `{"error": ...}` — recording the trait in `nonfinite_traits` only for the non-finite case.
      Never mutate `h2_raw`. Then `result = HeritabilityResult.from_heritability_dict(scrubbed,
      params.threshold)`. The missing-key half is not optional: `from_heritability_dict` defaults
      `var_genetic`/`var_residual` to `0.0` and `n_genotypes` to `0`, so without it a renamed
      upstream key ships as a zero variance component on the default path.
- [x] 6.7 Build rows from `result.per_trait`, preserving resolved `trait_cols` order; cap at
      `_SUMMARY_TRAIT_CAP = 50` with `truncated_in_summary` / `omitted_traits`; take `method`,
      `mean_h2`, `n_above_threshold` from `result`. **`failed_traits` must be
      `result.failed_traits` UNION the requested trait_cols absent from the delegate's returned
      dict entirely** — `from_heritability_dict` iterates `d.items()`, so a trait the delegate
      omitted is invisible to it (verified: an omitted trait yields `failed_traits == []`). Set
      `n_failed = len(failed_traits)` and assert the invariant
      `n_traits_requested == n_traits_reported + n_failed`. Report `mean_h2` as `None` when
      `per_trait` is empty (§3.9b).
- [x] 6.8 Optional plots — validate keys and generate figures **before** `create_run`, wrapped in
      `try/finally` around the whole persistence region (mirroring `pca_analysis`'s figure/tempdir
      nesting). `_HERITABILITY_CATALOG_KEYS = frozenset({"create_heritability_plot",
      "create_variance_decomposition_plot"})`; import the plotters lazily inside the plot-call
      builder so the module stays matplotlib-free on the default path (§4.1b).
      **Compute `compare_trait_heritabilities`, apply D7's NaN-row drop, and raise D7's
      `assumption_violated` guard all BEFORE calling `generate_figures`** — capture only the
      finished `comparison_df` in the closure. Two reasons: the "no run committed on a bad frame"
      guarantee, and #726's process-wide `FIGURE_REGISTRY_LOCK`, which `generate_figures` holds
      across the whole loop (doing table work inside it would block every concurrent
      figure-creating call in the process). Skip the figure on an empty comparison frame (§4.7b).
      Pass `threshold=params.threshold` to **both** plotters. Do **not** route figures through
      `_viz_shared`'s save helpers — that lock is non-reentrant.
- [x] 6.9 Persist: `snapshot_frame(frame.df)` → `create_run(experiment=…, tool_class=_TOOL_CLASS
      ("heritability"), provenance=provenance.model_copy(update={"based_on_version":
      frame.source}), user_label=…, source_csv=…, source=frame.resolved_source)` → write
      `heritability.csv` (all scored traits, uncapped) + `heritability_result.json`
      (`result.to_json()`) → save each figure as `<key>.png` (or `<key>_page<N>.png`) → `commit`.
- [x] 6.10 Register: import in `sections/sleap_roots/__init__.py`, add to the single
      `register(section, ...)` call, update that module's docstring counts. Analysis modules carry
      no per-module `register()` — registration is centralized (confirm against `pca_analysis.py`).
- [x] 6.11 Add `heritability_analysis.py` to `bloommcp/tests/test_persistence_import_guard.py`'s
      `_CONSUMERS` list, or the new tool escapes the no-`supabase`/no-`AnalysisDir` import guard
      entirely. (That file's own comment records `umap_analysis` and `descriptive_stats` having
      been missed here for exactly this reason.)

## 7. GREEN — retire the two plot tools (C5)

- [x] 7.1 Delete `analysis/plot_heritability_bar.py` and `analysis/plot_variance_decomposition.py`;
      remove both imports and both `register(...)` entries from `sections/sleap_roots/__init__.py`.
- [x] 7.2 Delete `tests/smoke/test_plot_heritability_bar_smoke.py` and
      `tests/smoke/test_plot_variance_decomposition_smoke.py`.
- [x] 7.3 Update `tests/tools/test_viz_tools.py`: drop the two retired tools' cases and their module
      imports (currently at L29/L32) and their entries in the shared parametrized lists. **Keep**
      `save_plot_or_plots`'s own tests and
      `test_trait_batch_threshold_matches_heritability_plot_default` — the former still serves
      `plot_trait_histograms`/`plot_trait_boxplots`, and the latter pins the *upstream*
      `create_heritability_plot` default that `_viz_shared.TRAIT_BATCH_THRESHOLD` mirrors, which
      this change makes more load-bearing, not less.
- [x] 7.4 `_viz_shared.py`: update **both** module-docstring "5" references (the title line and the
      rationale line), plus `save_plot_or_plots`'s docstring — written entirely around
      `create_heritability_plot`, whose pagination now goes through `_plots.generate_figures`
      instead — and `TRAIT_BATCH_THRESHOLD`'s comment, whose named exemplar is no longer one of the
      tools it governs. Leave `save_plot`, `save_plot_or_plots`, `parse_traits`,
      `validate_filename`, `TRAIT_BATCH_THRESHOLD` in place; verify each still has a live caller
      with a grep before deleting anything.
- [x] 7.5 Roster surfaces: `server.py`'s module docstring (also add the missing
      `cross_experiment_correlations`); `tests/test_sections_scaffold.py`'s `expected` set (add
      `heritability_analysis`, remove the two retired names, add the missing
      `cross_experiment_correlations`) and its docstring count.
- [x] 7.5b `tests/test_devendor_invariants.py`: **move** `sleap_roots_plot_heritability_bar` /
      `sleap_roots_plot_variance_decomposition` from `expected` into the existing `not_expected`
      block (which already holds `sleap_roots_plot_dendrogram` etc. under "Retired / dropped
      tools"), and **add** `sleap_roots_heritability_analysis` to `expected`. Merely deleting them
      from `expected` drops them out of `relevant = expected | not_expected`, so the assertion
      `live & relevant == expected` would pass whether or not the tools are still registered — the
      retirement would be unenforced. Also add the corresponding **absence** half of §3.1 to
      `test_heritability_analysis_tool.py` here (it can only be true from this commit on).
- [x] 7.5c Retirement guard (the spec's "The retired modules no longer exist" scenario, which had
      no task in an earlier draft): assert
      `importlib.util.find_spec("bloom_mcp.sections.sleap_roots.analysis.plot_heritability_bar")
      is None` and likewise for `plot_variance_decomposition`; assert neither `.py` exists in the
      package directory; walk every `.py` under `src/bloom_mcp/` asserting `heritability_analysis.py`
      is the **only** module whose source contains `calculate_heritability_estimates`; and assert
      no file under `tests/smoke/` mentions either retired namespaced tool name.
- [x] 7.6 Add `"heritability"` to `sections/core/list_existing_analyses.py`'s `TOOL_CLASSES` and
      `"heritability": "heritability_analysis"` to `_TOOL_CLASS_TO_PUBLIC_NAME` (both together —
      `test_list_existing_analyses_staleness.py` requires every `TOOL_CLASSES` entry to have a
      mapping). No `manifest/__init__.py` edit needed; `"heritability"` is already canonical.
- [x] 7.7 Update `tests/test_oracle.py`'s comment naming `viz_tools.plot_variance_decomposition` as
      the `var_genetic`/`var_residual` consumer to name `heritability_analysis`. **Do not** change
      either heritability oracle test's assertions — they exercise the library delegate on
      `turface_19_final_data.csv`, not the retired wrappers, and are `integration`-marked (they do
      not run in per-PR CI at all).
- [x] 7.8 **Two greps, not one.** (a) `grep -rn "plot_heritability_bar\|plot_variance_decomposition"`
      — the tool names; (b) `grep -rn "5 plot\|five plot\|5 surviving\|7 granular\|8 granular\|10 tool"
      --include="*.md" --include="*.py" --include="*.toml"` — the **count** claims, which (a)
      cannot find and which is how an earlier draft of this plan missed six surfaces. Exclude
      `.venv` and `openspec/changes/archive`. Resolve every hit.
- [x] 7.9 Resolve the count-claim surfaces (b) finds — known at drafting time:
      `bloommcp/pyproject.toml` (the comment justifying the direct `matplotlib` dep: "the
      `sleap_roots` section's 5 plotting tools import it directly");
      `bloommcp/docs/2026-06-29-bloom-mcp-contributor-namespacing.md` (two "5 surviving plotting
      tools" references **and** its `analysis/` tree, which also omits
      `umap_analysis`/`descriptive_stats`/`cross_experiment_correlations`) — this is the canonical
      sections design doc that `sections/sleap_roots/__init__.py`'s docstring cites, so leaving it
      stale makes the two contradict each other;
      `_WIKI/BLOOMMCP/README.md`'s tree; `.claude/commands/pre-merge.md`'s `live_smoke_slow` roster
      (which names "the per-trait MixedLM heritability/variance-decomposition plots");
      `bloommcp/tests/smoke/live_plot_tool_smoke.py`; `bloommcp/tests/smoke/conftest.py`'s four
      "the 7 granular analysis tools" occurrences (→ 8, and it documents the fixture path §8.1
      uses); and the docstrings of `test_sections_scaffold.py`, `test_devendor_invariants.py`,
      `test_viz_tools.py`.
- [x] 7.10 Fix `_WIKI/BLOOMMCP/writing-a-new-tool.md`, which tells a contributor that adding a tool
      class means editing `bloommcp/src/bloom_mcp/tools/storage_tools.py` — **a file that does not
      exist**. The edit is now `sections/core/list_existing_analyses.py`, which is exactly §7.6.
      Pre-existing rot, but this change is the one performing that edit.
- [x] 7.11 `bloommcp/docs/roadmap.md`: the #462 reference is **not** a row in the tier table — it
      lives inside the `#116` bullet of "Related dependencies (not slice-gating)", and it already
      points at #462. The work is to flip its forward-looking sentence to past tense and link the
      shipped tool, not to "add a #462 row".
- [x] 7.12 Add the migration table to `bloommcp/docs/connecting-claude-code.md` (a new "Retired
      tools" section) — the doc `bloommcp/README.md` sends every client user to, and the canonical
      user-facing home per the proposal. Assert in a test that both retired names **and**
      `heritability_analysis` appear there, so a user who knows only the deleted names can grep to
      the replacement.
- [x] 7.13 Amend the two **unarchived sibling proposals** whose ADDED requirements mandate the
      retired tools, so archiving either after this change cannot publish a live requirement for
      deleted modules: `devendor-bloommcp-analysis/specs/bloommcp-tool-sections/spec.md` (its
      umbrella-section requirement and two scenarios, "the five surviving plotting tools") and
      `fix-bloommcp-experiment-identifier-wording/specs/bloommcp-experiment-identifier-wording/spec.md`
      (its scenario over "the five plotting tools"). They are proposals, not published truth, so a
      corrective delta from this change cannot target them — edit them in place, noting #462 as the
      reason. Re-run `openspec validate --strict` on both afterward.
- [x] 7.14 **DONE (review round 2).** PR #724 merged to `staging` on 2026-09-03, ahead of
      this change, and CI went red exactly as predicted — at *collection*, taking the whole
      bloommcp suite with it. Executed on rebase: dropped both retired tools from
      `test_viz_snapshot.py`'s `_SNAPSHOT_TOOLS` and `_LOCALIZED_REGRESSION_CASES` and from
      `scripts/gen_plot_snapshots_golden.py`'s `_TOOLS` (plus both module-level imports),
      deleted `tests/fixtures/plot_baselines/{heritability,variance_decomposition}_turface_19_baseline.png`,
      and updated the count claims in `tests/fixtures/README.md` and both files' docstrings.
      `MANIFEST.json` records environment provenance only and names no PNGs, so it needed no
      edit. The measured `_TOL` figures are left as recorded (a measurement over the 5
      baselines that existed then) rather than restated for 3, which would imply a
      re-measurement that did not happen. Original contingency text: If PR #724 has landed (re-check before
      merge; if it lands first, this becomes required). If PR #724 has landed by then, also in this commit: drop the `heritability_bar` /
      `variance_decomposition` parametrize cases and the two module-level imports from
      `tests/tools/test_viz_snapshot.py` and `scripts/gen_plot_snapshots_golden.py`, delete
      `tests/fixtures/plot_baselines/{heritability,variance_decomposition}_turface_19_baseline.png`
      and their `MANIFEST.json` entries, and update `tests/fixtures/README.md`. Those are
      **module-level** imports of the deleted modules: leaving them turns `python-audit` red at
      *collection*, failing the entire bloommcp suite rather than two tests. Check #724's state
      before starting §7 and re-check before pushing.
- [x] 7.15 Run the suite; debug to GREEN **without** weakening the golden or any §4 consistency
      assertion.

## 8. Live smoke + local-validation docs

- [x] 8.1 Add `bloommcp/tests/smoke/test_heritability_analysis_smoke.py` mirroring
      `test_pca_analysis_smoke.py`: `pytestmark = pytest.mark.live_smoke` — **`live_smoke` only,
      not `live_smoke_slow`**. The retired pair were slow because they read whole trait CSVs from
      `TRAITS_DIR` (846 traits at cylinder scale); this tool reads the DB-seeded smoke experiments,
      whose largest shape is 25 plants × 60 traits × 5 genotypes — measured **~1.7 s** end-to-end
      against a 120 s client timeout. Marking it slow would exclude it from `python-audit` (no
      stack) *and* `dev-stack-smoke`, leaving a breaking change with **zero** per-PR live signal.
      Use the `call_tool` + **`db_experiment_id`** fixtures every granular-tool smoke uses — **not**
      `seeded_experiment`, which returns a filename for the `TRAITS_DIR`-reading plot tools and
      would hand a filename to a DB-only tier. Parametrize over `turface_19` and `cylinder`; assert
      `n_traits_reported > 0`, a resolvable `run_ref`/`manifest_path`, and — with
      `include_plots=True` — a `create_heritability_plot_page1.png` in `outputs` on the cylinder
      seed (verified: 60 traits paginates into 2 figures, so the pagination path is genuinely
      exercised live).
- [x] 8.2 Add a `heritability_analysis` leg to `tests/smoke/live_persistence_smoke.py` through the
      **real** `SupabaseReader`/`SupabaseResultStore`: after the `qc_clean` leg commits a cleaned
      version, run `heritability_analysis(experiment=…)`, then assert the committed outputs include
      `heritability.csv` + `heritability_result.json`, the manifest's `schema_version ==
      CURRENT_SCHEMA_VERSION` (**5**, not 3 — assert against the constant so the next bump does not
      re-break it; every existing leg asserts 5), and each recorded `output_sha256` matches the
      stored bytes. Follow the existing legs' `try/except BloomMCPError → record a failed Check`
      shape rather than letting a non-`BloomMCPError` kill `main()` before it prints a summary.
      Assert **structural** invariants only — one row per scored trait — and **do not** assert
      `nonfinite_traits == []` or `n_failed == 0`: this leg runs live in `dev-stack-smoke` on every
      PR against a 5-genotype synthetic seed, where a thin mixed-model fit is a flake source.
- [x] 8.3 Add pure-logic unit tests for the new smoke helpers to
      `tests/scripts/test_live_persistence_smoke_logic.py`, matching the no-live-stack pattern.
- [x] 8.4 Add a `heritability_analysis` leg section to `bloommcp/docs/local-validation.md`
      (following the existing Leg 1–3 sections) and a Claude dogfood row: `qc_clean` →
      `heritability_analysis`, capturing the per-trait numbers, truncation on a wide experiment,
      and both figures from a single `include_plots=true` call. Link to §7.12's migration section
      rather than repeating the table.
- [ ] 8.5 **Partially done.** The smoke helper unit tests (§8.3) pass — 95 tests in
      `tests/scripts/`, no live stack needed. `make bloommcp-smoke` itself is **not run**: no
      dev stack in the implementing environment. Run it before merge.

## 9. Refactor & verify

- [x] 9.1 Refactor for clarity; confirm the server boots (`uv run python -c "import
      bloom_mcp.server"` clean, with `heritability_analysis` registered and neither retired tool
      present) and no vendored/Phase-1 module was touched.
- [x] 9.2 `/pre-merge`, plus the gaps `/pre-merge` itself does not cover:
      `uv run --extra test pytest tests/unit/ -v` **from the repo root** (`python-audit`'s *first*
      pytest step, and the home of `test_bloommcp_smoke_marker_split.py` and
      `test_bloommcp_live_smoke_gate.py` — the CI-convention guards this change's markers must
      satisfy); `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and
      not live_smoke"` (the exact CI invocation — without the filter, `tests/smoke/` collects with
      no stack up and errors rather than skipping); the `integration`-marked oracle tests;
      `uv run black --check` + `uv run ruff check` over `bloommcp/`;
      `uv run pre-commit run --all-files` (the repo's `uv-lock-check` gate, and the `prettier` hook
      that will reformat the new golden); `uv lock --check` + `python scripts/check-uv-locks.py`;
      `import bloom_mcp.server` boot; and `openspec validate
      add-bloommcp-heritability-analysis-tool --strict` (not run by any CI job — local only).
- [ ] 9.3 Validate on **Claude Desktop**: `heritability_analysis` is selectable after `qc_clean`,
      returns queryable per-trait H², and one `include_plots=true` call yields both figures;
      confirm neither retired name appears in the tool picker. Record in
      `docs/local-validation.md`'s manual checklist — leave unchecked if no dev stack / Desktop
      session is available, rather than claiming it.

## 10. Follow-ups — out of this change's scope, tracked not done

(Plain bullets, not checkboxes: these can never be truthfully checked, and OpenSpec's archive gate
expects every `- [ ]` to become `- [x]`.)

- **10.1** The richer upstream heritability surface (`identify_high_heritability_traits`,
  `analyze_heritability_thresholds`, `extract_blup_table`, `diagnose_heritability_issues`,
  `compare_trait_heritabilities` as a first-class output) — the issue's own stretch item. File a
  follow-up once the base tool has real usage.
- **10.2** `plot_font_family` / `plot_font_size` (#661) on `heritability_analysis` — mechanical once
  §5 lands (design D9).
- **10.3** Consolidating the 3 surviving plot tools onto the granular contract — #466, already in
  flight as PR #683.
- **10.5** `_SUMMARY_TRAIT_CAP = 50` is now declared independently in a third place
  (`heritability_analysis`, alongside `descriptive_stats` and `_viz_shared`'s
  `TRAIT_BATCH_THRESHOLD`), kept in sync only by a cross-file test. Hoisting it to one shared
  constant is a small cross-module refactor, deliberately not folded into an already-large
  breaking change.
- **10.6** Pixel-snapshot coverage for the two figures `heritability_analysis` now renders.
  PR #724's harness is built around `viz_env`/`PLOTS_DIR`, which this tool never writes to
  (it persists through `ResultStore`), so its baselines were deleted with the retired tools
  rather than re-pointed. Restoring equivalent coverage needs a different fixture.
- **10.7** Once bloom#721 / PR #726 lands its `plt.get_fignums()` diff and process-wide
  lock, tighten `test_generate_figures_records_pages_from_earlier_keys_when_a_later_key_raises`
  from "asserts 2 leak" to "asserts 0 leak" and update `generate_figures`' docstring.
- **10.4** **File upstream (talmolab/sleap-roots-analyze):** a degenerate fit with
  `var_genetic == 0.0` and `var_residual == 0.0` computes `0.0 / np.float64(0.0)` → `nan`, which
  `max(0, min(1, nan))` clamps to **`1.0`** — reporting a *perfect* heritability with all-finite
  variance components, undetectable by any wrapper-side scrub. Recorded in design D5; deliberately
  not worked around by re-deriving the delegate's arithmetic inside a thin wrapper.
