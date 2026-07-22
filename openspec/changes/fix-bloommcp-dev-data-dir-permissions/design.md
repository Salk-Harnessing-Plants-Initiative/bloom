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
  workaround for its one known symptom — the `setup-uv` ordering in CI. Whether it has a _write_
  problem too is out of scope here).
- **Non-goal:** fixing or even conclusively diagnosing the prod/staging case — see proposal.md's
  "Out of scope" note.

## Decision

**Pre-create the three directories with permissive local-dev permissions, before
`docker compose up` runs**, as `scripts/ensure_bloommcp_data_dirs.sh`, wired as a `.PHONY`
**Makefile prerequisite** of `dev-up` (`dev-up: ensure-bloommcp-data-dirs`) — not an inline
recipe line (which could accidentally land after the `docker compose up` line the way
`dev-up`'s existing trailing `@echo` lines are ordered), and not folded into `scripts/
doctor.sh` (CI runs `DOCTOR_SKIP=1 make dev-up`, which would silently disable an in-doctor
fix). On each directory: `mkdir -p` if missing, then `chmod 777`; either failing means the
directory (or its `bloommcp/data` parent) is already root-owned from a pre-fix run, which
`chmod` cannot self-heal (requires ownership) — the script fails loudly with a `sudo chown`/
`sudo rm -rf` remedy and aborts `dev-up` rather than silently continuing.

Verified live (not just unit-tested): reproduced the original bug by running `docker
compose run` directly (bypassing this fix) against a truly fresh `bloommcp/data` — Docker
created all three directories, and their parent, root-owned; `bloommcp` then either failed
a plot-tool call with a permission error, or (fully-local storage backend) crashed at boot
with `RuntimeError: BLOOM_STORAGE_BACKEND=local root /app/data/ANALYSIS_OUTPUT is not
writable`. Ran `ensure_bloommcp_data_dirs.sh` against that exact root-owned state and
confirmed it fails loudly with the chown/rm remedy rather than silently proceeding. Applied
the remedy, re-ran the script (now succeeds, `almoab:almoab` `0777`), then re-ran the
container: booted clean, and `plot_trait_histograms` through the real MCP transport
succeeded with the PNG landing on the real bind-mounted `PLOTS_DIR`.

**Post-verification correction:** this branch was later brought up to date with
`origin/staging`, which by that point included the unrelated `devendor-bloommcp-analysis`
restructure — every section tool (this one included) is now mounted on the combined `/mcp`
surface under a namespace prefix, so the tool from the paragraph above is only reachable as
`sleap_roots_plot_trait_histograms`, not the bare name. The live verification narrated above
was accurate against the code as it existed at the time; `live_plot_tool_smoke.py` itself
was not updated for the rename until a follow-up commit on this same branch (see tasks.md
1.2's note) — recorded here rather than silently rewriting the original verification claim.

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

## Resolved Questions

1. **Where does the new plot-tool CI check live?** A new standalone script
   (`bloommcp/scripts/live_plot_tool_smoke.py`) + `make bloommcp-plot-smoke`, **not**
   `live_persistence_smoke.py`. Reason found during implementation, not just a style
   preference: `live_persistence_smoke.py` runs in-process on the host and deliberately
   overrides `BLOOM_TRAITS_DIR`/`BLOOM_OUTPUT_DIR`/`BLOOM_PLOTS_DIR` to fresh host temp dirs
   before `import bloom_mcp` — it never touches the real container, the real bind mount, or
   the non-root `bloom` user, so a check bolted onto it would pass unconditionally regardless
   of whether this bug were fixed. The new script is a plain `fastmcp.Client` network call
   into the already-running container instead — the only way to actually exercise the bind
   mount this bug lives in. Wired into `pr-checks.yml`'s `dev-stack-smoke` job after `make
bloommcp-smoke`.
2. **Prod/staging risk:** documented in proposal.md's "Out of scope" note (which also names
   `compose-health-check`, which boots `docker-compose.prod.yml` with zero preflight and the
   identical bind-mount shape) and tracked as issue #474, filed the same day — this change
   does not attempt to confirm or fix it, since there's no way to tell from this repo alone
   whether it's a live problem on the actual staging/prod hosts.
