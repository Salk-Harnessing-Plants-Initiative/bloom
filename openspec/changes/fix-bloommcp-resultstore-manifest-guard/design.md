## Context

Issue #596 was filed during #586/PR #588's review. PR #588 fixes
`experiment_utils._resolve_one_class`'s manifest read for the *reader* side, but is
**open, not yet merged** as of this writing — confirmed via `gh pr view 588`
(`state: OPEN`, `mergedAt: null`) and `git merge-base --is-ancestor` against this
proposal's branch (which forks from `origin/staging`, not from #588's branch). Verified
directly against this branch's actual code (not assumed):

- `experiment_utils._resolve_one_class` (`experiment_utils.py:415-418`) today catches
  only `ManifestSchemaError` around its `AnalysisDir.get_version()` call — this part is
  already merged, existing behavior. It has **no** bare-`Exception` fallback yet; that
  fallback, plus `FakeReader.fail_next_load` and `test_reader_parity.py`, exist only on
  PR #588's branch.
- `SupabaseResultStore` reaches the same underlying `AnalysisDir` read chain directly, at
  three call sites, with no guard of its own at any of them:
  - `create_run` (`adir.read_manifest()`) — outside `commit()`'s own hardened
    try/except (added by #324/PR #464, confirmed via `gh pr view 464`:
    "fix(#324): harden bloom-mcp ResultStore against orphaned objects + duplicate
    version ids", merged 2026-07-24).
  - `list_runs` (`adir.list_versions()`, itself calling `read_manifest()`).
  - `get_run` (`adir.get_version()`, itself calling `read_manifest()`).

Also verified against this branch: `list_runs`'s only production caller
(`sections/core/list_existing_analyses.py`) already wraps the call in `except Exception`
(bloom#585's disclosed-narrow-exception pattern) — accidentally safe, and today
stringifies the raw exception straight into agent-facing JSON
(`errors.append(f"{tool_class}: {exc}")`) — a real info-leak path this change
incidentally closes. `get_run` has no production caller anywhere under `bloommcp/src`
today — dead code, structurally unreachable in prod. `create_run`'s production callers
are all registered via `@as_mcp_tool` (`contract/wrap.py`), whose catch-all maps any
exception to a structured `BloomMCPError` before it reaches an agent; none declares a
`ResultStore`-specific type in its `errors=` tuple, so changing the raised type here
breaks nothing.

**No dependency on PR #588.** This proposal borrows the already-merged half of
`_resolve_one_class`'s pattern (catch `ManifestSchemaError` specifically) and
independently authors the not-yet-merged half's shape (a bare-`Exception` fallback, plus
a one-shot fake-failure-injection hook) for the result-store port. It does not require
#588 to merge first, and shares no code with it — the two ports (`ExperimentReader` and
`ResultStore`) have no common implementation.

