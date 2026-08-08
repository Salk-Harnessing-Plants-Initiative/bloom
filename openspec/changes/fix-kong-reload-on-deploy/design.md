## Context

Kong (`kong:2.8.1`) runs `KONG_DATABASE=off` (DB-less/declarative mode). Its service definition:

```yaml
volumes:
  - ./volumes/api/kong.yml:/home/kong/temp.yml:ro,z
environment:
  KONG_DECLARATIVE_CONFIG: /home/kong/kong.yml
entrypoint: bash -c 'eval "echo \"$$(cat ~/temp.yml)\"" > ~/kong.yml && /docker-entrypoint.sh kong docker-start'
healthcheck:
  test: ["CMD", "kong", "health"]
  interval: 10s
  timeout: 5s
  retries: 5
  start_period: 30s
```

`~/temp.yml` (the bind mount) holds `${VAR}`-style placeholders (e.g. `$SUPABASE_ANON_KEY`). The custom `entrypoint:` does the env-substitution into `~/kong.yml` — the file `KONG_DECLARATIVE_CONFIG` actually points at — once, at container start, before handing off to the stock upstream `/docker-entrypoint.sh kong docker-start`. There is no vendored copy of that upstream script in this repo.

## Decision 1: Full restart, not `kong reload`

**Decision: use `docker compose restart kong`, not an in-place `kong reload`.**

Two independent reasons rule out `kong reload`:

1. **It wouldn't even see the new config.** `kong reload` (or an Admin API `/config` POST) only re-reads whatever is already at the path `KONG_DECLARATIVE_CONFIG` points to (`~/kong.yml`). It does not re-run the entrypoint's substitution step, so unless something else first re-generates `~/kong.yml` from the (updated, bind-mounted) `~/temp.yml`, a reload would just re-apply the *same stale config* it already had.
2. **Even if we did the substitution manually first, `kong reload` in DB-less mode has a documented history of not reliably applying it.** Kong GitHub issues #4808 (1.2.1: DB-less reload changes the file but the running config doesn't pick it up — stale target still resolves), #5898 (2.0.4: declarative config loading is non-atomic, causing transient 404/503s and plugins that don't take effect on frequent reload), and #6447 (2.1.4: one node's `systemctl reload` "succeeded" but silently kept serving stale ACL data for one consumer) — spanning three different Kong versions — show this isn't a single fixed bug, but a recurring class of problem with DB-less hot-reload. There's no authoritative confirmation this is resolved in 2.8.1, and the failure mode when it silently doesn't work is exactly the bug this change exists to fix (config changes silently not taking effect) — adopting a second unreliable reload path would be a poor trade for the small (~1-3s) availability window a restart costs.

A full container restart sidesteps both problems: it re-runs the *entire* entrypoint (substitution + `kong docker-start`), which is exactly what already happens correctly whenever `up -d` recreates Kong for an unrelated reason (e.g. an env var change) — a mechanism already proven to work, not a new one.

**Trade-off accepted:** deploys that change `kong.yml` content now cause a brief Kong unavailability window (container stop + start, typically a few seconds, gated further by the existing `start_period: 30s` / `retries: 5` healthcheck before anything routes through it again). This is the same category of trade-off `deploy.yml` already accepts for Caddy's reload (Caddy's is zero-downtime because `caddy reload` re-reads its Caddyfile directly with no substitution step in between — Kong's extra indirection layer is what forces the difference).

## Decision 2: Delta-based crash-loop check, not an absolute threshold

Caddy's crash-loop check reads `RestartCount` once, unconditionally, and fails if `> 2` — this works because a clean `caddy reload` never restarts the container, so `RestartCount` should not move at all during a normal deploy; any nonzero movement already indicates a problem accumulated (possibly from before this deploy).

Kong's fix *deliberately* restarts the container as its normal, successful-path behavior. An absolute `RestartCount > 2` check would need at least 3 unplanned restarts to ever trip in the pathological case, but would also happily pass with our 1 deliberate restart plus 2 more crash-restarts hidden inside it — a much weaker guarantee than Caddy's check gives. Instead:

