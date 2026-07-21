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
      (`bloommcp/scripts/live_plot_tool_smoke.py`, 3/3 checks passed). **Note added after this
      branch was brought up to date with `origin/staging`:** staging had by then picked up the
      unrelated `devendor-bloommcp-analysis` restructure, which mounts every section tool
      (including this one) on the combined `/mcp` surface under a `sleap_roots_` namespace
      prefix. The 3/3-passed verification above was accurate for the code as it existed at the
      time it ran; `live_plot_tool_smoke.py` itself wasn't updated to call the namespaced
      `sleap_roots_plot_trait_histograms` until a later commit on this branch, without which
      `make bloommcp-plot-smoke` fails closed with `ToolError("Unknown tool")` against the
      current combined server — not a permission error, just a stale tool name in the check
      itself.
- [x] 1.3 Verified `ANALYSIS_OUTPUT` writability too, not just `PLOTS_DIR`: reproduced the
      original bug in fully-local storage-backend mode, where an unwritable `ANALYSIS_OUTPUT`
      crashes the container at **boot** (`RuntimeError: BLOOM_STORAGE_BACKEND=local root
/app/data/ANALYSIS_OUTPUT is not writable`) — confirmed the fix resolves this too (clean
      boot after `ensure-bloommcp-data-dirs`). `SLEAP_OUT_CSV` writability is covered by the
      hermetic unit suite (task 1.4) and by the live plot-tool smoke's fixture-seed step.
- [x] 1.4 Automated tests (`tests/unit/test_bloommcp_data_dirs.py`,
      `tests/unit/test_makefile_bloommcp_data_dirs.py`, mirroring `test_doctor.py`/
      `test_makefile_doctor.py`'s patterns): fresh creation, idempotency, re-chmod of an
      existing-but-wrong-mode directory (both a leaf and `$ROOT` itself — `$ROOT` is now
      explicitly `chmod`'d too, not just implied by `mkdir -p`'s parent-creation, since an
      owner-owned-but-narrow-mode `$ROOT` previously went untouched even after every leaf
      under it was corrected), an unwritable-parent-of-`$ROOT` abort with an actionable
      message, a `chmod`-itself-failing abort (stubbed `chmod` on `PATH` — the actual
      real-world failure mode: Docker already created the directory as root, so `mkdir`
      is a no-op and `chmod` is what fails), the Makefile wiring (target exists, `dev-up`
      depends on it, never folded into `doctor.sh`), and a behavioral `make dev-up`
      abort-before-frontend-step test (bounded by a `timeout=` so a regression here can't
      fall through into a real unbounded `docker compose up` from inside pytest). The
      "pre-existing directory owned by a genuinely _different_ user" (root) case itself
      still isn't hermetically unit-tested — simulating a different owner needs
      root/setuid privilege the test suite shouldn't assume — it's covered by the live
      reproduction in 1.2/1.3 instead.

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
- [x] 3.2 Added one-line cross-references (no restatement) to `bloommcp/docs/
local-validation.md` and `bloommcp/docs/storage-backends.md`. Originally marked done
      here too for `_WIKI/BLOOMMCP/README.md`, but that file was left untouched (verified:
      `git diff` against this change's merge-base was empty for that path) — added in a
      follow-up commit on this same branch; the record above was wrong until then.
- [x] 3.3 Added a `local-validation.md` Troubleshooting row for a pre-existing root-owned
      `bloommcp/data/*` from before this fix.

## 4. Flag the out-of-scope risk

- [x] 4.1 Decided: documented in proposal.md's "Out of scope" note (now also naming
      `compose-health-check`, which boots `docker-compose.prod.yml` with the identical
      bind-mount shape and zero preflight) — no way to confirm from this repo alone whether
      it's a live problem on the actual staging/prod hosts. Originally left unfiled by
      design; now tracked as issue #474 (filed same day) — proposal.md and design.md
      updated to cross-reference it instead of reading as if no tracking issue exists.

## 5. Validate

- [x] 5.1 `openspec validate fix-bloommcp-dev-data-dir-permissions --strict` — clean.
- [x] 5.2 Full `tests/unit/` suite plus `ruff check` + `black --check` on every new/changed
      file — clean. Re-verified after the review-response follow-up commit (`$ROOT`-chmod
      fix, new hermetic tests, doc corrections above): 336 passed, 0 skipped in this run;
      the skip count is environment-dependent (e.g. POSIX-`sh` availability), not a fixed
      number to pin here.
