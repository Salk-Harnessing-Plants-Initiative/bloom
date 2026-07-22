## Context

Same mechanism as #472, scoped to prod/staging: Docker Compose, when a bind-mount source
directory doesn't exist on the host, creates it automatically — owned by whichever user the
Docker daemon runs as (root, on the Salk server and on GitHub-hosted CI runners). The
`bloommcp` image runs as a non-root system user (`bloom`), which then can't write into a
root-owned directory.

Confirmed via repo inspection, not assumption:

- `bloommcp/data/` is entirely gitignored (`.gitignore:96`, "bloom-mcp local runtime output")
  and has zero tracked files (`git ls-files bloommcp/data/` returns nothing) — any CI runner
  or freshly-provisioned host starts with the directory absent.
- `docker-compose.ci.yml` / `docker-compose.ci-cache.yml` (the overlay files layered onto
  `docker-compose.prod.yml` in `compose-health-check`) do not override `bloommcp`'s
  `volumes:` — the bind mounts that job actually uses are `docker-compose.prod.yml`'s
  unmodified `./bloommcp/data/{TRAITS_DIR,PLOTS_DIR,ANALYSIS_OUTPUT}` mounts.
- Why this isn't already a visible CI failure: `bloommcp` defines no `healthcheck:` in
  `docker-compose.prod.yml`, so `compose-health-check`'s "Wait for services to become
  healthy" step can only fail it via a nonzero container exit code, not a permission problem
  alone. `BLOOM_STORAGE_BACKEND` defaults to `supabase` (`storage_backend.py`'s
  `_DEFAULT_BACKEND`), and `.env.ci` never sets `BLOOM_STORAGE_BACKEND=local`, so the
  boot-time local-root-writability assertion in `validate_storage_backend()` — which does
  hard-crash the container in local mode — never fires. `compose-health-check` also never
  calls a plot-writing tool. The bug is real and reproducible (root-owned dirs get created
  every run) but latent: nothing in that job's current steps exercises the broken write path.
- This repo already has precedent for exactly this preflight shape, in this exact job:
  `pr-checks.yml`'s "Create MinIO data directory" step (`mkdir -p /tmp/minio-ci && chmod 777
