## 1. `docker-compose.dev.yml`

- [x] 1.1 Replace the three commented-out literal lines in the `bloommcp` service env block
      (`BLOOM_STORAGE_BACKEND`, `BLOOM_STORAGE_LOCAL_ROOT`, `BLOOM_EXPERIMENT_LOCAL_ROOT`) with
      `${VAR:-}` interpolation.
- [x] 1.2 Rewrite the "uncomment to run FULLY LOCAL..." comment above those three lines to point
      at `.env.dev` / `make dev-up-local` instead of editing this file. Do **not** touch the two
      lines immediately below (`NUMBA_CACHE_DIR`, `PYTHONPYCACHEPREFIX`) or their own "Kept in
      sync with `docker-compose.prod.yml` for dev/prod parity" comment — that comment describes
      the NUMBA/pycache pair, not the storage-backend block, and is easy to misattribute or
      delete by accident while editing the block directly above it.

## 2. `.env.dev.example`

- [x] 2.1 Add a `# ── BloomMCP fully-local/offline storage backend (opt-in) ──` section near the
      existing `# ── BloomMCP server ──` block with `BLOOM_STORAGE_BACKEND=`,
      `BLOOM_STORAGE_LOCAL_ROOT=`, `BLOOM_EXPERIMENT_LOCAL_ROOT=` (empty by default), matching the
      `LOCAL_LLM_URL`/`LOCAL_LLM_MODEL` "disabled — leave empty" comment convention. Include a
      one-line cross-reference to `bloommcp/docs/storage-backends.md`'s "do not mix backends for
      one experiment" warning.
- [x] 2.2 Add a test to `tests/unit/test_init_dev.py` that runs `render()` over a template
      (fixture or the real `.env.dev.example`) containing the three new lines and asserts they
      survive as empty values — not `CHANGEME`-substituted, not dropped, not populated. (No
      existing test enumerates specific passthrough keys today — `test_render_fills_every_placeholder`
      only asserts no `CHANGEME` remains anywhere — so this is a new assertion, not an extension
      of a conditional one.)

## 3. `Makefile`

- [x] 3.1 Add a `dev-up-local` target that delegates to `dev-up` rather than duplicating its
      recipe body:
      ```makefile
      .PHONY: dev-up-local
      dev-up-local:
      	BLOOM_STORAGE_BACKEND=local $(MAKE) dev-up
      ```
- [x] 3.2 List `make dev-up-local` in the `help` target's usage output, next to `make dev-up`,
      with a one-line note that it's fully-local/offline mode and not to be mixed with a
      Supabase-backed experiment's existing outputs.
- [x] 3.3 `make -n dev-up-local` (dry-run) prints the delegated `$(MAKE) dev-up` line with
      `BLOOM_STORAGE_BACKEND=local` prefixed — a syntax/wiring check that costs seconds, done
      right after adding the target rather than deferred to the full stack bring-up in section 7.

## 4. Boot-time backend visibility (`bloommcp/src/bloom_mcp/server.py`)

- [x] 4.1 In `main()`, after `fully_local = is_local_backend()` is computed, print which storage
      backend is active (`local (fully-local/offline)` vs `supabase`) alongside the existing
      API-key-auth-mode print. Observability only — no change to `is_local_backend()`,
      `_ports.configure(...)`, or any selection/fail-fast logic.

## 5. Docs

- [x] 5.1 Update `bloommcp/docs/storage-backends.md`'s "To enable it in dev" paragraph (currently:
      "uncomment the storage-backend lines ... in `docker-compose.dev.yml`") to describe setting
      the vars in `.env.dev` or running `make dev-up-local`.

## 6. CI regression coverage

- [x] 6.1 In `.github/workflows/pr-checks.yml`'s `dev-stack-smoke` job, add a step immediately
      after "make dev-up (build + start the dev stack)" (currently `DOCTOR_SKIP=1 make dev-up`)
      that asserts the `bloommcp` container's `BLOOM_STORAGE_BACKEND` is empty/absent — proving
      plain `dev-up` never inherits `local` mode even after this change wires the interpolation
      token in.

## 7. Validation

- [x] 7.1 Fast interpolation check, no container bring-up: `docker compose -f
      docker-compose.dev.yml --env-file .env.dev config` run plain vs. with
      `BLOOM_STORAGE_BACKEND=local` prefixed, diff the rendered `bloommcp.environment` block —
      confirms the `${VAR:-}` wiring is correct in seconds, before paying for a full stack
      bring-up in 7.2. Verified: plain render shows all three vars as `""`; with
      `BLOOM_STORAGE_BACKEND=local` prefixed, only that var resolves to `local`, the other two
      stay `""`.
