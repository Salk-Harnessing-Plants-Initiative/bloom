## Why

Found while dogfooding PR #438 against the running dev-stack MCP server (issue #472): all 5
`sleap_roots` plotting tools fail with a permission error writing their output PNG to
`BLOOM_PLOTS_DIR` (`/app/data/PLOTS_DIR` in the container).

**Root cause, now confirmed (was only a hypothesis in #472):**

1. `docker-compose.dev.yml` (and `docker-compose.prod.yml`, same shape) bind-mount
   `./bloommcp/data/PLOTS_DIR` (and its siblings `SLEAP_OUT_CSV`, `ANALYSIS_OUTPUT`) onto
   `/app/data/...`. **None of these three directories are committed to the repo, and
   nothing in `make init` / `make dev-up` / `scripts/doctor.sh` creates them.** On a fresh
   clone, the first `docker compose up` is what creates the host-side directory — and Docker
   creates a missing bind-mount source **as root**.
2. The `bloommcp` image runs as a non-root system user (`bloom`, from
   `addgroup --system bloom && adduser --system --ingroup bloom bloom` in the Dockerfile) —
   it cannot write to a root-owned host directory.
3. **This exact failure mode is already known in this repo for a different directory**:
   `.github/workflows/pr-checks.yml`'s `dev-stack-smoke` job has a standing comment — *"Setup
   uv BEFORE compose starts (the dev stack creates a root-owned volumes/db/data bind mount;
   setup-uv's lockfile glob would EACCES on it)"* — for the Postgres data dir. Same mechanism,
   different directory.
4. **Why CI never caught this for `PLOTS_DIR` specifically:** `make bloommcp-smoke`
   (`bloommcp-result-store`'s "Live Supabase Persistence Smoke") only drives `qc_clean` /
   `remove_outliers` / `clustering` — tools whose persistence goes through the
   `SupabaseResultStore`/`SupabaseReader` ports. In the CI job's default (non-`local`)
   storage backend, those ports write to Supabase Storage (MinIO), **never touching the local
   bind-mounted `ANALYSIS_OUTPUT` directory at all**. Plotting is different: `_viz_shared.
   save_plot()` **always** writes straight to the local `PLOTS_DIR` on disk, regardless of
   `BLOOM_STORAGE_BACKEND` — there is no Supabase-routed path for plots. So `PLOTS_DIR` is hit
   by every deployment (not just opt-in fully-local mode), but no CI job ever calls a
   plotting tool, so this was invisible until manual dogfooding surfaced it.

## What Changes

- **MODIFY** `development-environment`'s "Fresh-Clone Stack Startup" requirement: a
  successful `make dev-up` on a fresh clone SHALL also mean the `bloommcp` container's
  bind-mounted data directories (`SLEAP_OUT_CSV`, `PLOTS_DIR`, `ANALYSIS_OUTPUT`) are
  writable by its runtime user — not just that `docker compose up` exits 0.
- **ADD** `development-environment`'s "bloommcp Data Directory Writability" requirement: the
  three host directories SHALL be created (if missing) with permissions the container's
  runtime user can write to, **before** `docker compose up` runs — so Docker's default
  create-as-root behavior for a missing bind-mount source never leaves them unwritable. See
  design.md for the specific mechanism (permissive local-dev directories vs. UID-matching)
  and its trade-offs.
- **ADD** a CI check that exercises at least one plotting tool end-to-end against the running
  dev stack, closing the coverage gap described in point 4 above, so a regression here fails
  CI rather than a developer. Where exactly this lives (extend `make check`, extend
  `live_persistence_smoke.py`, or a new lightweight script) is an implementation decision —
  see design.md's open question.

## Impact

- **Affected specs:** `development-environment` (MODIFIED "Fresh-Clone Stack Startup", ADDED
  "bloommcp Data Directory Writability").
- **Affected code:** `Makefile` (`dev-up` target, or a new preflight step it calls — likely
  mirroring the existing `scripts/doctor.sh` preflight pattern rather than growing `dev-up`
  inline); possibly `scripts/doctor.sh` itself; `make check` or
  `bloommcp/scripts/live_persistence_smoke.py` (new writability/plot-tool check, per the
  design decision); `bloommcp/docs/local-validation.md` / `DEV_SETUP.md` (note the
  directories are now auto-provisioned).
- **Out of scope, flagged not fixed:** `docker-compose.prod.yml` has the **identical**
  bind-mount shape for the same three directories, so staging/production may have the same
  latent issue. I have no visibility from this repo alone into whether the staging/prod
  hosts already provision these directories correctly outside of what's checked in here
  (e.g. server setup scripts, Ansible/Terraform, or a one-time manual step). This proposal
  fixes the **dev** path only (`make dev-up`, this repo's own CI) and calls out the prod/
  staging risk for whoever owns that deployment to confirm or file a follow-up on.
- **Branch/PR:** branches off `origin/staging`; this branch
  (`egao28/bloommcp-plotsdir-permission-fix-472`). No PR opened yet — proposal only, pending
  review.
