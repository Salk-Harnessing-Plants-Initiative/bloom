## Context

`matplotlib.pyplot`'s figure registry (`Gcf.figs` in `matplotlib._pylab_helpers`) is a
single class-level `OrderedDict` shared by the whole process — not per-thread. FastMCP
dispatches sync tool handlers on a thread pool, so two `bloommcp` tool calls that handle
figures can genuinely interleave against it.

`bloom_mcp.tools._plots.FIGURE_REGISTRY_LOCK` exists to serialize registry mutation. Its
contract has two halves:

| Phase | Hazard | Owner |
|---|---|---|
| **create** | `call_with_figure_cleanup`'s "new since I started" fignum diff cannot tell its own orphaned figure from one another concurrent call just allocated, and would close the other call's figure — silently blanking its plot with no error surfaced | `call_with_figure_cleanup` (#721/#726) — done everywhere |
| **close** | `plt.close` → `Gcf.destroy_fig` scans `Gcf.figs.values()` unsynchronized; a concurrent locked create mutating the dict mid-scan raises `RuntimeError("OrderedDict mutated during iteration")` out of the closing caller | per call site — **5 sites still unlocked** |

Round 6 of #466's review shipped a create-only half-fix; round 7 caught it. The 3 converged
`plot_*` tools and `_plots.close_figures` were fixed in #683. This change closes the
remaining 5.

Inventory of every close site in `bloommcp/src` at the time of writing:

| Site | State |
|---|---|
| `_plots.call_with_figure_cleanup` (exception path) | locked (inside its own `with`) |
| `_plots.close_figures` | locked (one acquisition per batch) |
| `plot_trait_histograms` / `plot_trait_boxplots` / `plot_correlation_matrix` | locked (explicit `with` in `finally`, #683) |
| `pca_analysis` / `umap_analysis` / `clustering` / `heritability_analysis` | locked (via `close_figures`) |
| `qc_inspect._render_report` ×2 | **unlocked** |
| `remove_outliers` tool body `finally` ×1, `_make_figures` ×2 | **unlocked** (all via `_close_figure`) |

## Goals / Non-Goals

- **Goals:** every `plt.close` reachable from a `bloommcp` tool executes under
  `FIGURE_REGISTRY_LOCK`; one acquisition per close *batch*; the lock is never held across
  `savefig`/commit I/O; the invariant is pinned by a test per site so a refactor cannot
  silently reopen it; `_plots.py`'s lock comment stops advertising an outstanding gap.
- **Non-Goals:** see `proposal.md`'s Non-Goals. In particular: no change to figure
  creation, no reentrant lock, no `generate_figures` migration, and no fix for the issue's
  stale `_viz_shared.save_plot` bullet (that code no longer exists).

## Decisions

### Decision 1 — Reuse `_plots.close_figures` rather than adding `with FIGURE_REGISTRY_LOCK:` at each site

`close_figures(figures: dict[str, Figure])` already provides, exactly, everything the issue
asks each site to do:

- **one acquisition around the whole batch**, which is what the issue specifies ("one
  acquisition per close batch, as `close_figures` does") — and what keeps a multi-figure
  cleanup from interleaving with a create halfway through;
- **empty-dict early return before importing `matplotlib.pyplot`**, so a no-plots run adds
  neither lock traffic nor an import (load-bearing for `remove_outliers`, which imports
  matplotlib lazily on the plots path only — see Decision 6);
- **best-effort, never raises**, which is the correct posture for cleanup that runs in a
  `finally` (Decision 3);
- and it carries the 8-line rationale comment explaining *why* the close is locked, in one
  place instead of five.

All 5 sites already hold a `dict[str, Figure]` (or can name one with a single
comprehension), so no adapter is needed.

**Alternatives considered.**

- *Per-site `with FIGURE_REGISTRY_LOCK:` blocks*, mirroring the 3 converged `plot_*` tools
  literally. Rejected: 5 near-identical copies of logic and rationale that
  `close_figures` already single-sources. The `plot_*` tools use an explicit `with` for a
  reason that does not apply here — they close a `list[Figure]`, not the `dict[str, Figure]`
  `close_figures` takes, and their `finally` must skip the lock entirely when creation
  failed before allocating anything (`if figures:`), which `close_figures` gets for free
  from its own empty check.
- *Make `remove_outliers._close_figure` acquire the lock.* Rejected: that is one
  acquisition **per figure**, which the issue explicitly rules out, and it lets a
  concurrent create interleave between figures of the same cleanup batch — the exact
  interleaving the lock exists to prevent.

### Decision 2 — Delete `remove_outliers._close_figure` instead of leaving it unused

Converting its 3 call sites leaves it dead. Keeping a private, lock-free, single-figure
close helper inside a plotting module is a live footgun: the next close site added to that
file reaches for the local helper and silently reopens this race. `grep` confirms it is
referenced nowhere else in `bloommcp/src` or `bloommcp/tests` (the `_close_figures*` hits in
`test_plots_helpers.py` are tests of `_plots.close_figures` and a local test helper, not
this function).

### Decision 3 — Accept that `qc_inspect`'s close becomes best-effort

`close_figures` wraps each `plt.close` in `try/except: pass`. `qc_inspect`'s two closes are
currently bare, so this is a real behavior change — and an improvement:

- Today, a raising `plt.close` mid-loop **aborts the loop**, leaking every figure after the
  failing one, in a `finally` whose entire purpose is to not leak.
- Today, that exception also **replaces whatever exception was already propagating** through
  the `finally` — masking the tool's real error with a cleanup artifact.
- `remove_outliers` already had swallow-and-continue semantics here via `_close_figure`'s
  `except Exception: pass`, so this makes the two tools agree rather than diverge.

Noted for the record: the exception most likely to have been raised there is precisely the
`RuntimeError("OrderedDict mutated during iteration")` this change prevents from being
raised at all. Swallowing it is not how the race is fixed — holding the lock is; the
swallow only stops an unrelated cleanup failure from eating the caller's error.

### Decision 4 — No nesting, so the non-reentrant lock is safe at all 5 sites

`FIGURE_REGISTRY_LOCK` is a plain `threading.Lock`. Acquiring it inside a
`call_with_figure_cleanup` call would self-deadlock. Verified, site by site, that all 5
sit outside any such call:

- `qc_inspect` site 1 — `eda_figs = call_with_figure_cleanup(...)` completes and returns
  *before* the `try:`/`finally:` whose `finally` closes; the lock is released at that point.
- `qc_inspect` site 2 — the `call_with_figure_cleanup` call sits in its own
  `try/except Exception` (the best-effort heatmap); the close is in a **second, separate**
  `try/finally` after it.
- `remove_outliers` tool body — the `finally` close is in `remove_outliers`, outside
  `_make_figures` entirely.
- `remove_outliers` `_make_figures` ×2 — both the unknown-key raise and the
  unselected-subset discard run *after* `call_with_figure_cleanup(lambda: plot_outlier_analysis(...))`
  has returned.

A test per site asserting `locked()` was `True` at close time would hang rather than fail if
this analysis were wrong, which is an acceptable (and loud) failure mode.

### Decision 5 — ADDED, as a new `bloommcp-figure-registry-safety` capability

No spec in `openspec/specs/` owns the lock contract today. The nearest statement of it —
`Requirement: Figure-Registry Concurrency Safety` — lives in the **unarchived**
`converge-bloommcp-viz-tools` change's delta for the `bloommcp-viz-tools` capability, and is
scoped to "each of the 3 tools" that change converges.

A `MODIFIED` delta is therefore not available (OpenSpec's `MODIFIED` requires pasting the
full existing requirement from `openspec/specs/<capability>/spec.md`, and that file does not
exist yet), and would be wrong even if it were: this change's subject is the **process-wide**
invariant across every figure-handling call site, which is orthogonal to — and strictly
broader than — a per-tool requirement on 3 specific tools. Per OpenSpec's own ADDED-vs-MODIFIED
guidance ("prefer ADDED when the change is orthogonal rather than altering the semantics of
an existing requirement"), this is ADDED.

The two coexist without contradiction: the viz-tools requirement becomes the per-tool
restatement of this capability's invariant for the 3 tools it converges, and both demand the
same property. Neither archives into the other's file.

### Decision 6 — Preserve `remove_outliers`' lazy-matplotlib posture

`remove_outliers` imports matplotlib only inside `_make_figures` (the plots path), keeping it
out of the module's runtime import graph — the Tier-0 import-clean guarantee its own comment
calls out. `_close_figure` also imported `pyplot` lazily, and on an `include_plots=False`
run it was never called, so matplotlib was never imported.

`close_figures` preserves this exactly: its `if not figures: return` guard precedes its
`import matplotlib.pyplot as plt`, and on an `include_plots=False` run `figures` is `{}`.
Pinned by a test (task 4.3) asserting `"matplotlib.pyplot" not in sys.modules` after a
no-plots run, so a future refactor that hoists the import cannot regress it silently.

## Risks / Trade-offs

- **Lock contention.** `FIGURE_REGISTRY_LOCK` is process-wide, so these 5 new acquisitions
  serialize against every other figure create/close in the process. Mitigated by scope: each
  is held across `plt.close` calls only (microseconds per figure), never across the
  `savefig`/`to_csv`/`commit` I/O — which in all 5 cases already sits outside the converted
  block, in the `try:` body rather than the `finally:`. Net effect is the same shape #683
  already shipped for the 3 `plot_*` tools.
- **Deadlock if the nesting analysis in Decision 4 is wrong.** Mitigated by per-site
  verification above and by the new tests, which would hang (not silently pass) on a nested
  acquisition. `pytest-timeout` is not configured in `bloommcp`, so such a hang surfaces as
  a stuck CI job rather than a failed assertion — accepted, since the analysis is
  mechanically checkable from the 5 call sites and each is a few lines.
- **Swallowed cleanup errors at `qc_inspect`** (Decision 3). Accepted, and strictly better
  than the status quo; `close_figures`'s inner `except` is already marked
  `# pragma: no cover — best-effort cleanup`, matching how the other 4 tools treat it.
- **The pinning tests monkeypatch `matplotlib.pyplot.close` process-globally** (the module
  object is shared, so patching `<tool>.plt.close` patches it for `close_figures` too — which
  is *why* this test shape works across the indirection). `monkeypatch` restores it per test.

## Migration Plan

None — internal refactor of cleanup paths. No API, schema, manifest, output-key, or
tool-signature change; no persisted artifact changes; nothing to roll forward or back beyond
the commits. Rollback is a plain revert.

## Open Questions

None. The one judgment call (batch-close via `close_figures` vs. 5 local `with` blocks) is
settled in Decision 1 in favor of the helper the issue itself points at.
