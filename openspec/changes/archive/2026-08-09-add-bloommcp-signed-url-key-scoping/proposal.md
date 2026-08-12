## Why

`StorageBackend.create_signed_url(key, expires_in)` (shipped via #581/#595) signs a URL for
whatever `key` string it's given, with no check that the calling context actually owns/should
access that key's experiment. Today this is safe only *by construction*: `key` is always derived,
inside the very same `commit()` call, via the identical `key_for(rel)` closure used to upload the
bytes moments earlier — no code path today can hand it a caller-supplied, listed, or historical
key. But the primitive itself has no guard rail, and bloommcp's storage layer authenticates as one
shared `bloom_agent` role with no per-user/per-experiment scoping — so a future refactor of
`commit()`, or a new call site added without noticing this invariant, could sign a URL for
something outside the current run's own scope with nothing to catch it. This is defense-in-depth
against a future bug, not a fix to a live exploit — verified by tracing every current call site
(see design.md).

`#406/#563` added verified per-user identity (`X-Bloom-Identity`) to bloommcp, but its own spec
explicitly and deliberately scopes it to usage-tracking only, with a standing requirement that it
"SHALL NOT be used as an authorization principal for any database or Storage operation." That
decision was itself forced by a concrete constraint: the identity middleware cannot thread a value
into the tool-dispatch code path at all (FastMCP's session/task model means a `ContextVar` set by
a later request's middleware can never reach an already-running dispatch task) — so extending it
here would mean building new plumbing this repo has already tried and reverted once, not reusing
something that exists.

## What Changes

- **ADD** a structural key-scoping guard to `ResultStore.commit()`'s existing sign-and-link step
  (`build_output_links` in `result_store/_artifacts.py`, shared by both `SupabaseResultStore` and
  `FakeResultStore`): before calling `url_for(key)` for any output, verify `key` starts with the
  prefix `commit()` itself just computed for *this* run (`{output_root}/{tool_class}_{stem}/
  {version_dir}/`) — data `commit()` already holds, not anything new. A key outside that prefix
  raises (structural bug, not caller input), which the existing broad `except Exception` in both
  `commit()` implementations already catches and converts to the existing `CommitFailedError`
  fail-closed/cleanup path — no new *public* exception type (`ports.py`'s `ResultStoreError`
  hierarchy is unchanged; the guard raises a private `RuntimeError` subclass local to
  `_artifacts.py`, added post-review so the two `commit()`s' `except` blocks can give this
  structural failure a non-transient message instead of the generic one — see design.md), no new
  contract-layer wiring, no behavior change for any of today's 8 correctly-scoped call sites.
- **DECISION RECORDED (per the issue's acceptance criteria):** a narrower structural scoping
  check, not extending `#406/#563`'s caller identity to carry Storage authority. See design.md for
  the full reasoning; in short, identity has no plumbing into this code path today (extending it
  would mean building new infrastructure, not reusing existing infrastructure), extending it would
  reverse an already-reviewed and still-current requirement ("Caller Identity Never Grants
  Database or Storage Authority"), and the narrower check is already fully expressible with data
  `commit()` holds — no new plumbing needed either way, but one path requires none at all.
- **NO change** to `create_signed_url`'s own signature or either backend implementation — the
  `StorageBackend` Protocol has no concept of "run scope" (it's a generic object-storage
  primitive), and the one production call site (`SupabaseResultStore.commit()`) is exactly where
  the scope is already known. The storage-backend spec gets a documentation-only clarification
  that the primitive itself performs no ownership check and callers are responsible for restricting
  keys to their own authorized scope — making the trust boundary explicit rather than implicit.
- **NO change** to any of the 8 consumer tools (`qc_clean`, `qc_inspect`, `pca_analysis`,
  `remove_outliers`, `descriptive_stats`, `cross_experiment_correlations`, `umap_analysis`,
  `clustering`) — all reach `create_signed_url` only through `_ports.store().commit()`, and every
  one of them already passes correctly-scoped keys.
- Tests cover: the guard rejects a deliberately mismatched key (both adapters, via a thin
  test-only monkeypatch/injection point, since no real call site can produce one to exercise this
  end-to-end); the guard accepts every key either adapter's own `key_for` produces (no false
  positive against real usage); the resulting failure surfaces as the existing `CommitFailedError`
  fail-closed/cleanup path (mirroring the existing "signing failure fails the whole commit" test);
  and all 8 consumer tools' existing test suites remain green unmodified (no behavior change for
  legitimate call sites, satisfying that acceptance criterion directly). The both-adapters
  mismatched-key case lives as one parametrized test in
  `tests/result_store/test_store_parity.py` (extended, per the repo's existing fake/real parity
  convention) — **correction, post-review:** the first implementation instead hand-duplicated this
  case as two near-identical tests in `test_supabase_result_store.py`/`test_fake_result_store.py`,
  while this proposal already (incorrectly) claimed `test_store_parity.py` was extended; a reviewer
  caught the mismatch between the claim and the actual diff, and the fix folded both duplicates
  into one real parity test instead of just correcting the sentence.

## Impact

- **Affected specs:**
  - `bloommcp-result-store` (**modified**) — the "Per-Output Signed Links And Size At Commit"
    requirement gains the scoping guarantee. **Note:** this requirement does not yet exist in
    `openspec/specs/bloommcp-result-store/spec.md` — it currently lives only in the still-
    unarchived `openspec/changes/add-bloommcp-signed-url-download/specs/bloommcp-result-store/
    spec.md`, even though its code has already merged to `staging` (confirmed directly: this
    branch, freshly cut from `origin/staging`, already has `output_links`/`OutputLink` in
    `qc_clean.py` and the rest of the 8 tools). This change's delta targets that requirement's
    actual (shipped) text; whoever archives `add-bloommcp-signed-url-download` will need to fold
    both deltas together — the same situation that change's own docs already flag for
    `bloommcp-tool-contract`. **This change MUST NOT be archived independently of
    `add-bloommcp-signed-url-download`** — `openspec validate --strict` passing here is not
    evidence otherwise (verified: the validator never cross-checks a MODIFIED requirement's name
    against the base spec or sibling changes, so it would say "valid" even if the requirement
    existed nowhere).
  - `bloommcp-storage-backend` (**modified**) — the "Signed URL Generation" requirement gains a
    documentation-only trust-boundary clarification. Same unarchived-sibling caveat as above.
- **Affected code:**
  - `bloommcp/src/bloom_mcp/result_store/_artifacts.py` (`build_output_links` gains an
    `expected_prefix` parameter + the guard);
  - `bloommcp/src/bloom_mcp/result_store/supabase_store.py` (`commit()`'s call site passes
    `expected_prefix=adir.key(f"{version_dir}/")`);
  - `bloommcp/src/bloom_mcp/result_store/fake_store.py` (`commit()`'s call site passes
    `expected_prefix=f"{state.prefix}{version_dir}/"`, for fake/real parity);
  - `bloommcp/src/bloom_mcp/storage_backend.py` (docstring-only: `create_signed_url`'s contract
    documents that it performs no ownership check);
  - `bloommcp/docs/storage-backends.md` (its existing "Downloading outputs: signed URLs" section
    gains the same trust-boundary note);
  - new `bloommcp/tests/result_store/test_artifacts.py` (direct unit coverage for
    `build_output_links`/`hash_outputs`/`validate_outputs` — none exists today);
  - extended: `bloommcp/tests/result_store/test_store_parity.py` (the both-adapters
    mismatched-key case, via the repo's existing parity-test convention). **Post-review
    correction:** the first implementation instead extended
    `bloommcp/tests/result_store/test_supabase_result_store.py` and
    `bloommcp/tests/result_store/test_fake_result_store.py` with two hand-duplicated tests and left
    `test_store_parity.py` untouched, contradicting this bullet as originally written; both
    per-adapter tests were removed once folded into the one parity test, so neither file has any
    net diff against `origin/staging` anymore.
- **Dependencies:** none new.
- **Sequencing:** independent of any other in-flight change; base is `origin/staging` directly.
  PR targets `staging`.
- **Resolves #598.** Related: `#581`/`#595` (introduced `create_signed_url`/`output_links`,
  unarchived), `#406`/`#563` (caller identity, whose non-goal this change reaffirms rather than
  reverses).
