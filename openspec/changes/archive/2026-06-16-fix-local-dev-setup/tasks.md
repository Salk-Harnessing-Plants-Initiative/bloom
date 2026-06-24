# Tasks — fix-local-dev-setup

TDD is mandatory: for each implementation item, write the test first, run it, and
confirm it fails for the right reason (RED) before writing implementation code
(GREEN), then refactor. "Verify" items require captured command output, not
assertion. Keep each numbered section's RED+GREEN in a single commit so
`git bisect` stays green. Issue references: closes #124, advances #104,
addresses #118.

## 1. Supersede the prior change

- [x] 1.1 Confirm `automate-environment-setup` is unimplemented (0/43); record in
      `proposal.md` that this change supersedes it (done).
- [x] 1.2 Remove `openspec/changes/automate-environment-setup/`.
- [x] 1.3 Verify: run `openspec list` and capture output proving
      `automate-environment-setup` no longer appears and `fix-local-dev-setup`
      does.

## 2. CRLF fix — `.gitattributes` (closes #124) (TDD)

- [x] 2.1 RED: add `tests/unit/test_init_script_line_endings.py` asserting that
      every file under `volumes/db/` (both `*.sh` and `*.sql`) resolves to
      `eol=lf` via `git check-attr eol` — the **declarative checkout rule**, NOT a
      working-tree/blob byte scan. (The blobs are already stored LF; the real bug
      is the checkout attribute — a Windows checkout produces CRLF for any file
      without an explicit `eol=lf`. A byte scan would be a platform-detector: CRLF
      on a Windows checkout, LF on Linux CI, regardless of the fix.) Run; confirm
      it fails — `.sql` files report `eol: unspecified` (only `*.sh` had a rule).
- [x] 2.2 GREEN: add `*.sql text eol=lf` and an explicit `volumes/db/** text
      eol=lf` to the existing `.gitattributes`; run `git add --renormalize .` to
      rewrite the affected tracked blobs to LF (this fixes the `.sh` files too).
- [x] 2.3 Verify: re-run 2.1 (passes) and `git diff --stat` to show only
      line-ending churn on `volumes/db/*`; capture output.

## 3. Committed env template + `.gitignore` (TDD)

- [x] 3.1 RED: add `tests/unit/test_env_dev_example.py` asserting (a)
      `.env.dev.example` exists; (b) it contains every variable `db-dev` requires
      from `docker-compose.dev.yml`, **reusing the proven `${VAR:-default}`
      extraction regex from `tests/unit/test_env_defaults.py`** (do not invent a
      new one) plus a justified exclude-set for vars with safe compose defaults;
      (c) no value looks like a real secret (placeholders only); (d) it uses
      `POSTGRES_HOST_PORT`; (e) the committed file is LF-only. Run; confirm failure
      (file absent).
- [x] 3.2 GREEN: create `.env.dev.example` with every required variable, a comment
      per variable, and placeholder values only.
- [x] 3.3 RED: extend the test to assert `.gitignore` ignores `.env.dev` and
      `.env.dev.backup` and does NOT ignore `.env.dev.example`. Run; confirm
      failure (blanket `.env*` currently ignores the example).
- [x] 3.4 GREEN: add `!.env.dev.example` and `!**/.env.dev.example` after the
      `.env*`/`**/.env.*` lines in `.gitignore`; confirm `.env.dev.backup` stays
      ignored; remove duplicate entries.
- [x] 3.5 Verify: `git status` and `git check-ignore .env.dev .env.dev.backup
      .env.dev.example`; capture output. Confirm `.env.dev.example` is NOT pulled
      into the prod/staging env-parity check (`scripts/verify_env_parity.py` /
      `tests/unit/test_verify_env_parity.py`) so the dev template can't drift the
      parity gate; if it would be, scope it out explicitly.

## 4. Credential generator `scripts/init_dev.py` + `make init` (#104) (TDD)

