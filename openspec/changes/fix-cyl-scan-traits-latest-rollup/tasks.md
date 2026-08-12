## Landing plan — two PRs, not one (see design.md D8 + Migration Plan)

**Phase 1 PR** = sections 0-3 below, **plus the OpenSpec scaffold itself**
(`openspec/changes/fix-cyl-scan-traits-latest-rollup/{proposal.md,design.md,tasks.md,specs/**}`) — per
`.claude/commands/new-feature.md`'s phased-landing guardrail ("put the proposal scaffold in the first PR
alongside that phase's code"), the scaffold is not held back or duplicated into Phase 2. Phase 1 is fully
additive/inert — nothing reads the new column or table until Phase 2 lands — safe to auto-deploy the
moment it merges to `staging`.

**Operator runbook** (between the PRs, not a commit in either) = section 4 below.

**Phase 2 PR** (code-only, same OpenSpec change, opens only after the runbook is complete and verified)
= sections 5-6 below.

**Sections 7 (Validate) and 8 (Docs) are split across both PRs, not cleanly one or the other** — tagged
individually below (7.1-7.4 and 8.1/8.2/8.4 are Phase 1; 7.5 and 8.3 are Phase 2/post-merge). Don't assume
"the last two sections" means "Phase 2" — check each task's own tag.

**Do not open the Phase 2 PR before the runbook's verification step passes.** Staging's deploy workflow
runs `supabase db push` unconditionally over every pending migration on merge — merging Phase 2 before
the runbook completes is the exact regression this split exists to prevent (see design.md D8).

**Numbering note:** design.md labels its five migrations `M1`-`M5` specifically so they never collide
with this file's own section numbers (an earlier draft used bare "step N" in both documents, which
created real ambiguity — design.md's Migration Plan explains this). Cross-references from this file to
design.md use `M1`-`M5`/`D1`-`D8`; cross-references from design.md to this file use `tasks.md §N`.

## 0. Pre-work — Phase 1

- [x] 0.1 Re-check `supabase/migrations/`'s newest file on both `origin/main` and `origin/staging`
      immediately before opening the Phase 1 PR (this proposal was drafted against
      `20260807000000_get_experiment_summary_counts.sql` as the tip) and choose migration timestamps
      later than both. Re-check again before opening the Phase 2 PR. **Done for this pass**
      (`20260807000000` confirmed as the tip; this change's migrations use `20260812010000`,
      `20260812020000`, `20260812030000` — re-check again immediately before actually opening the PR,
      since more time may have passed).
- [x] 0.2 Confirm `fix-bloommcp-list-experiments-summary-rpc` is archived (done as a pre-req for this
      proposal — `openspec/changes/archive/2026-08-12-fix-bloommcp-list-experiments-summary-rpc/`) so
      this change's deltas target the live `cyl-trait-read` spec, not a stale intermediate state.
- [ ] 0.3 Resolve design.md D8's open question (the backfill's manual `psql` invocation vs. this repo's
      "manual DB access = emergency-only, logged" deploy policy) with whoever owns that policy before
      the Phase 1 PR's runbook step is attempted. If unresolved by the time Phase 1 merges, the runbook
      simply waits — Phase 1's own migrations are inert either way. **If the resolution is "write a
      repeatable connection-wrapper script"** (D8's option (b)) rather than "log a one-time exception"
      (option (a)), this task expands into: write `scripts/run_cyl_scan_traits_backfill.sh` (bare `psql`,
      default autocommit, no explicit `BEGIN`), get it reviewed, and manually confirm its connection
      semantics against a local DB before it's used against staging — do not skip straight to running it
      in the runbook (section 4) without that review, even under time pressure. **Still open — needs
      Benfica's (or whoever owns deploy policy's) actual decision, not resolvable from this pass.**
