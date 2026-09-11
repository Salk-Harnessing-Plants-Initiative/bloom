## Why

[#808](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/808) — split out
of #466's PR review (round 7, PR #683) after #726 landed — reports the last
`plt.close()` call sites in `bloommcp` that still run **outside**
`bloom_mcp.tools._plots.FIGURE_REGISTRY_LOCK`.

`plt.close(fig)` → `Gcf.destroy_fig` **scans** `Gcf.figs.values()` to find the manager
owning `fig`, and that scan is unsynchronized. A concurrent `FIGURE_REGISTRY_LOCK`-holding
create inserting a **new** figure number into that `OrderedDict` mid-scan raises
`RuntimeError("OrderedDict mutated during iteration")` out of the *closing* caller —
reproduced deterministically on PR #683 by pausing inside the scan via a blocking `__eq__`
and popping from another thread. FastMCP dispatches sync tool handlers on a thread pool, so
any two figure-handling tool calls in this process can genuinely interleave. The lock
contract is therefore **create AND close**, not create alone.

#726 (`call_with_figure_cleanup`) locked the create side at every call site and locked its
own *exception-path* close. #466/#683 locked both sides in the 3 converged `plot_*` tools
and in `close_figures`. What is still unlocked is the ordinary **success-path** (and
discard- and validation-failure-path) close in `qc_inspect` and `remove_outliers`. Those two
files were #726's own diff while both PRs were in flight, so #683 deliberately did not touch
them; #726 then merged without locking them, leaving the gap with no owner PR.

**The two failure modes differ per site, and both matter.** At `qc_inspect` the closes are
bare, so the race propagates: out of `_render_report`'s `finally`, through the tool body's
`except Exception: rmtree(staging); raise`, into `as_mcp_tool`, where `RuntimeError` is
undeclared and maps to a fixed `internal_error` carrying only a correlation id
(`contract/errors.py:96-102`). A scientist mid-QC gets an opaque, non-reproducible failure
with no indication it is transient and nothing to do with their data, and the report is
discarded. At `remove_outliers` all three closes already swallow (`_close_figure`), so the
same race is **invisible** there — it leaks a figure in a long-lived server process instead
of raising. So this is a 3-site leak fix plus a 2-site error-surface fix, not one uniform
bug. Both are cured at the cause.

`_plots.py`'s own lock comment already carries a `STILL OUTSTANDING` block naming these
sites and pointing at #808 — this change is what lets that block be deleted, and turns the
lock from "a precondition for closing the race process-wide" into an invariant that
actually holds.

## What Changes

- **`qc_inspect.py` `_render_report` (2 sites)** — replace the two unlocked
  `finally: for fig in ...: plt.close(fig)` loops (the delegated EDA figure set, and the
  best-effort missingness-heatmap set) with `close_figures(...)`, the shared batch-close
  helper that already acquires the lock once per batch. The module's now-dead
  `import matplotlib.pyplot as plt` is deleted with them (`matplotlib.use("Agg")` keeps
  pinning the backend on its own line, unaffected).
- **`remove_outliers.py` (3 sites, not 1)** — the issue enumerates only `_make_figures`,
  but that file has three unlocked closes, all routed through a lock-free private
  `_close_figure` helper: the tool body's `finally` over the persisted `figures`, plus
  `_make_figures`' unknown-plot-key validation-failure path and its unselected-subset
  discard path. All three become `close_figures(...)`.
- **Delete `remove_outliers._close_figure`.** With its only 3 call sites converted it is
  dead code — and leaving a lock-free single-figure close helper in a plotting module is a
  footgun for the next call site added there. Not a breaking change: it is module-private
  and referenced nowhere else in `bloommcp` (`src/` or `tests/`).
- **Log the swallowed close in `_plots.close_figures`.** Today both its handlers are a bare
  `pass` and `_plots.py` imports no logger. The two `qc_inspect` sites are currently the
  *only* `plt.close` calls in `bloom_mcp` that are neither swallowed nor already locked —
  i.e. the only place this race can leave evidence anywhere in the process. Converting them
  to `close_figures` without adding a log line would mean **no close failure anywhere in
  `bloommcp` produces any signal at all**, while the consequence of a swallowed close is a
  leaked figure in a long-lived server (cylinder's 846 traits render 53 pages per call, and
  a single render is on record costing seconds and multiple GB). So `close_figures` gains a
  module logger and a `WARNING` naming the figure key and the exception on each swallowed
  close. This is the one behavior addition beyond relocating the lock, and it is what keeps
  the fix observable if it is ever incomplete.
- **Behavior change at `qc_inspect`:** its two closes become best-effort (never-raising),
  because `close_figures` swallows per-figure failures. Today an exception from `plt.close`
  in either `finally` block aborts the loop — leaking every remaining figure — *and*
  replaces whatever exception was already in flight, so a cleanup artifact masks the tool's
  real error. No persisted artifact changes: both `outputs` maps are fully populated in the
  `try` body before either `finally` runs, and which artifacts are guaranteed is unchanged
  (`missing_data_pattern.png` stays optional). See `design.md` Decision 3;
  `remove_outliers` already had exactly these semantics via `_close_figure`.
- **Correct and update `_plots.py`'s `FIGURE_REGISTRY_LOCK` comment** — delete the
  now-satisfied `STILL OUTSTANDING` block, and fix the **create-side** list, which is
  independently wrong today: it claims `clustering.py` calls `call_with_figure_cleanup`
  directly (it reaches the lock via `generate_figures`) and omits `pca_analysis.py` and
  `umap_analysis.py` entirely. Verified direct callers are exactly `qc_inspect`,
  `remove_outliers`, and the 3 `plot_*` tools.
- **New tests** pinning `FIGURE_REGISTRY_LOCK.locked() is True` at the moment `plt.close`
  runs, for each of the 5 sites, in the shape of
  `test_closes_figures_while_holding_the_figure_registry_lock` in
  `tests/tools/test_plot_trait_histograms_tool.py` — each bound to the figures the site
  under test actually produced, so it cannot pass vacuously on a run where the delegate
  raised and the only recorded closes were `call_with_figure_cleanup`'s (legitimately
  locked) ones. Plus tests for the properties no test covers today: one acquisition per
  batch (not per figure), persistence I/O running unlocked, a failing close not stranding
  the rest of the batch, cleanup not masking the caller's error, a no-plots run taking the
  lock zero times, and an AST guard that no `plt.close` in `bloom_mcp` sits outside a lock
  acquisition.

