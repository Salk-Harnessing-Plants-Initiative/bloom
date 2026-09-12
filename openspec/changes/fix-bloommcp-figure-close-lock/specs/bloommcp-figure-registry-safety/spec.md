## ADDED Requirements

### Requirement: Locked Figure Creation

Figure creation SHALL go through `bloom_mcp.tools._plots.call_with_figure_cleanup`.
Every call site in `bloom_mcp`'s own source that creates a matplotlib figure, or that calls
a delegate which creates one, MUST use it — directly, or via
`bloom_mcp.tools._plots.generate_figures`, which calls it per key. That helper holds
`FIGURE_REGISTRY_LOCK` for the duration of the call and, on exception, closes any figure
registered during it before re-raising.

This is the create half of the lock's two-phase contract. It is already satisfied
everywhere; it is stated here so this capability owns the whole contract rather than half
of it, and so a new figure-creating tool cannot be added without it.

#### Scenario: A figure-creating delegate call holds the lock

- **WHEN** a `bloom_mcp` tool calls a delegate that creates one or more figures
- **THEN** `FIGURE_REGISTRY_LOCK` is held for the duration of that call, and released once
  it returns or raises

#### Scenario: A delegate that allocates and then raises leaks no figure

- **WHEN** a figure-creating delegate registers one or more figures and then raises before
  returning them
- **THEN** the tool raises a structured `BloomMCPError` and matplotlib's global figure
  registry holds exactly the figures it held before the call

### Requirement: Locked Figure Close

Every `matplotlib.pyplot.close` **call site in `bloom_mcp`'s own source** SHALL execute
while `bloom_mcp.tools._plots.FIGURE_REGISTRY_LOCK` is held, on every exit path — success,
validation failure, discard of an unrequested figure, and exception cleanup alike.

`matplotlib`'s pyplot figure registry (`Gcf.figs`) is a single process-wide `OrderedDict`,
and FastMCP dispatches sync tool handlers on a thread pool, so two figure-handling tool
calls in this process can genuinely interleave against it. `plt.close(fig)` →
`Gcf.destroy_fig` **scans** `Gcf.figs.values()` to find the owning manager, and that scan is
unsynchronized: a concurrent lock-holding create inserting a new figure number into the dict
mid-scan raises `RuntimeError("OrderedDict mutated during iteration")` out of the *closing*
caller.

Locking figure creation alone SHALL NOT be treated as satisfying this requirement.

The delegate (`sleap_roots_analyze`) closes figures internally in some plotters. Those
closes satisfy this contract only transitively, because every `bloom_mcp` call site that
reaches a figure-handling delegate wraps it per **Locked Figure Creation**, which holds the
lock for the delegate's whole call. A `bloom_mcp` tool SHALL NOT call a delegate entry point
that creates or closes matplotlib figures outside that wrapper; verification of this
requirement by inspecting `bloom_mcp`'s own source is conditional on that, and on the pinned
delegate version.

The nearest narrower statement of this invariant is `bloommcp-viz-tools`'
`Requirement: Figure-Registry Concurrency Safety`, which restates it for the 3 tools #466
converged. This capability is canonical; that one is a per-tool restatement.

#### Scenario: A tool's success-path close holds the lock

- **WHEN** any `bloom_mcp` tool closes the figure(s) it created after successfully
  persisting them
- **THEN** `FIGURE_REGISTRY_LOCK` is held at the moment each `plt.close` executes, and is
  released once that close batch returns

#### Scenario: A tool's failure-path and discard-path closes hold the lock

- **WHEN** a tool closes figures because it is aborting (a validation failure, a persistence
  failure, or a delegate exception) or because it is discarding figures the caller did not
  request
- **THEN** `FIGURE_REGISTRY_LOCK` is held at the moment each `plt.close` executes

#### Scenario: `remove_outliers` discarding unrequested figures holds the lock

- **WHEN** `remove_outliers` runs with `include_plots=True` and a `plots` subset naming
  fewer keys than the method produces, so the remainder are discarded
- **THEN** each discarding `plt.close` executes with the lock held, **and** the persisted
  `outputs` contains the requested figure and none of the discarded ones

#### Scenario: No unlocked close call site remains in the package

- **WHEN** `bloom_mcp`'s own source is parsed and every `pyplot.close` call site located
- **THEN** each one is lexically inside a `with FIGURE_REGISTRY_LOCK:` block

### Requirement: Batch Close Acquisition Discipline

A tool closing more than one figure SHALL acquire `FIGURE_REGISTRY_LOCK` **once per close
batch**, not once per figure, so no concurrent create can interleave between two figures of
the same cleanup.

The lock SHALL NOT be held across the `savefig`, CSV-write, or result-store `commit` work
that sits between figure creation and cleanup: hold time stays proportional to registry
mutation, never to disk I/O.

`FIGURE_REGISTRY_LOCK` is non-reentrant, so a close batch SHALL NOT be nested inside a
`bloom_mcp.tools._plots.call_with_figure_cleanup` call, which already holds the same lock.

#### Scenario: A multi-figure cleanup takes exactly one acquisition

- **WHEN** a tool closes a batch of three or more figures
- **THEN** the lock is entered exactly once for that batch, not once per figure

#### Scenario: Persistence I/O runs unlocked

- **WHEN** a tool writes its figures to disk with `savefig` and commits a run between
  creating and closing those figures
- **THEN** that I/O executes with `FIGURE_REGISTRY_LOCK` **not** held

#### Scenario: Nothing to close acquires no lock

- **WHEN** a tool call produces no figures at all (for example, `include_plots=False`)
- **THEN** its cleanup path acquires `FIGURE_REGISTRY_LOCK` not at all, and executes no
  fresh `import matplotlib` statement on behalf of the cleanup

### Requirement: Best-Effort, Observable Close

Figure cleanup SHALL be best-effort: a failure closing one figure SHALL NOT abort the batch
(leaking the remaining figures) and SHALL NOT replace an exception already propagating
through the `finally` that invoked it.

A swallowed close SHALL NOT be silent. Each one SHALL be logged at `WARNING` naming the
figure and the exception, because a failed close leaks a figure in a long-lived server
process and, once no close site raises, the log line is the only remaining signal that a
registry race is still occurring.

#### Scenario: One failing close does not strand the rest of the batch

- **WHEN** `plt.close` raises on one figure of a multi-figure cleanup batch
- **THEN** the remaining figures in that batch are still closed, and the failure is not
  re-raised to the caller

#### Scenario: Cleanup does not mask the caller's error

- **WHEN** a tool is already raising and its `finally` cleanup encounters a failing
  `plt.close`
- **THEN** the caller still observes the original error, not the cleanup failure

#### Scenario: A swallowed close is not silent

- **WHEN** `plt.close` raises on a figure of a cleanup batch
- **THEN** a `WARNING` is logged naming the figure and the exception

### Requirement: Lock Contract Documentation Currency

`bloom_mcp.tools._plots.FIGURE_REGISTRY_LOCK`'s explanatory comment SHALL NOT describe any
close site as outstanding or unlocked once every close site in `bloom_mcp` holds the lock.

This comment is the only place the two-phase contract and its rationale are written down, so
a stale "still outstanding" entry misleads the next contributor into believing the race is
open, or that an already-deleted helper still needs wiring.

#### Scenario: The comment reflects a fully locked close side

- **WHEN** every close site in `bloom_mcp` holds the lock
- **THEN** the lock's comment names no site as still outstanding, and names
  `close_figures` as the close path for the tools that use it
