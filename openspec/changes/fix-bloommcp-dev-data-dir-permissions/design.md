## Context

Docker Compose, when a bind-mount source directory doesn't exist on the host, creates it
automatically — owned by whichever user the Docker daemon runs as (root, on a typical Linux
install and on GitHub-hosted CI runners). `bloommcp/data/{SLEAP_OUT_CSV,PLOTS_DIR,
ANALYSIS_OUTPUT}` are none of them committed to the repo and nothing in this repo's tooling
(`make init`, `make dev-up`, `scripts/doctor.sh`) creates them ahead of time — so on a fresh
clone, the first `docker compose up` is what creates them, as root. The `bloommcp` image runs
as a non-root system user (`bloom`), which then can't write into a root-owned directory.

The `volumes/db/data` Postgres bind mount hits the identical mechanism today (see the
standing comment in `pr-checks.yml`'s `dev-stack-smoke` job) — this isn't a new class of bug
in this repo, just a previously-unfixed instance of it for a different directory.

## Goals / Non-Goals

- **Goal:** `make dev-up` on a truly fresh clone leaves all three `bloommcp` data
  directories writable by the container, with no manual step.
- **Goal:** a regression here fails CI, not a developer running the dev stack for the first
  time.
- **Non-goal:** fixing the `volumes/db/data` instance of this pattern (already has a documented
  workaround for its one known symptom — the `setup-uv` ordering in CI. Whether it has a *write*
  problem too is out of scope here).
- **Non-goal:** fixing or even conclusively diagnosing the prod/staging case — see proposal.md's
  "Out of scope" note.

## Decision

**Pre-create the three directories with permissive local-dev permissions, before
`docker compose up` runs**, as a preflight step (Makefile target or a small script, mirroring
`scripts/doctor.sh`'s existing preflight pattern rather than growing `dev-up` inline):

```sh
mkdir -p bloommcp/data/SLEAP_OUT_CSV bloommcp/data/PLOTS_DIR bloommcp/data/ANALYSIS_OUTPUT
chmod 777 bloommcp/data/SLEAP_OUT_CSV bloommcp/data/PLOTS_DIR bloommcp/data/ANALYSIS_OUTPUT
```

### Alternatives considered

- **Match the container's UID on the host.** Rejected: `adduser --system --ingroup bloom
  bloom` in the Dockerfile gets an arbitrary system UID assigned at image-build time, not a
  fixed, predictable one — there's nothing stable to `chown` the host directory to without
  also pinning the `bloom` user's UID/GID in the Dockerfile (a second change, with its own
  risk of colliding with an existing UID on some host), and it still wouldn't help on hosts
  where the compose-invoking user's UID doesn't match whatever we pinned.
- **Switch to named volumes.** Rejected for `SLEAP_OUT_CSV` specifically: the documented dev
  workflow (`docs/local-validation.md`, the "Claude dogfood validation" checklist) seeds raw
  CSVs by copying them directly into the host-visible `bloommcp/data/SLEAP_OUT_CSV/`
  directory — a named volume loses that direct host-filesystem visibility. Named volumes
  would be initialized with the image's baked-in ownership (solving the permission problem
  cleanly) for `PLOTS_DIR`/`ANALYSIS_OUTPUT` specifically, which nobody needs raw host access
  to today, but using bind mounts for one and named volumes for the others is an odd
  asymmetry for a 3-line `chmod` fix to avoid. Not pursued in this proposal; worth
  reconsidering if `chmod 777` on local-only, non-secret scratch directories draws pushback
  in review.
- **`chmod 777` is a security smell in general** — accepted here specifically because these
  three directories hold only local-dev-scratch analysis artifacts (never secrets, never
  committed, gitignored), and only affect the local dev host, not the running containers'
  internal filesystem or any shared/prod host. Flagged explicitly so review can push back if
  this trade-off isn't acceptable.

## Open Questions

1. **Where should the new plot-tool CI check live?** `make check` (extends "Local Stack
   Health Verification") is the more natural fit purpose-wise (it already asserts the stack
   is *actually* healthy, not just that containers booted) but currently has no MCP-calling
   logic. `live_persistence_smoke.py` already has MCP-calling machinery but is scoped by its
   own docstring to the `SupabaseResultStore`/`SupabaseReader` write path specifically, which
   plots don't go through — bolting a plot check on would stretch that scope. A third option:
   a small new standalone script/`make` target. Needs a decision before task 3 in `tasks.md`.
2. **Should the prod/staging risk get its own tracked issue now, or wait until someone
   confirms it's actually a live problem there?** I don't have visibility into how the
   staging/prod hosts are provisioned outside this repo.
