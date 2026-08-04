## Why

Issue #596 (deferred from #586/PR #588): `SupabaseResultStore.create_run`, `list_runs`,
and `get_run` each read the manifest (`AnalysisDir.read_manifest`/`list_versions`/
`get_version`) with no guard of their own. Today a storage/network failure (transient or
not — a corrupt/shape-invalid `manifest.json`, a permanent permission denial, or an
actual network blip all reach the same unguarded call), or a manifest whose schema
version is unsupported (`ManifestSchemaError`: missing or newer than this server
understands), escapes as a raw exception from all three call sites, and safety
rests entirely on caller-side catch-alls — `@as_mcp_tool`'s exception mapping for
`create_run`'s tool callers, `list_existing_analyses.py`'s own `except Exception` for
`list_runs` (which today stringifies the raw exception straight into agent-facing JSON —
a real, not just theoretical, info-leak path) — rather than on the read itself. `get_run`
has no caller-side net at all today (no production caller — dead code, structurally
unreachable in prod), and `create_run`'s read sits *outside* `commit()`'s own hardened
try/except (added by #324/PR #464).

`experiment_utils._resolve_one_class` already guards its own manifest read against
`ManifestSchemaError` (merged, existing behavior). PR #588 (open, not yet merged as of
this writing) additionally adds a bare-`Exception` fallback there plus a
`FakeReader.fail_next_load` test hook, for the *reader* side. This proposal applies the
same two-part shape to the *result-store* side independently — it does not depend on PR
#588 merging first, and does not literally share code with it (the two ports have no
shared implementation to begin with).

## What Changes

- Add two caller-safe errors to `result_store/ports.py`: `ManifestReadError
  (ResultStoreError)` for the generic branch — deliberately does not claim the failure
  is transient, since it also covers non-transient causes (see design.md) — and
  `ManifestIncompatibleError(ManifestReadError)` — a subclass, not a sibling — for a
  `ManifestSchemaError` (manifest schema missing or newer than this server understands).
  Both exported from `result_store/__init__.py`.
- Wrap `AnalysisDir.read_manifest()`/`list_versions()`/`get_version()` at each of
  `SupabaseResultStore.create_run`, `list_runs`, and `get_run` in its own `try/except`:
  `ManifestSchemaError` first (→ `ManifestIncompatibleError`, logged at `error`), then
  bare `Exception` (→ `ManifestReadError`, logged via `logger.exception`). The try body
  wraps only the read call itself, not surrounding pure logic (e.g. `create_run`'s
  `next_version_id(...)` call), so a bug in that logic can't be mislabeled as a
  manifest-read failure.
- `create_run`'s new guard is independent of `commit()`'s existing hardened try/except —
  not folded into it (different call, outside `commit()`'s per-key lock; see design.md).
- Add a `fail_next_read(experiment, tool_class)` one-shot injection hook to
  `FakeResultStore`, mirroring `fail_next_commit`'s established one-shot pattern, so the
  new guard is exercisable in `test_store_parity.py` with no live Supabase adapter (the
  in-memory fake has no real I/O of its own to fail). Consumed by whichever of
  `create_run`/`list_runs`/`get_run` is called first for that key; its check-then-discard
  is protected by its own `threading.Lock` so the one-shot contract holds under real
  concurrency, not just single-threaded test usage.
- No change to `list_existing_analyses.py` or any `create_run` tool caller — their
  existing catch-alls remain in place, now backstopping a structured `ManifestReadError`
  instead of an arbitrary exception type (and, incidentally, closing `list_runs`'
  existing raw-exception-text leak into agent-facing JSON).

## Impact

- Affected specs: `bloommcp-result-store` (MODIFIED: SupabaseResultStore Adapter,
  FakeResultStore Adapter)
- Affected code: `bloommcp/src/bloom_mcp/result_store/{ports.py,supabase_store.py,fake_store.py}`
- Affected tests: `bloommcp/tests/result_store/{test_supabase_result_store.py,test_store_parity.py,test_fake_result_store.py}`
- Spec bookkeeping note: `openspec/changes/update-bloommcp-resultstore-fake-parity/`
  (PR #465, merged 2026-07-21) shipped `FakeResultStore`'s fuller failure-injection
  behavior in code but was never archived, so the deployed
  `openspec/specs/bloommcp-result-store/spec.md`'s "FakeResultStore Adapter" section is
  stale (2 scenarios) relative to actual code. This change's delta is written against
  the fuller, code-accurate text (matching that unarchived change's own delta), not the
  stale deployed text — see design.md for the archive-ordering implication.
- Refs: #596, #586, PR #588, #324, PR #464, #585
