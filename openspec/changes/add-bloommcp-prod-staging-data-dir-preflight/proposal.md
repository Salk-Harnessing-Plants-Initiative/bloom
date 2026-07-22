## Why

Issue #474: `docker-compose.prod.yml` has the **identical** unguarded bind-mount shape
(`bloommcp/data/{TRAITS_DIR,PLOTS_DIR,ANALYSIS_OUTPUT}`) as the dev-stack bug fixed —
dev path only — by #472/PR #473. Verified from the repo alone (not assumed): `bloommcp/data/`
is entirely gitignored (`.gitignore:96`) with zero tracked files, and neither CI overlay
(`docker-compose.ci.yml`, `docker-compose.ci-cache.yml`) overrides `bloommcp`'s `volumes:` —
so `pr-checks.yml`'s `compose-health-check` job, which boots `docker-compose.prod.yml` on a
fresh GitHub-hosted runner every run, hits the exact same "Docker auto-creates the missing
bind-mount source as root" mechanism on every single run. It doesn't yet fail visibly there
only because `bloommcp` defines no Docker `healthcheck:` (so the job's health-wait step can't
detect a permission problem) and `BLOOM_STORAGE_BACKEND` defaults to `supabase`, not `local`
(so the boot-time local-root-writable assertion in `storage_backend.py` never fires) — the
bug is real and reproducible there, just latent. `deploy.yml`'s `deploy-production` and
`deploy-staging` jobs have the same zero-preflight gap on the actual Salk server.

## What Changes

- **ADD** a data-directory preflight step to `deploy.yml`'s `deploy-production` and
  `deploy-staging` jobs, run over SSH on the deploy host immediately before
  `docker compose ... up -d --build`, reusing `scripts/ensure_bloommcp_data_dirs.sh`
  (landing via PR #473) rather than re-implementing its mkdir/chmod/remedy logic a third time.
- **ADD** the same preflight to `pr-checks.yml`'s `compose-health-check` job, placed the same
  way that job already provisions `/tmp/minio-ci` (see its "Create MinIO data directory"
  step) — immediately before the job's main `docker compose ... up -d --build` step.
- **DO** treat confirming current on-disk ownership of `bloommcp/data/*` on the live Salk
  staging/production host as a **pre-merge gate for the `deploy.yml` half of this change**
  (tasks under "Pre-merge host-state verification" in `tasks.md`) — not an optional
  afterthought. `scripts/ensure_bloommcp_data_dirs.sh` has no privilege escalation: if the
  directories are already root-owned on the live host, the preflight fails loudly and
  **every deploy after merge fails identically** until someone with server `sudo` manually
  `chown`s them. The `pr-checks.yml` half has no such risk (GitHub-hosted runners are always
  fresh), so it isn't gated on this.
- **DO** reconcile the reused script's `chmod 777` with `openspec/project.md`'s existing
  constraint that `chmod 777` "should not be propagated to staging/prod" — see `design.md`'s
  "Decisions" for the full reasoning and the accompanying task to document this as a narrow,
  explicit exception rather than a silent contradiction.

## Impact

- **Affected specs:** `deploy-health-check` (two ADDED requirements — deploy hosts and CI,
  split by call site; see `design.md`).
- **Affected code:** `.github/workflows/deploy.yml` (both deploy jobs), `.github/workflows/
pr-checks.yml` (`compose-health-check` job). `scripts/ensure_bloommcp_data_dirs.sh` is reused
  as-is, not modified.
- **Affected docs:** `PROD_SETUP.md` (its "Deploying" numbered steps and a new "bloommcp Data
  Directories" note, mirroring `DEV_SETUP.md`'s equivalent section from #473) and
  `openspec/project.md` (a documented exception to the existing `chmod 777`
  should-not-be-propagated-to-staging/prod constraint — see `design.md`).
- **Depends on:** PR #473 (`egao28/bloommcp-plotsdir-permission-fix-472`) merging to `staging`
  first — or this branch rebasing onto it — so `scripts/ensure_bloommcp_data_dirs.sh` exists
  to reuse. Not yet merged as of this proposal (PR #473 is still open).
- **References:** issue #474, issue #472, PR #473, `openspec/changes/
fix-bloommcp-dev-data-dir-permissions` (the dev-only fix this follows up on).
- **Branch/PR:** branches off `origin/staging`; this branch
  (`egao28/bloommcp-prod-staging-data-dir-preflight-474`).