## Non-Goals

- **`_viz_shared.py`'s `save_plot`** (the issue's third bullet) — **stale, no code to fix.**
  #462 (merged as PR #777) deleted `save_plot`/`save_plot_or_plots` along with their only
  two callers; `gh pr view 777 --json files` shows `removed
  .../plot_heritability_bar.py`, `removed .../plot_variance_decomposition.py`, `modified
  .../_viz_shared.py`. `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/_viz_shared.py`
  today exports only `TRAIT_BATCH_THRESHOLD` and `resolve_trait_columns` (pinned by
  `bloommcp/tests/tools/test_viz_shared.py`), and both its module docstring and `_plots.py`'s
  lock comment already record the deletion. The timeline is decisive: **#808 was opened
  2026-09-10T16:11Z; #777 merged 2026-09-11T13:36Z** — the issue text predates the deletion.
- **Adding `pytest-timeout`.** Two reviewers proposed it so a nested-acquisition deadlock
  would fail rather than hang. The underlying risk is now **empirically retired**: an
  instrumented `plt.close` recording `FIGURE_REGISTRY_LOCK.locked()` at every close, run
  against unmodified code, reports `False` at all 5 sites (so none is nested), and the
  converted code was executed end-to-end at all 5 with no hang. A hang would also be
  bounded by `pr-checks.yml`'s `timeout-minutes: 20` on `python-audit`, not the 6 hours of
  #454. Adding a test dependency plus `uv.lock` churn to a race fix is disproportionate;
  recommended as a follow-up instead.
- **Making `FIGURE_REGISTRY_LOCK` reentrant** (`threading.RLock`). Deliberately
  non-reentrant so a transitive re-entry deadlocks loudly instead of silently reopening the
  race; all 5 sites are verified — by reading *and* by execution — to sit outside any
  `call_with_figure_cleanup` call (`design.md` Decision 4), so no site needs reentrancy.
