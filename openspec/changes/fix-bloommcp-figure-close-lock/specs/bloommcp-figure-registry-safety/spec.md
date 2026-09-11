## ADDED Requirements

### Requirement: Process-Wide Figure-Registry Lock Coverage

Every `matplotlib.pyplot.close` call reachable from a `bloom_mcp` tool SHALL execute while
`bloom_mcp.tools._plots.FIGURE_REGISTRY_LOCK` is held, on **every** exit path — success,
validation failure, discard of an unrequested figure, and exception cleanup alike.

`matplotlib`'s pyplot figure registry (`Gcf.figs`) is a single process-wide `OrderedDict`,
and FastMCP dispatches sync tool handlers on a thread pool, so two figure-handling tool
calls in this process can genuinely interleave against it. `plt.close(fig)` →
`Gcf.destroy_fig` **scans** `Gcf.figs.values()` to find the owning manager, and that scan is
unsynchronized: a concurrent lock-holding create (`Gcf.set_active` inserts then
`move_to_end`s) mutating the dict mid-scan raises
`RuntimeError("OrderedDict mutated during iteration")` out of the *closing* caller.

Locking figure **creation** alone SHALL NOT be treated as satisfying this requirement.

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

#### Scenario: No unlocked close site remains in the package

- **WHEN** `bloom_mcp`'s source is inspected for `plt.close` call sites
- **THEN** every one of them is either inside a `FIGURE_REGISTRY_LOCK` acquisition or inside
  `bloom_mcp.tools._plots.close_figures`, and no module defines a private lock-free
  close-a-figure helper of its own

### Requirement: Batch Close Acquisition Discipline

A tool closing more than one figure SHALL acquire `FIGURE_REGISTRY_LOCK` **once per close
batch**, not once per figure, so no concurrent create can interleave between two figures of
the same cleanup.

The lock SHALL NOT be held across the `savefig`, CSV-write, or result-store `commit` work
that sits between figure creation and cleanup: hold time stays proportional to registry
mutation, never to disk I/O.

`FIGURE_REGISTRY_LOCK` is non-reentrant, so a close batch SHALL NOT be nested inside a
`bloom_mcp.tools._plots.call_with_figure_cleanup` call, which already holds the same lock.

#### Scenario: A multi-figure cleanup takes one acquisition

- **WHEN** a tool closes a set of two or more figures
- **THEN** the lock is acquired once before the first `plt.close` and released after the
  last, rather than acquired and released per figure

#### Scenario: Persistence I/O runs unlocked

- **WHEN** a tool writes its figures to disk with `savefig` and commits a run between
  creating and closing those figures
- **THEN** that I/O executes with `FIGURE_REGISTRY_LOCK` **not** held

#### Scenario: Nothing to close acquires no lock

- **WHEN** a tool call produces no figures at all (for example, `include_plots=False`)
- **THEN** its cleanup path acquires `FIGURE_REGISTRY_LOCK` not at all, and does not import
  `matplotlib.pyplot` on behalf of the cleanup

### Requirement: Shared Batch-Close Helper

Figure cleanup SHALL route through `bloom_mcp.tools._plots.close_figures` — the single
shared implementation of "close a batch of figures under the lock" — rather than an ad hoc
per-module close helper, wherever the cleanup owns a `dict[str, Figure]`.

`close_figures` SHALL remain best-effort: a failure closing one figure SHALL NOT abort the
batch (leaking the remaining figures) and SHALL NOT replace an exception already propagating
through the `finally` that invoked it.

#### Scenario: One failing close does not strand the rest of the batch

- **WHEN** `plt.close` raises on one figure of a multi-figure cleanup batch
- **THEN** the remaining figures in that batch are still closed, and the failure is not
  re-raised to the caller

#### Scenario: Cleanup does not mask the caller's error

- **WHEN** a tool is already raising a `BloomMCPError` and its `finally` cleanup encounters a
  failing `plt.close`
- **THEN** the caller still observes the original `BloomMCPError`, not the cleanup failure

### Requirement: Lock Contract Documentation Accuracy

`bloom_mcp.tools._plots.FIGURE_REGISTRY_LOCK`'s explanatory comment SHALL enumerate the
create-side and close-side call sites accurately, and SHALL NOT claim an outstanding
unlocked close site once none remains.

This comment is the only place the two-phase contract and its rationale are written down;
a stale "still outstanding" entry there misleads the next contributor into believing the
race is open (or that a already-deleted helper still needs wiring).

#### Scenario: The comment reflects a fully locked close side

- **WHEN** every close site in `bloom_mcp` holds the lock
- **THEN** the lock's comment states that, and names no site or tracking issue as still
  outstanding
