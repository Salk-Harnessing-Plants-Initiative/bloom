## Context

`matplotlib.pyplot`'s figure registry (`Gcf.figs` in `matplotlib._pylab_helpers`) is a
single class-level `OrderedDict` shared by the whole process — not per-thread. FastMCP
dispatches sync tool handlers on a thread pool, so two `bloommcp` tool calls that handle
figures can genuinely interleave against it.

**Mechanism, verified against the pinned matplotlib 3.10.8.** `Gcf.destroy_fig` is:

```python
num = next((manager.num for manager in cls.figs.values()
            if manager.canvas.figure == fig), None)
```

an unsynchronized generator scan (the `== fig` comparison is the `__eq__` call PR #683 used
to pause it). `Gcf.set_active` does `cls.figs[manager.num] = manager` then
`cls.figs.move_to_end(...)`. Tested directly on an `OrderedDict` mid-`.values()`-iteration:
inserting a **new** key raises `RuntimeError("OrderedDict mutated during iteration")`;
`move_to_end` on an existing key raises nothing. So the trigger is specifically the
size-changing insert of a brand-new figure number — exactly what a concurrent *create*
does, which is why create-vs-close is the racing pair and why locking creation alone cannot
protect the closer.

`bloom_mcp.tools._plots.FIGURE_REGISTRY_LOCK` exists to serialize registry mutation. Its
contract has two halves:

