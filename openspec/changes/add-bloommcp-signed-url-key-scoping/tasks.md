> **TDD note:** write the RED tests (§1) against the not-yet-guarded `build_output_links`, confirm
> each fails, then implement (§2) to GREEN. Commit RED+GREEN together — do not push a RED-only
> commit.
>
> **Archive-ordering note (load-bearing, not optional):** this change's spec deltas MODIFY
> requirements ("Per-Output Signed Links And Size At Commit", "Signed URL Generation") that do not
> yet exist in `openspec/specs/` — they exist only in the still-unarchived sibling change
> `add-bloommcp-signed-url-download`. `openspec validate --strict` does not check a MODIFIED
> requirement's name against the base spec (verified: the validator only checks each delta file's
> own internal shape), so a clean `validate` result here is NOT evidence this delta targets a real
> requirement — it would say "valid" even if the named requirement existed nowhere. This change
> MUST NOT be archived independently of `add-bloommcp-signed-url-download` — archive together, or
> only after that change archives first, so the fold produces one coherent requirement rather than
> an `openspec archive` step trying to MODIFY a requirement absent from the base spec.
>
> **Commit plan:**
> 0. `docs(#598): draft add-bloommcp-signed-url-key-scoping proposal` — already committed (`2a81bb2`).
> 1. `feat(#598): add structural key-scoping guard to ResultStore.commit()` — `_artifacts.py`,
>    `supabase_store.py`, `fake_store.py`, and all new/updated tests, atomic (green). Both adapters
>    MUST land in the same commit as the signature change: `build_output_links` gains a *required*
>    keyword-only parameter, so any commit wiring only one adapter would leave the other's
>    `commit()` raising on every call (caught by its own broad `except Exception` → universal
>    `CommitFailedError` — a clean failure, but still a fully broken intermediate state; `fake_store`
>    alone backs all 8 consumer-tools' test suites, so this would redden far more than the new
>    tests).
> 2. `docs(#598): document the create_signed_url trust boundary` — `storage_backend.py` docstring +
>    `bloommcp/docs/storage-backends.md`.

## 1. RED — the scoping guard does not exist yet

- [x] 1.1 Add `bloommcp/tests/result_store/test_artifacts.py` (new file — confirmed no existing
      file unit-tests `hash_outputs`/`build_output_links`/`validate_outputs` directly; they're
      only exercised indirectly today through `test_supabase_result_store.py`/
      `test_fake_result_store.py`). Call `build_output_links(output_keys, output_sha256,
      output_size_bytes, url_for, expected_prefix="some/prefix/")` — the new keyword-only
      argument. Confirm RED (`TypeError: build_output_links() got an unexpected keyword argument
      'expected_prefix'`).
- [x] 1.2 Same file: a key in `output_keys` that does NOT start with `expected_prefix` → the call
      raises `RuntimeError` naming the offending key and the expected prefix; spy/mock `url_for`
      and assert it is called **zero** times when the *first* key is already out of scope — the
      guard must fire before any signing call, not partway through a multi-output commit. Confirmed
      RED.
