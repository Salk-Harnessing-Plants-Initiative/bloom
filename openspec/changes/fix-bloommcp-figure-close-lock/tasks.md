## 0. Preconditions (verify before writing any test)

- [ ] 0.1 Confirm the 5 unlocked sites are still exactly where `design.md`'s inventory says,
      on this branch's base (`origin/staging`):
      `rg -n 'plt\.close|_close_figure' bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/{qc_inspect,remove_outliers}.py`
      → expect `qc_inspect.py:403,440` and `remove_outliers.py:548,618,628` plus the
      `_close_figure` definition (~632) and its own `plt.close` (~636).
- [ ] 0.2 Confirm the issue's third bullet is stale (nothing to implement):
      `rg -n 'def save_plot|def plot_heritability_bar|def plot_variance_decomposition' bloommcp/src`
      → expect **no matches**. If any match appears, STOP and re-scope: `proposal.md`'s
      Non-Goals assert this code is gone.
- [ ] 0.3 Confirm `_close_figure` has no reader outside `remove_outliers.py`:
      `rg -n '_close_figure' bloommcp/src bloommcp/tests` → expect hits only in
      `remove_outliers.py` (the `_close_figures*` names in `test_plots_helpers.py` are
      `_plots.close_figures` tests and a local test helper, not this function). If a reader
      exists, keep the helper and make it delegate to `close_figures({"fig": fig})` instead
      of deleting it (`design.md` Decision 2).
- [ ] 0.4 Re-read `design.md` Decision 4's per-site nesting analysis against the live code
      and confirm all 5 sites sit **outside** any `call_with_figure_cleanup` call. The lock
      is non-reentrant: a nested acquisition deadlocks, and `bloommcp` has no
      `pytest-timeout`, so the tests below would hang CI rather than fail. Do not proceed
      past this task on a site whose nesting you cannot confirm by reading it.

## 1. `qc_inspect._render_report` — lock both success-path closes (RED → GREEN)

Tasks 1.1–1.3 land in one atomic commit: 1.1 asserts behavior that does not exist yet.

- [ ] 1.1 In `bloommcp/tests/tools/test_qc_inspect_tool.py`, add
      `test_closes_figures_while_holding_the_figure_registry_lock`, in the shape of the
      same-named test in `tests/tools/test_plot_trait_histograms_tool.py:538`: import
      `bloom_mcp.tools._plots as _plots`, spy `qc_inspect_tool.plt.close` with a wrapper
      that appends `_plots.FIGURE_REGISTRY_LOCK.locked()` and then delegates to the real
      `close`, `monkeypatch.setattr(qc_inspect_tool.plt, "close", _spy)`, call `_run()`, then
      assert `held` is non-empty (`"the tool never closed a figure"`) and `all(held)`
      (`"plt.close ran without FIGURE_REGISTRY_LOCK held"`). Patching the attribute on the
      shared `pyplot` module object is deliberate — it is what makes the spy visible through
      `close_figures`' own internal `import matplotlib.pyplot as plt` (`design.md` Risks).
      **This test MUST fail against current code** (both closes at
      `qc_inspect.py:403,440` are bare) before task 1.3 lands. Record the observed failure.
- [ ] 1.2 In the same file, add
      `test_heatmap_figure_close_also_holds_the_figure_registry_lock`: monkeypatch
      `qc_inspect_tool.create_exploratory_summary_plots` to return a **real** single-entry
      `{"missing_data_pattern": <Figure>}` (build it with `matplotlib.figure.Figure()` under
      `Agg`, or `plt.figure()`), so site 2's close is exercised on a frame where the real
      delegate's heatmap may be absent. Use the same `locked()` spy, and additionally assert
      the result's `outputs` still contains `missing_data_pattern.png` — the close must be
      locked without changing which artifacts get persisted. MUST fail before 1.3.