| Phase | Hazard | Owner |
|---|---|---|
| **create** | `call_with_figure_cleanup`'s "new since I started" fignum diff cannot tell its own orphaned figure from one another concurrent call just allocated, and would close the other call's figure — silently blanking its plot with no error surfaced | `call_with_figure_cleanup` (#721/#726) — done everywhere |
| **close** | `plt.close` → `Gcf.destroy_fig` scans `Gcf.figs.values()` unsynchronized; a concurrent locked create inserting a new fignum mid-scan raises `RuntimeError` out of the closing caller. Secondarily, an unlocked close landing *inside* another thread's `call_with_figure_cleanup` window (after its `before = plt.get_fignums()`) frees a number that `plt.figure()` then reuses — so that call's own orphan is already in `before` and its exception-path diff fails to close it, leaking it | per call site — **5 sites still unlocked** |

Round 6 of #466's review shipped a create-only half-fix; round 7 caught it. The 3 converged
`plot_*` tools and `_plots.close_figures` were fixed in #683. This change closes the
remaining 5.

Inventory of every close site in `bloommcp/src` (exhaustive grep for `plt.close`,
`pyplot.close`, `.clf()`, `close("all")`):

| Site | State |
|---|---|
| `_plots.call_with_figure_cleanup` (exception path) | locked (inside its own `with`) |
| `_plots.close_figures` | locked (one acquisition per batch) |
| `plot_trait_histograms` / `plot_trait_boxplots` / `plot_correlation_matrix` | locked (explicit `with` in `finally`, #683) |
| `pca_analysis` / `umap_analysis` / `clustering` / `heritability_analysis` | locked (via `close_figures`) |
| `qc_inspect._render_report` ×2 | **unlocked**, and bare (can raise) |
| `remove_outliers` tool body `finally` ×1, `_make_figures` ×2 | **unlocked**, all via `_close_figure` (already swallow) |

No site is missed: outside these files and `_viz_shared.py`, nothing in `bloom_mcp`
references matplotlib at all (`sections/phenotyping_segmentation/`, `sections/core/`,
`sections/sleap_roots/extraction/` and `tools/` render nothing);
`scripts/gen_plot_snapshots_golden.py` is a dev-only golden generator outside the package.

## Goals / Non-Goals

- **Goals:** every `plt.close` call site in `bloom_mcp`'s own source executes under
  `FIGURE_REGISTRY_LOCK`; one acquisition per close *batch*; the lock never held across
  `savefig`/commit I/O; a swallowed close leaves a log line rather than nothing; the
  invariant pinned per site by tests bound to the figures that site produced, plus one AST
  guard that catches a *newly added* unlocked site; `_plots.py`'s lock comment accurate on
  both sides.
- **Non-Goals:** see `proposal.md`. In particular: no change to figure creation, no
  reentrant lock, no `generate_figures` migration, no `pytest-timeout`, and no fix for the
  issue's stale `_viz_shared.save_plot` bullet (that code no longer exists).

## Decisions

### Decision 1 — Reuse `_plots.close_figures` rather than adding `with FIGURE_REGISTRY_LOCK:` at each site

`close_figures(figures: dict[str, Figure])` already provides:

- **one acquisition around the whole batch** — what the issue specifies ("one acquisition
  per close batch, as `close_figures` does") and what keeps a multi-figure cleanup from
  interleaving with a create halfway through;
- **empty-dict early return before importing `matplotlib.pyplot`**, so a no-plots run adds
  neither a lock acquisition nor an import of its own (Decision 6);
- **best-effort, never raises** — the correct posture for cleanup in a `finally`
  (Decision 3), and load-bearing at `remove_outliers` specifically (Risks);
- **falsy-safe** — `close_figures(None)` returns cleanly, where today a delegate that
  returned `None` would make `qc_inspect`'s `finally` raise `AttributeError` and mask the
  real error;
- and it single-sources the 8-line rationale for *why* the close is locked, instead of five
  copies.

All 5 sites already hold a real `dict[str, Figure]` (or name one with a single
comprehension), so no adapter is needed.

**Alternatives considered.**

- *Per-site `with FIGURE_REGISTRY_LOCK:` blocks*, mirroring the 3 converged `plot_*` tools
  literally. This is the strongest objection to this decision, and it has real force:
  wrapping the two `qc_inspect` loops in a `with` would close the race with **zero**
  behavior change and no Decision 3 at all, whereas routing through the helper bundles a
  semantic change (bare → swallowing) into a race fix. Rejected on balance because the 4
  other dict-holding tools already do exactly this, because deleting `_close_figure`
  removes a real footgun, and because the swallow is an improvement in a `finally` (see
  Decision 3) — but the bundling is why this change also makes the swallow observable
  (Decision 7). Note the `plot_*` tools use an explicit `with` for a reason that does not
  apply here: they close a `list[Figure]`, not the `dict[str, Figure]` `close_figures`
  takes.
- *Make `remove_outliers._close_figure` acquire the lock.* Rejected: one acquisition **per
  figure**, which the issue explicitly rules out, and it lets a concurrent create interleave
  between figures of the same cleanup batch — the exact interleaving the lock prevents.

For the record, the issue text prescribes `with FIGURE_REGISTRY_LOCK:` and cites
`close_figures` only for *granularity*; this decision is a deliberate (and, per the
alignment review, faithful) reading of its intent, not a quotation of it.

### Decision 2 — Delete `remove_outliers._close_figure` instead of leaving it unused

Converting its 3 call sites leaves it dead. Keeping a private, lock-free, single-figure
close helper inside a plotting module is a live footgun: the next close site added to that
file reaches for the local helper and silently reopens this race. Verified it has zero
readers in `bloommcp/src` or `bloommcp/tests` (the `_close_figures*` names in
`test_plots_helpers.py` are tests of `_plots.close_figures` plus a local test helper).

### Decision 3 — Accept that `qc_inspect`'s close becomes best-effort

`close_figures` wraps each `plt.close` in `try/except`. `qc_inspect`'s two closes are
currently bare, so this is a real behavior change — and an improvement:

- Today, a raising `plt.close` mid-loop **aborts the loop**, leaking every figure after the
  failing one, in a `finally` whose entire purpose is to not leak.
- Today, that exception also **replaces whatever exception was already propagating** —
  masking the tool's real error (e.g. a disk-full `savefig`) with an `OrderedDict` artifact.
- `remove_outliers` already had swallow-and-continue semantics via `_close_figure`, so this
  makes the two tools agree rather than diverge.

**No persisted artifact changes.** Both `outputs` maps are fully populated in the `try` body
before either `finally` runs, and `store.commit` consumes that same dict afterwards; a close
failure has no path to mutate it. Pre-change the outcome of a raising close was "no report
at all" (the tool body `rmtree`s staging and re-raises), never "a report missing a figure",
and `create_run` writes nothing persistent, so no orphan run or version-number gap arises
either. Which artifacts are guaranteed is unchanged — `missing_data_pattern.png` stays
optional exactly as `_render_report` already documents.

Noted for the record: the exception most likely to have been raised there is precisely the
`RuntimeError("OrderedDict mutated during iteration")` this change prevents from being
raised at all. Swallowing is not how the race is fixed — holding the lock is; the swallow
only stops an unrelated cleanup failure from eating the caller's error. And because
swallowing removes the last raising close in the package, Decision 7 makes it observable.

**Divergence left in place:** the 3 converged `plot_*` tools do *not* swallow — they call
bare `plt.close(fig)` under the lock in a `finally` that runs *after* `store.commit`, so a
raising close there could produce a committed-but-reported-failed run. This change makes
`qc_inspect`/`remove_outliers` immune to that class and leaves those three exposed.
Deliberately out of scope for #808, recorded here so the next reviewer need not rediscover
it.

### Decision 4 — No nesting, so the non-reentrant lock is safe at all 5 sites

`FIGURE_REGISTRY_LOCK` is a plain `threading.Lock`; acquiring it inside a
`call_with_figure_cleanup` call would self-deadlock. Verified site by site by reading
control flow, and then **empirically**: an instrumented `plt.close` recording
`FIGURE_REGISTRY_LOCK.locked()` at every close, run against unmodified code, reports
`False` at all 5 sites — proof that none is nested — and the converted code was then
executed end-to-end at all 5 with no hang.

- `qc_inspect` site 1 — `eda_figs = call_with_figure_cleanup(...)` returns *before* the
  `try:`/`finally:` whose `finally` closes; the lock is released at that point.
- `qc_inspect` site 2 — the `call_with_figure_cleanup` call sits in its own
  `try/except Exception` (the best-effort heatmap); the close is in a **second, separate**
  `try/finally` after it.
- `remove_outliers` tool body — the `finally` close is outside `_make_figures` entirely.
- `remove_outliers` `_make_figures` ×2 — both the unknown-key raise and the
  unselected-subset discard run *after*
  `call_with_figure_cleanup(lambda: plot_outlier_analysis(...))` has returned.

`close_figures` can never be reached from inside a delegate (`sleap_roots_analyze` contains
zero references to `bloom_mcp`), and neither `apply_font_style` nor `savefig` touches `Gcf`
or acquires the lock. `_render_report` and `_make_figures` each have exactly one caller, at
tool-body level.

### Decision 5 — ADDED, as a new `bloommcp-figure-registry-safety` capability

No spec in `openspec/specs/` owns the lock contract today. The nearest statement of it —
`Requirement: Figure-Registry Concurrency Safety` — lives in
`converge-bloommcp-viz-tools`, **merged via PR #683 on 2026-09-11 but not yet archived**,
and is scoped to "each of the 3 tools" that change converges.

A `MODIFIED` delta is therefore not available (OpenSpec's `MODIFIED` requires pasting the
full existing requirement from `openspec/specs/<capability>/spec.md`, and that file does not
exist yet), and would be wrong even if it were: this change's subject is the process-wide
invariant across every figure-handling call site, orthogonal to and broader than a per-tool
requirement on 3 specific tools. Per OpenSpec's ADDED-vs-MODIFIED guidance ("prefer ADDED
when the change is orthogonal rather than altering the semantics of an existing
requirement"), this is ADDED. To make that superset claim honest rather than half-true, this
delta states the create-side invariant too (already satisfied everywhere) — otherwise it
would be *narrower* on one axis (close-only) while broader on the other, i.e. merely
half-overlapping.

**Canonical file, once both archive:** `openspec/specs/bloommcp-figure-registry-safety/`
owns the invariant; `bloommcp-viz-tools`' `Figure-Registry Concurrency Safety` is its
per-tool restatement for the 3 converged tools, and R1 names it as such so a later reader
does not mistake the narrower statement for the contract. Reducing that requirement to a
pointer is a follow-up, not attempted here (archiving is a separate PR by convention, and
two archive PRs — #815, #779 — are already open).

### Decision 6 — What the no-plots path actually preserves (corrected)

An earlier draft claimed `remove_outliers` keeps matplotlib out of its runtime import graph
and that an `include_plots=False` run leaves `matplotlib.pyplot` unimported. **Both are
false**, and were before this change: `remove_outliers.py:82` does
`from sleap_roots_analyze import plot_outlier_analysis, remove_outlier_samples` at module
level, and importing `sleap_roots_analyze` imports `matplotlib.pyplot` eagerly — verified,
`"matplotlib.pyplot" in sys.modules` is `True` immediately after importing the tool module.
(`sections/sleap_roots/__init__.py` also imports `qc_inspect`, whose module level runs
`matplotlib.use("Agg")` + `import matplotlib.pyplot as plt`.) `remove_outliers`' own comment
asserting the Tier-0 guarantee is therefore already inaccurate — pre-existing, not
introduced here, and left alone as out of scope.

What is true, and what this change preserves, is narrower but real: `close_figures`'
`if not figures: return` guard precedes its own `import matplotlib.pyplot as plt`, so on an
`include_plots=False` run (where `figures` is `{}`) the cleanup path executes **no fresh
`import matplotlib` statement of its own and takes the lock zero times** — matching
`_close_figure`'s never-called behavior exactly. Pinned by two tests: the repo's existing
idiom (`monkeypatch.setitem(sys.modules, "matplotlib", None)`, so any fresh import statement
raises `ImportError` — the same guard and the same corrected framing already used in
`test_umap_analysis_tool.py`, `test_clustering_tool.py`, `test_pca_analysis_tool.py` and
`test_heritability_analysis_tool.py`), and a counting proxy on the module's
`FIGURE_REGISTRY_LOCK` attribute asserting zero acquisitions.

### Decision 7 — Log the swallowed close

`close_figures`' two handlers are a bare `pass` today, `_plots.py` imports no logger, and
the other four callers log nothing. Since this change converts the last two raising closes
in the package, without a log line **no close failure anywhere in `bloommcp` would produce
any signal at all** — no log, no metric, no error. That is not acceptable for a fix whose
justification is a race that was reproduced deterministically: if the fix is ever incomplete
(a future create site bypassing `call_with_figure_cleanup`, a delegate registering figures
from its own thread, a matplotlib bump), the residual race becomes permanently invisible,
and its symptom — a leaked figure in a long-lived server process, at 53 pages per
cylinder-scale call — grows RSS until the container OOMs with nothing linking it to the
cause.

So `close_figures` gains `logger = logging.getLogger(__name__)` and logs a `WARNING` naming
the figure key and the exception on each swallowed close, on both the inner (per-figure) and
outer (import/batch) handler. The inner `# pragma: no cover` comes off, since a test now
exercises it.

### Decision 8 — Spec states behavior; routing stays in design

An earlier draft's spec required cleanup to "route through `close_figures`". That writes a
style preference into a spec, and would make the 3 `plot_*` tools compliant only by the
accident of holding a `list` rather than a `dict`. The delta now states only the *behavioral*
half (best-effort; does not abort the batch; does not mask the caller's error; logs), plus
the enforceable structural property the AST guard can actually check (every `plt.close` call
site lexically inside a lock acquisition). The preference for the shared helper lives here,
in Decision 1.

## Risks / Trade-offs

- **Lock contention, and a new post-commit wait.** At `remove_outliers` the converted close
  runs in a `finally` that executes *after* `store.commit` — the point at which the trimmed
  `outliers` version becomes resolvable by every `require_clean=True` consumer. A *raising*
  close there would leave a committed dataset the caller believes failed; that is not
  reachable, because `close_figures` never raises, exactly as the `_close_figure` it replaces
  never did. **This property is load-bearing, not incidental** — it is why `close_figures`
  (never a raising local `with` block) is the right helper at this site specifically. What
  the change does add post-commit is a **wait**: acquiring a process-wide lock can block for
  as long as any concurrent create holds it, and creates are not cheap — the batched `plot_*`
  tools deliberately consume their whole generator inside the lock, which at cylinder scale
  is 53 pages in one acquisition. Should a client time out inside that window the committed
  run is still fully traceable (`Provenance` stamps tool, params and seed into the
  `VersionEntry`) and discoverable via `list_existing_analyses`. Narrowing the window by
  closing before committing is deliberately not attempted: it reorders persistence relative
  to cleanup at a tool that writes a cleaned artifact, a larger change than this race fix.
- **No lock-order inversion.** Only `plt.close` executes under `FIGURE_REGISTRY_LOCK` and
  nothing under it acquires another lock, so there is no inversion with the result store's
  per-manifest `KeyedLock`. At `remove_outliers`, `commit`'s `KeyedLock` is always released
  (success or exception) before the `finally` acquires the figure lock — sequential, never
  nested. Contention can delay a call but cannot stall a session indefinitely.
- **Deadlock if Decision 4 were wrong** → the tests would hang, not fail (`bloommcp` has no
  `pytest-timeout`; a hang is bounded by `pr-checks.yml`'s `timeout-minutes: 20` on
  `python-audit`). Mitigated by the empirical verification in Decision 4 rather than by a new
  dependency; see `proposal.md` Non-Goals.
- **Swallowed cleanup errors** (Decision 3) — accepted and strictly better than the status
  quo, and now logged (Decision 7).
- **An `assert` raised inside a close spy is swallowed** by `close_figures`' inner handler.
  Every new test must therefore *record* lock state into a list and assert **outside** the
  spy. Stated in the tasks so a future test cannot pass vacuously by asserting inside one.
- **The pinning tests monkeypatch `matplotlib.pyplot.close` process-globally** (the module
  object is shared, which is *why* the spy is visible through `close_figures`' own lazy
  import). `monkeypatch` restores it per test. Note a spy must not be installed before a
  `plt.close("all")` hygiene call, or that call is itself recorded with the lock unheld.
- **`matplotlib.use("Agg")` at `remove_outliers.py`'s plots path is an unlocked global
  mutation.** Benign on the installed 3.10.8 (`use()` short-circuits on the same backend,
  and `switch_backend` no longer performs the `close("all")` its docstring still advertises),
  but `matplotlib>=3.7.0` is unbounded in `pyproject.toml`: a version restoring the
  documented behavior would blank other threads' in-flight figures, unlocked and invisible
  to the AST guard. Noted, not fixed.

## Migration Plan

None — internal refactor of cleanup paths plus one added log line. No API, schema, manifest,
output-key, or tool-signature change; no persisted artifact changes; no RNG or seeding
involvement; the scan → plant → wave → experiment traceability chain is untouched. Rollback
is a plain revert.

## Open Questions

None. The one judgment call (batch-close via `close_figures` vs. 5 local `with` blocks) is
settled in Decision 1, including the strongest case against it.
