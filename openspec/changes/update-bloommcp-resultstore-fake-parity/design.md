## Context

`FakeResultStore` (`bloommcp/src/bloom_mcp/result_store/fake_store.py`) and `SupabaseResultStore` (`bloommcp/src/bloom_mcp/result_store/supabase_store.py`) both implement the `ResultStore` Protocol, and `test_store_parity.py` runs a shared scenario set against both to prove tools built against the fake behave the same against the real adapter. #324 (PR #464, open against `staging` at the time of writing) hardens `SupabaseResultStore.commit` with two guards neither the fake nor the parity suite know about:

- **Orphan cleanup**: a failed upload mid-loop triggers a best-effort `delete_files` of whatever uploaded before it, then re-raises `CommitFailedError` with the handle left open and staging dir intact for retry (`test_commit_failure_is_retryable_and_does_not_leak`, `test_commit_failure_cleans_up_orphaned_objects_from_partial_upload`).
- **Duplicate-id guard**: `commit` re-reads the manifest immediately before uploading (reallocating `version_id`/`version_dir` together via `next_version_id`, bounded at `_MAX_ID_ATTEMPTS = 3`) and once more immediately before the manifest write, treating a late collision as an ordinary retryable failure (`test_interleaved_commits_get_distinct_ids_with_consistent_provenance`, `test_retry_exhaustion_before_upload_raises_with_no_uploads`, `test_prewrite_collision_cleans_up_and_retry_succeeds`).

`FakeResultStore` has no analog for either, and not just as a missing test surface — as a **structural** gap:

- `commit` calls `hash_outputs` once and has no per-output loop at all (`fake_store.py:86`), unlike `SupabaseResultStore.commit`'s explicit `for _name, rel in outputs.items(): ... uploaded_keys.append(key)` loop (`supabase_store.py:136-139`). There is no per-iteration checkpoint to fail at, so "fail after N outputs" cannot be injected without first giving the fake an equivalent loop.
- `create_run` allocates `version_id = f"v{len(existing) + 1}"` (`fake_store.py:54`) — a pure count, not a scan for `max(N) + 1` like `next_version_id` (`bloom_mcp/storage/versioning.py:21-35`). Seeding a collision and expecting the fake to reallocate around it requires the fake to actually scan for a free id, not just count list length.

A workflow test that only exercises the fake therefore cannot catch a regression in either guard, and `test_store_parity.py`'s current scenario set (single-output happy path, not-found, double-commit, empty-outputs) never touches either.

## Goals / Non-Goals

- Goals:
  - Let `FakeResultStore` simulate a mid-commit failure and a version-id collision (both immediate and only-detected-late), with the same *externally observable* contract as `SupabaseResultStore`: handle stays open and staging dir intact after a failure, nothing partial is recorded, a retry on the same handle succeeds, and colliding runs land on distinct ids with neither's bytes/hash clobbered.
  - Make the fake's id allocation **provably identical in semantics** to the real adapter's — reuse `next_version_id` itself (via a small duck-typed adapter), not a second hand-rolled scan that could quietly diverge from it.
  - Extend `test_store_parity.py` so these scenarios — plus v2-manifest back-compat — run against both backends from one shared scenario body.
  - Fix the two latent ordering bugs in the fake's `commit` (below) that would otherwise make the injected-failure tests fail even though the bug is unrelated to injection itself.
- Non-Goals:
  - `ExperimentReader`/`FakeReader` parity. The issue title says "ResultStore/ExperimentReader," but the issue body's Context and Scope sections describe only write-path risk (`FakeResultStore.commit`'s divergence from `SupabaseResultStore`) and only ask to parametrize `test_store_parity.py`. `SupabaseReader.load_experiment` (`bloommcp/src/bloom_mcp/data_access/supabase_reader.py:41-74`) is a pure read with no upload loop, no manifest-append, and no retry/rollback semantics — there is no analogous "partial write" or "collision" hazard for a fake read-path to diverge on, and no `test_reader_parity.py` exists today to extend. **This is a scoping judgment call, not a call confirmed by the issue itself — issue #325 has zero comments, so no maintainer has explicitly blessed narrowing away from the title.** Flagged in Open Questions below for explicit sign-off before/at approval; this change does not touch `fake_reader.py` or `supabase_reader.py` either way.
  - Real concurrency. The fake stays single-process/in-memory; injection simulates the same *sequential*-interleaving collision #324's guard targets (one commit fully lands before the next starts), not genuinely simultaneous writers.
  - Any change to the `ResultStore` Protocol or the manifest schema. Injection hooks are additional public methods on the concrete `FakeResultStore` class, invisible to `Protocol`-typed callers and to `SupabaseResultStore`.
  - A reaper/background sweep for orphaned fake state — not applicable; the fake has no persistent storage to leak into.

