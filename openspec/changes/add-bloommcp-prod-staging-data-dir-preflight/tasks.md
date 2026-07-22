## 1. Dependency

- [x] 1.1 Confirm PR #473 (`egao28/bloommcp-plotsdir-permission-fix-472`) has merged to
      `staging`, or rebase this branch onto it, so `scripts/ensure_bloommcp_data_dirs.sh`
      exists to reuse. Do not proceed past task 3 until this is confirmed true (`test -f
scripts/ensure_bloommcp_data_dirs.sh` on the working branch).
      **Done** — PR #473 merged 2026-07-22 (`bd3bf5f`); branch fast-forwarded onto
      `origin/staging`.

## 2. Pre-merge host-state verification (blocking gate for section 3 only)

- [x] 2.1 Before merging the `deploy.yml` changes in section 3 (NOT required for the
      `pr-checks.yml` change in section 4, which has no persistent-host risk): get someone
      with SSH access to the live Salk staging and production hosts to check current ownership
      of `bloommcp/data/{TRAITS_DIR,PLOTS_DIR,ANALYSIS_OUTPUT}`.

      **Done — confirmed by Elizabeth (direct `docker exec` into both live containers,
      2026-07-22), and the risk was real, not hypothetical.**

      Production — currently broken: `bloom` runs as `uid=100 gid=101`, not in the `root`
      group. All three directories are `root:root` mode `755` (owner-only write), dated May 6
      (initial deploy) and untouched since. `PLOTS_DIR` — the one directory confirmed
      load-bearing on every storage backend — is empty; any plotting-tool call against prod
      today fails with a permission error, and has since the May 6 deploy.

      Staging — partially, manually fixed: `PLOTS_DIR` and `ANALYSIS_OUTPUT` were `chown`'d to
      `bloom:bloom` by hand around 2026-05-27 as a workaround (both have real content dated
      then) and have worked since. `SLEAP_OUT_CSV`/`TRAITS_DIR` is still `root:root`, untouched
      — consistent with it not having been exercised on this host.

      This confirms `scripts/ensure_bloommcp_data_dirs.sh`'s no-privilege-escalation failure
      mode (design.md's "Decisions") is a live risk on production specifically: its `chmod`
      will fail there exactly as designed, aborting `deploy-production` on the first run after
      merge — expected and correct, not a bug in the preflight.

- [ ] 2.2 Coordinate a one-time manual `chown bloom:bloom` on **production**'s three
      directories (or at minimum `PLOTS_DIR`, per Elizabeth's suggested immediate stopgap)
      BEFORE or immediately after merging section 3, so the new preflight's first run
      succeeds there rather than failing the deploy. **Staging does not block on this** for
      `PLOTS_DIR`/`ANALYSIS_OUTPUT` (already `bloom:bloom`); its `TRAITS_DIR`/`SLEAP_OUT_CSV`
      will still need the same one-time `chown` once the rename in
      `rename-bloommcp-sleap-out-csv-dir` reaches it, unless task 2.3 below concludes the
      mount should be dropped instead. **NOT done by this change — needs someone with server
      `sudo` access; flagged in the PR description as the specific pre-merge/immediate action
      item, independent of and not blocking this PR's merge per Elizabeth's own suggestion
      ("that's a one-line stopgap independent of any proposal and doesn't need to wait on
      it").**
- [x] 2.3 Re-examine whether `TRAITS_DIR`/`SLEAP_OUT_CSV` and `ANALYSIS_OUTPUT` should be
      dropped from `docker-compose.prod.yml` entirely (shrinking the permission surface to
      `PLOTS_DIR` only), given Elizabeth's live observation that neither has been written to
      since initial deploy on either host.
      **Resolved — keep all three, per full code re-verification (see
      `rename-bloommcp-sleap-out-csv-dir`'s tasks.md task 1.9 for the complete finding):**
      `qc_clean`'s forced `version="raw"` read has **no Storage fallback to fall back from** —
      when `version="raw"`, `experiment_utils.load_experiment_data` skips the Storage-checking
      tiers entirely and reads local disk as the **sole** source; there is no ingestion pipeline
      that uploads raw CSVs to Supabase Storage first (`supabase_client.read_input_csv()` exists
      but is never called; the repo's own `live_persistence_smoke.py` comments confirm raw
      inputs "have not yet migrated to the `bloommcp_input/` bucket"). Elizabeth's mtime
      observation is accurate but not evidence of non-use: every cited consumer only _reads_
      `TRAITS_DIR` (open/stat/hash), and a directory's mtime does not change on a read — only on
      an entry being created, deleted, or renamed. A better live-usage check for future
      reference: `bloommcp_output/qc_<experiment>/manifest.json` **version timestamps in
      Supabase Storage itself** (not the container's local disk) — a `qc_clean` run past the
      initial deploy date is direct proof the raw read fired.

## 3. deploy.yml

- [x] 3.1 Add a preflight step to `deploy-staging`, before "Deploy staging stack", invoking
      `scripts/ensure_bloommcp_data_dirs.sh` against `${{ secrets.STAGING_DEPLOY_PATH }}`.
      Land this before 3.2 — `deploy-staging` fires on every push to `staging` (i.e., every
      merged PR), so it battle-tests the pattern before `deploy-production` (which only fires
      on push to `main` or manual dispatch) ever executes it.
- [x] 3.2 Add the equivalent preflight step to `deploy-production`, before "Deploy production
      stack", against `${{ secrets.PROD_DEPLOY_PATH }}`.
- [x] 3.3 Confirm the step's failure surfaces clearly in the Action log (the script's own
      stderr remedy plus non-zero exit under `set -euo pipefail` should be sufficient — verify
      rather than adding a redundant diagnostics step).
      **Confirmed** — no additional diagnostics step added; the script's own remedy plus
      `set -euo pipefail` is sufficient, consistent with how every other preflight step in
      this job already behaves.
- [x] 3.4 Note in the PR description that `deploy.yml` has no `pull_request` trigger, so this
      change is not exercised by PR CI — after merging to `staging`, manually trigger
      `workflow_dispatch` (`environment=staging`) and confirm the new step's log output before
      trusting the pattern against `main`/production.

## 4. pr-checks.yml — compose-health-check

- [x] 4.1 Add a step calling `scripts/ensure_bloommcp_data_dirs.sh`, placed immediately after
      "Create MinIO data directory" and before the job's main
      `docker compose $COMPOSE_FILES -f docker-compose.ci-cache.yml --env-file .env.ci up -d
--build` step (the only steps in between are "Start MinIO and create buckets" and "Start
      database", neither of which this preflight needs to precede, but placing it right after
      the analogous MinIO step keeps the two preflights adjacent and easy to compare).

## 5. Project conventions

- [x] 5.1 Add a note to `openspec/project.md`'s "Technical Constraints" section documenting
      `bloommcp/data/*` as a narrow, explicitly-approved exception to the existing "`chmod 777`
      should not be propagated to staging/prod" guidance, with the rationale from `design.md`'s
      "Decisions" (the `bloom` user's UID is arbitrary and assigned at Docker image-build time,
      so host UID/GID matching isn't achievable without a separate Dockerfile change) and a
      pointer to task 5.2's follow-up issue as the closure path.
      **Done** — extended the existing "bloommcp data directories" bullet (added by #473) to
      also cover staging/production.
- [ ] 5.2 File a follow-up GitHub issue: pin the `bloom` user's UID/GID in
      `bloommcp/Dockerfile` so a future change can switch prod/staging to proper UID/GID
      matching + `chmod 770`, retiring the exception added in task 5.1.
      **NOT filed** — opening a new GitHub issue is a visible action; deferred to the PR
      author's discretion rather than done unprompted.

## 6. Tests

- [x] 6.1 Add `tests/unit/test_deploy_data_dir_preflight_ordering.py`, parametrized over all
      three call sites (`deploy.yml:deploy-production`, `deploy.yml:deploy-staging`,
      `pr-checks.yml:compose-health-check`), asserting: (a) a step invoking
      `scripts/ensure_bloommcp_data_dirs.sh` exists in that job; (b) its index precedes the
      job's `docker compose ... up` step; (c) in `deploy.yml`, that step's own `run:` block
      contains `cd ${{ secrets.PROD_DEPLOY_PATH }}` or `STAGING_DEPLOY_PATH` as appropriate.
      **Done** — 338/338 `tests/unit/` pass, including the 2 new tests in this file.
- [x] 6.2 ~~Add a regression test asserting `scripts/ensure_bloommcp_data_dirs.sh` itself is
      unmodified~~ **Reconsidered, not added**: `tests/unit/test_bloommcp_data_dirs.py`
      (from #473) already regression-guards the script's actual behavior (creation,
      idempotency, remedy-on-failure) — a permanent content-hash pin on top of that would
      only catch cosmetic edits, not behavioral ones, and would become stale technical debt
      the next time the script is legitimately improved. This PR's diff not touching the
      script is directly visible in the PR itself; no separate test earns its keep here.
- [ ] 6.3 File a follow-up GitHub issue to add a live smoke test to `compose-health-check`
      (mirroring #473's `live_plot_tool_smoke.py`) that actually boots `bloommcp` against a
      fresh directory and confirms a real write to `PLOTS_DIR` — without it, CI only verifies
      that the preflight step exists and is ordered correctly, never that the underlying bug
      is actually fixed. **Elevated from "maybe" to "should really happen"** — Elizabeth's
      review confirmed this exact gap is why production's `PLOTS_DIR` broke on May 6 and
      stayed broken, undetected, for 2.5+ months (see design.md's "Open Questions" —
      `bloommcp`'s healthcheck is a liveness-only HTTP ping; `compose-health-check` never
      calls a real tool). Still out of scope for THIS change (needs its own design — `bloommcp`
      is `expose:`-only in prod compose, unlike dev's host-published port, so the existing dev
      script's connection approach doesn't carry over as-is). **NOT filed by this change** —
      filing a new GitHub issue is a visible action; flagged to the PR author/reviewer as a
      strong recommendation rather than done unprompted.

## 7. Docs

- [x] 7.1 Update `PROD_SETUP.md`: insert the new preflight step into the numbered "Deploying"
      list (between the env-validation step and `docker compose up`), and add a short note
      mirroring `DEV_SETUP.md`'s "bloommcp Data Directories" section from #473 (auto-provisioned,
      no manual step needed).
      **Done.**
- [x] 7.2 After PR #473 merges: if `openspec/changes/fix-bloommcp-dev-data-dir-permissions/`
      still exists under `changes/` (not yet archived), append to its "Out of scope" note
      pointing at this change as the resolution; if it has already moved to
      `changes/archive/YYYY-MM-DD-.../`, edit the archived copy instead. Either way, comment on
      issue #474 pointing at this change as the resolution — that comment doesn't depend on
      the archive timing and is the reliable fallback. Blocked on task 1.1.
      **Done** — proposal still unarchived; "Out of scope" note updated directly. Issue #474
      is linked via the PR body (`Closes #474`) rather than a separate manual comment.

## 8. Validation

- [x] 8.1 `openspec validate add-bloommcp-prod-staging-data-dir-preflight --strict`
      **Done** — passes.
