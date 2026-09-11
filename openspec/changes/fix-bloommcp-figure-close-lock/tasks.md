## 0. Preconditions and RED observation

All `pytest` invocations below are run from `bloommcp/` as
`uv run --frozen --extra test pytest ...` (matching `pr-checks.yml`'s `python-audit` job).

- [ ] 0.1 Confirm the 5 unlocked sites are still present on this branch's base, by pattern
      rather than by line number (line numbers drift on rebase):
      `rg -n 'plt\.close|_close_figure' bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/{qc_inspect,remove_outliers}.py`
      → expect 2 bare `plt.close(fig)` in `qc_inspect._render_report`, 3 `_close_figure(fig)`
      call sites in `remove_outliers`, and the `_close_figure` definition.
- [ ] 0.2 Confirm the issue's third bullet is stale (nothing to implement):
      `rg -n 'def save_plot|def plot_heritability_bar|def plot_variance_decomposition' bloommcp/src`
      → expect **no matches**. If any match appears, STOP and re-scope.
- [ ] 0.3 Confirm `_close_figure` has no reader outside `remove_outliers.py`:
      `rg -n '_close_figure' bloommcp/src bloommcp/tests` → expect hits only in
      `remove_outliers.py` (the `_close_figures*` names in `test_plots_helpers.py` are
      `_plots.close_figures` tests plus a local test helper). If a reader exists, keep the
      helper and make it delegate to `close_figures({"fig": fig})` rather than deleting it.
- [ ] 0.4 **Mechanical nesting check — replaces reading the five sites.** Write a throwaway
      script that monkeypatches `matplotlib.pyplot.close` with a spy recording
      `_plots.FIGURE_REGISTRY_LOCK.locked()` plus the innermost caller frame name, then runs,
      against **unmodified** code: `qc_inspect._run()`; `remove_outliers` isolation_forest
      with `plots=None`, with a 1-key `plots`, and with an unknown key. Every recorded
      `locked()` must be `False`. A `True` at any site means that site is nested inside
      `call_with_figure_cleanup` and MUST NOT be wrapped (the lock is non-reentrant and
      `bloommcp` has no `pytest-timeout`, so a nested acquisition **hangs** rather than
      fails). This doubles as the RED observation for tasks 1.1 and 2.1-2.3. Record the
      output in the PR body.
- [ ] 0.5 Confirm no *unwrapped* delegate call in either tool touches pyplot at the pinned
      `sleap-roots-analyze` version: `rg -n 'pyplot|plt\.|savefig|subplots'` in the
      delegate's `data_cleanup.py` and `outlier_removal.py` (backing
      `apply_data_cleanup_filters`, `inspect_nan_samples`, `remove_outlier_samples`) →
      expect no matches. If any appears, that call site needs `call_with_figure_cleanup`
      too and this change's scope grows.

## 1. `qc_inspect._render_report` — lock both closes (RED → GREEN)

Tasks 1.1-1.6 land in one atomic commit: 1.1 and 1.2 assert behavior that does not exist yet.

**Spy discipline for every lock test in this plan.** `close_figures` wraps each `plt.close`
in `try/except`, so an `assert` raised *inside* a close spy is **swallowed** and the test
passes vacuously. Always append to a list inside the spy and assert **outside** it. And never
call `plt.close("all")` *after* installing the spy — that call is itself recorded, with the
lock unheld, and poisons `all(held)` permanently. Put any such hygiene call before the
`monkeypatch.setattr`.

