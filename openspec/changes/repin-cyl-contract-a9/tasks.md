> **Commit plan and atomicity (read first).**
>
> - This is one PR to `staging`, and it is squash-merged. The squash message is `COMMIT_MESSAGES`, so
>   every branch commit message reaches `staging` and later `main` verbatim.
> - **No closing keywords** (close, fix, resolve, and their variants followed by `#N`) may appear in
>   any commit message, the PR title or the PR body. Use "Part of #895" and "Refs #N". bloom#895 is
>   closed by hand after §5.3.
>
> Commit order:
>
> | # | Commit | Tasks | CI after it |
> |---|---|---|---|
> | C0 | `chore(openspec): archive repin-cyl-contract-a7` | §0 | green, since no CI job touches openspec |
> | C1 | `docs(openspec): propose repin-cyl-contract-a9` | this directory | green |
> | C2 | `test(cyl): pin contract_version explicitly where a7 bodies are re-applied` | §3.1 | green, because the explicit version equals the current pin |
> | C3 | `chore(contracts): re-pin vendored contract v0.1.0a7 -> v0.1.0a9` | §1, §3.3 | green |
> | C4 | `feat(db): re-pin insert_cyl_result_envelope to 0.1.0a9, no cutover guard` | §2, §3.2 | green only as a whole |
> | C5 | `test(bloomctl): move the mocked mismatch message to 0.1.0a9` | §3.4 | green |
> | C6 | `docs(openspec): tick repin-cyl-contract-a9 tasks` | — | green |
>
> - C4 cannot be split. The migration without the `PINNED_VERSION` flip is red, and so is the flip
>   without the migration.
> - RED-before-GREEN is a **local** discipline. Record the observed RED failures in C4's body. Never
>   push a red commit.
> - The local DB must be built from this worktree's tree (`make dev-up` and `make migrate-local`).
>   Don't reuse a stack that has another branch's migrations, such as #902's `20260924120000`, applied.

## 0. Archive `repin-cyl-contract-a7` first (C0)

- [x] 0.1 Tick a7's §2.4 and §4.2, carrying PR #779's recorded evidence:
      - *Docker Compose Health Check* runs 33524629806 and 33566795258 passed, 838 passed and 7
        skipped, including every §2.1 test and `test_execute_grants_are_exactly_the_sanctioned_roles`;
      - the a7 body has been live on staging since #787 closed on 2026-09-08.
- [x] 0.2 Run `openspec archive repin-cyl-contract-a7 --yes`. Confirm:
      - `openspec/specs/cyl-trait-writeback/spec.md`'s *Write-back validates the contract version*
        now reads `0.1.0a7`;
      - `git diff --stat openspec/specs/` touches only that file.
- [x] 0.3 `openspec validate --specs --strict` passes. `openspec validate repin-cyl-contract-a9
      --strict` passes against the new base. A dry `openspec archive repin-cyl-contract-a9` on a
      scratch copy yields `0.1.0a9` in both specs.
- [ ] 0.4 **After the user explicitly says yes in the moment**, close PR #779 as superseded, with a
      one-line comment linking this PR.

## 1. Re-pin the vendored contract, `v0.1.0a7` → `v0.1.0a9` (C3)

- [ ] 1.1 Re-verify against the bytes about to be committed:
      `git -C ../sleap-roots-contracts show v0.1.0a9:schema/result_envelope.schema.json`, diffed
      against the vendored copy, differs only in the `$id` line.
- [ ] 1.2 Write that exact output to `contracts/schema/result_envelope.schema.json` (LF, as
      published). Don't run prettier from inside `contracts/`.
- [ ] 1.3 In `contracts/pin.json`, set `version`, `id` and `source` to `v0.1.0a9`. Check `source` by
      eye, because the drift guard doesn't check it.
- [ ] 1.4 `npm run contracts:gen` must leave `generated/result-envelope.ts` unchanged, since it has
      no `$id` or version string. **If it shows a diff, stop and re-scope.**