**Unarchived sibling spec.** `openspec/changes/update-bloommcp-resultstore-fake-parity/`
(#325, PR #465, merged 2026-07-21) shipped `FakeResultStore`'s fuller behavior
(`fail_next_commit`, duplicate-id reallocation, v2-manifest back-compat) in code, but
that OpenSpec change was never archived — `openspec/specs/bloommcp-result-store/spec.md`'s
"FakeResultStore Adapter" section still shows only its pre-#325 shape (2 scenarios),
stale relative to actual code. This change's `FakeResultStore Adapter` delta is written
against the fuller, code-accurate text (the same text `update-bloommcp-resultstore-fake-parity`'s
own unarchived delta already carries), not the stale deployed text, so it can be
maintained as one coherent requirement. **Ordering implication:** if
`update-bloommcp-resultstore-fake-parity` is archived independently of this change using
its own (older) delta text, it will overwrite `openspec/specs/bloommcp-result-store/spec.md`
and silently drop this change's manifest-read scenario. Whoever archives either change
should archive both together, or archive `update-bloommcp-resultstore-fake-parity` first
and re-derive this change's delta against the freshly-archived spec before archiving this
one.

## Goals / Non-Goals

- Goals: make each of the three read call sites caller-safe on its own, independent of
  any particular caller getting its own error handling right; keep `FakeResultStore` and
  `SupabaseResultStore` observably equivalent for this failure mode; preserve a
  type-level (not just message-level) distinction between "storage flaked" and "manifest
  schema unsupported."
- Non-Goals: touching `commit()`'s existing try/except (already hardened by #324/#464);
  changing any tool caller or `list_existing_analyses.py` (their catch-alls stay in
  place, now just backstopping a narrower, structured error type instead of an arbitrary
  exception); giving `get_run` a production caller (out of scope — it becomes read-safe
  regardless of whether/when one appears); archiving `update-bloommcp-resultstore-fake-parity`
  (flagged above, but a separate housekeeping change, not bundled into this one).

## Decisions

- **Guard every call site rather than continuing to rely on caller-side nets** (closes
  issue #596's checklist item 1). A future tool registered through `contract/wrap.py`'s
  bare `register()` path (no catch-all — `list_existing_analyses.py` and
  `load_experiment_data` are *already* registered this exact way today, in
  `sections/core/__init__.py`) or a future direct caller of `get_run` would otherwise
  inherit today's unguarded exception. Guarding at the port adapter, not the caller,
  removes the dependency on every current and future caller getting this right.

- **`create_run`'s guard stays independent of `commit()`'s try/except** (closes issue
  #596's checklist item 3). `create_run` runs outside `commit()`'s per-key lock, often
  well before `commit()` is even called — the tool computes its analysis in between, so
  there is no shared critical section to extend into. It gets its own try/except around
  `adir.read_manifest()` only — not the surrounding `next_version_id(...)` call, so a bug
  in that pure allocation logic (which does not raise today) can never be mislabeled as
  a manifest-read failure. This follows the same shape as `commit()`'s guard (log
  server-side, raise a structured port error) without sharing code or a lock scope with
  it.

- **Two error types, not one, with a real subclass relationship:**
  `ManifestReadError(ResultStoreError)` for the generic branch, and
  `ManifestIncompatibleError(ManifestReadError)` for the `ManifestSchemaError` branch. A
  single shared type (collapsing both into one `ManifestReadError`, distinguished only
  by message text) was considered and rejected during review: nothing downstream could
  `isinstance()`-branch on it, and a future automatic-retry wrapper could retry a
  permanently-unsupported manifest forever, believing it transient. The subclass
  relationship means every existing `except ManifestReadError` / `except
  ResultStoreError` / `except Exception` still catches both, but a caller that cares can
  distinguish them.

- **`ManifestReadError`'s generic branch does not claim the failure is transient.**
  Caught during review: `except ManifestSchemaError` is narrow (only a too-new or
  missing schema version), so the generic `except Exception` fallback is genuinely a
  catch-all — it also catches a `json.JSONDecodeError` from a truncated/corrupt
  `manifest.json`, a `pydantic.ValidationError` from a shape-invalid one (`Manifest`
  validates under `extra="forbid"`), and a permanent storage-permission/RLS denial, none
  of which a retry would ever fix. An earlier draft's message included
  `"(transient — retry)"` (mirroring `CommitFailedError`'s existing convention, where
  upload/manifest-write failures genuinely are I/O-only) — that claim is dropped here
  since it would be false for this branch's non-transient members. This is exactly the
  same "collapsed distinction" risk that motivated splitting out
  `ManifestIncompatibleError` in the first place, reappearing via a different path (bad
  content vs. an unsupported schema version) — so `ManifestIncompatibleError`'s branch is
  now logged at `error` (not `warning`), since an unsupported schema at least names an
  actionable fix (a server upgrade) that the generic bucket may not.

- **`ManifestIncompatibleError`'s message says "unsupported," not "newer."**
  `validate_schema` raises `ManifestSchemaError` for two distinct causes — a schema
  version newer than this code understands, *and* a manifest missing the
  `manifest_schema_version` field entirely — so a message hardcoding "is newer than this
  server understands" would misdescribe the missing-field case. "Unsupported" stays
  accurate for either, with the underlying `ManifestSchemaError`'s own text still
  interpolated for detail (matching `_resolve_one_class`'s existing (merged) convention
  for that same exception type — its messages are structured schema-version detail, not
  raw storage/network internals, so including them is not a leak).

- **Message content otherwise mirrors each branch's actual safety profile.** The
  generic-exception message never includes the raw exception text
  (`f"manifest read failed for {tool_class}/{stem}"`), matching `commit()`'s own
  established no-leak convention (its message is exc-free by construction, not by
  redaction) — a regression test pins the template rather than asserting active
  redaction, since there is no dynamic scrubbing step to test.

- **`FakeResultStore` gets a `fail_next_read` hook, not real I/O to fail on.** A flat
  in-memory store has no read to fail organically, so the fake needs an explicit
  injection point to exercise the guard at all — the same reasoning
  `fail_next_commit` already established for commit failures on this exact adapter.
  One-shot, keyed by `(experiment, tool_class)`, consumed by whichever of `create_run` /
  `list_runs` / `get_run` is called *first* for that key (checked at the top of each
  method, before any other logic), raising `ManifestReadError` (the fake does not model
  the schema-incompatible subtype — manifest schema parsing is a real-backend-only
  concern the flat in-memory model has no equivalent of). Its check-then-discard on the
  shared `_fail_next_read` set is itself guarded by a plain `threading.Lock` — flagged
  during review: unlike `fail_next_commit` (only ever consumed inside `commit()`'s own
  per-key lock), this hook had no mutual exclusion of its own, so two threads racing the
  same armed key could both observe it set before either discarded it. Test-only code, so
  production risk was nil, but the fix is cheap and keeps the "consumed by whichever is
  called first" contract actually true under real concurrency, not just single-threaded
  test usage.

## Risks / Trade-offs

- Broadening `except Exception` at three more call sites could, in principle, swallow a
  real programming error (e.g. a `TypeError` in adapter code) and re-raise it as
  `ManifestReadError` — the same accepted trade-off `_resolve_one_class`'s established
  shape already makes for its own bare-`Exception` case (in PR #588). `logger.exception`
  keeps the original traceback visible server-side, so this is observable, not silent.
- `fail_next_read`'s cross-method key (rather than one hook per method) means a test that
  calls a different method than intended first for the same `(experiment, tool_class)`
  consumes the flag on the wrong call — a false pass, not a false fail. Mitigated by an
  explicit ordering test (tasks.md 4.6) proving the "whichever is called first" contract,
  so this is a documented, tested property rather than an implicit assumption.
- **Disclosed residual gap, out of scope for this change:** in `list_runs`,
  `_guarded_manifest_read` wraps only `adir.list_versions()` itself — the list
  comprehension projecting each returned `VersionEntry` through
  `StoredRun.from_version_entry` runs *after* the guarded call returns, unguarded. A
  failure there (not observed today — it is a plain dataclass/`.model_dump()`
  projection over already-validated Pydantic models) would still escape as a raw
  exception through `list_existing_analyses.py`'s `except Exception`, which — flagged
  during review — stringifies it straight into agent-facing JSON
  (`errors.append(f"{tool_class}: {exc}")`) rather than through that file's own
  `safe_error_text()` helper (already used two lines below for a different error). This
  change closes that leak only for failures inside the manifest read itself, which is
  issue #596's actual scope (`AnalysisDir.read_manifest`/`list_versions`/`get_version`,
  not `StoredRun.from_version_entry`'s projection step) — worth a follow-up if this
  projection step ever grows a way to fail.

## Migration Plan

Adapter-internal change only; no schema or data migration. No behavior change on any
existing caller's happy path — only the shape of what escapes on a read failure changes
(raw exception → `ManifestReadError`/`ManifestIncompatibleError`), and both are
subclasses of `Exception`, so every existing `except Exception` catch-all keeps working
unmodified.

## Open Questions

None — issue #596's own checklist item 1 (dead-code / bypass-plausibility check) is
resolved above as "a plausible bypass exists," settling the fork it left open in favor of
adding the guards (checklist item 2) rather than closing with a no-code-change rationale.