- [ ] 1.3 In `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/qc_inspect.py`: add
      `close_figures` to the existing `from bloom_mcp.tools._plots import
      call_with_figure_cleanup` at line ~69, and replace both
      `finally:\n    for fig in <figs>.values():\n        plt.close(fig)` blocks with
      `finally:\n    close_figures(<figs>)` (`eda_figs` at ~403, `summary_figs` at ~440).
      Leave both `try:` bodies untouched — `savefig` must stay outside the lock. Add a short
      comment at each site pointing at `_plots.py`'s lock comment for the rationale rather
      than restating it. Confirm 1.1 and 1.2 now pass.
- [ ] 1.4 Check whether `plt` is still referenced anywhere in `qc_inspect.py` after 1.3
      (`rg -n '\bplt\.' bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/qc_inspect.py`).
      **Keep the `import matplotlib.pyplot as plt` either way** — it is load-bearing: the
      module's `matplotlib.use("Agg")` preamble depends on the import ordering documented in
      the comment at line ~46, and task 1.1's test patches `qc_inspect_tool.plt.close`. If
      `ruff` flags it as unused, keep the import and silence the specific rule inline with a
      one-line reason; do not delete it.
- [ ] 1.5 Update `_render_report`'s docstring: it promises "All matplotlib figures the
      delegates create are closed before returning"; extend it to say the closes run under
      `FIGURE_REGISTRY_LOCK` (one acquisition per batch) and are best-effort.
- [ ] 1.6 Run `uv run --frozen --extra test pytest tests/tools/test_qc_inspect_tool.py -v`
      from `bloommcp/` and confirm no other test in the file regressed — in particular any
      test asserting `plt.get_fignums() == []` after a run (the figures must still all be
      closed, not merely closed under a lock).

## 2. `remove_outliers` — lock all 3 closes, delete `_close_figure` (RED → GREEN)

Tasks 2.1–2.4 land in one atomic commit.

- [ ] 2.1 In `bloommcp/tests/tools/test_remove_outliers_tool.py`, add
      `test_success_path_closes_figures_while_holding_the_figure_registry_lock`: spy on
      `matplotlib.pyplot.close` directly (this module has no module-level `plt` — import
      `matplotlib`, `matplotlib.use("Agg")`, `import matplotlib.pyplot as plt`, then
      `monkeypatch.setattr(plt, "close", _spy)`), `plt.close("all")` first, then
      `_run(method="isolation_forest", include_plots=True)` — `isolation_forest` is never
      gated by the fit-trustworthiness check, unlike `mahalanobis` on this fixture, so the
      run genuinely reaches figure cleanup instead of passing vacuously (same reasoning as
      `test_include_plots_success_closes_all_figures` at line ~1119). Assert `held` is
      non-empty and `all(held)`. MUST fail against current code (site
      `remove_outliers.py:548` → `_close_figure`, which holds no lock).
- [ ] 2.2 Add `test_unknown_plot_key_close_holds_the_figure_registry_lock`: same spy, then
      `pytest.raises(BloomMCPError)` around
      `_run(method="isolation_forest", include_plots=True, plots=["not_a_real_figure"])`
      (mirrors `test_unknown_plot_key_failure_closes_all_figures` at ~1132). Assert `held`
      non-empty and `all(held)` — this pins site `remove_outliers.py:618`, the error path
      that discards everything the delegate produced.
- [ ] 2.3 Add `test_unselected_figure_discard_holds_the_figure_registry_lock`, pinning site
      `remove_outliers.py:628`. This one needs a **multi-figure** method: `isolation_forest`
      on turface_19 produces a single-figure set, so selecting its one key discards nothing
      and the test would be vacuous. Use the existing
      `_force_trustworthy_mahalanobis_fit(monkeypatch)` helper (line ~96) with
      `_run(method="mahalanobis", include_plots=True, plots=["mahalanobis_pc_analysis"])`,
      so 3 of the 4 keys in `_MAHALANOBIS_FIGS` (line ~1098) are discarded. Assert `held`
      non-empty and `all(held)`, **and** that `result.outputs` contains
      `mahalanobis_pc_analysis.png` and none of the other 3 — the discard must close the
      unselected figures without dropping the selected one.
