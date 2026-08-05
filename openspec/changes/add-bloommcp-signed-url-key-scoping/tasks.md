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

- [ ] 1.1 Add `bloommcp/tests/result_store/test_artifacts.py` (new file — confirmed no existing
      file unit-tests `hash_outputs`/`build_output_links`/`validate_outputs` directly; they're
      only exercised indirectly today through `test_supabase_result_store.py`/
      `test_fake_result_store.py`). Call `build_output_links(output_keys, output_sha256,
      output_size_bytes, url_for, expected_prefix="some/prefix/")` — the new keyword-only
      argument. Confirm RED (`TypeError: build_output_links() got an unexpected keyword argument
      'expected_prefix'`).
- [ ] 1.2 Same file: a key in `output_keys` that does NOT start with `expected_prefix` → the call
      raises `RuntimeError` naming the offending key and the expected prefix; spy/mock `url_for`
      and assert it is called **zero** times when the *first* key is already out of scope — the
      guard must fire before any signing call, not partway through a multi-output commit. Confirm
      RED (parameter doesn't exist yet).
- [ ] 1.3 Same file, three more cases, all confirmed RED for the same reason:
      (a) every key in `output_keys` starts with `expected_prefix` → `url_for` is called once per
      key and the resulting `OutputLink`s are built exactly as before (multi-output case, e.g.
      mirroring `qc_clean`'s two real outputs `_cleaned.csv` + `cleanup_log.json` — not just a
      single-key case);
      (b) a **confusable** prefix is correctly rejected, not accepted — e.g. expected
      `"bloommcp_output/qc_experiment/v1/"` but a key under `"bloommcp_output/qc_experiment2/v1/"`
      or `"bloommcp_output/qc_experiment/v10/"` — pins that the trailing `/` in `expected_prefix`
      (present in both real call sites' construction) is load-bearing and this test would catch a
      future refactor that dropped it;
      (c) `output_keys == {}` does not crash (defensive; `validate_outputs` already guarantees
      `outputs` is non-empty upstream, but `build_output_links` is now directly unit-tested in
      isolation from that guarantee).
- [ ] 1.4 In `bloommcp/tests/result_store/test_supabase_result_store.py`, add an end-to-end test.
      **Injection mechanism (verified against the actual code — do not use the naive approach of
      monkeypatching `AnalysisDir.key`):** `expected_prefix` and every real `output_keys` entry are
      both derived from the *same* `adir.key(...)` call inside `commit()`'s `key_for` closure, so
      patching `AnalysisDir.key` corrupts both sides identically — the mismatch this test needs to
      produce would never actually occur, and the guard would never fire (confirmed by tracing
      `key_for`'s and the proposed `expected_prefix`'s construction against each other). Instead,
      monkeypatch the **module-level `build_output_links` name imported into `supabase_store`**
      (`from ._artifacts import build_output_links`, at the top of `supabase_store.py`) with a thin
      wrapper that delegates to the real `bloom_mcp.result_store._artifacts.build_output_links` but
      substitutes a deliberately wrong `expected_prefix` (e.g. a different experiment's prefix),
      leaving the real `output_keys`/`output_sha256`/`output_size_bytes`/`url_for` untouched — this
      keeps upload and hashing fully self-consistent (real bytes land at the real, correctly-scoped
      key) while exercising exactly what this test needs: that `commit()`'s wiring correctly
      converts the guard's `RuntimeError` into `CommitFailedError`. Assert: `commit()` raises
      `CommitFailedError` (via the existing broad `except Exception` path), best-effort cleans up
      the uploaded objects (mirror the assertions in the existing
      `test_signing_failure_fails_commit_and_cleans_up_orphans`), and records no new version.
      Confirm RED (no guard exists yet — the wrong `expected_prefix` passed to the *real*
      `build_output_links` would currently just… also not exist as a parameter, so this actually
      fails at the `TypeError` level until §2 lands; that is the correct RED state here too).
- [ ] 1.5 Same pattern (module-level `build_output_links` import, not `AnalysisDir`/`key_for`) in
      `bloommcp/tests/result_store/test_fake_result_store.py` for `FakeResultStore.commit()` —
      fake/real parity for this guarantee. Confirm RED.
- [ ] 1.6 In `bloommcp/tests/test_storage_backend.py`, add a test for the
      `bloommcp-storage-backend` spec's "The primitive itself performs no ownership check"
      scenario: call `create_signed_url` directly (against either backend, via the existing test
      doubles in that file) with a key that is NOT under any real run's prefix, and assert it
      succeeds (is NOT rejected on ownership/scope grounds) — proving the primitive itself
      is, by design, unguarded; the guarantee lives one layer up, in `commit()`. This test should
      already pass today (RED is not expected here — it documents current, unchanged behavior of
      `create_signed_url` itself; the new behavior is entirely inside `commit()`). Add it in this
      section for completeness of the scenario-to-test mapping, not because it's expected to fail.
- [ ] 1.7 Confirm the full existing suite for all 8 consumer tools, `test_supabase_result_store.py`,
      `test_fake_result_store.py`, `test_store_parity.py`, and the new `test_artifacts.py`
      (1.1-1.3, currently erroring on `TypeError`) is otherwise green — i.e. the *only* failures at
      this point are the new tests added in 1.1–1.5, and nothing pre-existing broke. This is the
      baseline the "no behavior change" acceptance criterion is measured against in §2.4.

## 2. GREEN — implement the guard

- [ ] 2.1 `result_store/_artifacts.py`: add `expected_prefix: str` as a required keyword-only
      parameter to `build_output_links`. Before building each `OutputLink` (i.e. before calling
      `url_for(key)`), check `key.startswith(expected_prefix)`; on the first violation raise
      `RuntimeError(f"output key {key!r} is outside the expected run prefix {expected_prefix!r}")`
      — matching this file's existing "structural invariant violated" idiom (the same class of
      check `supabase_store.py` already uses bare `RuntimeError` for). Do not add a new
      `ResultStoreError` subclass — this is caught immediately by the callers' existing broad
      `except Exception` and remapped to `CommitFailedError`.
- [ ] 2.2 `result_store/supabase_store.py`: at the `build_output_links(...)` call site
      (`commit()`, currently line ~255), add `expected_prefix=adir.key(f"{version_dir}/")` —
      reusing `adir.key(...)`, the same method `key_for` itself calls, so the expected prefix and
      the actual keys can never independently drift.
- [ ] 2.3 `result_store/fake_store.py`: at the equivalent `build_output_links(...)` call site in
      `commit()`, add `expected_prefix=f"{state.prefix}{version_dir}/"` — mirroring its own
      `key_for`'s construction exactly.
- [ ] 2.4 Run §1's suite; debug to GREEN. Confirm the "every real call site's keys satisfy the
      scoping check" scenario holds by re-running the full 8-tool + result_store suite from 1.7
      unmodified and green — the pass/fail set before this section and after SHALL be identical
      except for the new tests this change adds.

## 3. Docs

- [ ] 3.1 `storage_backend.py`: add a short docstring on the `StorageBackend.create_signed_url`
      Protocol method (currently a bare `...` stub, like all 7 Protocol methods — this will be the
      first of the 7 to carry one, a deliberate, noted exception) stating plainly that this
      primitive performs no ownership/scope check and that `ResultStore.commit()` is responsible
      for restricting `key` to its own authorized scope before calling it — matching the spec
      delta's "Trust boundary" paragraph so code and spec say the same thing.
- [ ] 3.2 `bloommcp/docs/storage-backends.md`: this doc already has a dedicated section,
      "Downloading outputs: signed URLs (`output_links`)," documenting `create_signed_url` in
      detail (extraction quirks, the `BLOOM_PUBLIC_SUPABASE_URL` host-rewrite, the local backend's
      caveats) — the canonical place a human reader would actually look. Add a short paragraph
      there stating the same trust-boundary fact: `create_signed_url` itself performs no
      ownership check; the guarantee that only this run's own freshly-uploaded keys ever get
      signed lives in `ResultStore.commit()`, not in the primitive.

## 4. Refactor & verify

- [ ] 4.1 Refactor for clarity; confirm `create_signed_url`'s signature, both backend
      implementations' behavior, and every existing `output_links`/signing test are unchanged
      except for the new scoping-violation ones added in §1.
- [ ] 4.2 `/pre-merge`: lint (`black --check` + `ruff check`, pinned versions) + the exact CI
      suite command `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not
      integration and not live_smoke" -v --tb=short` (matches `pr-checks.yml`'s `python-audit` job
      exactly) + `uv run --frozen` import (server boots) + `python scripts/check-uv-locks.py` (no
      drift — no new dependency) + `openspec validate add-bloommcp-signed-url-key-scoping --strict`.
- [ ] 4.3 Confirm no behavior change end-to-end: diff the pass/fail set from 1.7 (baseline) against
      the post-2.4 run — identical except for this change's own new tests.
