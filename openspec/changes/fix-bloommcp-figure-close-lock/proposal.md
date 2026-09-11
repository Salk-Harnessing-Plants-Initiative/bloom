## Why

[#808](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/808) — split out
of #466's PR review (round 7, PR #683) after #726 landed — reports the last
`plt.close()` call sites in `bloommcp` that still run **outside**
`bloom_mcp.tools._plots.FIGURE_REGISTRY_LOCK`.

`plt.close(fig)` → `Gcf.destroy_fig` **scans** `Gcf.figs.values()` to find the manager
owning `fig`, and that scan is unsynchronized. A concurrent `FIGURE_REGISTRY_LOCK`-holding
create (`Gcf.set_active` does `figs[num] = manager` then `move_to_end`) mutating that
`OrderedDict` mid-scan raises `RuntimeError("OrderedDict mutated during iteration")` out of
the *closing* caller — reproduced deterministically on PR #683 by pausing inside the scan
via a blocking `__eq__` and popping from another thread. FastMCP dispatches sync tool
handlers on a thread pool, so any two figure-handling tool calls in this process can
genuinely interleave. The lock contract is therefore **create AND close**, not create alone.

#726 (`call_with_figure_cleanup`) locked the create side at every call site and locked its
own *exception-path* close. #466/#683 locked both sides in the 3 converged `plot_*` tools
and in `close_figures`. What is still unlocked is the ordinary **success-path** (and
discard-path) close in `qc_inspect` and `remove_outliers`. Those two files were #726's own
diff while both PRs were in flight, so #683 deliberately did not touch them; #726 then
merged without locking them, leaving the gap with no owner PR.

`_plots.py`'s own lock comment already carries a `STILL OUTSTANDING` block naming these
sites and pointing at #808 — this change is what lets that block be deleted, and turns the
lock from "a precondition for closing the race process-wide" into an invariant that
actually holds.

## What Changes

- **`qc_inspect.py` `_render_report` (2 sites)** — replace the two unlocked
  `finally: for fig in ...: plt.close(fig)` loops (the delegated EDA figure set, and the
  best-effort missingness-heatmap set) with `close_figures(...)`, the shared batch-close
  helper that already acquires the lock once per batch.
- **`remove_outliers.py` (3 sites, not 1)** — the issue enumerates only `_make_figures`,
  but that file has three unlocked closes, all routed through a lock-free private
  `_close_figure` helper: the tool body's `finally` over the persisted `figures`, plus
  `_make_figures`' unknown-plot-key error path and its unselected-subset discard path.
  All three become `close_figures(...)`.
- **Delete `remove_outliers._close_figure`.** With its only 3 call sites converted it is
  dead code — and leaving a lock-free single-figure close helper in a plotting module is a
  footgun for the next call site added there. **BREAKING for nothing:** it is module-private
  and referenced nowhere else in `bloommcp` (`src/` or `tests/`).
- **Behavior change at `qc_inspect`:** its two closes become best-effort (never-raising),
  because `close_figures` swallows per-figure failures. Today an exception from `plt.close`
  in either `finally` block aborts the loop — leaking every remaining figure — *and*
  replaces whatever exception was already in flight. See `design.md` Decision 3; this is
  strictly better `finally`-cleanup behavior, and `remove_outliers` already had exactly
  these semantics via `_close_figure`'s `except Exception: pass`.
- **Update `_plots.py`'s `FIGURE_REGISTRY_LOCK` comment** — delete the now-satisfied
  `STILL OUTSTANDING` block and record that every close site in `bloommcp` holds the lock.
- **New tests** pinning `FIGURE_REGISTRY_LOCK.locked() is True` at the moment `plt.close`
  runs, for each of the 5 sites, in the shape of
  `test_closes_figures_while_holding_the_figure_registry_lock` in
  `tests/tools/test_plot_trait_histograms_tool.py`. Plus a no-plots test pinning that a
  run with nothing to close acquires the lock **not at all** (and does not import
  `matplotlib.pyplot`) — the property `close_figures`' empty-dict early return provides and
  which `remove_outliers`' Tier-0 import posture depends on.

## Non-Goals

- **`_viz_shared.py`'s `save_plot`** (the issue's third bullet) — **stale, no code to fix.**
  #462 (merged as PR #777, `heritability_analysis`) deleted `save_plot`/`save_plot_or_plots`
  along with their only two callers (`plot_heritability_bar`,
  `plot_variance_decomposition`). `_viz_shared.py` today exports only
  `TRAIT_BATCH_THRESHOLD` and `resolve_trait_columns`, and both its module docstring and
  `_plots.py`'s lock comment already record the deletion. The issue text predates #777
  landing. Verified by `grep`: no `save_plot`, `plot_heritability_bar`, or
  `plot_variance_decomposition` definition remains in `bloommcp/src`.
- **Making `FIGURE_REGISTRY_LOCK` reentrant** (`threading.RLock`). The lock is deliberately
  non-reentrant so a transitive re-entry deadlocks loudly instead of silently reopening the
  race; all 5 sites this change touches are verified to sit *outside* any
  `call_with_figure_cleanup` call (see `design.md` Decision 4), so no site needs reentrancy.
- **Migrating `qc_inspect`/`remove_outliers` to `generate_figures`.** Both delegate to
  plotters returning a `dict[str, Figure]` keyed by the delegate's own names, not to the
  per-key `resolved_calls` shape `generate_figures` expects; converting them is a larger
  refactor with its own key-naming and pagination questions, and is orthogonal to closing
  this race.
- **Retiring `PLOTS_DIR`'s remaining plumbing** (static mount, env validation, compose
  bind-mount), which `_viz_shared.py`'s docstring notes is a separate retirement.
- **Any change to figure *creation*.** Every create site already goes through
  `call_with_figure_cleanup` (#721/#726); this change is close-side only.

## Impact

- **Affected specs:** `bloommcp-figure-registry-safety` (**ADDED** — a new capability
  owning the *process-wide* create-and-close lock invariant across every
  matplotlib-figure-handling call site in `bloommcp`. No existing spec in
  `openspec/specs/` owns it; the nearest is `Requirement: Figure-Registry Concurrency
  Safety` in the still-in-flight `converge-bloommcp-viz-tools` delta, which is scoped to
  "each of the 3 tools" that change converges. These do not conflict — see `design.md`
  Decision 5 for why this is ADDED as a new capability rather than a MODIFIED delta
  against an unarchived one.)
- **Affected code:**
  `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/qc_inspect.py` (2 close sites),
  `.../remove_outliers.py` (3 close sites + delete `_close_figure`),
  `bloommcp/src/bloom_mcp/tools/_plots.py` (lock comment only — no behavior change).
- **Affected tests:** `bloommcp/tests/tools/test_qc_inspect_tool.py` (new),
  `bloommcp/tests/tools/test_remove_outliers_tool.py` (new).
- **Dependencies:** none. No new packages; `close_figures` already exists and is already
  imported from `_plots` by 4 other tools.
- **Runtime/performance:** adds one lock acquisition per close *batch* (never per figure),
  held only across `plt.close` calls — never across `savefig`/commit I/O, which stays
  outside every converted block. Runs with `include_plots=False` acquire it zero times.
- **Branch/PR:** `egao28/bloommcp-figure-close-lock-808`, branched from `origin/staging`;
  PR targets `staging` with `Fixes #808`, noting the stale third bullet.
