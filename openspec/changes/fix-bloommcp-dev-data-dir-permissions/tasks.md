## 1. Fix the dev path

- [x] 1.1 Add `scripts/ensure_bloommcp_data_dirs.sh` + a `.PHONY` `ensure-bloommcp-data-dirs`
      Makefile prerequisite of `dev-up` (not an inline recipe line, not inside `scripts/
doctor.sh`). `mkdir -p` + `chmod 777` per directory; on failure (root-owned leftover, or an
      unwritable parent), fails loudly with a `sudo chown`/`sudo rm -rf` remedy and aborts before
      `docker compose up`.
- [x] 1.2 Verified on a genuinely fresh state (removed `bloommcp/data` entirely, matching a
      fresh clone): `make ensure-bloommcp-data-dirs` creates all three directories `0777`,
      owned by the invoking user; `docker compose run` (bypassing `make dev-up`, to isolate the
      fix) then boots the `bloommcp` container clean, and `plot_trait_histograms` called through
      the real MCP transport succeeds with the PNG landing on the real bind-mounted `PLOTS_DIR`
      (`bloommcp/scripts/live_plot_tool_smoke.py`, 3/3 checks passed).
- [x] 1.3 Verified `ANALYSIS_OUTPUT` writability too, not just `PLOTS_DIR`: reproduced the
      original bug in fully-local storage-backend mode, where an unwritable `ANALYSIS_OUTPUT`
      crashes the container at **boot** (`RuntimeError: BLOOM_STORAGE_BACKEND=local root
/app/data/ANALYSIS_OUTPUT is not writable`) — confirmed the fix resolves this too (clean
      boot after `ensure-bloommcp-data-dirs`). `SLEAP_OUT_CSV` writability is covered by the
      hermetic unit suite (task 1.4) and by the live plot-tool smoke's fixture-seed step.
- [x] 1.4 Automated tests (`tests/unit/test_bloommcp_data_dirs.py`,
      `tests/unit/test_makefile_bloommcp_data_dirs.py`, mirroring `test_doctor.py`/
      `test_makefile_doctor.py`'s patterns): fresh creation, idempotency, re-chmod of an
      existing-but-wrong-mode directory, an unwritable-parent abort with an actionable message,
      the Makefile wiring (target exists, `dev-up` depends on it, never folded into `doctor.sh`),
      and a behavioral `make dev-up` abort-before-frontend-step test. The "pre-existing directory
      owned by a _different_ user" (root) path isn't hermetically unit-tested — simulating a
      different owner needs root/setuid privilege the test suite shouldn't assume — it's covered
      by the live reproduction in 1.2/1.3 instead.

## 2. Close the CI coverage gap

- [x] 2.1 Resolved (design.md): a new standalone script (`bloommcp/scripts/
live_plot_tool_smoke.py`) + `make bloommcp-plot-smoke`, calling the real MCP transport into
      the already-running container — not `live_persistence_smoke.py` (its in-process,
      host-tempdir-overridden design would never touch the real bind mount).
- [x] 2.2 Built and verified live against the real dev-stack container (see 1.2) — confirmed
      it fails against the pre-fix (root-owned) state and passes after.
- [x] 2.3 Wired into `.github/workflows/pr-checks.yml`'s `dev-stack-smoke` job, after `make
bloommcp-smoke`. Pinned by `tests/unit/test_ci_dev_stack_smoke.py::
test_dev_stack_smoke_runs_the_plot_tool_check_after_bloommcp_smoke`.

## 3. Docs

- [x] 3.1 Added a "bloommcp Data Directories" section to `DEV_SETUP.md` (mirroring the
      existing "MinIO Storage Setup" section's style) and a peer "bloommcp data directories"
      bullet to `openspec/project.md`'s Technical Constraints (next to MinIO's).
- [x] 3.2 Added one-line cross-references (no restatement) from `bloommcp/docs/
local-validation.md`, `bloommcp/docs/storage-backends.md`, and `_WIKI/BLOOMMCP/README.md`.
- [x] 3.3 Added a `local-validation.md` Troubleshooting row for a pre-existing root-owned
      `bloommcp/data/*` from before this fix.

## 4. Flag the out-of-scope risk

- [x] 4.1 Decided: documented in proposal.md's "Out of scope" note (now also naming
      `compose-health-check`, which boots `docker-compose.prod.yml` with the identical
      bind-mount shape and zero preflight) rather than filing a separate issue — no way to
      confirm from this repo alone whether it's a live problem on the actual staging/prod hosts.

## 5. Validate

- [x] 5.1 `openspec validate fix-bloommcp-dev-data-dir-permissions --strict` — clean.
- [x] 5.2 Full `tests/unit/` suite (334 passed, 1 skipped — pre-existing, unrelated to this
      change) plus `ruff check` + `black --check` on every new/changed file — clean.
