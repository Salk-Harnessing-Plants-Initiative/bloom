## Why

Found while dogfooding PR #438 against the running dev-stack MCP server (issue #472):
plotting tools fail with a permission error writing PNGs to `BLOOM_PLOTS_DIR`
(`/app/data/PLOTS_DIR` in the container). Root cause (see design.md's Context for the
full mechanism, confirmed by live reproduction — not just a hypothesis): none of
`bloommcp/data/{SLEAP_OUT_CSV,PLOTS_DIR,ANALYSIS_OUTPUT}` are committed or pre-created,
so on a fresh clone Docker auto-creates them as root on the first `docker compose up`,
and the non-root `bloommcp` container user can't write into them. In fully-local
storage-backend mode this is worse than a tool-call failure — `validate_storage_backend()`
hard-crashes the container at boot (reproduced live: `RuntimeError: BLOOM_STORAGE_BACKEND=local
root /app/data/ANALYSIS_OUTPUT is not writable`).

## What Changed

- **MODIFY** `development-environment`'s "Fresh-Clone Stack Startup" requirement: a
  successful `make dev-up` on a fresh clone now also means the `bloommcp` container's
  bind-mounted data directories are writable by its runtime user.
- **ADD** `development-environment`'s "bloommcp Data Directory Writability" requirement,
  implemented as `scripts/ensure_bloommcp_data_dirs.sh`, wired as a `.PHONY` **prerequisite**
  of `dev-up` (`dev-up: ensure-bloommcp-data-dirs`) — deliberately not folded into
  `scripts/doctor.sh`, since CI runs `DOCTOR_SKIP=1 make dev-up` and folding it in there
  would make CI silently skip the fix. On each of the three directories: `mkdir -p` if
  missing, then `chmod 777`; if either fails (the directory, or its `bloommcp/data` parent,
  is already root-owned from a `docker compose up` that ran before this fix existed), it
  fails loudly with an actionable `sudo chown`/`sudo rm -rf` remedy and aborts `dev-up`
  before `docker compose up` — it does not silently continue into the bug it exists to
  prevent, and (confirmed: `chmod` requires ownership) cannot self-heal that state without
  the caller's own privilege escalation.
- **ADD** `bloommcp/scripts/live_plot_tool_smoke.py` + `make bloommcp-plot-smoke`: a real
  MCP-transport call (`fastmcp.Client`) into the already-running `bloommcp` container,
  calling `sleap_roots_plot_trait_histograms` (the `sleap_roots`-namespaced tool name —
  see tasks.md 1.2's note) and verifying both the tool's response and that the PNG
  actually landed on the real bind-mounted `PLOTS_DIR`. Deliberately **not** added to
  `live_persistence_smoke.py`, whose in-process, host-tempdir-overridden design would never
  touch the real bind mount and would pass regardless of whether this bug were still
  present. Wired into `pr-checks.yml`'s `dev-stack-smoke` job, after `make bloommcp-smoke`.

## Impact

- **Affected specs:** `development-environment` (MODIFIED "Fresh-Clone Stack Startup",
  ADDED "bloommcp Data Directory Writability").
- **Affected code:** `Makefile` (`ensure-bloommcp-data-dirs` + `bloommcp-plot-smoke`
  targets), `scripts/ensure_bloommcp_data_dirs.sh` (new), `bloommcp/scripts/
live_plot_tool_smoke.py` (new), `.github/workflows/pr-checks.yml` (`dev-stack-smoke` job),
  `tests/unit/test_bloommcp_data_dirs.py` + `test_makefile_bloommcp_data_dirs.py` (new),
  `tests/unit/test_ci_dev_stack_smoke.py` (new pinning test), `DEV_SETUP.md` +
  `openspec/project.md` + `bloommcp/docs/{local-validation.md,storage-backends.md}` +
  `_WIKI/BLOOMMCP/README.md` (docs).
- **Out of scope, flagged not fixed:** `docker-compose.prod.yml` has the **identical**
  bind-mount shape for the same three directories, and `compose-health-check` (the same
  `pr-checks.yml` file) boots it with zero preflight — so staging/production, and that CI
  job, may have the same latent issue. I have no visibility from this repo alone into
  whether the staging/prod hosts already provision these directories correctly outside of
  what's checked in here. This proposal fixes the **dev** path only (`make dev-up`, the
  `dev-stack-smoke` CI job); the prod/staging/`compose-health-check` risk is tracked
  separately as issue #474 (filed same day).
- **Branch/PR:** branches off `origin/staging`; this branch
  (`egao28/bloommcp-plotsdir-permission-fix-472`).