## Decisions

### A. Give `commit` a real per-output checkpoint, then hang `fail_next_commit` off it

- Decision: add an explicit loop after `hash_outputs` in `FakeResultStore.commit` — `for name in outputs: recorded.append(name)` — mirroring where `SupabaseResultStore.commit`'s upload loop sits relative to its own `hash_outputs` call. In the non-injected case this loop is a no-op (nothing to upload anywhere; the fake's "storage" is the `StoredRun` appended at the end), so it changes no existing observable behavior.
- `fail_next_commit(experiment, tool_class, *, after_outputs=0)`: one-shot per `(experiment, tool_class)`; the next `commit()` call for that pair raises `CommitFailedError` once `after_outputs` outputs have been recorded by the loop above, then clears itself. `after_outputs` ranges `0..len(outputs)` inclusive:
  - `0..len(outputs)-1` models a failure *during* the per-output loop — the analog of `test_commit_failure_cleans_up_orphaned_objects_from_partial_upload` (an upload fails partway; some but not all outputs "recorded").
  - `len(outputs)` (all outputs recorded, nothing appended yet) models a failure at the pre-append/manifest-write-equivalent step — the analog of a failure after every upload succeeds but before `write_manifest` lands. This single parameter covers both of `SupabaseResultStore`'s distinct failure points without a second mode, resolving the earlier open question about whether `fail_next_commit` needs a separate "pre-write" flag.
- On injected failure: nothing is appended to `self._runs`, the run stays in `self._open` (see Decision C for why this requires reordering existing code), and the staging dir is left intact for retry.
- Alternatives considered: injecting the fault inside `_artifacts.hash_outputs` itself — rejected, since that module is shared by both adapters and is explicitly the *tautological* half of parity per the issue ("both stores share `_artifacts.hash_outputs`"); faulting it would touch production code neither adapter's real failure path actually goes through (Supabase's failures are in the upload loop, which runs *after* `hash_outputs` returns cleanly).

### B. Reuse `next_version_id` itself for the fake's allocation, via a duck-typed adapter

- Decision: import `next_version_id` from `bloom_mcp.storage.versioning` into `fake_store.py` (it already imports `version_dir_name` from the same module). Since `next_version_id(manifest: Optional[Manifest])` only reads `manifest.versions` and each entry's `.id` attribute (`versioning.py:21-35`), build a tiny local adapter — e.g. a `SimpleNamespace(versions=[SimpleNamespace(id=r.run_ref) for r in existing])` — from the fake's `list[StoredRun]` and pass that in. No `Manifest`/Pydantic instance is required; Python attribute access doesn't enforce the type hint.
- Why reuse rather than reimplement: a second hand-written "scan for max(N)+1" in the fake risks silently drifting from the real adapter's semantics over time (e.g. if `next_version_id`'s "never reuse N even after deletion" rule changes) — exactly the kind of divergence this whole change exists to close. Reusing the same function makes that impossible by construction.
- `create_run`'s provisional allocation and `commit`'s two collision checks (below) all go through this same adapter + `next_version_id` call, replacing every use of `f"v{len(existing) + 1}"`.
- A local `_MAX_ID_ATTEMPTS = 3` constant is added to `fake_store.py` (not imported from `supabase_store.py`, whose constant is module-private) — same value, independent constant, matching the existing pattern of each adapter owning its own tuning knob.
- `next_version_id` is imported as a plain module-level name in `fake_store.py`, exactly as it already is in `supabase_store.py` — so a test can `monkeypatch.setattr(fake_store_module, "next_version_id", ...)` to force every reallocation attempt to keep colliding, the same technique `test_retry_exhaustion_before_upload_raises_with_no_uploads` already uses against the real adapter.

### C. `commit`'s two-phase collision guard + `seed_collision`

