## Why

Twice now — bloom#410 (2026-07-07, wedged deploy runner) and bloom#685 (2026-09-02, the a3→a7
cutover guard correctly aborting on real staging data) — a PR merged into `staging`, this repo's
own `auto-close-issues-on-staging.yml` workflow closed the issue(s) its title/body named via
`Closes #N`, and the deploy that was supposed to make the fix real then failed. Both times the
issue sat closed — silently claiming the fix was live — until someone happened to notice the
failed deploy run and reopen it by hand. bloom#780 tracks this as one incident with two distinct
angles, both addressed here in one change because they're the reactive and preventive halves of
the same postmortem, not two unrelated fixes riding the same PR:

- **Reactive**: nothing currently reopens or flags an auto-closed issue when the deploy behind it
  fails. This is the automation half.
- **Preventive**: the specific failure mode that bit bloom#685 — a migration's cutover guard
  tripping on real historical data — can only ever be discovered at deploy time today, because
  CI's `compose-health-check` job always runs against a fresh, empty Postgres. A documented
  pre-merge check would have caught it before merge. This is the documentation half.

Both stay narrowly scoped: bloom#185 (CI and deploy running migrations via different code paths)
and bloom#699 (no pre-merge check that a compose `${VAR}` has a matching GitHub secret) are
different, adjacent problems and are explicitly out of scope here.

## What Changes

- **Automation**: when the "Apply database migrations" step fails (not skipped — see design.md)
  in `deploy.yml`'s `deploy-staging` or `deploy-production` job, a new step resolves the PR that
  produced the triggering commit (`GET /repos/{owner}/{repo}/commits/{sha}/pulls`), re-runs
  `auto-close-issues-on-staging.yml`'s own closing-keyword regex against that PR's live
  title/body, and — for each referenced issue that is currently closed with
  `state_reason: completed` and was closed by `github-actions[bot]` (checked via the issue's own
  `closed_by` field) — reopens it and posts a comment explaining what happened and linking the
  failed run and step.
- **Documentation**: a new pre-merge convention, as a new step in `contracts/README.md`'s existing
  "Re-pin procedure" section (the established home for contract-version-migration guidance — see
  `contract-pinning`'s existing spec requirement), for any migration carrying a data-dependent
  cutover guard: check the real target-environment(s) for rows that would trip the guard before
  merging, and fold any needed reconciliation into the same PR — a manual, SSH-based check
  (regular PR CI has no route to staging/production secrets), not new CI automation.
- **BREAKING**: none. This changes CI/deploy tooling and documentation only; no application
  behavior, schema, or API changes.

## Impact

- Affected specs: `deploy-migrations` (new requirements: PR-resolution + issue re-extraction,
  reopen/comment guard, explicit job permissions), `contract-pinning` (modified requirement:
  vendored-schema documentation now also covers the cutover-guard pre-merge check).
- Affected code: `.github/workflows/deploy.yml` (new step(s) in `deploy-staging` and
  `deploy-production`, plus an explicit `permissions:` block on both jobs), `contracts/README.md`
  (new "Re-pin procedure" step), `tests/unit/` (new regression-guard test mirroring
  `test_auto_close_workflow_shape.py`'s pattern).
- Not affected: `auto-close-issues-on-staging.yml` itself (unchanged — this change reads its
  closing-keyword convention, doesn't modify it), `.claude/commands/database-migration.md` (not
  touched — the checklist lives in `contracts/README.md`, not the migration slash-command doc),
  bloom#685's own migration/rollback files (already resolved and closed), bloom#185, bloom#699.
