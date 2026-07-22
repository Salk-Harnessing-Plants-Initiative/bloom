## 1. Investigation (confirms scope before touching any file)

- [x] 1.1 Confirm whether `viz_tools.py`'s "legacy outlier-comparison plot" (the issue's cited
      `ANALYSIS_OUTPUT` consumer) still exists — refuted: deleted by commit `5ce48af`
      (2026-07-20 17:12:59 -0700) and the subsequent sections/ refactor (`779c1d6`,
      2026-07-20 17:15:52 -0700), both landing ~1 hour after #477 was filed
      (2026-07-20T23:12:38Z). Dates verified against full commit hashes, not abbreviated ones.
- [x] 1.2 Confirm the `phenotyping_segmentation` section's `compute_min`/`compute_median`/
      `compute_mode` tools read `BLOOM_TRAITS_DIR` and write under `BLOOM_OUTPUT_DIR`, and are
      registered + mounted on the live `/mcp` surface (not blocked by the stale "empty scaffold"
      docstring in `server.py`).
- [x] 1.3 Confirm `SupabaseReader.load_experiment`'s raw-input fallback tier and
      `SupabaseReader.raw_source_path` (used by `tools._ports.start_run` for provenance hashing)
      both read `BLOOM_TRAITS_DIR`. Corrected framing: `qc_clean.py` forces `version="raw"` on
      **every** call, not only an experiment's first — this is a permanent dependency, not a
      cold-start-only one.
- [x] 1.4 Confirm `docker-compose.prod.yml` never sets `BLOOM_STORAGE_BACKEND=local`, so
      `storage_backend.py`'s `BLOOM_OUTPUT_DIR` bridge-fallback for `BLOOM_STORAGE_LOCAL_ROOT` is
      dormant in staging/prod today — true but not, by itself, grounds to unmount `ANALYSIS_OUTPUT`
      given 1.2's live consumer.
- [x] 1.5 Second `BLOOM_TRAITS_DIR` consumer the issue itself pointed at but the first investigation
      pass under-chased: confirm `qc_inspect_tool.py`'s direct local-disk bypass. Confirmed still
      present at its post-refactor location, `sections/sleap_roots/analysis/qc_inspect.py:503`
      (`local_src = TRAITS_DIR / params.experiment`).
- [x] 1.6 Cross-check open issues in the same territory: **#476** (retire the
      `supabase_reader.py`/`qc_inspect.py` `BLOOM_TRAITS_DIR` bypasses — targets the exact two
      consumers 1.3/1.5 rely on; filed one second before #477), **#478** (move
      `BLOOM_STORAGE_BACKEND` out of tracked `docker-compose.dev.yml` — same file this change
      edits), **#479** (collapse `BLOOM_TRAITS_DIR`/`PLOTS_DIR`/`OUTPUT_DIR` into one
      `BLOOM_LOCAL_ROOT` var). None block this change; #476 is the one to watch for a
      necessity re-evaluation.