- [ ] 1.5 `contracts/README.md`:
      - set "Currently pinned: `v0.1.0a9`";
      - add an **a9 note**: per-run run-manifest naming and resolution (writer helpers plus
        `load_run_manifest`, srp#71). bloomctl still uses the legacy name, which the a9 traits reader
        accepts via `allow_legacy=True` until srp#82. `$id`-only for the schema;
      - add an **a8 note**, for a skipped release: the BREAKING `ModelCard` → `selectors` reshape and
        `Selector`. These are predict-side, Bloom imports neither, and the schema change is
        `$id`-only;
      - don't reuse the "TS byte-identical apart from the `$id` line" phrasing, because the generated
        TS carries no `$id`;
      - re-pin procedure step 2: add `source`;
      - add a step: re-pin the RPC literal in a new forward migration in the same change;
      - rewrite step 5 per the `contract-pinning` delta. Don't add a retiring-version guard, and say
        why (#685 wedge, #787 restamp). Keep the manual staging and production check, re-scoped to
        data-dependent migrations;
      - "Consumer hand-offs": the compatibility set was reconsidered for a9 (bloom#895 option (b),
        with a live a7 producer) and declined because the window is loud and recovered by recompute.
        Drop the claim "no functional case for a range".
- [ ] 1.6 `npm run contracts:check` and `npm run contracts:test` pass.

## 2. Write-back RPC accepts `0.1.0a9`, with no guard (C4, TDD)

- [ ] 2.1 RED, in `tests/integration/test_cyl_writeback_rpc.py`:
      - set `PINNED_VERSION = "0.1.0a9"`;
      - move the literal-bearing boundary tests to a9: bare, `v`-prefixed, and
        `test_version_boundary_forms_rejected[V0.1.0a9,0.1.0a9 ,0.1.0a90,vv0.1.0a9,0.1.0a8]`;
      - add `test_a7_contract_version_rejected[0.1.0a7,v0.1.0a7]` with
        `match="contract_version mismatch"`, then `ROLLBACK TO SAVEPOINT` and assert that no source
        row exists for the key;
      - add `match="contract_version mismatch"` to the existing a2 and a3 rejection tests.

      Against the a7 DB, expect only these to go RED: bare a9 accepted, `v0.1.0a9` accepted, and a7
      rejected. The boundary forms and `0.1.0a8` stay green under a7 as well. They are
      normalization regressions, not RED discriminators.
- [ ] 2.2 RED, file-level, in the new `tests/unit/test_cyl_writeback_a9_migration_files.py` (no DB).
      For each file, take the region from the `CREATE OR REPLACE FUNCTION` line through the final
      `GRANT … bloom_workflows;` line:
      - read with `read_text(encoding="utf-8").splitlines()`, which is CRLF-safe;
      - assert `text.count("$fn$") == 2` per file;
      - assert `MIGRATION_A9.exists()`, with a clear message;
      - assert equal line counts before any comparison;
      - the migration against `20260917140000` must differ in exactly
        `[("    pinned_version constant text := '0.1.0a7';", "    pinned_version constant text := '0.1.0a9';")]`;
      - the rollback against `20260917140000` must give `diffs == []`;
      - the migration's text outside the function region, excluding `--` comments, must contain no
        `DO $`, `RAISE`, `UPDATE` or `DELETE`.
- [ ] 2.3 RED, integration, each in an uncommitted transaction. The CI DB will already be a9, so
      every test **first restores the a7 body in-transaction** with
      `_sql_body(MIGRATION_REDELIVERY_STATUS_FALLBACK)`:
      - `test_a9_migration_body_is_idempotent`:
        - restore a7, then apply a9 twice;
        - assert `SELECT pronargs … WHERE proname='insert_cyl_result_envelope'` returns exactly
          `[(2,)]`;
        - a9 is accepted;
        - under a `SAVEPOINT`, a7 is rejected with `contract_version mismatch`.
      - `test_a9_rollback_restores_a7_with_redelivery_fallback`:
        - apply a9, then the rollback;
        - under a `SAVEPOINT`, a9 is rejected;
        - a7 is accepted under `wf-a`;
        - the same envelope under `wf-b` returns `was_noop=True, status_update_matched=True`. That
          proves the fallback body was restored, not the older one.
      - `test_a9_migration_applies_over_existing_a7_rows`, the no-guard regression:
        - restore a7 and seed via `_call` with an a7 envelope carrying traits and a blob;
        - snapshot the source row (`to_jsonb`), the sorted trait rows and the intermediates. Assert
          the snapshot's `contract_version == "0.1.0a7"`, and that the trait is visible via
          `cyl_scan_traits_source`;
        - apply a9, which must not raise. The snapshot and the read-path visibility are unchanged;
        - under a `SAVEPOINT`, the same-key a7 re-delivery raises `contract_version mismatch`, not
          `was_noop`;
        - `ROLLBACK TO SAVEPOINT`, and the snapshot is unchanged.

        Never commit, so the cross-file invariant noted in `test_a7_cutover_guard_raises_on_a3_row`
        still holds.
- [ ] 2.4 GREEN: add `supabase/migrations/20260925120000_cyl_writeback_contract_a9.sql`:
      - the `20260917140000` region, verbatim except the literal, inside `BEGIN;`/`COMMIT;`;
      - no `DROP FUNCTION` and no `DO` guard;
      - a header that explains why and links design.md, with no closing keywords.
- [ ] 2.5 GREEN: add `supabase/rollbacks/20260925120000_cyl_writeback_contract_a9_rollback.sql`,
      holding the `20260917140000` region verbatim (a7). Its header states:
      - it is the staging hot-apply only; a durable rollback is a new forward migration plus the
        `PINNED_VERSION` flip plus the traits re-pin to `sha-689cffb@sha256:ab5a1f43…`, all together
        (design § Rollback);
      - applying it by hand leaves `20260925120000` recorded as applied.
- [ ] 2.6 GREEN: `test_rpc_pinned_version_matches_vendored_contract_pin` in
      `tests/integration/test_contract_migration_match.py`. It regex-extracts
      `pinned_version constant text := '([^']*)'` from
      `pg_get_functiondef('public.insert_cyl_result_envelope(jsonb,text)'::regprocedure)` and asserts
      it equals `pin.json`'s `version` with the leading `v` removed. The failure message names both
      values.
- [ ] 2.7 Apply locally and confirm GREEN for §2.1–§2.3 and §2.6, with `test_execute_grants_*`
      unchanged. Then confirm, before C2's explicit versions are present, that exactly the three §3.1
      tests fail. That is the check that the list in §3.1 is exhaustive.

## 3. Dependent tests and references

- [ ] 3.1 (C2, lands before C4) Add an explicit `contract_version="0.1.0a7"` to
      `test_scan_status_rollback_restores_1arg_signature`,
      `test_redelivery_status_fallback_migration_is_idempotent` and
      `test_redelivery_status_fallback_rollback_restores_prior_body`. Each re-applies an a7-pinned
      body and then calls with the default version.
- [ ] 3.2 (C4) `tests/integration/test_cyl_read_path.py`: set `PINNED_VERSION = "0.1.0a9"`.
- [ ] 3.3 (C3) `tests/integration/test_contract_migration_match.py`: move the `v0.1.0a7` docstring
      references to `v0.1.0a9`.
- [ ] 3.4 (C5) `bloomcli/tests/test_cyl_ingest.py`:
      - move the mocked `pinned 0.1.0a7` string and its assertion to a9;
      - update the stale migration path in the comment at around line 34. `_current_migration_sql`
        will now pick the a9 file, since it globs `*cyl_writeback*`;
      - run the CI command from `bloomcli/`: `uv run --extra test pytest tests/ -m "not integration"`.
      No `bloomctl` source, pin or lock change.
- [ ] 3.5 `git grep -n "0\.1\.0a7"` outside `openspec/changes/archive/`. List every remaining hit as
      intentional in the PR body:
      - the a7, `20260912110000` and `20260917140000` migrations and their rollbacks;
      - the new a9 rollback;
      - the a7-rejection and a7-restore tests;
      - the README's historical a7 note;
      - `bloomcli/{CHANGELOG.md,pyproject.toml,uv.lock}`;
      - `tests/unit/test_release_bloomcli_workflow_shape.py:174`;
      - `openspec/changes/fix-cyl-pipeline-run-scan-status/{design,tasks}.md`.

      Leave `bloomcli/tests/fixtures/scan0K9E8BI.result.json` (`0.1.0a3`, used only by the env-gated
      live test) untouched, and name it as known-stale.

## 4. Validate

- [ ] 4.1 `openspec validate repin-cyl-contract-a9 --strict` and `openspec validate --specs --strict`.
- [ ] 4.2 `scripts/lint_migrations.sh origin/staging`, and
      `python3 scripts/lint_migration_isolation.py origin/staging`. The isolation lint's warning is
      expected and justified in the PR body.
- [ ] 4.3 `uv run pre-commit run --files <changed files>`, and `uvx ruff@0.9.9 check bloomcli/`.
- [ ] 4.4 Run the cyl integration suites and `tests/unit/` locally against a live Postgres. Then
      observe CI's *Docker Compose Health Check* green on the final head before merge.
- [ ] 4.5 The PR body says "Part of #895", includes `No schema changes.` under Schema changes and
      the §3.5 list, and notes the #902 timestamp ordering and the stuck staging deploy.

## 5. After merge: the gate is APPLIED, not merged (not part of this PR's diff)

- [ ] 5.1 The user clears the pending staging environment approval, the deploy runs, and the live
      literal is verified by a read-only query: `pg_proc` returns exactly one row,
      `insert_cyl_result_envelope(jsonb,text)`, whose extracted `pinned_version` is `0.1.0a9`.
      Alternatively, a `contract_version: "probe"` call through the cluster's credential returns
      `pinned 0.1.0a9`. The colour of the "Apply database migrations" step alone is not evidence,
      because a zero-pending run shows as skipped.
- [ ] 5.2 Bump the traits template in `talmolab/sleap-roots-pipeline` (prepared before this merges,
      merged only after 5.1):
      - `image:` becomes `sha-e373b0f@sha256:<re-verified index digest>`, changed together with
        `SRT_TRAITS_CONTAINER_DIGEST`;
      - `argo template update`, then `scripts/check_cluster_drift.sh`.
- [ ] 5.3 Acceptance (bloom#895): new `cyl_trait_sources` rows carry `contract_version: 0.1.0a9`,
      judged by `write-back succeeded (source_id=…)` lines and the rows themselves. Re-trigger scans
      that were rejected during the window. Then close #895 by hand.
- [ ] 5.4 Archive `repin-cyl-contract-a9` in a follow-up PR once 5.1–5.3 hold.