- [x] 1.3 Same file, three more cases, all confirmed RED for the same reason:
      (a) every key in `output_keys` starts with `expected_prefix` → `url_for` is called once per
      key and the resulting `OutputLink`s are built exactly as before (multi-output case, mirroring
      `qc_clean`'s two real outputs `_cleaned.csv` + `cleanup_log.json`);
      (b) a **confusable** prefix is correctly rejected, not accepted — sibling stem
      (`qc_experiment2`) and sibling version (`v10` vs `v1`) — pins that the trailing `/` in
      `expected_prefix` is load-bearing;
      (c) `output_keys == {}` does not crash.
- [x] 1.4 In `bloommcp/tests/result_store/test_supabase_result_store.py`, added
      `test_key_outside_run_prefix_fails_commit_and_cleans_up`. **Injection mechanism (verified
      against the actual code — did NOT use the naive approach of monkeypatching
      `AnalysisDir.key`):** monkeypatched the **module-level `build_output_links` name imported
      into `supabase_store`** with a thin wrapper delegating to the real function but substituting
      a wrong `expected_prefix`. Strengthened beyond the original plan: asserts on
      `excinfo.value.__cause__` being specifically a `RuntimeError` naming the mismatched key —
      asserting only `pytest.raises(CommitFailedError)` would have passed identically both before
      and after the guard exists (the pre-guard `TypeError` from the missing kwarg also becomes
      `CommitFailedError` via the same broad `except Exception`), so that alone would not have
      been genuine RED. Confirmed RED (cause was `TypeError`, not `RuntimeError`, before §2).
- [x] 1.5 Same pattern in `bloommcp/tests/result_store/test_fake_result_store.py` —
      `test_key_outside_run_prefix_fails_commit_and_cleans_up`, same `__cause__`-based assertion.
      Confirmed RED.
- [x] 1.6 In `bloommcp/tests/test_storage_backend.py`, added
      `test_create_signed_url_performs_no_ownership_check` for the `bloommcp-storage-backend`
      spec's "The primitive itself performs no ownership check" scenario. As expected, this passed
      immediately (documents current, unchanged behavior of `create_signed_url` itself — the new
      behavior is entirely inside `commit()`).
- [x] 1.7 Confirmed baseline: full suite run before any implementation change showed exactly the
      9 new tests failing (1.1–1.5) and 995 pre-existing tests green — the baseline the "no
      behavior change" acceptance criterion is measured against.

## 2. GREEN — implement the guard

- [x] 2.1 `result_store/_artifacts.py`: added `expected_prefix: str` as a required keyword-only
      parameter to `build_output_links`. Checks every key with `key.startswith(expected_prefix)`
      before building any `OutputLink`; on the first violation raises
      `RuntimeError(f"output key {key!r} (output {name!r}) is outside the expected run prefix
      {expected_prefix!r}")`. No new `ResultStoreError` subclass added.
- [x] 2.2 `result_store/supabase_store.py`: `commit()`'s `build_output_links(...)` call now passes
      `expected_prefix=adir.key(f"{version_dir}/")`.
- [x] 2.3 `result_store/fake_store.py`: `commit()`'s `build_output_links(...)` call now passes
      `expected_prefix=f"{state.prefix}{version_dir}/"`.
- [x] 2.4 Ran §1's suite: GREEN (181/181 in `tests/result_store/` + `tests/test_storage_backend.py`).
      Full suite re-run: **1004 passed** (995 baseline + 9 new), pass/fail set identical to baseline
      except for this change's own new tests — no behavior change for any of the 8 consumer tools.

## 3. Docs

- [x] 3.1 `storage_backend.py`: added a docstring to the `StorageBackend.create_signed_url`
      Protocol method (the first of the 7 Protocol methods to carry one — a deliberate, noted
      exception) stating plainly that this primitive performs no ownership/scope check and that
      `ResultStore.commit()` is responsible for restricting `key` to its own authorized scope.
- [x] 3.2 `bloommcp/docs/storage-backends.md`: added a paragraph to the existing "Downloading
      outputs: signed URLs" section stating the same trust-boundary fact, plus that every current
      caller already only passes correctly-scoped keys by construction (this is defense-in-depth,
      not a fix for a live gap).

## 4. Refactor & verify

- [x] 4.1 Confirmed `create_signed_url`'s signature and both backend implementations' behavior are
      unchanged; every existing `output_links`/signing test passes unmodified.
- [x] 4.2 `/pre-merge`: `black --check` clean (26.3.1) + `ruff check` clean (0.9.9) + full suite
      green (1004 passed, `-m "not integration and not live_smoke"`) + server boots (`tools/list`
      confirms 20 tools registered) + `check-uv-locks.py` clean (no drift, no new dependency) +
      `openspec validate add-bloommcp-signed-url-key-scoping --strict` valid.
- [x] 4.3 Confirmed no behavior change end-to-end: baseline (1.7, 995 passed / 9 new failing) vs.
      post-implementation (1004 passed) — identical pass set plus exactly this change's own tests.