- **Migrating `qc_inspect`/`remove_outliers` to `generate_figures`.** Both delegate to
  plotters returning a `dict[str, Figure]` keyed by the delegate's own names, not the
  per-key `resolved_calls` shape `generate_figures` expects; converting them is a larger
  refactor with its own key-naming and pagination questions. **Recommended follow-up
  issue** — no issue tracks it today, and `_plots.py`'s comment should point at one rather
  than leave it unowned (the same "no owner PR" failure that produced #808).
- **Retiring `PLOTS_DIR`'s remaining plumbing** (static mount, env validation, compose
  bind-mount), which `_viz_shared.py`'s docstring calls "a separate retirement" with no
  issue link. **Recommended follow-up issue**; no issue tracks it today (#476 and #591 are
  about `BLOOM_TRAITS_DIR`, not `PLOTS_DIR`).
- **The 3 converged `plot_*` tools' non-swallowing closes.** They call bare `plt.close(fig)`
  under the lock in a `finally` that runs *after* `store.commit`, so a raising close there
  could still produce a committed-but-reported-failed run. This change makes
  `qc_inspect`/`remove_outliers` immune to that class while leaving those three exposed.
  Out of scope for #808; noted in `design.md` Decision 3 so it is not rediscovered.
- **`qc_inspect`'s unguarded heatmap `savefig`.** Creation of the heatmap is best-effort but
  its `savefig` has no `except`, so a failing write aborts the whole tool, contradicting
  the "logged, never raised" promise in `_render_report`'s docstring. Pre-existing and
  adjacent (task 1.5 rewrites that docstring); explicitly not fixed here.
- **Any change to figure *creation*.** Every create site already goes through
  `call_with_figure_cleanup` (#721/#726) directly or via `generate_figures`; this change is
  close-side only, plus the comment correction above.

## Impact

- **Affected specs:** `bloommcp-figure-registry-safety` (**ADDED** — a new capability
  owning the *process-wide* create-and-close lock invariant across every
  matplotlib-figure-handling call site in `bloommcp`. No spec in `openspec/specs/` owns it;
  the nearest is `Requirement: Figure-Registry Concurrency Safety` in
  `converge-bloommcp-viz-tools`, **merged via PR #683 on 2026-09-11 but not yet archived**,
  and scoped to "each of the 3 tools" that change converges. See `design.md` Decision 5 for
  why this is ADDED as a new capability, and which file is canonical once both archive.)
- **Affected code:**
  `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/qc_inspect.py` (2 close sites, one
  deleted import, one docstring),
  `.../remove_outliers.py` (3 close sites + delete `_close_figure`),
  `bloommcp/src/bloom_mcp/tools/_plots.py` (add a module logger + a `WARNING` on each
  swallowed close; correct the lock comment's create-side list and delete its
  `STILL OUTSTANDING` block; update `close_figures`' docstring).
- **Affected tests:** `bloommcp/tests/tools/test_qc_inspect_tool.py` (3 new tests),
  `bloommcp/tests/tools/test_remove_outliers_tool.py` (5 new tests),
  `bloommcp/tests/tools/test_plots_helpers.py` (5 new tests, incl. the AST guard and the
  batch-acquisition count).
- **Dependencies / in-flight overlap:** no new packages. **PR #778**
  (`egao28/bloommcp-inline-csv-all-tools-582`, open, same author) modifies
  `test_qc_inspect_tool.py` and `test_remove_outliers_tool.py` — the two files this change
  adds tests to. The overlap is **additive-only and small** (two 4-line hunks) and there is
  **no source intersection**: #778's src changes are in `qc_clean.py`, `_qc_shared.py`,
  `_inline_input.py` and `contract/models.py`, none of which this change touches. Spec
  directories do not collide either (#778 carries `bloommcp-qc-inspect-tool` /
  `bloommcp-remove-outliers-tool` deltas). #808 exists precisely because two in-flight PRs
  each avoided the other's diff, so this is named rather than assumed away — and task 6.4
  carries #683's own hard-won instruction to merge `staging` **locally** rather than via
  GitHub's *Update branch*.
- **Runtime/performance:** adds one lock acquisition per close *batch* (never per figure),
  held only across `plt.close` calls — verified at all 5 sites to sit in a `finally` whose
  `try` body holds all the `savefig`/`to_csv`/`commit` I/O, so the lock never spans I/O.
  Runs with `include_plots=False` acquire it zero times. Measured cost of the new tests:
  ~4-5s on a 39.5s/116-test baseline for the two tool files.
- **Branch/PR:** `egao28/bloommcp-figure-close-lock-808`, branched from `origin/staging`;
  PR targets `staging` with `Fixes #808`. Task 6.5 comments on #808 before merge recording
  why its third bullet needed no change, since the issue title will outlive the fix.