- [ ] 2.4 In `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/remove_outliers.py`:
      add `close_figures` to the `from bloom_mcp.tools._plots import
      call_with_figure_cleanup` import at line ~108; replace
      (a) the tool body's `finally: for fig in figures.values(): _close_figure(fig)` (~548)
      with `close_figures(figures)`,
      (b) `_make_figures`' unknown-key path `for fig in available.values(): _close_figure(fig)`
      (~618) with `close_figures(available)`, and
      (c) its discard path `for name, fig in available.items(): if name not in selected:
      _close_figure(fig)` (~628) with
      `close_figures({k: v for k, v in available.items() if k not in selected})`;
      then **delete `_close_figure` entirely** (~632–638, per task 0.3). Confirm 2.1–2.3
      pass.
- [ ] 2.5 Confirm `remove_outliers.py` still has no module-level `matplotlib` import after
      2.4 (`rg -n '^import matplotlib|^from matplotlib|^import matplotlib.pyplot'`) — the
      lazy import inside `_make_figures` must stay the only one, and `close_figures` does its
      own lazy import behind an empty-dict guard (`design.md` Decision 6).
- [ ] 2.6 Run `uv run --frozen --extra test pytest tests/tools/test_remove_outliers_tool.py -v`
      and confirm the four pre-existing figure-cleanup tests still pass unchanged:
      `test_include_plots_success_closes_all_figures`,
      `test_unknown_plot_key_failure_closes_all_figures`,
      `test_persistence_failure_closes_all_figures`,
      `test_figure_allocated_then_abandoned_mid_render_is_still_closed`.

## 3. `_plots.py` — retire the `STILL OUTSTANDING` block (documentation only)

- [ ] 3.1 In `bloommcp/tests/tools/test_plots_helpers.py`, add
      `test_lock_comment_claims_no_outstanding_unlocked_close_site`: read
      `bloom_mcp.tools._plots`'s source (`inspect.getsource(_plots)` or
      `Path(_plots.__file__).read_text()`) and assert `"STILL OUTSTANDING"` is absent and
      `"issues/808"` is absent. This is the spec's "Lock Contract Documentation Accuracy"
      requirement, and the cheapest possible guard against the comment silently rotting back
      into a false claim. MUST fail before 3.2.
