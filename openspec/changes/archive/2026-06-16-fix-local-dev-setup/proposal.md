# Fix Local Development Setup for Reliable, Cross-Platform Reproducibility

## Why

A fresh clone of Bloom cannot reliably reach a working local Supabase stack: a
CRLF bug bricks a clean init on Windows (issue #124), there is no committed env
template or runnable local secret generator, the docs point at make targets that
do not exist, the integration tests silently skip locally, and nothing verifies
the stack came up correctly — so this change makes the local stack reproducible
and proven cross-platform (Windows via WSL2), advancing deploy-blocker **#104**,
closing **#124** and **#118**, and superseding the unimplemented
`automate-environment-setup` change (0/43).

Verified defects (mechanisms and evidence are detailed in `design.md`):

- **CRLF init scripts (#124)** — the `volumes/db/*` scripts are tracked CRLF; on a
  Windows checkout the `#!/bin/bash\r` shebang fails (`bad interpreter`), init
  dies, Postgres then "Skips initialization", and the Supabase roles + `auth`/
  `storage` schemas are never created. The existing `.gitattributes` forces LF on
  `*.sh` but has **no `*.sql` rule** (and was never renormalized).
- **No committed env template / generator (#104)** — no `.env.dev.example` (vars
  gated behind Notion); `.gitignore`'s blanket `.env*` would block a template; no
  runnable script writes a local `.env.dev`.
- **Phantom make targets** — `DEV_SETUP.md` (and `PROD_SETUP.md`) reference
  `make apply-migrations`/`drop-tables`; neither exists (the real target is
  `make migrate-local`, which `make help` mis-advertises).
- **`make migrate-local` hardcodes `:5432` and password `postgres`** — breaks with
  a generated password and, under the documented `5433` override, would migrate
  the *wrong* Postgres (data-integrity footgun); it also omits `--debug` (needed
  for `sslmode=disable`, supabase-cli #4839).
- **Tests not wired to local dev** — `conftest.py` loads `.env.prod`/`.env.ci`,
  never `.env.dev`, so DB-backed tests silently skip; no documented local command.
- **Split port config** — `.env.dev` uses `POSTGRES_EXTERNAL_PORT` while everything
  else uses `POSTGRES_HOST_PORT`; host `5432` is often shadowed (forcing `5433`).
- **No Windows path / no health check** — `DEV_SETUP.md` is bash/macOS-only (#118);
  nothing asserts roles, schemas, or migration completeness after a clean init.

**Scope decision (Windows = WSL2; fix CRLF for all; no compose surgery):** per the
user's decision, Windows developers use WSL2 (repo on the Linux FS) so the working
macOS/Linux/CI infrastructure is untouched; we additionally complete the
`.gitattributes` CRLF fix (benefits everyone). We change **no compose data-mounts**
and treat native-Windows (no-WSL) as a non-goal. Rationale and alternatives are in
`design.md` (Decision 1).

## What Changes

- **Complete `.gitattributes`** — add `*.sql` and explicit `volumes/db/**` LF
  coverage to the existing file (which only handles `*.sh`); renormalize the
  affected tracked files (closes #124).
- **Commit `.env.dev.example`** — a complete, secret-free template for the local
  stack with a comment per variable, including `BLOOM_AGENT_KEY` (the
  `bloom_agent`-role JWT bloommcp/langchain-agent now use, per current staging).
  Satisfies the dev half of #104's `.env.example` deliverable. Prod/staging
  already use committed
  `.env.prod.defaults`/`.env.staging.defaults`; we do not duplicate a
  `.env.prod.example`. Update `.gitignore` so `.env.dev.example` is tracked while
  `.env.dev`/`.env.dev.backup` stay ignored.
- **Add a cross-platform credential generator `scripts/init_dev.py` + `make
  init`** (issue #104), run via `uv run --with pyjwt,python-dotenv`. It produces a
  working `.env.dev` from the template, **reusing the verified key-size logic
  already in `scripts/generate-secrets.sh`** (notably `DB_ENC_KEY` = exactly 16
  ASCII bytes for Realtime AES-128, `VAULT_ENC_KEY` 32, `SECRET_KEY_BASE` ≥64,
  `SUPAVISOR_ENC_KEY` 64 hex, `JWT_SECRET` ≥32), and minting the three role JWTs
  the dev stack consumes — `ANON_KEY` (`anon`), `SERVICE_ROLE_KEY`
  (`service_role`), and `BLOOM_AGENT_KEY` (`bloom_agent`) — **signed by the
  generated `JWT_SECRET`** with the correct `role` claims (independent random keys
  are rejected by GoTrue/PostgREST).
  Idempotent: refuses to overwrite `.env.dev` without `--force` (which backs up to
  `.env.dev.backup` first). Fix/retire the non-runnable `scripts/generate_KEYS`
  stub referenced by #104.
- **Fix `make migrate-local`** — parameterize the `--db-url` to honor
  `POSTGRES_HOST_PORT`, the generated `POSTGRES_PASSWORD`, user, and db (sourced
  from `.env.dev`), and add `--debug` to mirror CI.
- **Unify the host Postgres port** — rename `.env.dev`/`docker-compose.dev.yml`'s
  `POSTGRES_EXTERNAL_PORT` to `POSTGRES_HOST_PORT` (one name across `.env.dev`,
  compose, `conftest.py`, CI). Keep the `5432` default; document the `5433`
  override for the WSL-Postgres conflict.
- **Wire integration tests to local dev** — `conftest.py` also loads `.env.dev`
  when present (preserving `.env.prod`/`.env.ci` precedence so CI/prod still win),
  and a `make test-integration` target wraps `uv run --extra test pytest
  tests/integration/`. Append `pyjwt` and `python-dotenv` to the `test` extra
  (which already carries `pandas`/`supabase` from current staging) so the
  generator's unit test can verify minted JWTs at collection time.
- **Add a health check `scripts/check_health.py` + `make check`** (issue #104):
  verifies all expected services are up/healthy; the required base roles
  (`postgres`, `anon`, `authenticated`, `service_role`, `authenticator`,
  `supabase_*_admin`) **and the application roles** (`bloom_admin`, `bloom_user`,
  `bloom_writer`, `bloom_agent`) exist; the `auth`/`storage` schemas exist; and
  **all** migrations are applied (applied count equals `supabase/migrations/*.sql`
  count / zero pending, mirroring CI's `supabase migration list`). A
  `make verify-dev` target performs a clean reset → up → migrate → `check` in one
  shot.
- **Add a clean-init verification test** `tests/integration/test_local_dev_bootstrap.py`
  asserting the required roles, `auth` schema + `auth.uid()`, `storage` schema +
  `storage.buckets`, and migration completeness — written to be CI-safe (bounded
  wait; passes on a correct init, fails loudly on a broken one).
- **Fix the setup docs** — rewrite `DEV_SETUP.md`: remove phantom targets, fix
  `make help`, document the canonical path (`make init` → `make dev-up` →
  `make migrate-local` → `make load-test-data` → `make test-integration` →
  `make check`/`verify-dev`), add the env keys incl. `BLOOM_AGENT_KEY`, add a
  **WSL2 section** (clone into the Linux FS, not `/mnt/c`; `supabase` CLI install
  per platform; pinned `SUPABASE_VERSION` to avoid CLI drift), document the
  `POSTGRES_HOST_PORT` override, and list `make` as a Windows prerequisite (#118).
  Also fix the **same `apply-migrations` phantom target in `PROD_SETUP.md`**, drop
  the hardcoded `localhost:5432` and stale command list in `README.md` (point it
  at `DEV_SETUP.md` for DRY), and correct the hardcoded `5432`/`PGPASSWORD=postgres`
  and removed-`_migrations`-table references in `.claude/commands/validate-env.md`
  and `.claude/commands/database-migration.md`. Link to `_WIKI/SUPABASE/README.md`
  for the roles/RLS picture rather than duplicating it (the wiki defers
  getting-started to `DEV_SETUP.md`), and add the CRLF/#124 note to the wiki's
  Known Issues. A doc-lint test covers `DEV_SETUP.md`, `PROD_SETUP.md`, and
  `README.md` for phantom make targets.
- **Document compose `db-dev` as the one canonical local path** — no
  `supabase/config.toml`/`supabase start` flow is introduced.
- **Close `automate-environment-setup`** — remove the superseded change directory.

## Impact

- **Affected specs**: `development-environment` (new capability; this change
  introduces it, superseding `automate-environment-setup`).
- **Affected code / files**:
  - New: `.env.dev.example`, `scripts/init_dev.py`,
    `scripts/check_health.py`, `tests/integration/test_local_dev_bootstrap.py`,
    `tests/unit/test_init_dev.py`, `tests/unit/test_env_dev_example.py`,
    `tests/unit/test_init_script_line_endings.py`,
    `tests/unit/test_port_var_consistency.py`,
    `tests/unit/test_conftest_env_loading.py`,
    `tests/unit/test_makefile_migrate_local.py`,
    `tests/unit/test_dev_setup_doc.py`, `Makefile` targets `init`,
    `test-integration`, `check`, `verify-dev`.
  - Modified: `.gitattributes` (add `*.sql`/`volumes/db/**` LF rules),
    `volumes/db/*` (line-ending renormalization only — no logic change),
    `.env.dev` (port var rename; local only, gitignored),
    `docker-compose.dev.yml` (port var rename only — **no data-mount change**),
    `Makefile` (`migrate-local` db-url + `--debug`; `help` text),
    `tests/integration/conftest.py` (load `.env.dev`), `pyproject.toml` (`test`
    extra: append `pyjwt`, `python-dotenv`) + root `uv.lock` regen, `DEV_SETUP.md`,
    `PROD_SETUP.md` (phantom target), `README.md` (port + command list → point to
    `DEV_SETUP.md`), `.claude/commands/validate-env.md` +
    `.claude/commands/database-migration.md` (stale port/password/`_migrations`),
    `_WIKI/SUPABASE/README.md` (Known Issues: CRLF/#124), `.gitignore`,
    `scripts/generate_KEYS` (fix/retire).
  - Removed: `openspec/changes/automate-environment-setup/` (superseded).
- **Breaking changes**: None for macOS/Linux/CI (CI uses prod compose + `.env.ci`
  and never references the renamed dev var or `make migrate-local`; the
  `volumes/db/*` LF renormalization only changes line endings — already LF on
  Linux, so it is inert in CI). The only behavioral change is the
  `POSTGRES_EXTERNAL_PORT` → `POSTGRES_HOST_PORT` rename in local `.env.dev`,
  handled by updating the template and docs together; the `.gitattributes`
  renormalization changes only line endings of init scripts.
- **Related issues**: closes #124; advances/closes #104; addresses #118
  (Windows `make` prerequisite). Supersedes OpenSpec `automate-environment-setup`.
- **Dependencies**: `uv` (already required); `pyjwt`, `python-dotenv` (added to
  `test` extra; `pyjwt` already used by `scripts/generateJWT_key.py`); `supabase`
  CLI (documented install; pinned `SUPABASE_VERSION` mirrored from CI).
- **Non-goals**: native-Windows (PowerShell-only, no WSL) support; switching the
  Postgres data dir to a named volume; any change to prod/staging/CI compose or
  deploy workflows.
