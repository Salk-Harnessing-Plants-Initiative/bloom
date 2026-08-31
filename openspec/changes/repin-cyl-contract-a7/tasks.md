> **Commit atomicity (read first).** §2 (RPC migration) and §3 (dependent tests) are genuinely
> coupled — §3.1's `PINNED_VERSION` flip seeds via the RPC, so it is red until the §2.2 migration
> applies — and must land together. **§1 (vendored contract re-pin) is NOT coupled to §2/§3**:
> verified independently CI-green (the `contracts:check`/`contracts:test` job is scoped to
> `contracts/**` and separate from the migrations/integration-test job; `test_contract_migration_match.py`
> only asserts structural shape, never compares `pin.json`'s version to the RPC's literal) — it may
> land as its own commit, or even its own smaller PR, since it's a pure no-op restamp with no runtime
> dependency on §2/§3. Whether §1–§3 end up as one PR or two, this repo squash-merges PRs onto
> `staging` (confirmed against PR #399's history), so "single commit" describes the squashed result,
> not a constraint on how many commits the branch itself has along the way. RED-before-GREEN (§2.1,
> §3.1) is a local, developer-machine discipline (run the suite against an un-migrated DB, confirm
> the new/updated tests fail for the expected reason, then apply and confirm green) — CI applies
> migrations from the branch tree and runs the whole `tests/integration/` dir in one job, so no
> intermediate commit is expected to be independently green on that suite.

## 1. Re-pin the vendored contract (`v0.1.0a5` → `v0.1.0a7`)

- [x] 1.1 Fetch the published `v0.1.0a7` schema from `talmolab/sleap-roots-contracts` and diff it
      against the vendored `v0.1.0a5` copy with the version string normalized out — confirm the
      payload is byte-identical (already verified once during proposal research; re-verify here
      against the file about to be committed, not just the earlier fetch)
- [x] 1.2 Replace `contracts/schema/result_envelope.schema.json` with the published `v0.1.0a7` copy
      (LF, trailing newline preserved; no prettier from inside `contracts/`)
- [x] 1.3 Bump `contracts/pin.json` `version`/`id`/`source` to `v0.1.0a7`
- [x] 1.4 Regenerate `contracts/generated/result-envelope.ts` (`npm run contracts:gen`) — expect a
      byte-identical diff (no field/type change). **If the diff is non-empty**: this is a real
      contract-field change, not the verified no-op — stop, do not proceed with this task list as
      written, and re-scope (a real field change needs its own `contract-pinning` spec delta, per
      the archived `repin-cyl-contract-a3` precedent for the a2→a3 additive revision)
- [x] 1.5 Update `contracts/README.md` — "Currently pinned: `v0.1.0a7`" + re-pin notes for `a6` and
      `a7`, following the existing note format/style used for the `a4`/`a5` notes (each names the
      release's actual substantive package-side change, not just "$id-only no-op"). Research what
      `a6` and `a7` actually shipped on the package side (check `talmolab/sleap-roots-contracts`
      release notes/CHANGELOG for each tag) and narrate it, the way the `a4` note names
      `resolve_params` and the `a5` note names `PredictionArtifact`/`PredictionManifest`; if `a6`
      genuinely shipped nothing beyond the `$id` restamp, say so explicitly rather than silently
      writing a thinner note than the established pattern. Also fix the "Consumer hand-offs"
      section: it currently says to validate `contract_version` "against the pinned `version` (or
      an explicit compatibility set)" — strike the compatibility-set parenthetical (rejected twice
      now, in `repin-cyl-contract-a3` and again in this change's own `design.md`) — and its "no
      consumer yet" line is stale (the write-back RPC has been the live consumer since
      `repin-cyl-contract-a3`/change D); update both
- [x] 1.6 Drift guards pass: `npm run contracts:check` (types agree) and `npm run contracts:test`

## 2. Write-back RPC accepts `0.1.0a7`, prefix-tolerant (TDD)

