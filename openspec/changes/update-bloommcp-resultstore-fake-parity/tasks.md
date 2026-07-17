## 0. Pre-check

- [ ] 0.1 Confirm PR #464 (#324) status. If it has merged to `staging`, rebase this branch and re-verify `_MAX_ID_ATTEMPTS`/the two-phase collision check in `supabase_store.py` still match design.md's Context before starting section 2. If it's still open, proceed but do not merge this change ahead of it.

## 1. Fix latent ordering bugs in `FakeResultStore.commit` (prerequisite, currently unreachable/untestable in isolation)

- [ ] 1.1 Move `self._open.discard(id(run))` from the top of `commit` to the success branch, after the entry is appended to `self._runs` — matching `SupabaseResultStore.commit`'s `state.committed = True` placement.
- [ ] 1.2 Move the staging-dir `shutil.rmtree` out of `hash_outputs`'s `finally` block and into the success branch only, so a failed commit leaves the staging dir intact for retry.
- [ ] 1.3 Confirm existing `test_fake_result_store.py` and `test_store_parity.py` tests still pass unmodified (both bugs are currently unreachable, so this should be a no-op for today's suite). Note: tasks 2.5/2.6 below are what retroactively regression-guard 1.1/1.2 — a pin-first red test isn't possible before section 2's hooks exist.

## 2. Version-id allocation + failure-injection hooks (each paired with its test)

- [ ] 2.1 Add a local adapter that builds a `SimpleNamespace(versions=[SimpleNamespace(id=r.run_ref) for r in existing])` from `self._runs[(experiment, tool_class)]`, and switch `create_run`'s provisional allocation to `next_version_id(that adapter)` (imported from `bloom_mcp.storage.versioning`, already used for `version_dir_name`), replacing `f"v{len(existing) + 1}"`.
- [ ] 2.2 Test: confirm all pre-existing `test_fake_result_store.py`/`test_store_parity.py` tests still pass unmodified with the new allocator (no observable change on the non-colliding path — `next_version_id`'s max(N)+1 agrees with count-based allocation whenever ids were never skipped/reused, which is every existing scenario).
- [ ] 2.3 Add an explicit per-output recording loop in `commit`, after `hash_outputs`, tracking recorded output names (mirrors where `SupabaseResultStore.commit`'s upload loop sits). No-op in the non-injected case — no observable behavior change.
- [ ] 2.4 Add `fail_next_commit(experiment, tool_class, *, after_outputs=0)` hooking into the loop from 2.3: one-shot; raises `CommitFailedError` once `after_outputs` outputs are recorded (`0..len(outputs)` inclusive); reverts partial state (nothing appended to `self._runs`, handle stays open via the fix in 1.1, staging dir intact via the fix in 1.2).
- [ ] 2.5 Test: `test_commit_failure_is_retryable_and_does_not_leak` (fake variant) — inject `after_outputs=0`, assert `list_runs` stays empty, handle retryable, retry succeeds with the expected `run_ref`.
- [ ] 2.6 Test: `test_commit_failure_cleans_up_orphaned_objects_from_partial_upload` (fake variant, reinterpreted for an in-memory store as "no partial state survives") — 2-output run, inject `after_outputs=1`, assert `list_runs` stays empty and no reference to either output survives; retry succeeds.
- [ ] 2.7 Add `_MAX_ID_ATTEMPTS = 3` (local constant, independent of `supabase_store.py`'s) and give `commit` the pre-record collision check (via 2.1's adapter + `next_version_id`, bounded reallocation) and the pre-append recheck, exactly mirroring `SupabaseResultStore.commit`'s two-phase guard.
- [ ] 2.8 Add `seed_collision(experiment, tool_class, version_id)`: appends a placeholder `StoredRun` at that id directly into `self._runs`, simulating an interloper.
- [ ] 2.9 Test: `test_interleaved_commits_get_distinct_ids_with_consistent_provenance` (fake variant) — `seed_collision` before `commit`; pre-record check catches it, reallocates once, succeeds on a distinct id; neither run's outputs/hashes are clobbered.
- [ ] 2.10 Test: `test_retry_exhaustion_before_upload_raises_with_no_uploads` (fake variant) — `seed_collision` + `monkeypatch` the fake module's `next_version_id` to always return the same colliding id; assert `CommitFailedError` after exactly `_MAX_ID_ATTEMPTS` attempts, nothing recorded.
- [ ] 2.11 Test: `test_prewrite_collision_cleans_up_and_retry_succeeds` (fake variant) — time `seed_collision` so it's visible only to the pre-append check, not the pre-record check (confirm via call-ordering per design.md Decision C; add a `visible_at` param to `seed_collision` if call-ordering can't reliably target one check over the other). Assert safe failure (cleanup, nothing recorded) and a successful retry.
- [ ] 2.12 Add `seed_v2_run(experiment, tool_class, *, tool, outputs)`: builds a `StoredRun` directly with `seed=None`/`agent=None`/`output_sha256={}`/`output_keys={}` (matching `manifest_v2.json`'s shape) and appends it to `self._runs`. No dedicated unit test here — exercised via the parametrized parity scenario in section 3.

**Not mirrored** (see design.md "Explicitly not mirrored, and why" for rationale): `test_cleanup_failure_does_not_mask_original_error` (no fallible cleanup op exists in-memory) and `test_noncolliding_commit_reads_manifest_twice_with_no_reallocation` (asserts Supabase-internal read-count plumbing, not an observable contract).

## 3. Parametrize `test_store_parity.py`

- [ ] 3.1 Add `_inject_commit_failure(kind, store, monkeypatch, *, after_outputs, ...)`: branches to `monkeypatch.setattr(bloom_mcp.supabase_client, "upload_file", ...)` for `"supabase"` vs. `store.fail_next_commit(...)` for `"fake"`.
- [ ] 3.2 Add `_seed_collision(kind, store, monkeypatch, *, version_id, ...)`: branches to seeding raw manifest JSON into `fake_supabase_storage.objects` (matching `test_retry_exhaustion_before_upload_raises_with_no_uploads`'s technique) for `"supabase"` vs. `store.seed_collision(...)` for `"fake"` — a distinct helper from 3.1, not folded into it (different injection shape).
- [ ] 3.3 Parametrized scenario: commit-failure retry — both backends leave `list_runs` unadvanced after an injected failure and succeed on retry with the same `run_ref`; assert on `version_dir` namespacing (non-shared logic), not only the shared `hash_outputs` output.
- [ ] 3.4 Parametrized scenario: duplicate-id reallocation, immediate — both backends land colliding runs on distinct ids with neither's stored bytes/hash overwritten; assert on `latest` resolution across both.
- [ ] 3.5 Parametrized scenario: duplicate-id reallocation, exhaustion-safe — both backends raise a structured failure with nothing recorded when every reallocation attempt collides.
- [ ] 3.6 Parametrized scenario: v2-manifest back-compat — seed a v2-shaped run (`seed_v2_run` on the fake, the existing `manifest_v2.json` fixture on `SupabaseResultStore`) and commit a new run; assert it appends alongside the v2 entry on both backends with `get_run("latest")` resolving to the new one.

## 4. Documentation

- [ ] 4.1 Update `bloommcp/docs/2026-06-15-bloom-mcp-phase2-persistence-design.md`: correct the #324 bullet (line ~113) and the §7 Tier-2 follow-up note (line ~107) to reflect this change's closure of #325, once implemented and merged.

## 5. Validation

- [ ] 5.1 `openspec validate update-bloommcp-resultstore-fake-parity --strict` passes.
- [ ] 5.2 Run `uv run pytest bloommcp/tests/result_store/test_fake_result_store.py bloommcp/tests/result_store/test_store_parity.py -v` explicitly (not just the full suite) and confirm the parametrized case count in `test_store_parity.py` doubles per new scenario (once for `"fake"`, once for `"supabase"`) — a missing `kind` case or a silently-skipped parametrization should fail this check, not just "the suite is green."
- [ ] 5.3 Full `bloommcp` unit test suite passes with no regressions elsewhere (baseline: 513 passed per #324's tasks.md — confirm the new count and that nothing dropped).
- [ ] 5.4 Confirm PR #464 has merged to `staging` before merging this change (see task 0.1).