- [x] 7.2 Live proof, scoped to avoid the shared dev machine's already-running `bloom_v2_dev`
      stack (3+ days uptime, same fixed compose project name — a full `make dev-up-local` would
      have recreated those containers and did in fact collide on host ports the first time it was
      tried under an isolated project name). Instead: brought up just the `bloommcp` service
      (`--no-deps`, isolated `-p` project name, alternate host port) with
      `BLOOM_STORAGE_BACKEND=local`. Confirmed live: `docker exec ... printenv
      BLOOM_STORAGE_BACKEND` → `local`; container logs show
      `BLOOM_STORAGE_BACKEND=local is using BLOOM_OUTPUT_DIR as the local storage root...` and the
      task 4.1 boot-print `Bloom MCP Server storage backend: local (fully-local/offline)`;
      container reported healthy and served `/health` 200 OK. Torn down and cleaned up afterward
      (verified 0 leftover containers; shared stack's 16 containers unaffected throughout).
- [x] 7.3 Setting `BLOOM_STORAGE_BACKEND=local` directly in a copy of the real `.env.dev` (not via
      `dev-up-local`'s shell prefix) and rendering with `docker compose config` resolves to
      `BLOOM_STORAGE_BACKEND: local` — confirming the `.env.dev`-direct path resolves identically
      to the shell-prefix path, not just the latter. (Static config-render check, not a full
      container bring-up — sufficient here since 7.2 already live-proves the container-level
      consumption of this same env var once it reaches the container.)
- [x] 7.4 Static inspection + `tests/unit/test_makefile_dev_up_local.py`'s
      `test_dev_up_local_delegates_to_dev_up_without_duplicating_it`: `dev-up-local`'s recipe is
      exactly `BLOOM_STORAGE_BACKEND=local $(MAKE) dev-up` — one line, no file-write commands —
      so it cannot mutate `.env.dev`. Not re-verified with a live hash/mtime diff (that requires
      the full `dev-up` bring-up this task's own 7.2 note explains is impractical to repeat here).
- [x] 7.5 `make help` lists `dev-up-local`.
- [x] 7.6 `openspec validate update-dev-local-mode-toggle --strict` passes.

## 8. Round-2 fixes (PR #513 review — eberrigan)

- [x] 8.1 **Blocking:** the new CI step's error message contained the literal substring
      "make dev-up", which `test_dev_stack_smoke_skips_the_doctor_preflight` (tests/unit/
      test_ci_dev_stack_smoke.py) scans for across every `run:` block in the job, requiring
      `DOCTOR_SKIP=1` on each match — the new step isn't a `dev-up` invocation and doesn't set
      it, so it failed deterministically. Reworded to "plain dev-up" throughout.
- [x] 8.2 Added a bounded-wait/retry (`docker compose exec bloommcp true`, up to 60s) before the
      real check in that CI step, matching the `migrate-local`/`check` bounded-wait convention
      elsewhere in the Makefile, instead of assuming the container is immediately exec-able right
      after `docker compose up -d`.
- [x] 8.3 Moved the boot-time backend print in `server.py`'s `main()` to BEFORE
      `validate_data_env()`/`validate_supabase_env()` (was after), so it fires even when
      validation fails fast, not only on the happy path.
- [x] 8.4 Added a foreground `@echo` to `dev-up-local`'s Makefile recipe announcing fully-local
      mode at invocation time — `dev-up`/`dev-up-local` both run `docker compose up -d`
      (detached), so the container-log print alone is invisible without a separate `make
      dev-logs`/`docker compose logs` call.
- [x] 8.5 Documented the shared, fixed `bloom_v2_dev` compose project name in `dev-up-local`'s
      Makefile comment and `storage-backends.md`: it recreates the same single per-machine dev
      stack `dev-up` uses, not an isolated instance (pre-existing repo convention — the
      `development-environment` spec's "Canonical Local Stack Path" requirement — not a new
      collision mechanism introduced here, but now called out explicitly).
- [x] 8.6 Fixed the misleading `.env.dev.example` comment claiming `BLOOM_OUTPUT_DIR`/
      `BLOOM_TRAITS_DIR` are vars "above" in that file — they're `docker-compose.dev.yml`
      literals, not `.env.dev.example` entries. Also added an explicit warning there recommending
      the one-shot `make dev-up-local` form over setting the var directly in `.env.dev`/a shell
      profile, since the latter silently applies to every future plain `make dev-up`.
- [x] 8.7 Added unit test coverage that didn't exist for any of the three new mechanisms:
      `tests/unit/test_makefile_dev_up_local.py` (target exists, delegates via `$(MAKE) dev-up`
      without duplicating `docker compose`, listed in `make help`, `make -n` dry-run resolves
      correctly), a new test in `tests/unit/test_compose_dev_env_files.py` (the three vars use
      `${VAR:-}` interpolation, not literals), and three new tests in `bloommcp/tests/
      test_package_baseline.py` (the boot-print fires for both backends, and fires even when
      validation fails fast).
- [x] 8.8 Live proof that `BLOOM_STORAGE_BACKEND=local` actually activates local mode — see 7.2
      above (isolated `-p` project name + `--no-deps`, so the shared `bloom_v2_dev` stack was
      never touched).