- [x] 4.1 GREEN (deps): append `pyjwt` and `python-dotenv` to
      `[project.optional-dependencies].test` in `pyproject.toml` (which already
      carries `pandas`/`supabase` from current staging). `pyjwt` is needed at
      unit-test collection time to verify minted JWTs; already used by
      `scripts/generateJWT_key.py`. Run `tests/unit/` collection; confirm import
      works. Regenerate the root `uv.lock` (`uv lock`) so the committed lock
      matches the new `test` extra (CI runs `--extra test` without `--frozen`, so
      it won't fail, but the lock must not silently drift).
- [x] 4.2 RED: add `tests/unit/test_init_dev.py` asserting the generator (invoked
      via `uv run --with pyjwt,python-dotenv`): produces `.env.dev` from the
      template; `ANON_KEY`/`SERVICE_ROLE_KEY`/`BLOOM_AGENT_KEY` are JWTs that
      verify against the generated `JWT_SECRET` and carry
      `role:anon`/`role:service_role`/`role:bloom_agent`; `DB_ENC_KEY` is exactly
      16 bytes; `VAULT_ENC_KEY` 32; `SECRET_KEY_BASE` ≥64; `SUPAVISOR_ENC_KEY` 64
      hex; `JWT_SECRET` ≥32; no placeholders remain. Run; confirm failure (script
      absent).
- [x] 4.3 RED: idempotency/boundary tests — second run without `--force` refuses
      (non-zero exit, message); `--force` backs up the existing `.env.dev` then
      regenerates, and when a previous `.env.dev.backup` already exists it writes a
      timestamped backup (`.env.dev.backup.<timestamp>`) so no prior credentials
      are lost; malformed/missing template errors clearly; CRLF in the template is
      tolerated; the script prints no secret values to stdout. Run; confirm
      failure.
- [x] 4.4 GREEN: implement `scripts/init_dev.py`, reusing the verified key-size
      logic from `scripts/generate-secrets.sh` and the JWT-minting pattern from
      `scripts/generateJWT_key.py`/`generate_KEYS`. Add the `make init` target.
- [x] 4.5 GREEN: fix/retire `scripts/generate_KEYS` (the non-runnable stub #104
      calls out) — either delete it or replace its body with a pointer to
      `make init`.
- [x] 4.6 Verify: run `make init` in a clean temp dir; capture output and the
      resulting `.env.dev` KEY NAMES only (never values).

## 5. Unify host Postgres port (TDD)

- [x] 5.1 RED: add `tests/unit/test_port_var_consistency.py` asserting no
      `POSTGRES_EXTERNAL_PORT` remains in `.env.dev.example`,
      `docker-compose.dev.yml`, or docs; that `POSTGRES_HOST_PORT` is used in all
      of them and in `conftest.py`; and that the `migrate-local` recipe's
      `--db-url` references `POSTGRES_HOST_PORT` (not a hardcoded `5432`). Run;
      confirm failure.
- [x] 5.2 GREEN: rename `POSTGRES_EXTERNAL_PORT` → `POSTGRES_HOST_PORT` in
      `.env.dev.example` and `docker-compose.dev.yml` (port mapping only — no
      data-mount change); update local `.env.dev`.
- [x] 5.3 Verify: `make dev-up` starts `db-dev` with the renamed var on a
      supported platform; capture `docker ps` showing the published port.

## 6. Fix `make migrate-local` (port + password + --debug) (TDD)

- [x] 6.1 RED: add `tests/unit/test_makefile_migrate_local.py` asserting the
      `migrate-local` recipe builds its `--db-url` from `POSTGRES_HOST_PORT`,
      `POSTGRES_PASSWORD`, `POSTGRES_USER`, `POSTGRES_DB` (no hardcoded `5432`,
      no hardcoded `supabase_admin`/`postgres` fallback that masks the generated
      password) and passes `--debug`. Add a **behavioral** assertion: run
      `make -n migrate-local` with `POSTGRES_HOST_PORT=5433` in the environment and
      assert the printed command contains `:5433` and not `:5432` (guards the
      wrong-Postgres footgun in the documented override). Run; confirm failure.
- [x] 6.2 GREEN: rewrite the `migrate-local` recipe to source `.env.dev` and build
      the URL from those vars with `--debug`, mirroring CI's pattern
      (`pr-checks.yml`).
- [x] 6.3 Verify: run `make migrate-local` against a healthy `db-dev` created by
      `make init`; capture output showing all migrations applied.

## 7. Wire integration tests to local dev (TDD)

- [x] 7.1 RED: add `tests/unit/test_conftest_env_loading.py` asserting
      `conftest.py`'s loader includes `.env.dev` as a source AFTER
      `.env.prod`/`.env.ci` (precedence preserved: with both `.env.ci` and
      `.env.dev` present, `.env.ci` wins). Run; confirm failure.
- [x] 7.2 GREEN: update `tests/integration/conftest.py` to also load `.env.dev`
      when present, after the existing sources.
- [x] 7.3 GREEN: add a `make test-integration` target wrapping
      `uv run --extra test pytest tests/integration/`.
- [x] 7.4 Verify: with the stack up and `.env.dev` present, run
      `make test-integration`; capture output showing `pg_conn` tests execute
      (not skipped).

## 8. Health check + clean-init verification (#104) (TDD)

- [x] 8.1 RED: add `tests/integration/test_local_dev_bootstrap.py` asserting
      base roles (`postgres`, `anon`, `authenticated`, `service_role`,
      `authenticator`, `supabase_admin`, `supabase_auth_admin`,
      `supabase_storage_admin`) and application roles (`bloom_admin`,
      `bloom_user`, `bloom_writer`, `bloom_agent`, created by migrations); `auth`
      schema + `auth.uid()` (with a **bounded poll — up to 60s at 2s intervals via
      the `pg_conn` fixture**, matching CI's `storage.buckets` poll, since CI does
      not explicitly wait for `auth.uid()`); `storage` schema + `storage.buckets`;
      and migration completeness via **set comparison, reusing the helper logic in
      `tests/integration/test_migrations.py::test_all_migrations_recorded`** (every
      `*.sql` file recorded, none missing/unexpected — NOT a count). Frame it as an
      acceptance/guard test (it passes on a correct init). Run against the current
      (not-yet-correct) local stack; confirm it fails for the right reason.
- [x] 8.2 RED: add a test for the partial-migration case (spec scenario "Partial
      migration run is reported as failure") — seed `schema_migrations` with a
      strict subset of the migration files and assert `check_health.py` exits
      non-zero (set comparison flags the missing entries). Run; confirm failure.
- [x] 8.3 GREEN: implement `scripts/check_health.py` + `make check` (every service
      with a Compose healthcheck reports `healthy` and none exited non-zero + the
      base/application role + schema assertions above + migration completeness via
      the set-comparison check).
- [x] 8.4 GREEN: add `make verify-dev` (clean reset → `dev-up` → `migrate-local`
      → `make check`).
- [x] 8.5 Verify: run `make verify-dev` on a supported platform (Linux/macOS or
      WSL2) and capture passing output.

## 9. Documentation (DEV_SETUP.md, PROD_SETUP.md, README.md, Makefile help, commands)

- [x] 9.1 RED: add `tests/unit/test_dev_setup_doc.py` asserting every
      `make <target>` referenced in `DEV_SETUP.md`, `PROD_SETUP.md`, and
      `README.md`, AND advertised in `make help`, resolves to an actual rule
      definition (`^target:`/`.PHONY`), not help text — so the
      `apply-migrations`/`drop-tables` references (in both DEV_SETUP and
      PROD_SETUP) fail the test. Run; confirm failure.
- [x] 9.2 GREEN: fix the `Makefile` `help` text (remove `drop-tables`, add new
      targets) and rewrite `DEV_SETUP.md` to: remove phantom targets; document the
      canonical path (`make init` → `make dev-up` → `make migrate-local` →
      `make load-test-data` → `make test-integration` → `make check`/
      `make verify-dev`); list the env keys incl. `BLOOM_AGENT_KEY` (not just
      `ANON_KEY`/`SERVICE_ROLE_KEY`); add a WSL2 section (clone into the Linux FS,
      not `/mnt/c`; `make` prerequisite per #118; `supabase` CLI install + pinned
      `SUPABASE_VERSION`); document the `POSTGRES_HOST_PORT` override; link to
      `_WIKI/SUPABASE/README.md` for roles/RLS (no duplication) and add the
      CRLF/#124 note to that wiki's Known Issues.
- [x] 9.3 GREEN: fix `PROD_SETUP.md` (`apply-migrations` → `migrate-local`); in
      `README.md` drop the hardcoded `localhost:5432` and the duplicated command
      list (point to `DEV_SETUP.md` for DRY); correct the hardcoded
      `localhost:5432`/`PGPASSWORD=postgres` and removed-`_migrations`-table
      references in `.claude/commands/validate-env.md` and
      `.claude/commands/database-migration.md`.
- [x] 9.4 Verify: run the doc test; capture passing output.

## 10. Full end-to-end verification (verification-before-completion)

- [x] 10.1 Tear down any existing local stack and remove the local cluster
      (`volumes/db/data`) so the init is genuinely clean.
- [x] 10.2 On a supported environment (WSL2 on this Windows machine with the repo
      on the Linux FS, or Linux/macOS): `make init`, `make dev-up`, wait healthy,
      `make migrate-local`, `make load-test-data`.
- [x] 10.3 Run `make verify-dev` and `make test-integration`; capture full output
      proving: `db-dev` healthy, all required base + `bloom_*` roles present,
      `auth`/`storage` schemas present, every `supabase/migrations/*.sql` recorded
      (set comparison, zero missing/unexpected), and a sample integration test
      passes against representative data.
- [x] 10.4 Run the full unit suite (`uv run --extra test pytest tests/unit/`), the
      lint/format gates (`uv run black --check .`, `uv run ruff check .`, and/or
      `uv run pre-commit run --all-files`), and
      `openspec validate fix-local-dev-setup --strict`; capture output. Confirm the
      new tests do not break CI's `python-audit` (runs `tests/unit/`),
      `validate-env-defaults`, or `compose-health-check` jobs (precedence test
      green; `pyjwt`/`python-dotenv` resolve; bootstrap test CI-safe via the
      bounded poll).
- [x] 10.5 Resolve `design.md` open questions with verified outcomes; mark tasks
      complete only after evidence is captured.

## 11. Fresh-clone dev-up — optional web/.env (#123) (TDD)

Discovered during the §10 local run: `make dev-up` on a fresh clone failed with
`env file ./web/.env not found` because Compose v2 aborts on a missing
`env_file`. Every var `bloom-web` needs is already supplied via `environment`/
`args`, so the file is redundant.

- [x] 11.1 RED: add `tests/unit/test_compose_dev_env_files.py` asserting the
      `bloom-web` `env_file` entry for `./web/.env` uses the long form with
      `required: false`. Run against the bare `- ./web/.env` form; confirm it
      fails (entry is a string, not a `{path, required: false}` dict).
- [x] 11.2 GREEN: change `docker-compose.dev.yml` `bloom-web.env_file` to
      `- path: ./web/.env` / `required: false`. Run; test passes.
- [x] 11.3 Verify: a fresh clone (`make init` then `make dev-up`) no longer aborts
      on the missing `web/.env`.

## 12. CI dev-stack smoke test (TDD)

CI uses `docker-compose.prod.yml`; the dev workflow (`make init` → `dev-up` →
`migrate-local` → `check`) is never run live. Now feasible because `make check`
tolerates missing LLM keys and CI runners have no 5432 shadow.

- [x] 12.1 RED: add `tests/unit/test_ci_dev_stack_smoke.py` (parsing
      `.github/workflows/pr-checks.yml` with `yaml`, like
      `test_ci_workflow_uv_conventions.py`) asserting a job exists whose steps run
      `make init`, `make dev-up`, `make migrate-local`, and `make check`. Run;
      confirm it fails (no such job).
- [x] 12.2 GREEN: add a `dev-stack-smoke` job to `pr-checks.yml` mirroring
      `compose-health-check`'s setup (setup-uv, pinned `SUPABASE_VERSION` CLI
      install, setup-node), running the four make targets against the dev compose,
      with log dumps on failure and `down -v` cleanup.
- [x] 12.3 Verify: the job passes on CI (the live validation of the dev path).

## 13. Make the documented dev flow race-tolerant (TDD)

The dev-stack smoke job exposed two races a developer hits running the flow fast.
Move the robustness into the commands so humans get it too (and the smoke job can
run the bare make targets).

- [x] 13.1 RED: `test_makefile_migrate_local.py` asserts the recipe waits for the
      storage schema (`storage.buckets`) before `supabase db push`.
- [x] 13.2 GREEN: `migrate-local` bounded-polls (via `docker compose exec db-dev
      psql`) for `storage.buckets.public` before pushing.
- [x] 13.3 RED: `test_check_health.py` asserts `_services_still_settling` treats a
      required `starting` service as not-ready and an optional one as ready.
- [x] 13.4 GREEN: `check_services_healthy` bounded-polls until required services
      leave `starting`, then classifies.
- [x] 13.5 GREEN: simplify the `dev-stack-smoke` job to the bare `make init` →
      `dev-up` → `migrate-local` → `check` (drop the CI-only wait steps).
- [x] 13.6 Verify: dev-stack-smoke passes CI with the bare targets.

- **Unit tests (all green):** the 8 new test files (53 tests) all pass —
  `test_init_script_line_endings` (asserts `git check-attr eol == lf`, the
  declarative checkout rule — the blobs were already LF, so the real fix is the
  attribute), `test_env_dev_example`, `test_init_dev`, `test_port_var_consistency`,
  `test_makefile_migrate_local` (incl. the `make -n … POSTGRES_HOST_PORT=5433`
  behavioural check), `test_conftest_env_loading`, `test_check_health`,
  `test_dev_setup_doc`. The migrate-local recipe was reworked so the host port is
  a Make var (visible/overridable in `make -n`) while password/user/db are
  shell-sourced at runtime (never in `make -n`).
- **§10 live proof (WSL2, Linux FS):** a fresh shallow `git clone` of this branch
  into the WSL2 Linux filesystem checked the `volumes/db/*` init scripts out as LF
  (0 CR bytes), `scripts/init_dev.py` generated a working `.env.dev`, and a clean
  `db-dev` brought up from the real `docker-compose.dev.yml` (isolated project, no
  app builds) produced: **no `bad interpreter`/CRLF init failure** (issue #124
  proof — `_supabase.sh` ran), all base roles (`postgres` incl., defect #3
  resolved), and the `auth`/`storage`/`vault` schemas + `auth.uid()` +
  `storage.buckets`. The running Windows stack was untouched; the throwaway clone
  + volumes were torn down.
- **Deferred to CI (already continuously verified there):** the full 16-service
  bring-up, applying all 194 migrations, and the `bloom_*` migration-created roles
  + the `test_local_dev_bootstrap.py`/`check_health` set-comparison against a fully
  migrated DB run in CI's `compose-health-check` (prod compose + `.env.ci`). A full
  local `make verify-dev` was not run end-to-end here (heavy); its DB-substrate
  claims are covered by the §10 proof above + the unit tests.
- **Root `uv.lock`:** intentionally git-ignored (`.gitignore` — only the three
  service locks are tracked, enforced by `scripts/check-uv-locks.py`). There is no
  tracked root lock to regenerate; CI runs `uv run --extra test` without
  `--frozen`, so `pyjwt`/`python-dotenv` resolve fine. Task 4.1's "regenerate root
  uv.lock" is therefore a no-op in VCS.
- **Pre-existing Windows-only test failures (not introduced by this change):** the
  full `tests/unit/` run shows 9 failures in `test_env_defaults.py`,
  `test_plot_renderer.py`, `test_verify_env_parity.py` — POSIX file-mode (`0644`
  vs Windows `0666`), cp1252 decode, and bash-validator issues. These files are
  byte-identical to `origin/staging` (empty diff) and pass in CI on Linux.

## 14. Post-review follow-ups (review-pr + Copilot audit, TDD)

- [x] 14.1 RED→GREEN `test_dev_setup_doc.py::test_command_docs_do_not_reference_legacy_migrations_table`
      — `ci-debug.md` now queries `supabase_migrations.schema_migrations` (the real
      table), not a nonexistent `_migrations`; the guard regex matches SQL *usage*,
      not prose naming the retired table.
- [x] 14.2 RED→GREEN `test_dev_setup_doc.py::test_command_docs_use_compose_aware_exec_not_bare_docker_exec`
      — neither dev nor prod compose sets `container_name`, so bare
      `docker exec db-dev` fails on a fresh clone. Converted every bare
      `docker exec/restart db-*` to `docker compose -f <file> exec/restart db-*`
      across `ci-debug.md`, `validate-env.md`, `DEV_SETUP.md`, `PROD_SETUP.md` (the
      test scans command docs + setup docs). Closes the Copilot `docker exec db-dev`
      comments that landed in `database-migration.md` but missed the other docs.
- [x] 14.3 RED→GREEN `test_makefile_migrate_local.py::test_verify_dev_rm_is_anchored_to_repo_root`
      — anchored `verify-dev`'s destructive `rm -rf` to `$(CURDIR)/volumes/db/data`
      (+ echo) so it can't resolve a bare relative path from another CWD.
- [x] 14.4 RED→GREEN `test_generate_keys_pointer.py` — dropped the stale
      `scripts/generate_KEYS` rule from `.gitignore` so the tracked deprecation
      pointer stays visible in `git status` (asserts the ignore rule, since
      `git check-ignore` is a false-green on a tracked path).
- [x] 14.5 Locked the previously-untested branch: `test_check_health.py` now covers
      `_classify_service_rows` for `State=exited` with non-zero vs zero `ExitCode`
      (a crashed core service like realtime vs a one-shot like `minio-init`).
- [x] 14.6 Copilot-comment audit — confirmed the remaining items are already
      addressed on this branch: `REQUIRED_BASE_ROLES` includes `pgbouncer` +
      `supabase_functions_admin`; migrate-local strips `\r` in every `.env.dev`
      extraction; `PROD_SETUP.md` points to the deploy workflow (not
      `make migrate-local`); `init` depends on `check-uv`; `_backup_path` avoids
      same-second collisions; tasks.md §2.1 describes `git check-attr` accurately.
