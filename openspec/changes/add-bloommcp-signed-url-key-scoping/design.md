## Context

Traced every path that reaches `create_signed_url` (there is exactly one production call site):

- `StorageBackend.create_signed_url(key, expires_in)` is a `Protocol` method
  (`storage_backend.py:69`), implemented by `SupabaseStorageBackend` (calls the real Supabase
  Storage client's signing method on whatever `key` it's given, no check) and
  `LocalStorageBackend` (string-concatenates `key` onto `BLOOM_STORAGE_URL`, no check — notably
  the *one* method on that backend that skips the root-containment guard (`_resolve()`) its five
  siblings all use).
- The **only** production caller is `SupabaseResultStore.commit()`
  (`result_store/supabase_store.py:255-262`), inside `build_output_links(...,
  url_for=lambda key: _sc.create_signed_url(key, SIGNED_URL_EXPIRES_SECONDS))`. `build_output_links`
  (`result_store/_artifacts.py:65-84`) is shared by `FakeResultStore.commit()` too, which never
  calls the real backend (`url_for` there just formats `f"fake://signed/{key}?..."`).
- The `key` values `build_output_links` is handed (`output_keys`, from `hash_outputs`) are
  produced by a `key_for(rel)` closure defined *inside the same `commit()` call*
  (`supabase_store.py:239-240`: `adir.key(f"{version_dir}/{rel}")`; `fake_store.py:199-200`:
  `f"{state.prefix}{version_dir}/{rel}"`) — `adir`/`state.prefix` come from this call's own
  `experiment`/`tool_class` (set in `create_run()`), and `version_dir` is allocated earlier in
  this same `commit()` invocation. The **exact same closure** is used to upload the bytes moments
  before signing (`supabase_store.py:246-249`). There is no branch, optional parameter, or
  alternate code path by which a different key could reach `url_for`.