- [x] 2.1 (RED, code written; live-DB run not verified in this environment — no Docker/Postgres
      available, see §2.4 note) In `tests/integration/test_cyl_writeback_rpc.py`: set `PINNED_VERSION = "0.1.0a7"`;
      update the literal-bearing version-boundary tests to their `a7` forms
      (`test_bare_contract_version_accepted`, `test_v_prefixed_contract_version_accepted`,
      `test_version_boundary_forms_rejected[V0.1.0a7,0.1.0a7 ,0.1.0a70,vv0.1.0a7]`); confirm
      `test_non_string_contract_version_rejected` and `test_absent_or_empty_contract_version_rejected`
      need no literal changes (they're version-agnostic) and still pass unmodified under the new
      `PINNED_VERSION`; add `test_a3_contract_version_rejected[0.1.0a3,v0.1.0a3]` (symmetric to the
      existing `test_a2_contract_version_rejected` case, now testing the *previous* pinned version is
      rejected) — note `test_a2_contract_version_rejected` itself is untouched and must still pass
      (the `0.1.0a2`/`v0.1.0a2` scenario in the delta spec is retained, not replaced); run the suite
      against the un-migrated (still-`a3`) DB and confirm every new/updated case fails for the
      expected reason (old literal still active)
- [x] 2.2 Added `supabase/migrations/20260831130000_cyl_writeback_contract_a7.sql`: full-body
      `CREATE OR REPLACE` of `insert_cyl_result_envelope` (byte-identical to
      `20260706170000_cyl_writeback_contract_a3.sql` except the `pinned_version` literal and header
      comments), re-asserting `OWNER TO postgres` and the byte-identical `REVOKE/GRANT` block —
      **note in a comment** that `bloom_workflows`'s `EXECUTE` grant (added later by
      `20260720000000_grant_bloom_workflows_writeback_rpc.sql`) is preserved because `GRANT` is
      additive and this migration's re-asserted block does not `REVOKE` it; do not add an explicit
      `REVOKE ... FROM bloom_workflows` here or in any future re-pin. Prepend a cutover safety guard
      (`DO` block) that `RAISE`s if any `cyl_trait_sources` row carries `contract_version` matching
      `0.1.0a3` — a single `LIKE '%0.1.0a3%'` check, mirroring the a3 migration's own single-literal
      a2-guard exactly (only `a3`-stamped rows can exist under the current RPC history, since the a3
      migration never accepted `a2`)
- [x] 2.3 Added `supabase/rollbacks/20260831130000_cyl_writeback_contract_a7_rollback.sql`: full-body
      `CREATE OR REPLACE` restoring the strict `0.1.0a3` body verbatim, with a note that rolling back
      re-introduces rejection of real `a7` envelopes — **and, before applying this rollback in a real
      environment, check `talmolab/sleap-roots-pipeline#52`'s status first**: if that issue's image
      pin bump has already landed, rolling back reproduces the exact silent-rejection failure this
      change closes
- [ ] 2.4 **NOT VERIFIED IN THIS ENVIRONMENT** — no Docker daemon / Supabase CLI available here
      (confirmed: `docker ps` fails to reach the engine, no `supabase` binary on PATH). Apply the
      a7 migration to a live Postgres and re-run the writeback suite — confirm every test from
      §2.1 now passes (GREEN), plus `test_execute_grants_*` still confirms exactly
      `bloom_writer`/`service_role`/`bloom_admin`/`bloom_workflows`. **Must be run before merge** —
      CI's `compose-health-check` job will run this authoritatively, but do not treat this task as
      complete until a live-Postgres run (CI or local) is actually observed green
- [x] 2.5 Added `test_a7_migration_body_is_idempotent` + `test_a7_rollback_restores_strict_a3`
      (mirroring the existing a3-keyed idempotency/rollback tests), and
      `test_a7_cutover_guard_raises_on_a3_row` (seeds a `cyl_trait_sources` row with
      `metadata->>'contract_version' = '0.1.0a3'` via the a3 RPC, then asserts applying the a7
      migration raises `a7 cutover blocked`) — closing the gap where the a3 migration's own
      a2-guard had no equivalent test. **Not run against a live DB in this environment** — see §2.4

## 3. Update dependent tests and references

- [x] 3.1 `tests/integration/test_cyl_read_path.py`: flipped `PINNED_VERSION` to `"0.1.0a7"`. **Live
      run not verified in this environment** — see §2.4
- [x] 3.2 `tests/integration/test_contract_migration_match.py`: updated the two docstring version
      references to `v0.1.0a7`. **Live run not verified in this environment** — see §2.4
- [x] 3.3 Grepped `tests/` for `0.1.0a3`/`0.1.0a5` — all remaining hits are intentional (the new
      a3-rejection test and the a3-migration's own idempotency/rollback tests in
      `test_cyl_writeback_rpc.py`, which correctly still reference `a3` as *that* migration's own
      accepted/restored-to version; one unrelated hit in `test_docker_build_bloomcli_workflow_shape.py`
      is a sample Docker tag string for a regex test, not a contract-version pin)

## 4. Validate

- [x] 4.1 `openspec validate repin-cyl-contract-a7 --strict` — valid
- [ ] 4.2 **NOT VERIFIED IN THIS ENVIRONMENT** (no Docker/Supabase CLI available — see §2.4). Run
      the cyl integration suites (writeback, read-path, contract-migration-match) against a live
      Postgres — all green. CI re-runs the full suite against the clean compose stack
      (authoritative) — **must be observed green in CI (or a local run) before merge**
- [x] 4.3 Migration lint passes: `scripts/lint_migrations.sh origin/staging` → "Migration lint
      passed (checked 1 new file(s) against origin/staging; latest base timestamp
      20260825220000)."
- [x] 4.4 Opened the bundled PR (proposal + implementation) targeting `staging`:
      https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/766 — links bloom #685 and
      `talmolab/sleap-roots-pipeline#52`; still needs CI green (§2.4/§4.2 unverified locally) and a
      non-author reviewer (branch protection)