- [ ] 1.1 In `bloommcp/tests/tools/test_qc_inspect_tool.py`, add
      `test_closes_figures_while_holding_the_figure_registry_lock`, in the shape of the
      same-named test in `tests/tools/test_plot_trait_histograms_tool.py`. Patch
      **`matplotlib.pyplot.close` directly** (`import matplotlib; matplotlib.use("Agg");
      import matplotlib.pyplot as plt; monkeypatch.setattr(plt, "close", _spy)`) — *not*
      `qc_inspect_tool.plt.close`, because task 1.4 deletes that module attribute. This is
      equivalent: `close_figures` re-imports the same module object, so the spy is visible
      through it (verified). Record `(fig, _plots.FIGURE_REGISTRY_LOCK.locked())` pairs;
      assert `held` non-empty (`"the tool never closed a figure"`), `all(...)`
      (`"plt.close ran without FIGURE_REGISTRY_LOCK held"`), and
      `not _plots.FIGURE_REGISTRY_LOCK.locked()` afterwards (release).
      **Anti-vacuity:** also spy `qc_inspect_tool.create_trait_eda_plots`, capture the
      figures it returned, and assert those exact figure objects appear among the closed
      ones — otherwise a fixture change that makes the delegate raise leaves `eda_figs`
      empty, `close_figures` early-returns, and the only recorded closes are
      `call_with_figure_cleanup`'s legitimately-locked ones, so the test would pass
      **without the fix**. MUST fail against current code; record the failure.