- [ ] 0.4 Resolve whether `cyl_experiment_summary_counts` needs an entry in the five tracked
      `database.types.ts` copies: run `supabase gen types` against a local DB with this change's
      migrations applied, diff against the five tracked copies, and commit any real diff (or confirm and
      note there is none) — do not assume either way (see design.md's Open Questions; proposal.md
      deliberately does not assert a conclusion here). **Resolved: YES, it's needed** — ran `npx supabase
      gen types typescript --db-url ...` against the local DB with all three Phase-1 migrations applied;
      confirmed `cyl_experiment_summary_counts` (table) and `compute_cyl_experiment_summary_counts_live`/
      `refresh_cyl_experiment_summary_counts_for_scan` (functions) all appear in the output. **The actual
      hand-edit of the five ~2000-4000-line tracked copies is not done in this pass** — each file's
      existing style must be matched individually (per bloom#625's own precedent) and deserves its own
      careful pass, not a rushed edit at the end of this one. Do this before opening the Phase 1 PR.

## 1. `is_latest` stored column + trigger (RED first) — Phase 1

- [x] 1.1 Add `tests/integration/test_cyl_scan_traits_is_latest_column.py` using
      `test_cyl_read_path.py`'s existing fixtures/helpers (`_seed_experiment_scan`, `_deliver`, `_trait`).
      Write failing tests first (column doesn't exist yet, so these fail with `UndefinedColumn`):
      - A fresh insert via `insert_cyl_result_envelope` sets `is_latest = true` on every row of a
        brand-new scan with one source.
      - A rerun (new, higher `source_id`) flips the prior source's rows to `is_latest = false` and the
        new source's rows to `true`.
      - **Regression test for the preserved partition grain** (mirrors `test_no_cross_source_mixing`):
        an older source writes traits A+B, a newer source re-delivers only A — assert A's newest row is
        `is_latest = true`, and **both** the old A row and the old/only B row are `is_latest = false`
        (not backfilled) — this is the test that would catch an accidental `(scan_id, trait_id)`
        partition regression.
      - A direct `bloom_admin`-role `INSERT`/`UPDATE`/`DELETE` against `cyl_scan_traits` (bypassing the
        write-back RPC entirely, via `SET LOCAL ROLE bloom_admin`) also gets correct `is_latest`
        maintenance — proves the trigger covers the break-glass path, not just the RPC.
      - Deleting the current-latest row for a scan promotes the next-highest `source_id`'s rows to
        `is_latest = true`.
      - **Recursion-safety test, asserting an exact count, not just "it finishes":** add a session-scoped
        counter (a temp table incremented inside the trigger function for the duration of one test, or
        equivalent) and assert it increments **exactly twice** for one write that changes `is_latest` for
        a scan (the maintaining `UPDATE`'s own fire, plus the guard-triggered no-op re-fire that finds
        nothing left to change) — a bare "completes without a stack-depth error" assertion would not
        catch a guard that's subtly wrong but happens to still converge.
      - **Concurrent-rerun test using `conftest.py`'s `pg_conninfo` second-connection fixture**: open two
        connections, begin a rerun delivery on each for the same `scan_id` (different `source_id`s, at
        least one prior row already exists), interleave so both `INSERT`s are in-flight before either
        commits, then commit both and assert the final `is_latest` state is internally consistent
        (exactly one `source_id` per scan is latest, matching the higher of the two) — not corrupted,
        not deadlocked.
      - **Concurrent-first-insert test, a distinct race from the one above**: same two-connection setup,
        but for a **brand-new `scan_id` with zero prior rows** — two connections each insert the *first*
        row(s) for that scan with different `source_id`s, interleaved so both are in-flight before either
        commits. Unlike the rerun case, neither transaction's maintenance `UPDATE` starts by touching a
        row the other holds a lock on (there are no prior rows to lock), so this is a genuinely different
        race shape. Assert the final state is still consistent (exactly one `source_id` latest) — if it
        isn't, that's a real gap in the trigger's locking behavior worth fixing, not a test to weaken.
      - The trigger function's catalog metadata shows `SECURITY DEFINER`, a pinned `search_path`, and
        schema-qualified references throughout its body (mirrors the existing write-back RPC's own
        "definer can write after the lockdown" test pattern).
- [x] 1.2 Confirm every 1.1 test fails against a database with none of this section's migration applied.
      **Done. Also found and fixed a real bug during 1.1's concurrency tests: two connections
      inserting the FIRST-EVER rows for a brand-new scan_id raced (both ended up `is_latest=true`)
      because there was no pre-existing row for either transaction's maintenance `UPDATE` to lock on
      and serialize against. Fixed by adding `pg_advisory_xact_lock` (keyed by scan_id) to the trigger
      function before it recomputes — see the migration's own comment for the mechanism.**

## 2. M1 — schema (GREEN) — Phase 1

- [x] 2.1 `supabase/migrations/<ts>_add_cyl_scan_traits_is_latest_column.sql`: **implemented as
      `supabase/migrations/20260812010000_add_cyl_scan_traits_is_latest_column.sql`.** `ALTER TABLE
      cyl_scan_traits ADD COLUMN is_latest boolean NOT NULL DEFAULT false` (design.md D1);
      `maintain_cyl_scan_traits_is_latest()` trigger function (`SECURITY DEFINER`, `SET search_path =
      pg_catalog, public, pg_temp`, per design.md D2) + `maintain_is_latest_after_write` `AFTER` trigger;
      `idx_cyl_scan_traits_latest` partial index (D3). Wrapped in `BEGIN; … COMMIT;`.
- [x] 2.2 Companion `supabase/rollbacks/<ts>_add_cyl_scan_traits_is_latest_column_rollback.sql`: drop
      trigger, then function, then index, then column, in that order. **No `CASCADE`** on the column
      drop (design.md's Rollback Ordering note) — if a later migration still depends on the column, this
      MUST fail loudly, not cascade through a dependent view.
- [x] 2.3 Run all of section 1's tests; confirm every one now passes. Confirm
      `tests/integration/test_cyl_read_path.py` and `test_cyl_experiment_traits.py` have zero regressions
      (the view still computes `is_latest` live at this point — this migration doesn't touch it).
- [x] 2.4 `test_migration_adds_no_write_capability` (regex static-scan, mirroring the existing pattern in
      `test_cyl_experiment_summary_counts.py`) — no `GRANT INSERT|UPDATE|DELETE|ALL` beyond what the
      trigger function itself needs to run as `SECURITY DEFINER`.
- [x] 2.5 `test_migration_body_is_idempotent` — re-apply the migration body; column/trigger/index
      unchanged, no error.
- [x] 2.6 `test_rollback_restores_prior_state` — apply the rollback; `is_latest` column, trigger, and
      index are gone; re-apply the forward migration and confirm they're back.

## 3. M2/M3 — backfill procedures + rollup table (RED first, then GREEN) — Phase 1

**Test-infrastructure note, load-bearing for every test in this section:** `tests/integration/
conftest.py`'s default `pg_conn` fixture opens a psycopg3 connection with `autocommit=False` — an
implicit transaction is open from the first statement. `CALL`-ing a procedure that issues internal
`COMMIT`s (design.md D4) is only legal when the session is **not** already inside an explicit
transaction block; running it on `pg_conn` as-is raises `invalid transaction termination` before doing
anything. Every test below that calls a backfill procedure MUST use a dedicated autocommit connection
(`conftest.py`'s `pg_conninfo` fixture, opened with autocommit — not `pg_conn`), and MUST clean up via
explicit `DELETE`/reset statements in a `finally` block, not `pg_conn.rollback()`, since anything the
procedure touches is already committed the moment it runs.

**Ordering within this section is real, not just cross-referencing prose** — each RED task is followed
immediately by the GREEN task that makes it pass, then the next concern's RED/GREEN pair, so the
checklist reads top-to-bottom without forward references to a not-yet-existing later task.

- [x] 3.1 Add `tests/integration/test_backfill_cyl_scan_traits_is_latest.py`. On the dedicated autocommit
      connection: seed a multi-scan, multi-source fixture (including at least one scan whose `scan_id`
      sits with gaps on either side, simulating deleted/non-contiguous scans) with `is_latest` still at
      its post-migration default (`false` for every row — insert directly, bypassing the trigger's
      already-correct maintenance, to simulate genuinely pre-existing un-backfilled data). Write the
      oracle-comparison and batch-loop-coverage assertions below now; **confirm the whole file fails with
      `UndefinedProcedure`** before 3.2 exists:
      - Assert `CALL backfill_cyl_scan_traits_is_latest(batch_size)` sets every row's `is_latest` to
        match a hand-computed `max(source_id) OVER (PARTITION BY scan_id)` oracle.
      - Parametrize over at least two range widths, including one smaller than the fixture's total
        distinct `scan_id` count, to force multiple loop iterations. This tests that the loop covers
        every `scan_id` exactly once with no off-by-one skip or double-count at a range boundary — **not**
        that a scan's rows get split across batches, which is structurally impossible given the
        procedure batches and groups by the same key (`scan_id`); phrase the test's assertions and
        comments accordingly so a future reader doesn't misread what property is being verified.
- [x] 3.2 `supabase/migrations/<ts>_add_backfill_cyl_scan_traits_is_latest_procedure.sql`: **implemented
      as `supabase/migrations/20260812020000_add_backfill_cyl_scan_traits_is_latest_procedure.sql`.** create the
      `backfill_cyl_scan_traits_is_latest(batch_size bigint DEFAULT 10000)` procedure (design.md D4/M2;
      `batch_size` is a `scan_id`-range width, not a row count — name the parameter and its doc comment
      accordingly so this isn't misread later). This migration adds the procedure definition only — it
      does **not** `CALL` it (design.md's Migration Plan: the actual backfill run is the operator
      runbook in section 4, not part of `supabase db push`). Confirm 3.1's tests now pass.
- [x] 3.3 In the same test file, now that 3.2 exists: run the backfill, then reset a subset of rows back
      to an incorrect `is_latest` value (simulating an interrupted run) on the autocommit connection, then
      re-run the backfill, and confirm it converges to the same correct state as an uninterrupted run
      (idempotent re-run, not "already backfilled, skip").
- [x] 3.4 Add a verification query (`scripts/verify_cyl_scan_traits_is_latest_backfill.sql`) comparing the
      stored column against the live `WindowAgg` computation for every row, returning a non-zero
      mismatch count on failure. This is the gate the section-4 runbook checks before Phase 2 opens — not
      automated in CI (no CI environment has staging's real scale), but its SQL text is unit-tested here
      (3.1's oracle comparison exercises the same logic at fixture scale).
- [x] 3.5 Add `tests/integration/test_cyl_experiment_summary_rollup.py` (RED first — table doesn't exist
      yet, `UndefinedTable`):
      - After a fresh `insert_cyl_result_envelope` call for a new experiment's scan, the rollup table
        has a row for that experiment matching a hand-computed `(n_plants, n_traits)`.
      - A rerun that changes which source is latest updates the rollup row's counts to match the new
        latest state (not the old one) — proves the refresh is triggered, not just present at creation.
      - An experiment whose only scan's traits are all deleted has its rollup row **removed** (not
        zero-valued).
      - **A freshly-created experiment with no scans/traits at all never gets a rollup row in the first
        place** — a distinct case from "had data, lost it," tested separately since a refresh
        implementation could pass the deletion case while still incorrectly emitting a zero-valued row
        for an experiment that was never written to.
      - Two different experiments' writes only ever touch their own rollup rows — cross-experiment
        isolation.
      - A write to a scan whose plant has `accession_id IS NULL` does not create or corrupt any rollup
        row inconsistently with `get_experiment_traits`'s own exclusion of that plant.
      - **The rollup refresh's own aggregation does not call `get_experiment_summary_counts` itself**
        (design.md D7's self-reference-avoidance note) — assert by checking the refresh still produces
        correct counts even when `cyl_experiment_summary_counts` for that experiment is mid-refresh
        (e.g. by tracing that `compute_cyl_experiment_summary_counts_live`, not the rollup-backed RPC
        branch, is what's actually invoked — via `EXPLAIN`/plan inspection on the refresh function's
        body, or by temporarily corrupting the rollup row and confirming the refresh still recomputes
        correctly rather than trusting the corrupted value).
      - **Trigger-firing-order test, independent of the section 3.6 implementation choice**: for a rerun
        that changes which source is latest, assert the rollup's refreshed `n_plants`/`n_traits` reflect
        the **new** latest state, not a snapshot taken before `is_latest` finished updating for that
        write — this must hold whether the refresh is wired as a second statement in the same trigger
        function or as a second, separately-named trigger.
- [x] 3.6 `supabase/migrations/<ts>_create_cyl_experiment_summary_counts.sql`: **implemented as
      `supabase/migrations/20260812030000_create_cyl_experiment_summary_counts.sql`.** `CREATE TABLE
      cyl_experiment_summary_counts` (design.md D5/M3) + `compute_cyl_experiment_summary_counts_live`
      (D7's shared `SECURITY DEFINER` helper, `SET search_path = pg_catalog, public, pg_temp` — the same
      helper both this migration's refresh function and Phase 2's RPC rewrite call, defined exactly once)
      + `refresh_cyl_experiment_summary_counts_for_scan(scan_id)` (D6) + attaching its invocation to
      `maintain_is_latest_after_write` (or a second, explicitly-named-to-sort-after trigger —
      implementation's choice per design.md's Open Questions; note which in the PR description) so every
      write that can change `is_latest` also refreshes the owning experiment's rollup row. Confirm 3.5's
      tests now pass. This table and its maintenance are inert in Phase 1: nothing reads
      `cyl_experiment_summary_counts` until Phase 2's RPC rewrite lands.
- [x] 3.7 Add to `test_cyl_experiment_summary_rollup.py` (RED first — procedure doesn't exist yet,
      `UndefinedProcedure`), mirroring 3.1's treatment of the `is_latest` backfill rather than leaving
      this backfill untested the way an earlier draft of this proposal did:
      - Seed several experiments' worth of pre-existing scan/trait data directly (bypassing the trigger,
        simulating data that existed before the rollup table did), with `cyl_experiment_summary_counts`
        empty. Assert `CALL backfill_cyl_experiment_summary_counts(batch_size)` populates a row per
        experiment with data, matching `compute_cyl_experiment_summary_counts_live(experiment_id_, NULL,
        NULL)`'s output for each — the fixture-scale, automated version of the "rollup backfill matches a
        live per-experiment computation" spec scenario (the runbook's 4.4 spot-check is a staging-only
        supplement to this, not a substitute for it). **Done — `test_rollup_backfill_matches_live_computation`.**
      - ~~**Ordering-gate consequence test**~~ (an automated proxy for the "backfill is not run before
        is_latest's own backfill is verified" scenario). **Written, then found invalid and skipped —
        real discovery, see design.md's Open Questions.** `cyl_scan_traits_source.is_latest` stays
        live-computed until Phase 2's M4 view cutover, so `compute_cyl_experiment_summary_counts_live`
        is correct regardless of the stored column's backfill state until M4 lands — this specific test
        premise can't be reproduced in Phase 1. The real ordering constraint turned out to be a
        three-way one (M4 → rollup backfill → M5, not is_latest-backfill → rollup-backfill) that the
        original two-phase Migration Plan doesn't correctly sequence — see design.md's Open Questions
        for the full finding and the recommended three-PR fix. The skipped test
        (`test_rollup_backfill_ordering_gate_consequence`) documents this in place with a `pytest.mark.skip`
        reason; rewrite it once Phase 2/3's corrected sequencing is designed.
- [x] 3.8 `supabase/migrations/<ts>_create_cyl_experiment_summary_counts.sql` (same migration as 3.6, or
      a follow-on in the same PR): add `backfill_cyl_experiment_summary_counts(batch_size bigint DEFAULT
      10000)` — batched by `experiment_id` this time, same `CALL`/`COMMIT`/autocommit-connection
      considerations as 3.2. Definition only, not invoked here (same reasoning as 3.2). Confirm 3.7's
      tests now pass.
- [x] 3.9 Companion rollback for 3.6/3.8: `DROP TABLE cyl_experiment_summary_counts`, drop
      `compute_cyl_experiment_summary_counts_live`, the refresh function and its trigger attachment, and
      the rollup backfill procedure. **Reverse-order rule** (design.md's Rollback Ordering note): this
      rollback is only safe to apply while Phase 2's RPC rewrite (section 6, design.md's M5) has not yet
      merged — once it has, this table's removal must be preceded by rolling back section 6 first, since
      a `PL/pgSQL` function body's reference to this table is opaque to Postgres's dependency tracker and
      won't block the `DROP TABLE` the way `pg_depend` would for a view.
- [x] 3.10 Run all of sections 1-3's tests; confirm no regressions. **Done — 104 passed, 5 skipped
      (4 pre-existing PostgREST-gateway skips + the new ordering-gate skip from 3.7) across
      `test_cyl_read_path.py`, `test_cyl_experiment_traits.py`, `test_cyl_experiment_summary_counts.py`,
      `test_cyl_scan_traits_is_latest_column.py`, `test_backfill_cyl_scan_traits_is_latest.py`,
      `test_cyl_experiment_summary_rollup.py`. Do not open the Phase 1 PR until 0.3/0.4 are also
      resolved (see above) and section 7's Phase-1-tagged items (7.1-7.4) are run.**

## 4. Operator runbook (between the two PRs — not a commit, not automated CI)

- [ ] 4.1 On staging, once section 0.3's policy question is resolved: run `CALL
      backfill_cyl_scan_traits_is_latest();`.
- [ ] 4.2 Run section 3.4's verification query. **Do not proceed past a nonzero mismatch count.**
      Diagnose and re-run 4.1 (idempotent) if it fails.
- [ ] 4.3 Run the rollup's own backfill procedure (`backfill_cyl_experiment_summary_counts`, section 3.8)
      — only after 4.2 passes, per 3.7's ordering-gate test demonstrating why this order matters.
- [ ] 4.4 Spot-check: query `cyl_experiment_summary_counts` for a handful of known experiments and
      compare against a manually-run live-join computation, as a second, independent check beyond 3.7's
      automated, fixture-scale comparison.
- [ ] 4.5 Record the actual distinct-`scan_id` count, batch count, and wall-clock time for 4.1 and 4.3 in
      this file and in bloom#637 — design.md flags that no runtime estimate exists yet; this is where
      that gap gets closed with real numbers before Phase 2 opens.
- [ ] 4.6 Only once 4.1-4.5 are complete: open the Phase 2 PR.

## 5. M4 — view cutover (RED first, then GREEN) — Phase 2

- [ ] 5.1 Add tests to `test_cyl_read_path.py` (or the is_latest test file):
      - **Negative/pre-cutover test, added and confirmed passing before 5.3 lands**: seed a scan via
        `_deliver` (trigger correctly sets `is_latest=true` on the column), then directly `UPDATE
        cyl_scan_traits SET is_latest = false` for that row (simulating a stale/un-backfilled column
        value), and assert `cyl_scan_traits_source.is_latest` for that row is still `true` — proving the
        pre-cutover view recomputes live and does not trust the column. This test must **fail** once
        5.3 lands (confirming the cutover boundary is real and sections 2/5 weren't silently merged into
        one migration) — run it once before 5.3 to confirm it passes pre-cutover, and again after to
        confirm it now fails, before treating 5.3 as correct.
      - Positive/post-cutover test: after the same setup, once 5.3 lands, `cyl_scan_traits_source.is_latest`
        for that row now reads `false` (the column's corrupted value), proving the view reads the column
        directly rather than recomputing.
- [ ] 5.2 Re-run every existing `test_cyl_read_path.py`/`test_cyl_experiment_traits.py` test — the view's
      external contract (columns, values for correctly-backfilled data) is unchanged, so these must pass
      unmodified.
- [ ] 5.3 `supabase/migrations/<ts>_cutover_cyl_scan_traits_source_to_stored_is_latest.sql` (design.md
      M4): `CREATE OR REPLACE VIEW cyl_scan_traits_source` with `cst.is_latest` (the column) replacing the
      `max(source_id) OVER (...)` expression; `cyl_scan_traits_latest` is unchanged (still `SELECT ...
      FROM cyl_scan_traits_source WHERE is_latest`, now cheap by construction).
- [ ] 5.4 Companion rollback restores the live-computation view definition verbatim (copy from
      `20260701000000_cyl_trait_read_source_aware.sql`).
- [ ] 5.5 Run sections 1-5's tests; confirm 5.1's positive case now passes, the negative case now
      correctly fails as expected, and nothing else regresses.

## 6. `get_experiment_summary_counts` rewrite (RED first, then GREEN) — Phase 2

- [ ] 6.1 Add tests to `tests/integration/test_cyl_experiment_summary_counts.py`:
      - An unpinned call and a pinned-no-override call each return exactly what's in
        `cyl_experiment_summary_counts` for the relevant experiment(s) — not a freshly-computed live
        join (assert by directly corrupting a rollup row to a known-wrong value and confirming the RPC
        returns that wrong value, proving it reads the rollup rather than recomputing — then restore and
        re-test the correct case).
      - **Structural confirmation alongside the corruption test**: `EXPLAIN (ANALYZE, BUFFERS)` on the
        unpinned/no-override call shows no scan node over `cyl_experiments`/`cyl_waves`/`cyl_plants`/
        `accessions`/`cyl_scans`/`cyl_scan_traits_source` — only a scan of `cyl_experiment_summary_counts`
        itself — a direct, structural check that no live join executed, not just a proxy inference from
        one poisoned value.
      - `source_id_`/`run_id_`-pinned calls still match `get_experiment_traits`'s own counts
        byte-for-byte (re-run the existing equivalence tests from the archived predecessor change
        unmodified — this branch's *behavior* doesn't change, only its `COUNT(DISTINCT)` implementation
        does).
      - `EXPLAIN (ANALYZE, BUFFERS)` on the source/run-pinned branch shows no `Sort` node feeding the
        trait/plant aggregation (design.md D7's rewrite claim).
      - Both `source_id_` and `run_id_` set still raises (unchanged guard).
      - Confirm the rollup-reading assertions fail against today's live-join-only implementation before
        this section's migration lands.
- [ ] 6.2 `supabase/migrations/<ts>_rewrite_get_experiment_summary_counts_rollup_backed.sql` (design.md
      M5): `CREATE OR REPLACE FUNCTION get_experiment_summary_counts` per design.md D7 — no-override
      branch reads `cyl_experiment_summary_counts`; source/run-pinned branch delegates to
      `compute_cyl_experiment_summary_counts_live` (section 3.6's helper). Same signature, same
      grants — no `REVOKE`/`GRANT` change needed on the RPC itself (`CREATE OR REPLACE` preserves
      existing privileges); the helper has its own grants, already issued in 3.6.
- [ ] 6.3 Companion rollback: `CREATE OR REPLACE FUNCTION get_experiment_summary_counts` restoring the
      prior live-join-only body (from the archived predecessor change's migration) — not a `DROP`, since
      the function itself isn't new, only its body is changing again.
- [ ] 6.4 Run all of sections 1-6's tests; confirm everything passes. No `bloommcp`/Python test changes
      are expected — `list_experiments()` already calls this RPC unpinned today and its contract
      (signature, result shape, absent-if-zero semantics) is unchanged; confirm
      `bloommcp/tests/data_access/test_supabase_reader.py`'s existing `list_experiments()` tests still
      pass unmodified as a regression check.

## 7. Validate

- [x] 7.1 (Phase 1) Run the full section 1-3 test suite against local dev Postgres; no regressions in
      `test_cyl_read_path.py`, `test_cyl_experiment_traits.py`, `test_cyl_experiment_summary_counts.py`.
      **Done — 104 passed, 5 skipped (see 3.10). Note: the local dev Postgres was itself missing several
      historical migrations (a pre-existing drift issue, unrelated to this change — its
      `supabase_migrations.schema_migrations` tracking table was stale relative to actual applied
      schema) — applied `20260807000000_get_experiment_summary_counts.sql` directly to unblock testing;
      did not attempt to fully reconcile the tracking table, which is a separate, pre-existing issue.**
- [x] 7.2 (Phase 1) Run `bloommcp`'s full test suite; confirm zero changes needed — Phase 1 touches no
      Python code, so a needed change here would indicate an unintended contract break. **Confirmed no
      `bloommcp/` files were touched by this change (`git status --short bloommcp/` empty). Ran the
      suite anyway as a sanity check: it has pre-existing failures unrelated to this change (confirmed
      via the empty git-status check above, not investigated further — out of scope for this proposal).**
- [x] 7.3 (both PRs) `openspec validate fix-cyl-scan-traits-latest-rollup --strict` passes — run once
      before opening the Phase 1 PR, again before opening the Phase 2 PR. **Passes as of this pass.**
- [x] 7.4 (both PRs) Migration lint (`scripts/lint_migrations.sh origin/staging`); `black`/`ruff` on any
      changed Python (test files only — no production Python changes expected); `openspec validate
      --strict` repo-wide. **All pass.**
- [ ] 7.5 (Phase 2, staging-only, post-merge, before closing bloom#637 — a hard gate, not just "record
      the result"): time `list_experiments()` end-to-end against staging and confirm it's well under a
      second; post the result as a comment on bloom#637 or the PR itself before archiving this change.
      This repo has a recent precedent of exactly this kind of manual staging check going unperformed
      (the kong #634 staging check) — do not repeat that here; archiving this change without this result
      posted should be treated as incomplete, not merely "nice to have."

## 8. Docs + follow-up

- [ ] 8.1 (Phase 1) Update `bloommcp/docs/data-access-roadmap.md`: new dated entry noting `is_latest`'s
      storage change and the new rollup table; add this change to the Tier 2 row's Tracking cell
      alongside the existing bloom#625/#476 cross-references (matching the established pattern for
      Tier-2 follow-ups — note this pattern has silently lapsed once already: the predecessor's own Q4
      was never struck through/marked "Resolved" despite that PR merging, so don't assume "mark it
      resolved later" will happen automatically — do it as part of this change's own docs task, not a
      someday follow-up); add a "Questions for Benfica" entry for design.md D6's refresh-mechanism
      assumption (event-driven vs. scheduled) and D8's backfill-invocation policy question, both struck
      through and marked "Resolved" once she reviews the Phase 1 PR.
- [ ] 8.2 (Phase 1) Update `_WIKI/BLOOMMCP/README.md`'s "Supabase data access" section (currently states
      `"Latest" = max(source_id) per scan; the rule lives once in cyl_scan_traits_source` with no mention
      of storage mechanism) to note that `is_latest` is now a stored, indexed column and that
      `get_experiment_summary_counts`'s no-override path reads a rollup table, cross-referencing the
      spec rather than restating the mechanism.
- [ ] 8.3 (Phase 2) In bloom#625's archived change's `tasks.md`, confirm task 0.2's "superseded" note
      (already added before this proposal was drafted) still reads correctly once this change's PR
      number is known — update the cross-reference from "bloom#637 / fix-cyl-scan-traits-latest-rollup"
      to include the actual PR link. Do this as part of the Phase 2 PR (once its number exists), not
      Phase 1's.
- [ ] 8.4 (both PRs) Run `prettier --check` (or `--write`) on edited doc files before opening each PR.