- Decision: `commit` performs the same two reads `SupabaseResultStore.commit` does, against the adapted view from Decision B: a **pre-record check** at the start (reallocate via `next_version_id`, bounded at `_MAX_ID_ATTEMPTS`, before anything is recorded) and a **pre-append check** immediately before appending the finished `StoredRun` to `self._runs` (treating a late collision exactly like any other commit failure — safe cleanup, retryable).
- `seed_collision(experiment, tool_class, version_id)`: appends a placeholder `StoredRun` at that id directly into `self._runs`, simulating another writer's commit having already landed there. Combined with `fail_next_commit`'s `after_outputs` timing, this drives three distinct parity scenarios:
  - Called before `create_run`/`commit` at all → the pre-record check catches it, reallocates once, succeeds (mirrors `test_interleaved_commits_get_distinct_ids_with_consistent_provenance`).
  - Combined with a monkeypatched `next_version_id` that keeps returning the same colliding id → the pre-record check exhausts its bounded attempts and fails cheaply, nothing recorded (mirrors `test_retry_exhaustion_before_upload_raises_with_no_uploads`).
  - Called *between* a test's own `create_run` and `commit`, timed so it lands after the pre-record check already passed (e.g. the test calls `seed_collision` right before calling `commit`, and `commit`'s pre-record check already ran during `create_run`'s provisional allocation) → only the pre-append check catches it, exercising the "late collision" path (mirrors `test_prewrite_collision_cleans_up_and_retry_succeeds`). Implementation detail to confirm during coding: whichever precise call ordering makes the interloper visible only to the pre-append check, not the pre-record check — this may need `seed_collision` to accept an explicit `visible_at: Literal["pre_record", "pre_append"]` if simple call-ordering in the test can't reliably target one check over the other; default to call-ordering first since it needs no new hook parameter.

### D. Fix the two latent ordering bugs (prerequisite for A–C, not independent)

- Decision: move `self._open.discard(id(run))` (`fake_store.py:79`) and the staging-dir `shutil.rmtree` (currently unconditional inside `hash_outputs`'s `finally`, `fake_store.py:87-88`) to the success branch only — after the entry is appended to `self._runs` — matching `SupabaseResultStore.commit`'s structure (`state.committed = True` and its `shutil.rmtree` call both happen post-`try/except`, success-only).
- Why bundled here rather than filed separately: the bug is unreachable today (`commit` cannot fail), so there is nothing to regression-test independent of adding a failure path (Decision A). The tests Decision A adds (retry-after-injected-failure) are exactly what would catch a regression here going forward; a pin-first red test isn't possible before those hooks exist.
- Not independently observed elsewhere: the 7 other test files constructing `FakeResultStore` (`test_qc_clean_tool.py`, `test_straggler_routing.py`, `test_clustering_tool.py`, `test_pca_analysis_tool.py`, `test_qc_inspect_tool.py`, `test_remove_outliers_tool.py`, `test_workflow_persistence.py`) were checked — none inspect staging-dir presence or `_open` membership after a failure, so this reorder is behavior-preserving for them.

### E. `seed_v2_run`

- Decision: add `seed_v2_run(experiment, tool_class, *, tool, outputs)`, constructing a `StoredRun` directly (bypassing `Provenance.to_version_entry`) with `seed=None`, `agent=None`, `output_sha256={}`, `output_keys={}` — matching the checked-in `manifest_v2.json` fixture's shape field-for-field (verified: that fixture genuinely lacks all four fields; this is schema evolution, not a bug being papered over). Appends directly to `self._runs`, giving the fake a real historical v2-shaped entry to commit alongside, so the v2-back-compat parity scenario (issue #325's Scope bullet) has a fake-side counterpart instead of being `SupabaseResultStore`-only.

### F. Test-harness helper stays test-only, and needs two shapes, not one