- [ ] 1.2 In the same file, add `test_absent_heatmap_set_acquires_no_lock`, pinning site 2's
      "nothing to close" path — the genuinely uncovered case. Monkeypatch
      `qc_inspect_tool.create_exploratory_summary_plots` to raise (mirroring the existing
      `test_run_commits_without_heatmap_when_summary_plots_fail`), so `summary_figs == {}`,
      and assert via the `_CountingLock` proxy from task 4.3 that site 2 contributes **zero**
      acquisitions, while the run still commits and `outputs` omits
      `missing_data_pattern.png`.
      *Do not* substitute a fabricated single figure for the real delegate's output: on
      turface_19 `create_exploratory_summary_plots` returns 5 real figures including
      `missing_data_pattern` and does not raise, so site 2 already closes 5 real figures on a
      plain `_run()` (covered by 1.1's `all(held)`), and a 1-figure monkeypatch would be
      strictly *less* coverage.
- [ ] 1.3 In `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/qc_inspect.py`: add
      `close_figures` to the existing `from bloom_mcp.tools._plots import
      call_with_figure_cleanup` import, and replace both
      `finally:\n    for fig in <figs>.values():\n        plt.close(fig)` blocks with
      `finally:\n    close_figures(<figs>)` (`eda_figs`, then `summary_figs`). Leave both
      `try:` bodies untouched — `savefig` must stay outside the lock. Add a short comment at
      each site pointing at `_plots.py`'s lock comment rather than restating it; **do not
      write the literal string `plt.close(` in that comment**, or task 4.1's guard has to
      special-case this file.
- [ ] 1.4 **Delete** `import matplotlib.pyplot as plt` from `qc_inspect.py`. After 1.3 it has
      zero uses (its only two were the closes; the remaining `plt.` hit is prose in a
      comment), and it is **not** load-bearing: `matplotlib.use("Agg")` on its own preceding
      line is what pins the backend before the `from sleap_roots_analyze import ...` line,
      verified by deleting the import and confirming `matplotlib.get_backend() == "Agg"` with
      all existing tests passing. Keeping it is not an option: `.pre-commit-config.yaml` runs
      `ruff` with `args: [--fix]`, and ruff 0.9.9 reports `F401 [*] matplotlib.pyplot
      imported but unused` as auto-fixable, so the hook would **silently delete it anyway**
      and break any test reaching through `qc_inspect_tool.plt`. Confirm
      `test_no_figure_handle_leak_and_agg_backend` still passes.
- [ ] 1.5 Update `_render_report`'s docstring: it promises "All matplotlib figures the
      delegates create are closed before returning"; extend it to say the closes run under
      `FIGURE_REGISTRY_LOCK` (one acquisition per batch) and are best-effort and logged. Do
      **not** silently fix the adjacent unguarded heatmap `savefig` (a `proposal.md`
      Non-Goal) — if the docstring's "logged, never raised" wording is now misleading about
      that, narrow the wording rather than changing behavior.
- [ ] 1.6 Run `pytest tests/tools/test_qc_inspect_tool.py -v`; confirm 1.1/1.2 pass and no
      existing test regressed — in particular any asserting `plt.get_fignums() == []` after a
      run (figures must still all be closed, not merely closed under a lock).

## 2. `remove_outliers` — lock all 3 closes, delete `_close_figure` (RED → GREEN)

Tasks 2.1-2.5 land in one atomic commit. Spy discipline from section 1 applies.

- [ ] 2.1 Add `test_success_path_closes_figures_while_holding_the_figure_registry_lock`:
      patch `matplotlib.pyplot.close` (this module has no module-level `plt`), then
      `_run(method="isolation_forest", include_plots=True)`. `isolation_forest` is never
      gated by the fit-trustworthiness check, unlike `mahalanobis` on this fixture, so the
      run genuinely reaches cleanup instead of passing vacuously. Assert `held` non-empty and
      `all(...)`, plus the anti-vacuity binding from 1.1 (spy
      `remove_outliers_tool.plot_outlier_analysis`, assert its figures are among those
      closed). **No `plt.close("all")` after the spy is installed.** MUST fail against
      current code (the site routes through lock-free `_close_figure`).
- [ ] 2.2 Add `test_unknown_plot_key_close_holds_the_figure_registry_lock`: same spy, then
      `pytest.raises(BloomMCPError)` around
      `_run(method="isolation_forest", include_plots=True, plots=["not_a_real_figure"])`.
      Pins the validation-failure path that discards everything the delegate produced.
- [ ] 2.3 Add `test_unselected_figure_discard_holds_the_figure_registry_lock`, pinning the
      discard path. Use plain `_run(method="isolation_forest", include_plots=True,
      plots=["isolation_forest_analysis"])`: **`isolation_forest` on turface_19 produces two
      figures, not one** — `plot_outlier_analysis` is called with `which=None`, so the
      delegate's `genotype_requested = requested is None` branch adds `outliers_per_genotype`
      whenever a genotype column is present, and turface_19 has `geno`. So one key is
      requested and `outliers_per_genotype` is discarded: non-vacuous with no
      `_force_trustworthy_mahalanobis_fit` needed. Assert `held` non-empty, `all(...)`,
      `"isolation_forest_analysis.png" in result.outputs`, and
      `"outliers_per_genotype.png" not in result.outputs`. Add a second case using the
      existing `_force_trustworthy_mahalanobis_fit(monkeypatch)` helper with
      `plots=["mahalanobis_pc_analysis"]` for the 4-produced/1-kept/3-discarded shape, and
      assert `len(produced) >= 2` in both so a future fixture change cannot silently make
      either vacuous.
- [ ] 2.4 In `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/remove_outliers.py`: add
      `close_figures` to the `from bloom_mcp.tools._plots import call_with_figure_cleanup`
      import; replace (a) the tool body's `finally` loop with `close_figures(figures)`,
      (b) `_make_figures`' unknown-key loop with `close_figures(available)`, and (c) its
      discard loop with
      `close_figures({k: v for k, v in available.items() if k not in selected})`; then
      **delete `_close_figure` entirely** (per 0.3). Confirm 2.1-2.3 pass.
- [ ] 2.5 Run `pytest tests/tools/test_remove_outliers_tool.py -v` and confirm the four
      pre-existing figure-cleanup tests still pass unchanged:
      `test_include_plots_success_closes_all_figures`,
      `test_unknown_plot_key_failure_closes_all_figures`,
      `test_persistence_failure_closes_all_figures`,
      `test_figure_allocated_then_abandoned_mid_render_is_still_closed`.

## 3. `_plots.py` — observable swallow, accurate comment

- [ ] 3.1 In `bloommcp/tests/tools/test_plots_helpers.py`, add
      `test_a_failing_close_does_not_strand_the_rest_of_the_batch` (spec: "One failing close
      does not strand the rest of the batch" + "A swallowed close is not silent" — neither
      covered today; `test_close_figures_does_not_raise_on_already_closed_figure` does not
      cover it, since a double `plt.close(fig)` raises nothing at all). Build three real
      figures, patch `plt.close` to raise on the first and delegate for the rest, call
      `close_figures`, then assert outside the spy: nothing propagated, all three were
      attempted, figures 2 and 3 are gone from `plt.get_fignums()`, and — via `caplog` —
      a `WARNING` was emitted naming the failing figure's key. MUST fail before 3.3.
- [ ] 3.2 Add `test_lock_comment_describes_no_outstanding_close_site`: read `_plots.py`'s
      source and assert `"STILL OUTSTANDING"` and `"Until those are wired"` are absent, and
      — positively, so deleting the whole block does not pass — that the comment mentions
      `close_figures`, `qc_inspect` and `remove_outliers`. Do **not** assert the absence of
      `"808"`: that file cites #466/#683/#721/#726 for provenance and a post-fix
      "close side now covered everywhere (#808)" line is legitimate. MUST fail before 3.4.
- [ ] 3.3 In `bloommcp/src/bloom_mcp/tools/_plots.py`: add `import logging` +
      `logger = logging.getLogger(__name__)`; in `close_figures`, iterate `figures.items()`
      and log a `WARNING` naming the key and the exception in the inner handler, and the same
      in the outer handler; remove the inner `# pragma: no cover` (3.1 now covers it). Update
      `close_figures`' docstring to note the logging and that it is now the close path for
      `qc_inspect` and `remove_outliers` too. Confirm 3.1 passes.
- [ ] 3.4 Rewrite the `FIGURE_REGISTRY_LOCK` comment's "Where that stands" list: delete the
      `STILL OUTSTANDING` bullet and the "Until those are wired…" closing sentence, replace
      the `_viz_shared.save_plot` parenthetical with a plain note that #462 deleted that
      helper, and add `qc_inspect`/`remove_outliers` to the sites closing via
      `close_figures`. **Also correct the create-side list, which is independently wrong:**
      it claims `clustering.py` calls `call_with_figure_cleanup` around its own delegate call
      (it does not — it imports only `close_figures, generate_figures, validate_plot_keys`
      and reaches the lock via `generate_figures`), and it omits `pca_analysis.py` and
      `umap_analysis.py`, which reach it the same way. Verified direct callers are exactly
      `qc_inspect`, `remove_outliers`, `plot_trait_histograms`, `plot_trait_boxplots`,
      `plot_correlation_matrix`. Confirm 3.2 passes, and that
      `rg -n 'outstanding|unlocked' bloommcp/src/bloom_mcp/tools/_plots.py` surfaces no
      remaining claim of a gap.

## 4. Cross-cutting invariants

- [ ] 4.1 In `bloommcp/tests/tools/test_plots_helpers.py`, add
      `test_every_plt_close_in_bloom_mcp_is_lexically_inside_the_registry_lock` (spec: "No
      unlocked close call site remains in the package"). **AST-based, not a substring
      match** — the house rule, stated in `test_persistence_import_guard.py`'s own docstring
      ("The scan is AST-based … so a comment or docstring mentioning a forbidden name doesn't
      trip it") and in `test_devendor_invariants.py`. Walk every `*.py` under
      `src/bloom_mcp`, collect the `id()` of all nodes under any `ast.With` whose items
      include a `Name`/`Attribute` ending in `FIGURE_REGISTRY_LOCK`, then flag every
      `ast.Call` to `.close` on a pyplot-bound name whose `id()` is not in that set. Report
      offenders as `path:lineno`. This needs **no allow-list and no `_plots.py`
      exclusion** — both of its closes are already inside `with` blocks — so unlike a
      substring guard it also covers the owner module, and it is immune to the comments task
      1.3 adds. **This is a RED gate:** it fails on the pre-change tree (the two
      `qc_inspect` closes and `_close_figure`'s), so observe that failure before section 1
      or 2 lands.
- [ ] 4.2 Folded into 4.1 — dropped. A separate `rg 'def _close_figure'` guard adds nothing:
      it passes for `def _close_fig` or any other spelling, whereas 4.1's AST guard catches a
      reintroduced lock-free close helper by construction, whatever it is called.
- [ ] 4.3 Add `test_no_plots_run_acquires_the_figure_registry_lock_not_at_all` and
      `test_default_path_never_executes_an_import_matplotlib_statement` to
      `test_remove_outliers_tool.py` (spec: "Nothing to close acquires no lock").
      **Do not assert `"matplotlib.pyplot" not in sys.modules`** — it is already `True`
      before the tool is called, because `remove_outliers.py` imports `sleap_roots_analyze`
      at module level and that imports pyplot eagerly (and the section `__init__` pulls in
      `qc_inspect`'s module-level pyplot import too). Four sibling test files already
      document exactly this correction; reuse their idiom:
      `monkeypatch.setitem(sys.modules, "matplotlib", None)` then run with
      `include_plots=False` and assert no `ImportError` — proving no *fresh* import statement
      was reached. For the lock half, `monkeypatch.setattr(_plots, "FIGURE_REGISTRY_LOCK",
      _CountingLock(real))` — a small proxy class with `__enter__`/`__exit__`/`locked()`
      delegating to the real lock and counting entries — and assert zero entries.
      A proxy on the *module attribute* is required: `threading.Lock` is a C type whose
      `acquire` is read-only (`'_thread.lock' object attribute 'acquire' is read-only`), so
      patching a method on the lock itself is impossible. No subprocess (a cold import of
      this module costs ~4.4s).
- [ ] 4.4 Add `test_a_multi_figure_cleanup_takes_exactly_one_acquisition` using the same
      `_CountingLock`: call `close_figures` with three figures and assert exactly one entry.
      This pins the requirement Decision 1 rejects an alternative over (`_close_figure`
      acquiring per figure) and which nothing covers today — the existing
      `test_close_figures_holds_the_registry_lock_while_closing` asserts `held == [True,
      True]`, which a release-and-reacquire-per-figure implementation satisfies equally.
- [ ] 4.5 Add `test_persistence_io_runs_without_the_registry_lock_held` (spec: "Persistence
      I/O runs unlocked"): spy `matplotlib.figure.Figure.savefig` and the store's `commit`,
      asserting `_plots.FIGURE_REGISTRY_LOCK.locked() is False` inside each, on a
      `remove_outliers` isolation_forest run with plots. Catches the plausible regression of
      someone widening the `with` to span the whole `try/finally`.
- [ ] 4.6 Add `test_cleanup_failure_does_not_mask_the_tools_own_error` to
      `test_qc_inspect_tool.py` (spec: "Cleanup does not mask the caller's error") — the one
      test that pins Decision 3's actual claim. Make a post-`_render_report` step raise a
      recognizable error *and* `plt.close` raise a different one; assert the caller observes
      the first, not the cleanup failure. Verify it is RED on the pre-change tree (order the
      two patches so the pre-change `finally` genuinely re-raises the cleanup error).
- [ ] 4.7 Survey only, no code: `rg -n 'acquire' bloommcp/tests` to confirm no test asserts a
      whole-run *global* acquisition count — the shape #466 review round 7 replaced with
      `locked()`-at-close-time property assertions. The existing hit in
      `test_plots_helpers.py` (`FIGURE_REGISTRY_LOCK.acquire(blocking=False)`) is a liveness
      smoke test, not a count assertion — leave it. Note that 4.3/4.4's per-batch entry counts
      are deliberately *not* in that forbidden category.

## 5. Spec validation

- [ ] 5.1 Run `openspec validate fix-bloommcp-figure-close-lock --strict` and resolve issues.
- [ ] 5.2 Confirm no capability collision with the merged-pending-archive
      `converge-bloommcp-viz-tools`: `rg -l 'bloommcp-figure-registry-safety' openspec/changes`
      → expect this change only. Then **diff the requirement bodies**, not just the directory
      names: read `converge-bloommcp-viz-tools/specs/bloommcp-viz-tools/spec.md`'s
      `Figure-Registry Concurrency Safety` against this delta's `Locked Figure Close`, and
      confirm they agree (they restate the same invariant at different scopes) rather than
      contradict. This change's R2 already names that requirement as the per-tool restatement
      and itself as canonical.

## 6. Full verification and PR

- [ ] 6.1 From `bloommcp/`, run CI's exact invocation:
      `uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke" -v --tb=short`.
      Expect zero regressions. **If any test hangs rather than fails, suspect a nested
      acquisition** (task 0.4) and re-check that site before anything else.
- [ ] 6.2 Run `pre-commit run --files <touched files>`. Expect `ruff --fix` to be a no-op on
      `qc_inspect.py` now that task 1.4 deleted the dead import rather than `# noqa`-ing it.
- [ ] 6.3 Stage **by explicit pathspec only.** Six `test_data/*.csv` files show permanent
      phantom line-ending modifications in this repo and must never be committed; never use
      `git add -A` or `git commit -a` here.
- [ ] 6.4 Before pushing, merge `origin/staging` **locally** (`git merge origin/staging`) —
      do **not** use GitHub's *Update branch* button, which has been observed splicing a
      sibling PR's content into wholesale-rewritten files with no conflict shown. After any
      merge, run `python -m py_compile` on the touched source files and
      `pytest --collect-only` before pushing. This matters here because open **PR #778**
      touches both test files this change edits (additively, with no source overlap — see
      `proposal.md` Impact).
- [ ] 6.5 Open the PR against `staging` with `Fixes #808`, including task 0.4's recorded
      pre/post lock-state output, and stating that (a) the issue's `_viz_shared.save_plot`
      bullet was already resolved by #462/PR #777 (merged after #808 was opened) and (b)
      `remove_outliers` had 3 unlocked closes rather than the 1 the issue names.
- [ ] 6.6 Comment on #808 recording why its third bullet needed no change, citing #777's
      removal of both caller files and the merge timestamp. The issue title names
      `_viz_shared.save_plot` and will outlive the fix; with 0 comments on the issue, the
      closed record would otherwise read as a silently dropped location.
- [ ] 6.7 Set every task above to `- [x]` only once the work it describes is actually done.

## 7. Recommended follow-ups (not implemented here)

Listed rather than filed, since opening public issues was not in scope for this change. Each
is a `proposal.md` Non-Goal with no existing issue tracking it.

- [ ] 7.1 Retire `PLOTS_DIR`'s remaining plumbing (static mount, env validation, compose
      bind-mount). `_viz_shared.py`'s docstring already calls it "a separate retirement" with
      no issue link; #476 and #591 cover `BLOOM_TRAITS_DIR`, not this.
- [ ] 7.2 Migrate `qc_inspect`/`remove_outliers` to `generate_figures`, so the `dict`-holding
      tools converge on one figure-lifecycle path. Task 3.4's comment rewrite should point at
      this issue once filed, rather than leaving it unowned — the same "no owner PR" failure
      that produced #808.
- [ ] 7.3 Add `pytest-timeout` to `bloommcp`'s `test` extra with a per-test timeout on the
      lock tests, converting a nested-acquisition hang into a named failure (currently
      bounded only by `pr-checks.yml`'s `timeout-minutes: 20`; cf. #454).
- [ ] 7.4 Give the 3 converged `plot_*` tools the same non-raising close posture
      (`design.md` Decision 3's "divergence left in place"): their bare `plt.close(fig)` runs
      in a `finally` *after* `store.commit`, so a raising close there can still produce a
      committed-but-reported-failed run.
- [ ] 7.5 Guard `qc_inspect`'s heatmap `savefig`, whose failure aborts the whole tool despite
      the surrounding block being documented as best-effort.