- `get_run`/`list_runs` never populate `output_links` at all (`StoredRun.output_links` defaults to
  `{}`; only `commit()`'s own return value carries the freshly-built dict) — so listing or
  resolving historical runs, however many, never triggers a signing call for anything other than
  what a caller's own `commit()` invocation just produced.
- No file under `sections/` or `tools/` calls `create_signed_url`/`build_output_links`/
  `active_backend()` directly — all 8 consumer tools reach this only through
  `_ports.store().commit()`.
- `identity.py`'s `IdentityMiddleware` is a raw ASGI middleware wrapping the whole app
  (`server.py`'s `build_app()`), and its own module docstring documents — as an already-reviewed,
  deliberate decision — that a `ContextVar` it previously set was removed: FastMCP's
  `StreamableHTTPSessionManager` starts tool dispatch in one long-lived `asyncio` task *per
  session*, and a `ContextVar` set by a *later* request's middleware (a different task) can never
  reach an already-running dispatch task. The middleware now only records usage at the ASGI layer
  itself; there is no accessor, contextvar, or thread-local exposing "who is calling right now" to
  `result_store`/`tools` code. `tools/_ports.py`'s `reader()`/`store()` are plain module globals
  with zero per-call/per-caller parameterization.
- The already-shipped `bloommcp-caller-identity` capability's own ADDED requirement, "Caller
  Identity Never Grants Database or Storage Authority," states identity "SHALL NOT be used as an
  authorization principal for any database or Storage operation... regardless of whether the
  caller identity verified successfully, failed verification, or was absent."

## Goals / Non-Goals

- **Goals:** make the "keys are always correctly scoped" invariant structural (enforced by
  `commit()` itself) rather than merely true-by-construction-today; record the identity-vs-
  narrower-check decision the issue's acceptance criteria requires; zero behavior change for the
  8 existing call sites.
- **Non-Goals:** extending `#406/#563` identity to carry Storage authority (see Decision below); a
  web/CLI file explorer (explicitly out of scope in the issue); changing `create_signed_url`'s
  signature or either backend's implementation; adding per-user/per-experiment authentication to
  the shared `bloom_agent` Storage role (a much larger effort the issue doesn't ask for).

## Decisions

- **Decision (the one the issue's acceptance criteria requires recording): a narrower structural
  scoping check inside `ResultStore.commit()`, not extending caller identity to carry Storage
  authority.** Three independent reasons converge on this:
  1. **No plumbing exists today.** Reaching `create_signed_url` from an identity-aware context
     would require *building* a mechanism to carry per-request identity into the tool-dispatch
     code path — exactly the mechanism `#406/#563` already attempted (a `ContextVar`) and reverted
     for a structural reason (FastMCP's per-session dispatch task can't see a later request's
     context). This isn't "reuse an existing wire," it's "solve the problem #406/#563 already gave
     up on solving at this granularity."
  2. **It would reverse an already-reviewed decision.** `bloommcp-caller-identity`'s own spec
     states identity must never become a DB/Storage authorization principal, unconditionally. This
     change should reaffirm that, not carve out a Storage-specific exception the day after it
     shipped.
  3. **The narrower check needs nothing new.** `commit()` already knows the exact prefix that
     scopes this call — `output_root`, `tool_class`, the experiment stem, and the `version_dir` it
     just allocated — because it computed all four to do the upload one line above where it signs.
     A structural check is a same-function, few-line addition, not a new subsystem.
- **Decision: enforce inside `build_output_links` (shared by both adapters), not inside
  `create_signed_url` itself.** The `StorageBackend` Protocol is a generic object-storage
  primitive with no concept of "run" or "scope" — giving it one would mean threading
  run/experiment context through a Protocol six other call sites (`upload_file`, `download_file`,
  etc.) don't need, for a check only `commit()`'s one signing call actually requires. Enforcing at
  `build_output_links` also gets `FakeResultStore` the identical guarantee for free (both adapters
  call the same shared function), keeping fake/real parity — a repeatedly-reinforced convention in
  this codebase (`#325`, `#586`, `#596`).
- **Decision: `build_output_links` takes a new required `expected_prefix: str` parameter; a
  mismatched key raises `RuntimeError`, not a new exception class.** `RuntimeError` matches this
  same file family's existing idiom for "a structural invariant was violated, this should never
  happen" (`supabase_store.py` already raises bare `RuntimeError` for "could not allocate a free
  version id" and "version was claimed by another writer during upload" — the same class of
  defense-in-depth check). Introducing a new `ResultStoreError` subclass in `ports.py` would add
  public API surface for a condition that (a) should never actually fire and (b) is immediately
  caught and remapped anyway — see next decision.
- **Decision: no new contract-layer wiring — the existing `except Exception` in both `commit()`
  implementations already catches this and converts it to `CommitFailedError`.** Both
  `SupabaseResultStore.commit()` (`supabase_store.py:337-350`) and `FakeResultStore.commit()`
  (`fake_store.py:266-273`) wrap their entire commit body in a broad `except Exception as exc:
  raise CommitFailedError(...) from exc`, which already best-effort-cleans-up any uploaded objects
  and leaves the run retryable — the exact same path a real signing failure already takes
  (`bloom#581`'s own "signing failure fails the whole commit" requirement). A scoping violation is
  simply one more way that block can fail; no special-casing needed, and the fail-closed,
  no-partial-run, cleaned-up-orphans behavior is inherited automatically.
- **Decision: `expected_prefix` is computed from the same source data as the keys it checks, not
  independently re-derived.** `SupabaseResultStore` passes `adir.key(f"{version_dir}/")` — the
  identical `adir.key(...)` method `key_for` itself calls — so the expected prefix and the actual
  keys can never independently drift out of sync from a future refactor of `AnalysisDir`'s path
  format. `FakeResultStore` passes `f"{state.prefix}{version_dir}/"`, mirroring its own
  `key_for`'s construction exactly.
- **Decision: `create_signed_url`'s own docstring/spec gains a documentation-only clarification of
  the trust boundary, not new runtime behavior.** Making explicit, at the primitive's own
  contract, that it performs no ownership check and callers are responsible for restricting keys
  to their own authorized scope — so a future reader of `storage_backend.py` alone (without
  tracing into `result_store`) still learns where the actual guarantee lives.

## Risks / Trade-offs

- **This fixes a latent gap, not a live bug.** Every current call site is already provably
  correctly-scoped (traced in Context above); this change makes that invariant unbreakable by a
  future refactor rather than fixing anything exploitable today. Worth stating plainly so this
  isn't mistaken for a security incident response.
- **The guard can never be exercised by any real call path today** (by design — every real key is
  correctly scoped). Tests must inject a deliberately-wrong key via a test-only seam (e.g.
  monkeypatching `key_for` or calling `build_output_links` directly with a mismatched key) rather
  than through an end-to-end `qc_clean(...)` call, since no such call can produce one. This mirrors
  how `#581`'s own "signing failure" tests already work (monkeypatching
  `supabase_client.create_signed_url` directly, since no real input makes a well-formed signing
  call fail either).
- **`LocalStorageBackend.create_signed_url` still has no containment check of its own** (unlike
  its five siblings, which call `_resolve()`). Out of scope here — the guard added lives in
  `ResultStore.commit()`, which both backends' `create_signed_url` sit behind identically, so the
  fix protects both regardless of backend. Flagged as a residual asymmetry at the primitive level,
  not a gap in the actual guarantee this change ships.

## Migration Plan

Additive only — one new required parameter on an internal (non-tool-facing) helper function with
exactly two call sites, both updated in the same change; no schema, manifest, or public-API
change. Existing correctly-scoped calls are unaffected (every current key already satisfies the
new check by construction). Rollback = revert the parameter and its two call sites.

## Open Questions

None — the issue's own acceptance criteria are fully addressed by the decisions above: (1) the
guard structurally rejects an out-of-scope key, (2) the identity-vs-narrower-check decision is
recorded, (3) no behavior change for any of the 8 legitimate call sites (verified: their existing
test suites require no changes).