- Decision: `test_store_parity.py` gains two small per-kind helpers (not one): `_inject_commit_failure(kind, store, monkeypatch, *, ..., after_outputs)` (branches to `monkeypatch.setattr(bloom_mcp.supabase_client, "upload_file", ...)` for `"supabase"` vs. `store.fail_next_commit(...)` for `"fake"`), and a separate `_seed_collision(kind, store, monkeypatch, *, ...)` (branches to seeding raw manifest JSON into `fake_supabase_storage.objects` for `"supabase"`, matching `test_retry_exhaustion_before_upload_raises_with_no_uploads`'s technique, vs. `store.seed_collision(...)` for `"fake"`) — these are structurally different injection techniques (function monkeypatch vs. raw-storage seeding vs. a fake method call) and collapsing them into one helper would obscure more than it hides.
- Both helpers live in the test file only; no production code needs to know which kind of store is under test.
- Alternatives considered: a `FailureInjectingResultStore` Protocol extension implemented by both adapters — rejected as over-engineering for a test-only concern; `SupabaseResultStore` already has working injection points via `monkeypatch`, so adding a second, parallel injection API to the production adapter just to satisfy a shared test interface would be a change with no runtime consumer.

## Explicitly not mirrored, and why

Of `test_supabase_result_store.py`'s 8 failure/collision tests, 5 get a fake-side mirror (Decisions A–C above cover: retryable-and-does-not-leak, cleans-up-partial-upload, interleaved-distinct-ids, retry-exhaustion, prewrite-collision). Two are intentionally **not** mirrored, and one is out of this change's scope entirely:

- `test_cleanup_failure_does_not_mask_original_error` — this asserts that a *second* failure (the cleanup delete itself failing) doesn't mask the first. The fake has no delete operation that can independently fail — nothing ever left the process, so there is no fallible cleanup step to simulate a second failure in. No analog exists without inventing a cleanup operation purely to make it failable, which would be test theater, not parity.
- `test_noncolliding_commit_reads_manifest_twice_with_no_reallocation` — this asserts an implementation detail (`SupabaseResultStore.commit` calls `read_manifest()` exactly twice on the non-colliding path), not an externally observable contract. The fake has no "manifest reads" to count in the first place (Decision B's adapter is built in-memory, not fetched). Not a parity gap.
- Data integrity note (not a test gap): the v2-manifest fields `seed`/`agent`/`output_sha256`/`output_keys` being absent on `seed_v2_run`'s entries is intentional, historically-accurate schema evolution (v2 predates those v3-additive fields), not a defect to "fix" in the fake.

## Risks / Trade-offs

- The version-id allocation change (Decision B) is larger than "add two methods" — it changes `create_run`'s provisional-id behavior too, not only `commit`'s collision path. On the non-colliding path the observable output (`v1`, `v2`, ... in commit order) is unchanged, since `next_version_id`'s max(N)+1 scan agrees with the count-based scheme whenever ids were never skipped or reused — which is every existing test today. Existing tests are expected to pass unmodified; this is called out as its own task with an explicit "still green" check rather than assumed.
- The two ordering-bug fixes (Decision D) change `FakeResultStore.commit`'s behavior on any already-passing test that happens to depend on the old (buggy) ordering — checked directly (see Decision D) and found none.
- `fail_next_commit`/`seed_collision`/`seed_v2_run` are test-only public surface added to a production module (`fake_store.py`) with no non-test caller — consistent with `FakeReader.add_experiment`/`add_cleaned_version`, the same shape (seeding hooks, no production caller), so this matches existing precedent.
- This change should not merge ahead of PR #464 (see proposal.md's Why) — if #464's guard shape changes during its own review, this design's Decisions B/C (which assume `_MAX_ID_ATTEMPTS = 3` and the exact two-check placement) may need re-verification against the merged version.

## Migration Plan

Purely additive to `fake_store.py` (new loop, new allocation adapter, new methods) plus the two ordering-bug fixes (behavior change confined to the previously-unreachable failure path) and new/parametrized tests. No schema, no migration, no feature flag — rolls out via normal PR review, after PR #464 merges.

## Open Questions

- **Needs explicit reviewer sign-off**: is scoping `ExperimentReader`/`FakeReader` parity out of this change acceptable, given issue #325's title names it but its body/checklist don't, and there are no issue comments either way? Proposal owner's read is that there's no analogous failure surface to test (see Non-Goals), but this hasn't been confirmed by whoever files/triages #325.
- Decision C's "late collision" timing (making `seed_collision` visible only to the pre-append check, not the pre-record check) is sketched via call-ordering; confirm during implementation whether that's reliable or whether `seed_collision` needs an explicit `visible_at` parameter.
- Should `_MAX_ID_ATTEMPTS` in the fake be forced to literally match `supabase_store.py`'s value via a shared test assertion (so the two constants can't silently drift apart), or is "same value, independent constant, documented" (this change's choice) sufficient? Leaning toward documented-only for now — no test currently asserts the real adapter's constant value either.