- [ ] 3.2 In `bloommcp/src/bloom_mcp/tools/_plots.py`, rewrite the "Where that stands"
      bullet list in `FIGURE_REGISTRY_LOCK`'s comment (~lines 175–190): delete the
      `STILL OUTSTANDING` bullet and its `issues/808` link, replace the parenthetical about
      `_viz_shared.py`'s `save_plot` with a plain note that #462 deleted that helper, add
      `qc_inspect`/`remove_outliers` to the list of sites closing via `close_figures`, and
      replace the closing sentence ("Until those are wired, this lock is a precondition for
      closing the race process-wide, not by itself sufficient") with a statement that the
      close side is now covered everywhere. Do **not** touch the create-side list above it
      (still accurate) or the `threading.Lock()` statement itself. Confirm 3.1 passes.
- [ ] 3.3 Update `close_figures`' docstring to note it is now the close path for
      `qc_inspect` and `remove_outliers` as well, and confirm no other docstring in
      `_plots.py` still describes those two sites as unlocked
      (`rg -n 'outstanding|unlocked|808' bloommcp/src/bloom_mcp/tools/_plots.py`).

## 4. Cross-cutting invariants (new tests, expected to pass once 1–3 land)

- [ ] 4.1 In `bloommcp/tests/tools/test_plots_helpers.py`, add
      `test_no_unlocked_plt_close_call_site_remains_in_bloom_mcp`: walk
      `bloom_mcp/**/*.py` (excluding `_plots.py` itself, which owns the lock) and assert no
      file contains a `plt.close(` occurrence — every close must now route through
      `close_figures` or an explicit `with FIGURE_REGISTRY_LOCK:` block. Allow-list the 3
      converged `plot_*` tools by name (they hold the lock via an explicit `with`, for the
      `list[Figure]` reason in `design.md` Decision 1) and assert for each of those that a
      `with FIGURE_REGISTRY_LOCK:` line precedes its `plt.close(` line in the same file.
      This is the spec's "No unlocked close site remains in the package" scenario; a textual
      guard is the only thing that catches a *newly added* unlocked site, which no per-tool
      behavioral test can.
- [ ] 4.2 In the same file, assert no module under `bloom_mcp/sections/` defines a private
      close-a-figure helper of its own (`rg`-equivalent check for `def _close_figure`), so
      task 2.4's deletion cannot be quietly reintroduced.
- [ ] 4.3 In `bloommcp/tests/tools/test_remove_outliers_tool.py`, add
      `test_no_plots_run_imports_no_pyplot_and_takes_no_lock` (`design.md` Decision 6): in a
      subprocess (`subprocess.run([sys.executable, "-c", ...])` — `matplotlib.pyplot` is
      already in `sys.modules` from sibling tests in the same session, so an in-process
      assertion is unreliable), import the tool, configure `FakeReader`/`FakeResultStore`,
      run `remove_outliers` with `include_plots=False`, and assert
      `"matplotlib.pyplot" not in sys.modules` on exit. If wiring the fakes in a subprocess
      proves impractical, fall back to an in-process spy asserting
      `FIGURE_REGISTRY_LOCK.acquire` is never called on that run and note the weaker form in
      the test's docstring — do not silently drop the task.
- [ ] 4.4 Confirm no test asserts a *count* of lock acquisitions anywhere in the suite
      (`rg -n 'acquire' bloommcp/tests`). Count-based assertions are what #466 review round
      7 replaced with `locked()`-at-close-time property assertions, because a count survives
      a refactor that moves the close back outside the lock. If one exists, leave it alone
      but note it — converting it is out of scope.

## 5. Spec validation

- [ ] 5.1 Run `openspec validate fix-bloommcp-figure-close-lock --strict` and resolve any
      issues.
- [ ] 5.2 Confirm this change's `bloommcp-figure-registry-safety` delta does not collide
      with the in-flight `converge-bloommcp-viz-tools` change's `bloommcp-viz-tools` delta
      (different capability directories, so they archive into different spec files —
      `design.md` Decision 5): `openspec list` and
      `rg -l 'bloommcp-figure-registry-safety' openspec/changes` → expect this change only.

## 6. Full verification

- [ ] 6.1 From `bloommcp/`, run the invocation CI uses
      (`.github/workflows/pr-checks.yml`):
      `uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke" -v --tb=short`.
      Expect zero regressions — no production behavior changes except `qc_inspect`'s closes
      becoming best-effort (`design.md` Decision 3), which no existing test asserts against.
      **If any test hangs rather than fails, suspect a nested acquisition** (task 0.4 /
      `design.md` Decision 4) and re-check that site's nesting before anything else.
- [ ] 6.2 Run `pre-commit run --files <touched files>` (`bloommcp` has no dedicated CI lint
      job — `ruff`/`black`/`gitleaks` enforcement is via the root `.pre-commit-config.yaml`
      hooks only). Pay attention to `ruff`'s unused-import verdict on `qc_inspect.py`'s
      `plt` (task 1.4).
- [ ] 6.3 Set every task above to `- [x]` only once the work it describes is actually done,
      and open the PR against `staging` with `Fixes #808`, calling out in the body that the
      issue's `_viz_shared.save_plot` bullet was already resolved by #462/#777 and that
      `remove_outliers` had 3 unlocked closes rather than the 1 the issue names.
