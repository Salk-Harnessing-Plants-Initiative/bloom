## Context

`read_manifest(prefix)` already lists only the experiment's own prefix to check whether
`manifest.json` exists. So for a stem the operator names, direct resolution costs O(1). The
shared-root listing is needed only to _discover_ stems.

## Decisions

### Decision 1: Identifiers resolve through `Path(identifier).stem`, the same as `AnalysisDir`

Operators pass the same experiment identifier the MCP tools received: a filename under the local
backend, or a numeric id under Supabase. The scan then resolves the same prefix a tool wrote, so
the two cannot drift apart. `test_audit_scope.py` pins this with an `AnalysisDir` parity table.

A bare stem that contains a dot (`exp.v2`) is truncated to `exp`, exactly as a tool would truncate
it. To re-target a stem copied from a full-sweep report, pass `<stem>.csv`. The full sweep itself
rebuilds `AnalysisDir(f"{stem}.csv")` this way, so the round trip is exact. Every
`experiment_scope` entry records `requested` next to the resolved `stem`, and an unmatched stem
shows up in `unevaluated`, so a wrong derivation is always visible.

### Decision 2: Validate the raw value up front and exit `2`

`Path(...).stem` strips directory parts (`"../x.csv"` becomes `x`), so a check on the stem would
let path traversal through. Validation therefore runs on the raw value, and rejects:

- a non-string value, or an empty or whitespace-only one;
- any value containing `/`, `\` or NUL;
- any value whose stem is empty, `.` or `..`.

An empty `experiments` sequence is also rejected, because a silent full sweep is never what
`[]` meant. `run()` resolves the scope _before_ the scan's `except Exception` block. That way a
`ValueError` prints to stderr and exits `2`, the same code argparse uses for usage errors, and
never reaches the enumeration-failure path, which exits `1`.

### Decision 3: Disclosure in a scoped run

- **Scope record placement.** `experiment_scope` goes in the persisted payload beside
  `scanned_at`, `storage_backend` and `scope_note`, not in the scan dict. As a result, a full
  sweep's scan result keeps its exact shape, and the exact-equality tests stay green without edits.
  The name deliberately avoids the existing `scope_note` and `SCOPE_NOTE`, which describe the
  _detection_ caveat rather than which experiments ran.
- **Unevaluated experiments.** In a full sweep, a prefix that has no manifest came from
  enumeration and is unremarkable. In a scoped run the operator _named_ the experiment, so "nothing
  here" is the answer and must be reported. It goes in `unevaluated` with reason `no_manifest` or
  `no_latest`, not in `errors`. For the fit audit, an experiment never trimmed after #420 simply has
  no `outliers_<stem>` manifest.
- **All-errored runs.** A scoped run never lists the root, so an unreachable backend surfaces
  as a per-stem error on every requested experiment. When _every_ requested experiment errors,
  `run()` prints the result, persists nothing and returns `1`. This preserves the full sweep's
  "nothing to report, fail loudly" contract. If at least one experiment was read, the run
  completes and exits `0`, as it does today.

### Decision 4: The helper is pure, and each script keeps its own enumeration

The existing `run()` tests patch each script's module-level `list_prefix` to simulate an
enumeration failure. Moving that call into a shared module would silently turn those tests into
no-ops. So `audit_scope` does no storage I/O. It ships in the wheel, so it must import cleanly
with no environment set; `walk_packages` in `release-bloommcp.yml` imports every module.

### Decision 5: `run(argv=())`

Under pytest, `argparse.parse_args(None)` would read pytest's own `sys.argv`. So `run()` parses
only the arguments it is given, and `main()` passes `sys.argv[1:]`. The existing `audit.run()`
calls keep working unmodified.

## Risks / Trade-offs

- A scoped run says nothing about any experiment outside its scope. `experiment_scope` and the
  summary-line prefix make that explicit.
- The full-sweep collective failure mode remains. It is tracked in #919.
