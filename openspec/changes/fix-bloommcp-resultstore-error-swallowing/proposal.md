## Why

[#640](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/640) — when
`qc_inspect` (or any of its 7 sibling write-and-link analysis tools) fails inside
`ResultStore.create_run()`/`commit()`, the agent sees a bare `internal_error` with a
correlation ref and no indication of what actually went wrong. It has to read server logs
(or source) to find out. The issue was filed against a misconfigured local-storage-backend
scenario (`BLOOM_STORAGE_URL` unset — see #639): the tool call returned only a ref id, no
mention of storage, no mention of an env var.

**The issue's own suggested fix no longer applies to current `staging`.** It proposed
adding `StorageBackendError` to `qc_inspect`'s `errors=` tuple. Re-checked directly against
today's code (not the issue's point-in-time snapshot — `add-bloommcp-signed-url-download`
(#581, PR #595) landed in the interim): `SupabaseResultStore.commit()`'s own
`except Exception` block, and its `_guarded_manifest_read` helper, already catch *every*
raw failure inside `create_run`/`commit` — including a `StorageBackendError` raised by
`create_signed_url` — and re-raise it as a `ResultStoreError` subtype (`CommitFailedError`
or `ManifestReadError`) carrying its own already-redacted, caller-safe message. This
mirrors, on the write side, the intent of the "adapters MUST NOT leak a filesystem path,
bucket name, or storage traceback" contract `ExperimentReadError`'s docstring already
documents for the read side (`ResultStoreError`'s own docstring doesn't yet spell this out
as explicitly — tightened as part of this change, see `design.md` Decision 4.4).
`StorageBackendError` itself never reaches a tool function on this path any more —
declaring it in `errors=` today would be a no-op.

**Second correction (found during review):** for the local storage backend specifically,
the literal #640/#639 repro is not merely "relabeled" — it's **structurally gone**.
`add-bloommcp-local-url-defaults` (#642, PR #643, already merged and an ancestor of this
branch) changed `commit()`'s local-backend branch to hand back a direct filesystem path
instead of calling `create_signed_url` at all, so `BLOOM_STORAGE_URL` being unset no longer
causes any failure on that path (proven by the existing `test_local_mode.py` test that runs
`qc_clean`/`pca_analysis` end-to-end with it unset and asserts success). `CommitFailedError`/
`ManifestReadError` still fire for other real failures on both backends — a Supabase-backend
signing/upload failure, a local-backend permission/ENOSPC error, a manifest read/schema
failure — so the fix below remains necessary, just not for the exact symptom the issue
reporter originally saw. See `design.md`'s Context for the full correction.

The real, current gap is one level up: none of the 8 write-and-link analysis tools
(`qc_clean`, `qc_inspect`, `clustering`, `pca_analysis`, `remove_outliers`,
`descriptive_stats`, `cross_experiment_correlations`, `umap_analysis`) declare anything
from `bloom_mcp.result_store` in their `@as_mcp_tool(errors=...)` tuple — only
`ExperimentReadError` (the *read*-side base) is declared. So when `store.create_run()` or
`store.commit()` raises its own already-safe `CommitFailedError`/`ManifestReadError`, the
contract wrapper (`contract/wrap.py`'s `BloomMCPError.from_exception`) still can't tell it
apart from a truly undeclared failure, and discards the safe message anyway, returning only
`"An unexpected internal error occurred (ref: ...)."` The issue's own broader ask —
"Audit other tools that call `store.commit()`... for the same gap rather than fixing
`qc_inspect` alone" — is exactly right, just needs updating to name the exception types
that actually reach these tools today.

## What Changes

- Add `CommitFailedError` and `ManifestReadError` (both from `bloom_mcp.result_store`) to
  the `errors=` tuple of all 8 write-and-link analysis tools, alongside the existing
  `ExperimentReadError`. `ManifestIncompatibleError` (a `ManifestReadError` subclass) is
  covered automatically via `isinstance`, matching how `ExperimentReadError`'s own
  subclasses are already covered by declaring the base.
- Deliberately **not** the full `ResultStoreError` base (see `design.md` Decision 1) — two
  of its subtypes, `RunStateError` and `CorruptRunLinksError`, are documented as "never a
  caller-input condition," always a wiring/structural bug; they should stay mapped to
  `internal_error`, matching this same file's own existing precedent
  (`qc_inspect.py`'s `_samples_lost` raises `internal_error` directly for delegate
  contract-drift rather than relying on a declared exception). Two more subtypes
  (`RunNotFoundError`, `OutputFileMissingError`) are raised only by `get_run`/`list_runs`/
  `get_download_links`, none of which these 8 tools call — declaring them would be inert,
  not wrong, but adds surface area with no reachable benefit.
- Add regression tests (via `FakeResultStore.fail_next_commit`/`fail_next_read`, already
  used by `test_store_parity.py`) proving a `create_run`/`commit` failure now surfaces as a
  `tool_error` carrying the store's own message, not a bare `internal_error` ref — two
  cases (a commit failure, a manifest-read failure) for every one of the 8 tools, 16 tests
  total, not `qc_inspect` alone.
- Add three more regression tests closing gaps `openspec-review` found (see `design.md`
  Decision 4): a contract-layer unit test proving `ManifestIncompatibleError` (never
  directly simulatable via the fakes) is still caught via `isinstance` subclassing; a
  tool-boundary test proving `RunStateError` still maps to `internal_error` (not widened by
  this change); and a test proving a passed-through `CommitFailedError`/`ManifestReadError`
  message excludes planted unsafe content, not just that it passes through at all.
- Tighten `ResultStoreError`'s docstring (`result_store/ports.py`) to state the same
  explicit no-leak obligation `ExperimentReadError`'s docstring already states, so the
  claim above is backed by the code it cites.
- Update the `bloommcp-tool-contract` spec's "Structured Agent-Safe Errors" requirement:
  a tool whose body persists through `ResultStore.create_run`/`commit` SHALL declare
  `CommitFailedError` and `ManifestReadError` in its `errors=` tuple, so a write-port
  failure is not silently downgraded to a bare internal-error ref.

## Non-Goals

- No change to `contract/wrap.py`/`contract/errors.py`'s mapping mechanics.
  `BloomMCPError.from_exception`'s remedy text for a declared exception is a single fixed
  string ("Check the inputs/experiment for this tool and retry.") shared across every
  declared type today; a `CommitFailedError("...structural bug — do not retry...")` paired
  with that remedy reads a little oddly, but that mismatch is a pre-existing property of
  the shared-remedy design (already true for several `ExperimentReadError` subtypes whose
  message doesn't literally describe "inputs"), not something this fix's narrow scope
  should special-case per exception type.
- No change to `get_download_links.py` or `list_existing_analyses.py`. Both already call
  `ResultStore` methods (`get_download_links`/`list_runs`) but neither goes through
  `@as_mcp_tool(errors=...)` — they are plain functions with their own inline
  `try/except Exception` → `safe_error_text(exc)` handling, already redacting and
  surfacing the failure themselves. No gap to close there.
- No change to `storage_backend.py`, `StorageBackendError`, or the boot-time validation
  from #639.
- No change to `RunStateError`/`CorruptRunLinksError`/`RunNotFoundError`/
  `OutputFileMissingError`'s classification (see above).

## Impact

- **Affected specs:** `bloommcp-tool-contract` (MODIFIED — "Structured Agent-Safe Errors").
- **Affected code:** the 8 files under
  `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/`: `qc_clean.py`, `qc_inspect.py`,
  `clustering.py`, `pca_analysis.py`, `remove_outliers.py`, `descriptive_stats.py`,
  `cross_experiment_correlations.py`, `umap_analysis.py` — each gets one added import and
  one `errors=` tuple edit.
- **Affected tests:** 16 new regression tests (2 per tool: a commit failure, a
  manifest-read failure) across the 8 tools' `bloommcp/tests/tools/test_*_tool.py` files,
  plus 3 more closing review-found gaps (`ManifestIncompatibleError` isinstance coverage,
  `RunStateError` non-widening, no-leak-of-planted-content) — 19 new tests total.
- **Dependencies:** none — no `sleap-roots-analyze`/`sleap-roots-contracts` pin change, no
  schema/manifest change.
- **Branch/PR:** branches off `origin/staging`; PR targets `staging`. Recommend `Fixes #640`
  in the PR body — #640's generalized ask ("audit other tools... for the same gap") is
  fully met by this change with no deliberately-deferred AC — but the PR description should
  note the `#642` nuance explicitly: `#642` (already merged) independently closed off the
  exact `BLOOM_STORAGE_URL`-unset repro #640 was originally filed against for the local
  backend, before this proposal was written; this PR closes the remaining general gap
  (any other write-path failure still swallowed into `internal_error`, on either backend),
  not the literal originally-reported symptom.
