## Context

`@as_mcp_tool(errors=(...))` (`bloommcp/src/bloom_mcp/contract/wrap.py`) maps any exception
a tool body raises to a structured `BloomMCPError`: a *declared* type becomes `tool_error`
with the raw exception's message passed through (`errors.py`'s `from_exception`); anything
undeclared becomes a fixed-message `internal_error` plus a correlation ref, with the detail
logged server-side only. All 8 write-and-link analysis tools declare only
`errors=(ExperimentReadError,)` — the read-side base from `bloom_mcp.data_access`. None
declare anything from `bloom_mcp.result_store`, even though every one of them also calls
`store.create_run(...)` and `store.commit(...)`.

#640 was filed against a real observation: a misconfigured local-backend server
(`BLOOM_STORAGE_URL` unset per #639) made `qc_inspect` return a bare `internal_error`
ref with no actionable detail. The issue's own root-cause narrative and suggested fix
(declare `StorageBackendError`) were accurate against the code at the time it was filed.
They are not accurate against current `staging` — confirmed by reading
`supabase_store.py`'s `commit()` directly, not by trusting the issue text. `#581`'s signed-
URL work (merged since) wrapped `commit()`'s entire body — including the `create_signed_url`
call inside `build_output_links` — in one `except Exception as exc:` that unconditionally
re-raises `CommitFailedError` (structural-bug wording via `KeyScopeGuardError`, or generic
"transient — retry" wording otherwise). `create_run`'s own manifest read goes through
`_guarded_manifest_read`, which does the identical thing for `ManifestReadError`/
`ManifestIncompatibleError`. So `StorageBackendError` is caught and relabeled *before* it
would ever reach a tool function — the tool only ever sees `CommitFailedError` or
`ManifestReadError`, which are `ResultStoreError` subtypes, not `StorageBackendError`
itself.

**Correction found during review (openspec-review, 2026-08-13):** the above is not the
whole story for the local backend, and the literal #640/#639 repro (a local-backend server
with `BLOOM_STORAGE_URL` unset, calling `qc_inspect`) is not merely "relabeled" today — it
is **structurally unreachable**. `add-bloommcp-local-url-defaults` (#642, PR #643 — already
merged, an ancestor of this branch: `git merge-base --is-ancestor 716987e6 HEAD` succeeds)
changed `commit()`'s local-backend branch (`supabase_store.py`'s `_active_local_backend()`
check) to build `output_links` via `path_for=lambda key: str(local.resolve_path(key))`
instead of `url_for=lambda key: _sc.create_signed_url(...)`. `create_signed_url` — the only
call that ever raised `StorageBackendError` for an unset `BLOOM_STORAGE_URL`
(`storage_backend.py`'s `LocalStorageBackend.create_signed_url`) — is **never invoked at
all** on the local-backend commit path any more; that class's own docstring says so
explicitly ("Not called by the local backend's own output_links pipeline (#642
follow-up)"). This is proven by an existing test, `bloommcp/tests/test_local_mode.py`
(around line 730-774), which runs `qc_clean`/`pca_analysis` end-to-end with
`BLOOM_STORAGE_URL` unset and asserts **success**. Re-running #640's exact reported repro
today would not raise anything at all, structured or otherwise — not because of anything
in this change, but because #642 closed the trigger off by a different, earlier mechanism.

This does not invalidate the fix below — `CommitFailedError` still fires for other real
failures on both backends (a Supabase-backend `create_signed_url`/upload failure, a
local-backend permission/ENOSPC error inside `_atomic_write`, a version-id collision
exhausting its retry budget, an unextractable signed-URL/size response) and
`ManifestReadError`/`ManifestIncompatibleError` still fire for any manifest read/schema
failure on either backend — but this change's motivating example needed correcting: it is
general write-path hardening for the gap #640's own broader ask names ("audit other tools
... for the same gap"), not a fix for the exact symptom the issue reporter saw, which
`#642` had already independently eliminated by the time this proposal was written.

## Goals / Non-Goals

- **Goal:** a `create_run`/`commit` failure — on either storage backend, for any of the
  reasons `commit()`/`create_run()` can still genuinely raise `CommitFailedError`/
  `ManifestReadError` today (see the correction above — the original `BLOOM_STORAGE_URL`-
  unset trigger #640 named is no longer one of them, closed separately by #642) — surfaces
  to the calling agent as a `tool_error` carrying the store's own already-redacted message,
  not a bare `internal_error` ref, for all 8 write-and-link tools, not `qc_inspect` alone.
- **Non-Goal:** re-litigating whether `commit()`'s own messages are informative enough
  (e.g. "transient — retry" is not very specific). That wrapping already exists and was
  reviewed under `add-bloommcp-signed-url-download`; this change's job is only to stop
  *discarding* that message at the tool-contract boundary, not to reword it.
- **Non-Goal:** changing `from_exception`'s shared remedy string. See Decision 2.

## Decisions

### Decision 1 — Declare `(CommitFailedError, ManifestReadError)`, not the full `ResultStoreError` base

`bloom_mcp.result_store.ResultStoreError` has 6 subtypes:

| Subtype | Reachable from these 8 tools' calls? | Caller-safe to relabel `tool_error`? |
|---|---|---|
| `CommitFailedError` | Yes — `commit()`'s generic except-block | Yes — message is purpose-built redacted text |
| `ManifestReadError` | Yes — `create_run()` → `_guarded_manifest_read` | Yes — same |
| `ManifestIncompatibleError` | Yes — subclass of `ManifestReadError`, same call site | Yes — subclass, covered by `isinstance` |
| `RunStateError` | Only via a wiring bug (commit called twice on one handle, or an unopened handle) — never through any tool-input path | **No** — its own docstring's category ("misused... committed twice") is exactly the class of bug `qc_inspect.py`'s own `_samples_lost` already treats as `internal_error` directly, bypassing `errors=` entirely, rather than a user-facing condition |
| `CorruptRunLinksError` | No — raised only by `get_download_links()`/`build_download_links`, which none of these 8 tools call | N/A here, but its own docstring says "never a caller-input condition, always a corrupt-manifest-data or resolution-bug signal" |
| `RunNotFoundError` | No — raised only by `get_run`/`list_runs`/`get_download_links` | N/A here |
| `OutputFileMissingError` | No — raised only by `get_download_links()` | N/A here |

Declaring the full `ResultStoreError` base would be simpler (one symbol, mirrors how
`ExperimentReadError` is declared as a base covering ~9 read-side subtypes without
enumerating them). It was rejected here because, unlike every `ExperimentReadError`
subtype (each a legitimate "here's what went wrong with your read" condition worth
surfacing), two `ResultStoreError` subtypes are explicitly documented as *never*
caller-actionable — always a bloommcp-internal wiring bug. Surfacing
`RunStateError`/`CorruptRunLinksError` as `tool_error` (implying "check your inputs and
retry") would be actively misleading for a bug class this same tool already special-cases
away from the generic declared-error path elsewhere. The other two subtypes
(`RunNotFoundError`, `OutputFileMissingError`) are simply unreachable from `create_run`/
`commit` — declaring them costs nothing but also fixes nothing, so they're left out for
precision (declaring only what a code-reading audit shows is actually reachable).

**Alternative considered:** import and declare only `CommitFailedError` (skip
`ManifestReadError`). Rejected: `create_run()` is called by all 8 tools immediately before
`commit()`, and its `_guarded_manifest_read` failure path is structurally identical
(redacted-safe-message-into-a-`ResultStoreError`-subtype) — the same #640 class of bug,
just at the read-before-write step (`_guarded_manifest_read`'s own docstring: "guards
`create_run`/`list_runs`/`get_run`'s own manifest read independently of `commit()`'s
existing hardened try/except... a different call, outside `commit()`'s per-key lock, often
well before `commit()` is even reached (#596)"). Leaving it undeclared would reintroduce
the exact #640 pattern one call earlier.

### Decision 2 — Leave `from_exception`'s shared remedy text alone

`BloomMCPError.from_exception`'s `tool_error` branch hardcodes
`remedy="Check the inputs/experiment for this tool and retry."` for every declared
exception, regardless of type. Paired with a `CommitFailedError` message that itself says
"(structural bug — do not retry; see server logs)", the resulting `remedy` reads as
self-contradictory. This mismatch already exists today for some `ExperimentReadError`
subtypes too (e.g. `AmbiguousRunIdError` isn't really about "the experiment," it's about a
non-unique pin) — it is a property of `from_exception`'s intentionally-generic, one-remedy-
for-every-declared-type design, not something this narrowly-scoped fix introduces or
should special-case per exception. Reworking the remedy to vary by exception type is a
separate, broader change to the contract layer's error-mapping mechanics; out of scope
here.

### Decision 3 — No change to `get_download_links.py`/`list_existing_analyses.py`

Both call into `ResultStore` (`get_download_links`, `list_runs`) and could in principle hit
the same subtypes this change addresses. Neither goes through `@as_mcp_tool`/`errors=` at
all — they're plain functions wrapping their own call in `try/except Exception` and
returning `{"error": safe_error_text(exc)}` (or, for `list_existing_analyses`, appending to
an `errors: list[str]` and continuing). `safe_error_text` already strips
credential/token-shaped fragments and bounds length (per `get_download_links.py`'s own
docstring reference to the PR #611 review finding that fixed an earlier unredacted
version). There is no `internal_error`-ref swallowing here to fix — different contract,
already safe. **Caveat found during review:** `list_existing_analyses.py`'s per-tool_class
loop does `errors.append(f"{tool_class}: {exc}")` directly, with **no** `safe_error_text`
call — it is safe today only because `list_runs()` routes through `_guarded_manifest_read`,
whose exception messages are already pre-redacted by construction, an implicit invariant
rather than an explicit one at that call site. Out of scope here (this proposal touches
only the 8 `@as_mcp_tool` write-and-link tools), but worth a follow-up issue rather than
silently relying on that invariant indefinitely.

### Decision 4 — Close four coverage/documentation gaps review found

`openspec-review` (2026-08-13, 5-agent pass) found the code fix itself sound but the
supporting test/documentation coverage incomplete relative to what the proposal and spec
delta claim. Four additions, folded into `tasks.md`:

1. **`ManifestIncompatibleError` isinstance-subclass coverage was asserted, never tested.**
   `FakeResultStore.fail_next_read`'s own docstring says it only simulates the generic
   `ManifestReadError`, "not the schema-incompatible subtype... manifest schema parsing is
   a real-backend-only concern this flat model has no equivalent of" — so no planned test
   actually drove a `ManifestIncompatibleError` instance through `from_exception`. Added: a
   direct unit test at the contract layer asserting
   `BloomMCPError.from_exception(ManifestIncompatibleError("x"), declared=(CommitFailedError,
   ManifestReadError)).code == "tool_error"` — proves the `isinstance` subclass match
   mechanically, independent of which backend can produce one in practice.
2. **The new "RunStateError still maps to internal_error" scenario had no task exercising
   it at the tool boundary** — only at the `ResultStore` layer directly
   (`test_fake_result_store.py`/`test_store_parity.py`), never through an actual
   `@as_mcp_tool`-wrapped call. Added: one tool-boundary test (`qc_inspect`, arbitrarily —
   the mechanism is identical across all 8) that forces a `RunStateError` out of
   `commit()` and asserts the result is still `internal_error`, not `tool_error` — this is
   the test that would catch an accidental `errors=(..., ResultStoreError)` typo instead of
   the intended narrow tuple.
3. **No test asserted the actual safety property this whole change leans on: that a
   passed-through message never leaks unsafe content.** `contract/errors.py`'s
   `from_exception` does `message=str(exc) or exc.__class__.__name__` for any declared
   exception with **zero scrubbing** — no length bound, no credential/path stripping (unlike
   `get_download_links.py`'s `safe_error_text`, which layers that on top of the same kind of
   `ResultStore` exception as defense-in-depth). Today's `CommitFailedError`/
   `ManifestReadError` raise sites happen to be static templates with no exception-text
   interpolation (confirmed by reading every raise site in `supabase_store.py`), so nothing
   leaks *today* — but there was no regression test locking that in, and this change is what
   makes that property load-bearing at the tool-contract boundary for the first time (before
   this change, any accidental leak here was harmlessly swallowed into the generic
   `internal_error` branch instead). Added: a test constructing a `CommitFailedError`/
   `ManifestReadError` whose text is checked not to contain a planted path/host-shaped
   string, run through a tool via the fakes.
4. **`ResultStoreError`'s docstring doesn't literally state the no-leak obligation this
   proposal's own Why section claims it "mirrors" from `ExperimentReadError`.**
   `ExperimentReadError` (`data_access/ports.py`) spells out "Adapters MUST NOT leak a
   filesystem path, bucket name, or storage traceback in the message"; `ResultStoreError`
   (`result_store/ports.py`) just says "Base for write-port failures, with a caller-safe
   message" — true in effect, not in explicit obligation. Added a one-line task to tighten
   `ResultStoreError`'s docstring to state the same obligation explicitly, so the claim this
   proposal makes is actually backed by the code it cites, and future subtypes inherit a
   written, not merely inferred, contract.

## Risks / Trade-offs

- **Risk:** a future `ResultStoreError` subtype gets added and forgotten here, reproducing
  #640's exact pattern for the new type. Mitigated by this design doc's explicit
  reachability table — any change adding a new `create_run`/`commit`-reachable subtype
  should update this same table and the 8 tools' `errors=` tuples together, the same audit
  discipline this change itself performs. No automated enforcement added (out of scope —
  no existing precedent in this codebase for that kind of drift-detection test on
  `errors=` tuples specifically).
- **Trade-off:** declaring by exact subtype (Decision 1) is 2 imports instead of 1 per
  file, and requires updating this list if `commit()`'s own internals change which
  subtypes it can raise. Accepted for the precision it buys over the alternative
  (blanket-declaring `ResultStoreError`, which would also flip two "never a caller-input
  condition" bug classes to `tool_error`).
- **Risk (found during review):** surfacing `CommitFailedError`'s "(transient — retry)"
  wording as a `tool_error` makes that advice agent-visible and agent-actionable for the
  first time (previously hidden behind a bare `internal_error` ref an agent had no reason to
  act on). Verified this is safe against duplication/corruption today — `commit()` only
  marks a run committed after `write_manifest` succeeds, any failure path leaves the
  manifest untouched and best-effort-deletes already-uploaded objects, and the per-key
  `KeyedLock` serializes concurrent attempts — so a literal retry allocates a fresh
  `version_id` and either succeeds cleanly or fails cleanly. The one real edge case
  (server-side commit succeeds but the client-visible ack is lost, then a retry appends a
  second valid version) is pre-existing and orthogonal to this change (an agent could always
  have chosen to retry on a bare `internal_error` too) — not worsened here, just made more
  likely to actually happen since the advice is now visible. No action taken beyond this
  callout; message wording itself is Decision 2's explicit non-goal.