- [x] 1.7 ~~Confirm whether `.github/workflows/deploy.yml` (or any documented runbook) already
      has a mechanism for migrating a bind-mount host directory on rename.~~ **Superseded**: at
      original investigation time (pre-#473-merge), neither existed and
      `scripts/ensure_bloommcp_data_dirs.sh` "no longer exist[ed] in the repo." Both facts have
      since changed — PR #473 merged (2026-07-22), adding `scripts/ensure_bloommcp_data_dirs.sh`
      as a `deploy.yml`-adjacent preflight for issue #474 (`add-bloommcp-prod-staging-
data-dir-preflight`, implemented in the same PR as this change, #495). Re-confirmed: that
      preflight creates directories if missing but has no rename/migration logic of its own — it
      would silently auto-create an empty `TRAITS_DIR` on a host with a populated legacy
      `SLEAP_OUT_CSV`, which is exactly the orphaning failure this task originally flagged as
      unaddressed. This proposal's migration step (§2) still needs to be written fresh, but now
      also needs to run **before** that preflight in both deploy jobs — see proposal.md's "Why"
      for the full ordering rationale. `scripts/ensure_bloommcp_data_dirs.sh` itself (and its
      test) needed updating for the renamed directory name too, since it now exists and
      hardcoded `SLEAP_OUT_CSV`.
- [x] 1.8 Conclusion: neither `TRAITS_DIR` nor `ANALYSIS_OUTPUT` is dead weight; proceed with
      rename-only (issue's option 3), not unmounting (issue's option 2) — but the rename requires
      a deploy-time migration step, correctly ordered against #474's preflight, to be safe against
      real staging/prod data (see §2).

## 2. Deploy-safety prerequisite (BLOCKING — must land before/with the compose rename)

- [x] 2.1 Add an idempotent migration step to `.github/workflows/deploy.yml`'s
      `deploy-production` job, run on the remote host before `docker compose ... up` **and**
      before #474's data-dir preflight step: if `bloommcp/data/SLEAP_OUT_CSV` exists and
      `bloommcp/data/TRAITS_DIR` does not, rename it in place (`mv`); otherwise no-op. Must not
      fail the deploy if either directory is absent (fresh host) or already migrated (repeat
      deploy).
- [x] 2.2 Add the identical step to the `deploy-staging` job.
- [ ] 2.3 Manually verify against a copy of the actual staging host layout (or a close local
      simulation) that the step is genuinely idempotent: run it twice in a row and confirm the
      second run is a no-op with no error. **NOT done — requires SSH access to a real or
      close-simulation host; same access constraint as #474's own pre-merge host-state
      verification (see that change's tasks.md section 2). Flagged in the PR description as a
      required check before merge, alongside #474's.**
- [x] 2.4 Update `PROD_SETUP.md` if it documents the bind-mount directories by name, so the
      runbook doesn't describe the old `SLEAP_OUT_CSV` path as authoritative. (It didn't document
      the directory by name before this change; now does, via #474's own `PROD_SETUP.md` edit,
      and already reflects `TRAITS_DIR`.)

## 3. Rename `SLEAP_OUT_CSV` → `TRAITS_DIR`

- [x] 3.1 `docker-compose.dev.yml`: `BLOOM_TRAITS_DIR` env value, the commented-out
      `BLOOM_EXPERIMENT_LOCAL_ROOT` example, and the bind-mount
- [x] 3.2 `docker-compose.prod.yml`: `BLOOM_TRAITS_DIR` env value and the bind-mount
- [x] 3.3 `bloommcp/Dockerfile`: the `mkdir -p` line that pre-creates `/app/data/SLEAP_OUT_CSV`
- [x] 3.4 `.gitignore`: update the `SLEAP_OUT_CSV` example named in the `bloommcp/data/` comment
- [x] 3.5 `bloommcp/docs/storage-backends.md`: update both `SLEAP_OUT_CSV` references
- [x] 3.6 `_WIKI/BLOOMMCP/storage-workflow.md`: update the example `source_path`
- [x] 3.7 `_WIKI/BLOOMMCP/README.md`: update the directory-tree entry
- [x] 3.8 `bloommcp/src/bloom_mcp/storage/analysis_dir.py`: update the comment naming
      `SLEAP_OUT_CSV`
- [x] 3.9 **New, not in the original plan** (post-dates PR #473 merging): `scripts/
ensure_bloommcp_data_dirs.sh`'s directory-name loop and its two remedy-message occurrences;
      `tests/unit/test_bloommcp_data_dirs.py`'s `DIR_NAMES` tuple and two stderr assertions;
      `bloommcp/scripts/live_plot_tool_smoke.py`'s `TRAITS_DIR` path constant (previously pointed
      at `.../SLEAP_OUT_CSV` despite the Python variable already being named `TRAITS_DIR` — the
      exact naming mismatch this issue is about) and its docstring/check-message mentions;
      `bloommcp/docs/local-validation.md`'s troubleshooting-table reference; `DEV_SETUP.md`'s and
      `openspec/project.md`'s "bloommcp data directories" notes (both from #473, both live docs
      rather than historical narrative, so updated rather than left stale); the not-yet-archived
      `openspec/changes/fix-bloommcp-dev-data-dir-permissions/specs/development-environment/
spec.md` delta (prescriptive SHALL/scenario text that will become living spec truth on archive,
      unlike that same change's narrative `proposal.md`/`design.md`/`tasks.md`, left untouched as
      an accurate historical record of what was true when #472/#473 were authored).
- [x] 3.10 Grep the full repo for any remaining `SLEAP_OUT_CSV` reference outside
      `langchain/SLEAP_OUT_CSV/` (out of scope, tracked by #475), `volumes/minio-dev/**`
      (gitignored runtime state), and other proposals' own historical/unrelated text (the merged
      #472/#473 narrative docs; #475's own change directory; archived changes) — confirmed clean.
      §4 adds the permanent version of this check.

## 4. Tests

- [x] 4.1 New file `tests/unit/test_bloommcp_data_mount_rename.py`: - `test_traits_dir_name_matches_env_var_in_both_compose_files` — parses both compose files;
      asserts `BLOOM_TRAITS_DIR` and its bind-mount both resolve to `/app/data/TRAITS_DIR`. - `test_prod_compose_keeps_all_three_bloommcp_data_mounts` — asserts `docker-compose.
prod.yml`'s `bloommcp` volumes still mount `TRAITS_DIR`, `ANALYSIS_OUTPUT`, `PLOTS_DIR`. - `test_no_stale_sleap_out_csv_references` — parametrized over the 8 renamed files; asserts
      the literal string `SLEAP_OUT_CSV` is absent from each.
- [x] 4.2 **New, not in the original plan**: extended `tests/unit/
test_deploy_data_dir_preflight_ordering.py` (added alongside #474, shared file) with the
      migration-precedes-preflight ordering assertion — the invariant that makes composing this
      change with #474 actually safe (see proposal.md's "Why").
- [x] 4.3 Confirm the existing bloommcp suite passes with the rename applied:
      `uv run --frozen --extra test pytest bloommcp/tests/test_storage_backend.py
  bloommcp/tests/test_local_mode.py bloommcp/tests/test_package_baseline.py -v` — none
      hardcode the literal directory name, so a byte-identical pass/fail count before and after
      is the expected result. Also ran the full `bloommcp/tests/` suite (`-m "not integration"`):
      530 passed, 1 pre-existing failure unrelated to this change
      (`test_devendor_invariants.py::test_retired_workflow_tools_absent_and_package_gone`,
      confirmed to fail identically on `staging` tip before any of this change's edits — not
      introduced by this work, not fixed by it either; out of scope).
- [x] 4.4 `openspec validate rename-bloommcp-sleap-out-csv-dir --strict` passes.

## 5. Verify

- [ ] 5.1 `make dev-up` (or equivalent) boots cleanly with the renamed mount; `bloommcp` resolves
      `BLOOM_TRAITS_DIR=/app/data/TRAITS_DIR` and finds its fixture CSVs there. **NOT done in
      this session** — no local Docker stack available to drive end-to-end; the shape/unit tests
      above cover the config surface, but a live `make dev-up` + `make bloommcp-plot-smoke` run is
      still worth doing before merge if a dev environment is available.
- [x] 5.2 Run the new tests in §4 and confirm they exercise the change, not just pass vacuously:
      confirmed `test_no_stale_sleap_out_csv_references` and
      `test_traits_dir_name_matches_env_var_in_both_compose_files` would fail against the
      pre-rename tree (they assert the literal new values/absence of the old string, which the
      pre-rename tree doesn't satisfy) and pass post-rename.

## 6. PR

- [x] 6.1 PR description notes the developer-facing migration: anyone with a pre-existing local
      `bloommcp/data/SLEAP_OUT_CSV/` (gitignored) should rename it to `bloommcp/data/TRAITS_DIR/`.
- [x] 6.2 PR description flags the now-stale line-anchor references in the open (unarchived)
      `openspec/changes/add-ghcr-image-publishing/{proposal.md,tasks.md}` as a documentation
      coordination FYI — not a merge dependency.
- [x] 6.3 PR description cross-links #476, #478, #479 per proposal.md's "Related" section.
- [x] 6.4 Blended into PR #495 (branch `egao28/bloommcp-prod-staging-data-dir-preflight-474`,
      which also implements #474) rather than a separate PR — see proposal.md's "Why". Closes
      #477.