- The restart step captures `RestartCount` **immediately before** issuing `docker compose restart kong` (call it `before`).
- The crash-loop-check step captures `RestartCount` again **after** (`after`) and computes `delta = after - before`.
- `delta` should be exactly `1` on a clean restart (our own). A `delta > 2` (i.e., at least 2 *additional* restarts beyond our own) means Docker's `restart: unless-stopped` policy is retrying a crashing container — a bad `kong.yml` that fails Kong's own declarative-config validation at boot. Reusing Caddy's `> 2` tolerance (rather than a stricter `> 1`) keeps the two checks' sensitivity comparable and allows for one benign extra restart (e.g., a slow-starting dependency causing an early healthcheck-driven restart) without false-positiving on it.
- Both steps are gated on the same `kongfile_changed == 'true'` condition — this check exists specifically to catch our own restart going bad, not to be a general-purpose Kong crash-loop monitor (which doesn't exist for any other service in this workflow today and is out of scope for this issue).

## Decision 3: Extract the crash-loop threshold logic into a script

Caddy's crash-loop check is inlined directly in `deploy.yml` and has no unit test coverage today (confirmed: no `tests/unit/` file references it). For Kong, the delta computation is genuinely new conditional logic (not a copy of an existing, already-informally-verified pattern), and the feature's own task brief calls for TDD coverage even though "there's no unit-testable code path in the usual sense" for a deploy workflow.

This repo already has a precedent for exactly this situation: `scripts/validate_env.sh` contains the actual conditional logic deploy.yml depends on, and `tests/unit/test_env_defaults.py` invokes it directly via `subprocess` against fixture files — so the workflow and the tests can never drift, and the logic is verified without needing a live deploy. One difference: `validate_env.sh` runs on the **GitHub Actions runner** (operating on a locally-assembled env file via `${{ github.workspace }}`), while the crash-loop check must run **on the deploy host itself**, over the existing SSH session, because it inspects the live `kong` container's real Docker state. It reaches the deploy host the same way every other script this workflow depends on does: the `Pull latest code` step's `git reset --hard origin/<branch>` syncs the full repo tree onto the deploy host, so a script committed at `scripts/check_kong_restart_delta.sh` is present there (at `$PROD_DEPLOY_PATH/scripts/...` / `$STAGING_DEPLOY_PATH/scripts/...`) as soon as this change reaches that branch — no separate copy/deploy step needed.

Signature:

```
scripts/check_kong_restart_delta.sh <before-count> <threshold> -- <docker compose command...>
```

- Everything after `--` is the exact `docker compose -f docker-compose.prod.yml --env-file .env.<env> [-p bloom_v2_staging]` prefix the calling job already uses for every other compose invocation — passed through so the script never hardcodes prod/staging-specific flags itself.
- Resolves the container via `"${compose_cmd[@]}" ps -q kong` (same pattern Caddy's inline check already uses).
- Runs `docker inspect --format='{{.RestartCount}}' <container-id>` to get `after`.
- Computes `delta = after - before-count`.
- If `delta > threshold`: prints a `::error::` annotation, runs `"${compose_cmd[@]}" logs --tail=100 kong` and `"${compose_cmd[@]}" stop kong`, and exits 1.
- Otherwise prints `kong restart delta: <delta>` and exits 0.

The companion `Restart Kong config` step captures `before` the same way (`"${compose_cmd[@]}" ps -q kong` then `docker inspect`) immediately before calling `"${compose_cmd[@]}" restart kong`, then sleeps briefly (5s) before the crash-loop-check step runs — giving Docker's `restart: unless-stopped` policy a window to reveal a fast validation-failure crash loop before the check reads `RestartCount`. `before` is threaded from the restart step to the check step via a `GITHUB_OUTPUT` value, the same cross-step pattern already used for `caddyfile_changed`/`kongfile_changed`.

For unit testing without a real Kong container or SSH access, `docker` is stubbed as a single fake executable placed first on `PATH`: it dispatches on its first argument (`inspect` → prints a canned `RestartCount` from an env var; `compose` → matches `ps -q kong` / `logs --tail=100 kong` / `stop kong` against its remaining args, returning a canned container id and appending to a call-log file the test can assert against). This mirrors `test_env_defaults.py`'s technique of running the real script via `subprocess` against controlled fixtures rather than re-implementing its logic in Python.

The restart step itself (`docker compose restart kong` plus capturing the `before` count) is left inline, matching how Caddy's own reload step (`caddy reload --config ...`) is inline — it has no branching logic worth isolating.

## Decision 4: Extend the existing `Pull latest code` step, not a new step

`caddyfile_changed` and the new `kongfile_changed` both need the same pre-pull/post-pull `BEFORE`/`AFTER` SHAs, already computed once per job inside the existing `Pull latest code` step. Adding a second `git diff --quiet $BEFORE $AFTER -- volumes/api/kong.yml` line to that same step (with its own `KONGFILE_CHANGED=` marker line, parsed the same way as `CADDYFILE_CHANGED=`) avoids a duplicate SSH round-trip and duplicate `git fetch && git reset --hard` — the state that would need to be shared is already local to that one step.

## Rollback / verification limits

This environment has no SSH access to the staging or production deploy hosts. Correctness of the new steps' *shape* (ordering, gating, exact `if:` conditions) is verified by the PyYAML-based test; correctness of `scripts/check_kong_restart_delta.sh`'s *logic* is verified by direct subprocess invocation against stubbed `docker`. Correctness of the *end-to-end* behavior against a real Kong container (does `docker compose restart kong` actually pick up new routes; does the deployed staging Kong actually start serving `/auth/v1/.well-known/*` unauthenticated after this ships) can only be confirmed manually, post-merge, against the real staging host — tracked as an explicit manual task in `tasks.md`, not claimed as done by this change's automated tests.
