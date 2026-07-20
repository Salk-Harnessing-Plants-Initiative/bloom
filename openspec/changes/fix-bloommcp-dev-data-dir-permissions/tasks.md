## 1. Fix the dev path

- [ ] 1.1 Add a preflight step that `mkdir -p` + makes writable
  `bloommcp/data/{SLEAP_OUT_CSV,PLOTS_DIR,ANALYSIS_OUTPUT}` before `docker compose up` runs
  in `make dev-up` — as a small script (mirroring `scripts/doctor.sh`'s pattern) or a
  Makefile prerequisite target, not inlined into `dev-up` itself.
- [ ] 1.2 Verify on a genuinely fresh clone (no pre-existing `bloommcp/data/`): `make init &&
  make dev-up`, then call `plot_trait_histograms` (or any plot tool) against the running
  server and confirm it now saves successfully instead of hitting a permission error.
- [ ] 1.3 Confirm `SLEAP_OUT_CSV` and `ANALYSIS_OUTPUT` writability too (not just `PLOTS_DIR`)
  — e.g. seed a raw CSV into `SLEAP_OUT_CSV` and run `qc_clean` with
  `BLOOM_STORAGE_BACKEND=local` so `ANALYSIS_OUTPUT` is actually exercised.

## 2. Close the CI coverage gap

- [ ] 2.1 Resolve design.md's open question 1 (where the new check lives).
- [ ] 2.2 Add the check; confirm it fails on the pre-fix code (revert 1.1 locally and confirm
  red) and passes after.
- [ ] 2.3 Wire it into the `dev-stack-smoke` CI job (`.github/workflows/pr-checks.yml`) so it
  runs on every PR touching `bloommcp/` or the compose files.

## 3. Docs

- [ ] 3.1 Update `bloommcp/docs/local-validation.md` and/or `DEV_SETUP.md` to note the three
  data directories are now auto-provisioned by `make dev-up` — no manual `mkdir` needed.

## 4. Flag the out-of-scope risk

- [ ] 4.1 Decide (design.md open question 2) whether to open a separate tracked issue now for
  the prod/staging bind-mount risk, or wait for confirmation it's a live problem there. If
  opened, link it from this change's proposal.md.

## 5. Validate

- [ ] 5.1 `openspec validate fix-bloommcp-dev-data-dir-permissions --strict`.
