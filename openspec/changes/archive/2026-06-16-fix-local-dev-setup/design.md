## Context

Bloom's local stack is the self-hosted Supabase service set in
`docker-compose.dev.yml` (`db-dev`, `auth`, `rest`, `realtime`, `storage`,
`meta`, `kong`, `studio`, `imgproxy`, `supavisor`, plus `bloom-web`,
`langchain-agent`, `bloommcp`, `supabase-minio`). Migrations live in
`supabase/migrations/` (194 files as of this branch; the count grows, so checks
must be dynamic) and are applied with the `supabase` CLI via `supabase db push`. Integration tests are pytest under `tests/integration/`,
driven by `tests/integration/conftest.py`, and run in CI by
`.github/workflows/pr-checks.yml` against the **prod** compose with a generated
`.env.ci`. Existing secret tooling already lives in `scripts/`
(`generate-secrets.sh`, `generateJWT_key.py`, `generate_KEYS`, `dev_init.ts`).
Component reference docs live in `_WIKI/` — `_WIKI/SUPABASE/README.md` documents
the four `bloom_*` roles, the JWT role hook, storage buckets, and Known Issues,
and explicitly defers getting-started to `DEV_SETUP.md`. This change keeps
getting-started in `DEV_SETUP.md` and links to the wiki rather than duplicating
it (adding the CRLF/#124 entry to the wiki's Known Issues).

This branch is rebased on current `origin/staging`, which merged the bloommcp ↔
Supabase work (PRs #278/#279): the dev stack now authenticates bloommcp and
langchain-agent as `bloom_agent` via `BLOOM_AGENT_KEY`, adds the `bloommcp-data`
storage bucket and its RLS, and the `test` extra already carries `pandas`/
`supabase`.

This change advances deploy-blocker **issue #104** and closes **issue #124**.

Three facts established by investigation drive the design:

1. **The missing-roles/schemas symptom is a CRLF bug (#124), not a vague
   bind-mount wipe.** The ten `volumes/db/*` init scripts are tracked CRLF; on a
   Windows checkout they break with `/bin/bash^M: bad interpreter`, init dies,
   and the partial data dir then makes Postgres "Skip initialization" forever.
   The earlier "drvfs permissions" hypothesis was *unverified*: the named-volume
   spike that "worked" ran the image's own LF-encoded init scripts, not the
   repo's CRLF-afflicted bind-mounted scripts, so it never tested the real cause.
2. **`auth`/`storage` schemas are created by services, not DB init.** GoTrue
   creates `auth` and `auth.uid()`; storage-api creates `storage.buckets`. They
   appear only after those services start and connect — which requires the roles
   to exist first (hence the CRLF failure cascades into auth/storage crash loops).
3. **The bind-mount works on any real Unix filesystem.** CI runs the prod compose
   with the identical `./volumes/db/data:/var/lib/postgresql/data:Z` mount on
   Linux and passes. WSL2 with the repo on the Linux filesystem is equivalent.

## Goals / Non-Goals

- **Goals**
  - A fresh clone reaches a healthy `db-dev` with all required roles and
    `auth`/`storage` schemas on macOS, Linux, and Windows-via-WSL2, via one
    documented command path.
  - Every `supabase/migrations/*.sql` applies cleanly via a documented command,
    and a health check proves the stack is correct (issue #104).
  - A committed `.env.dev.example` and a runnable, cross-platform credential
    generator with no real secrets in git (issue #104).
  - The integration suite runs locally with a single documented command.
- **Non-Goals**
  - Native-Windows (PowerShell-only, no WSL) support.
  - Switching the dev Postgres data dir to a named volume.
  - Introducing a `supabase/config.toml` / `supabase start` flow.
  - Any change to prod/staging/CI compose, deploy workflows, or RLS/schema/data.

## Decisions

### Decision 1: Fix CRLF via `.gitattributes`; Windows support via WSL2; no data-mount change

Complete the existing `.gitattributes` (it forces LF on `*.sh` but omits `*.sql`,
leaving `volumes/db/*.sql` CRLF-exposed) by adding `*.sql` and an explicit
`volumes/db/** text eol=lf`, and renormalize — the direct fix for #124, cheap and
beneficial on every platform. Document WSL2 (repo on the Linux FS) as the Windows
path per the user's decision to avoid touching working infra.

- **Alternatives considered**:
  - *Named volume for dev data dir* — diverges dev from prod/CI, forces a
    one-time local DB reset, changes `down -v` semantics. Rejected.
  - *Enable native Windows now* — with the CRLF fix this is closer, but still
    requires solving Postgres data-dir permissions on `drvfs`. Out of scope;
    revisit only if requested.

### Decision 2: Credential generation as `scripts/init_dev.py` (issue #104), reusing existing key-size logic

Per #104, the generator is Python run via `uv run --with pyjwt,python-dotenv` as
`scripts/init_dev.py`, exposed as `make init`. It **reuses the verified key-size
constraints already encoded in `scripts/generate-secrets.sh`** rather than
re-deriving them — those comments capture hard-won failures:

- `DB_ENC_KEY` = **exactly 16 ASCII bytes** (Realtime AES-128; wrong size crashes
  Realtime ~90s into startup with a cryptic Elixir error).
- `VAULT_ENC_KEY` 32 bytes, `SECRET_KEY_BASE` ≥64, `SUPAVISOR_ENC_KEY` 64 hex,
  `JWT_SECRET` ≥32. PR #1's prior bash attempt was faulted for omitting
  `DB_ENC_KEY` entirely — the generator and its unit test must assert each size.
- **JWT minting**: `ANON_KEY`, `SERVICE_ROLE_KEY`, and `BLOOM_AGENT_KEY` are JWTs
  signed with `JWT_SECRET` (consumed by `GOTRUE_JWT_SECRET`, `PGRST_JWT_SECRET`,
  `API_JWT_SECRET`; passed as `Authorization: Bearer ${ANON_KEY}` in kong; and
  `BLOOM_AGENT_KEY` carries `role: bloom_agent`, which the JWT hook
  (`supabase/migrations/20260519140000_jwt_hook_read_app_meta_data.sql`) switches
  PostgREST/storage into — current staging passes it to bloommcp/langchain-agent).
  They cannot be independent random strings. The minting pattern already exists in
  `scripts/generate_KEYS` (commented-out node) and `scripts/generateJWT_key.py`
  (PyJWT); `init_dev.py` makes it runnable in Python, mints all three role keys,
  and fixes the `generate_KEYS` stub #104 calls out.
- **Idempotency / secret hygiene**: refuses to overwrite `.env.dev` without
  `--force` (backs up to `.env.dev.backup`, which must be gitignored). The script
  must never print secrets to stdout/logs.

### Decision 3: Canonical local path = compose `db-dev` + `supabase db push --debug`, port-correct

Keep the CI-aligned flow. Fix `make migrate-local` to build the `--db-url` from
`POSTGRES_HOST_PORT`, the generated `POSTGRES_PASSWORD`, user, and db (sourced
from `.env.dev`), and add `--debug`. The hardcoded `127.0.0.1:5432/postgres` with
`POSTGRES_PASSWORD:-postgres` (`Makefile:211-213`) is a real bug: with a generated
password it authenticates wrongly, and under the documented `5433` override it
would silently migrate a *different* Postgres. `--debug` is mandatory, not
cosmetic — without it the CLI ignores `sslmode=disable` on TLS-less local Postgres
(supabase-cli #4839). Document the pinned `SUPABASE_VERSION` (CI enforces
`2.92.1`) so local CLI drift is visible.

### Decision 4: Unify on `POSTGRES_HOST_PORT`

Rename `.env.dev`/`docker-compose.dev.yml`'s `POSTGRES_EXTERNAL_PORT` to
`POSTGRES_HOST_PORT`. Note: `conftest.py` reads `POSTGRES_HOST_PORT` but only from
`.env.prod`/`.env.ci` today — tests do **not** "follow automatically" until
Decision 5 adds `.env.dev` to the loader. Default stays `5432`; the `5433`
override is documented, not defaulted (keeps parity with CI's `5432`). The
`migrate-local` fix (Decision 3) is what makes the override actually safe.

### Decision 5: Test wiring + health check + verification

- `conftest.py` loads `.env.dev` *after* `.env.prod`/`.env.ci` so CI/prod values
  always win (`_load_env(".env.prod") or _load_env(".env.ci") or
  _load_env(".env.dev")`). A precedence test guards this so the new source can't
  silently break CI.
- `scripts/check_health.py` + `make check` (issue #104): asserts every service
  with a Compose healthcheck is `healthy` (none exited non-zero), required roles
  present, `auth`/`storage` schemas present, and migration **completeness** by
  **set comparison** — every `supabase/migrations/*.sql` file is recorded in
  `supabase_migrations.schema_migrations` with none missing/unexpected, reusing the
  approach in `tests/integration/test_migrations.py::test_all_migrations_recorded`.
  A count-equality check is brittle (missing+extra rows can cancel) and a "merely
  populated" check passes on a partial run — both rejected.
- `tests/integration/test_local_dev_bootstrap.py`: asserts base roles (`postgres`,
  `anon`, `authenticated`, `service_role`, `authenticator`, `supabase_admin`,
  `supabase_auth_admin`, `supabase_storage_admin`) and application roles
  (`bloom_admin`, `bloom_user`, `bloom_writer`, `bloom_agent`), `auth` schema +
  `auth.uid()`, `storage` schema + `storage.buckets`, and migration completeness.
  This is an
  **acceptance/guard test, not RED-first** (a correct Linux/WSL2/macOS init
  already passes it). Because CI runs `tests/integration/` unconditionally and
  only waits for `storage.buckets` (not `auth.uid()`), the test uses a **bounded
  poll** for `auth.uid()` to avoid a CI timing flake.
- `make verify-dev`: clean reset → `dev-up` → `migrate-local` → `check`, in one
  shot. `make load-test-data` is included in the documented flow so locally-run
  RLS/`pg_conn` tests exercise representative data, not empty tables.

## Risks / Trade-offs

- **WSL2 friction**: cloning into `/mnt/c` reintroduces drvfs issues; even there
  the `.gitattributes` fix removes the CRLF cause. → Documented; `make check`
  fails loudly if the substrate is wrong.
- **`.gitattributes` renormalization churn**: a one-time diff touching line
  endings of `volumes/db/*`. → Logic unchanged; reviewed as endings-only.
- **`supabase` CLI absence/drift**: not installed by default; local version may
  differ from CI's pinned `2.92.1`. → `migrate-local` guards existence; docs pin
  the version; `check` warns on mismatch.
- **Generated JWT correctness**: a subtle bug makes the whole stack reject the
  keys. → Unit tests assert minted keys verify against `JWT_SECRET` with correct
  `role` claims, and each key meets its required byte size.
- **New test sources/tests breaking CI**: adding `.env.dev` loading or the
  bootstrap test could affect CI. → Precedence test + bounded poll (60s/2s via
  `pg_conn`, matching CI's `storage.buckets` poll, since CI doesn't wait for
  `auth.uid()`) + `pyjwt`/`python-dotenv` added to the `test` extra (required at
  unit-test collection) with the root `uv.lock` regenerated to match. CI's
  `unit-tests` run is the `python-audit` job; it uses `uv run --extra test`
  without `--frozen`, so it re-resolves and won't trip on lock drift — but the
  lock is regenerated anyway to avoid silent drift.
- **Test brittleness**: compose `${VAR:-default}` parsing and Makefile/doc
  scanning. → Tests specify the extraction regex, an explicit justified
  exclude-set, and resolve `make` targets against real rule definitions
  (`^target:`/`.PHONY`), not `help` echo text.

## Migration Plan

1. Land `.gitattributes` + renormalization, template, `init_dev.py`, port rename,
   `migrate-local` fix, conftest/test wiring, health check, and docs together so
   the documented path is internally consistent. Keep each section's RED+GREEN in
   a single commit so `git bisect` stays green.
2. Existing devs: on pull, re-checkout `volumes/db/*` to pick up LF
   (`git add --renormalize .`), and rename `POSTGRES_EXTERNAL_PORT` →
   `POSTGRES_HOST_PORT` in their local `.env.dev` (one line; called out in
   DEV_SETUP.md and the PR). No data loss, no stack rebuild required.
3. Remove `openspec/changes/automate-environment-setup/` in the same PR.
4. Rollback: revert the PR; no persistent state changes (compose data-mounts
   untouched, no schema migrations added).

## Open Questions

- Should `make check`/`verify-dev` run in CI as a smoke check, or stay local?
  (Leaning local; CI already verifies the prod stack. Resolve in task 9.)
- Default local credentials: freshly minted per machine (plan) vs well-known
  Supabase demo keys for zero-config sharing (documented fallback).
- Reconcile `.env.dev.example` (repo per-env convention) vs #104's generic
  `.env.example` wording — proposal uses `.env.dev.example`; confirm at approval.