/tmp/minio-ci`) runs immediately before MinIO's `docker compose up`.

## Goals / Non-Goals

- **Goal:** close the confirmed latent gap in `compose-health-check` (fresh runner, every
  run) so the CI job meant to catch prod-shape regressions would actually catch this one if a
  future test step touched the write path.
- **Goal:** apply the same preflight on the real deploy host (`deploy.yml`) as cheap insurance
  for a re-provisioned or first-time host, without requiring confirmation of current host
  state first — the preflight is idempotent and harmless on an already-correct host.
- **Non-goal:** confirming or fixing the actual current on-disk state of `bloommcp/data/*` on
  the live Salk staging/production server. Needs SSH access owned by whoever runs that infra;
  flagged as an open question below, not a blocker for this change.
- **Non-goal:** adding a `bloommcp` Docker healthcheck or a live plot-tool smoke test against
  the prod/staging stack (the natural way to make this failure mode actually _visible_ in
  `compose-health-check`, mirroring #472's `live_plot_tool_smoke.py` for dev). Worth a
  follow-up issue; kept out of this change to scope it to the preflight fix only.
- **Non-goal:** re-implementing `scripts/ensure_bloommcp_data_dirs.sh`'s mkdir/chmod/remedy
  logic. This proposal reuses it directly.

## Decisions

- **Reuse `scripts/ensure_bloommcp_data_dirs.sh` rather than duplicating it.** The script
  already handles create-if-missing + `chmod 777` + loud-remedy-on-failure generically via
  `BLOOMMCP_DATA_ROOT` (default `bloommcp/data`, relative to the repo root it's invoked
  from) — both `deploy.yml` (repo checked out at `PROD_DEPLOY_PATH`/`STAGING_DEPLOY_PATH`)
  and `pr-checks.yml` (repo checked out at the runner's default working dir) invoke it from
  the repo root, so neither caller needs a path override.
  - Alternative considered: copy the mkdir/chmod lines inline, the way "Create MinIO data
    directory" does for its single directory. Rejected: three directories plus the
    loud-failure remedy is enough logic that duplicating it a third time (dev, prod, CI)
    risks drift; a fourth caller is exactly what reuse is for.
- **In `deploy.yml`, run the preflight over SSH on the deploy host, not on the GitHub Actions
  runner.** The runner has no filesystem overlap with the deploy host — `docker compose ...
up` itself runs via SSH (see "Deploy production/staging stack" steps) — so the preflight
  must run inside that same SSH session's working directory (`cd
${{ secrets.PROD_DEPLOY_PATH }}` / `STAGING_DEPLOY_PATH`), immediately before it.
- **In `pr-checks.yml`, place the preflight directly before `compose-health-check`'s main
  `docker compose ... up -d --build` step** (after "Generate .env.ci from secrets"),
  mirroring exactly where "Create MinIO data directory" already sits relative to its own
  `docker compose up`.
- **DO gate the `deploy.yml` half of this change on confirming live host state first.**
  Reconsidered from an earlier draft of this design, which argued idempotency made this
  unnecessary — that argument only holds if the directories are _already correctly
  provisioned_. `scripts/ensure_bloommcp_data_dirs.sh` has **no privilege escalation**: on a
  directory it can't `chmod`, it prints a `sudo chown`/`sudo rm -rf` remedy and exits 1. If
  `bloommcp/data/*` on the live Salk host is already root-owned — **confirmed, not just
  plausible**: Elizabeth checked both live containers directly (2026-07-22, see `tasks.md`
  task 2.1) and production's three directories are genuinely `root:root` mode `755`, unwritable
  by `bloom`, since the May 6 initial deploy — the first deploy after merge fails, and **every
  subsequent deploy fails identically** until someone with server `sudo` manually intervenes —
  because deploys fire automatically on every push to `main`/`staging`. This is a real
  liveness risk, not a hypothetical one, so `tasks.md` makes confirming current ownership (via
  SSH, by whoever owns the deploy host) a precondition for merging the `deploy.yml` steps
  specifically. The `pr-checks.yml` half carries no equivalent risk — GitHub-hosted runners
  are always fresh — so it isn't gated on this and can proceed independently.
- **Reconcile `chmod 777` with `openspec/project.md`'s existing constraint, rather than
  silently overriding it.** `project.md`'s Technical Constraints section states: "the
  preferred fix is to match host UID/GID to the container's (`chmod 770` on the data dir is
  enough); `chmod 777` is a last-resort dev workaround and should not be propagated to
  staging/prod." This proposal reuses a script that does exactly that on real prod/staging
  hosts. Unlike MinIO — whose prod/staging data path (`${MINIO_DATA_PATH}`) is provisioned
  out-of-band via UID/GID matching, with **no** `chmod 777` step anywhere in `deploy.yml` —
  there is no existing precedent in this repo for `chmod 777` in a production deploy path.
  This needed real engagement, not a one-line dismissal:
  - UID/GID matching for `bloommcp` specifically was already considered and rejected for the
    _dev_ case in #472/#473's own design.md ("Alternatives considered"): `adduser --system
--ingroup bloom bloom` in `bloommcp/Dockerfile` assigns an arbitrary system UID at
    image-build time, not a fixed, predictable one — there is nothing stable to `chown` the
    host directory to without first pinning the `bloom` user's UID/GID in the Dockerfile (a
    separate change, with its own risk of colliding with an existing UID on some host). That
    same constraint applies identically to prod/staging — the image is unchanged.
  - Given that, `chmod 777` is treated here as a **narrow, explicitly-documented exception**
    to `project.md`'s general guidance, not a silent violation of it: `tasks.md` includes a
    task to add that exception, with this rationale, directly to `project.md`'s Technical
    Constraints section — so the convention doc stays accurate and a future reader doesn't
    independently rediscover the same apparent contradiction.
  - `tasks.md` also includes a follow-up-issue task to pin the `bloom` user's UID/GID in
    `bloommcp/Dockerfile`, which would let a future change switch prod/staging to proper
    UID/GID matching + `chmod 770` and retire this exception. Out of scope here — it's a
    Dockerfile change with its own review surface, not a workflow preflight.
  - The trade-off is still narrower than it looks: these three directories hold only
    non-secret plot/analysis artifacts (gitignored, never committed), and on both prod and
    staging the directories are Docker bind-mounts on a single-tenant deploy host already
    trusted at the same privilege level as the deploy user itself — `chmod 777` widens
    _local-process_ access on that host, not external/network access.

## Risks / Trade-offs

- `chmod 777` on these three directories is now a **documented exception** to
  `project.md`'s general guidance (see "Decisions" above), not a silently inherited one —
  the exception, its rationale, and its closure path (pinning `bloom`'s UID/GID) are all
  tracked in `tasks.md`.
- **Re-running `chmod 777` on every deploy is a silent-clobber risk**: if someone manually
  tightens permissions on the live host for some reason, the next deploy's preflight silently
  re-loosens them back to 777 with no differentiating log signal between "just created,"
  "already correct," and "just re-loosened." Low-severity (these are non-secret scratch
  directories) but worth a one-line log distinction if the script is ever revisited.
- If PR #473 hasn't merged by the time this is implemented, this branch has nothing to reuse
  yet — `tasks.md`'s first task is to confirm #473's merge state (or rebase onto its branch)
  before wiring either workflow call site.
- Touches `deploy.yml` — a hard-to-reverse, high-blast-radius file (the actual production
  deploy path) that has **no `pull_request` trigger**, so this change is not exercised by PR
  CI before merge; the first real exercise of the `deploy.yml` steps is the first actual
  deploy after merge. Combined with the no-privilege-escalation risk above, this is why
  confirming live host state is a pre-merge gate (see "Decisions"), not a post-merge
  open question.
- **Confirmed, not hypothetical**: Elizabeth checked both live containers directly
  (2026-07-22; see `tasks.md` task 2.1 for the full finding) and production's three
  directories are genuinely `root:root` mode `755` today, unwritable by `bloom`
  (`uid=100`, not in the `root` group) — meaning `PLOTS_DIR` has been silently broken for
  every plotting-tool call since the May 6 initial deploy, undetected until now. Without
  task 2.2's one-time manual `chown` landing first (or at least concurrently), this
  proposal's `deploy-production` preflight step will correctly fail loudly on the very
  first deploy after merge — that's the preflight working as designed, not a regression,
  but it means production stays broken (rather than silently broken) until the manual step
  happens. Staging is lower-risk: `PLOTS_DIR`/`ANALYSIS_OUTPUT` were already manually
  `chown`'d there in May, so only `TRAITS_DIR`/`SLEAP_OUT_CSV` (pending §2.3's
  drop-vs-keep re-examination) carries the same risk on that host.

## Migration Plan

No migration — this is a preflight addition to CI/CD workflows, not a runtime or data change.
Rollout is merging the workflow diffs; there's no state to migrate and no rollback beyond
reverting the commit (and, given the `pull_request`-trigger gap above, forcing a
`workflow_dispatch` re-run against the reverted ref rather than waiting for the next natural
push to confirm the revert actually works).

## Open Questions

- Once the `bloom` user's UID/GID is pinned in `bloommcp/Dockerfile` (tracked as a follow-up
  issue in `tasks.md`), should this proposal's `chmod 777` calls be revisited in favor of
  proper UID/GID matching + `chmod 770`, closing the `project.md` exception? Left for that
  follow-up to decide, not this change.

**Resolved, no longer open:** whether a live smoke test should be added to
`compose-health-check`. Elizabeth's review confirmed this is a real, already-demonstrated gap,
not a hypothetical one: `bloommcp`'s Docker `HEALTHCHECK` (`bloommcp/Dockerfile`) is an HTTP
`/health` liveness ping only — no filesystem check — and `compose-health-check`'s "Wait for
services healthy" step only polls container `Health`/`State`, never an actual MCP tool call.
Dev's equivalent (`dev-stack-smoke`) only catches this class of bug because #473 added a
dedicated live plot-tool smoke test (`make bloommcp-plot-smoke`); `compose-health-check` has no
analogue, and production's `PLOTS_DIR` has in fact been broken and undetected since the May 6
deploy as a direct result. Filed as a follow-up issue (not folded into this change — it needs
its own design: `bloommcp` is `expose:`-only in `docker-compose.prod.yml`, not host-published
like dev, so the dev script's approach of connecting directly to
`http://localhost:$BLOOMMCP_PORT/mcp` doesn't carry over as-is and needs its own review).
