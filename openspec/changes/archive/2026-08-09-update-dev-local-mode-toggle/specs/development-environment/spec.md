## MODIFIED Requirements

### Requirement: Committed Local Environment Template

The repository SHALL contain a committed `.env.dev.example` template that
contains every variable required by the local stack, with an explanatory comment
per variable and no real secret values. `.gitignore` SHALL exclude `.env.dev` and
`.env.dev.backup` while keeping `.env.dev.example` tracked. An opt-in feature that
is disabled by default (e.g. a backend toggle) SHALL be represented as an
uncommented variable left empty, with a comment explaining what setting it does and
that it is off by default — not as a commented-out `VAR=value` line — so
`scripts/init_dev.py` copies it through to a freshly generated `.env.dev` in the
same (inert) state, ready to be filled in without un-commenting anything.

#### Scenario: Template is complete and secret-free

- **WHEN** `.env.dev.example` is inspected
- **THEN** it lists every variable `docker-compose.dev.yml` requires to start the
  stack (excluding variables compose supplies via `${VAR:-default}` defaults),
  every value is a placeholder (not a real secret), and each variable has a
  comment describing its purpose

#### Scenario: Real env files cannot be committed

- **WHEN** a developer runs `git status` after creating `.env.dev` and
  `.env.dev.backup`
- **THEN** `.env.dev` and `.env.dev.backup` are ignored by git and
  `.env.dev.example` is tracked

#### Scenario: Opt-in backend toggle documented empty, not commented out

- **WHEN** `.env.dev.example` documents `BLOOM_STORAGE_BACKEND`,
  `BLOOM_STORAGE_LOCAL_ROOT`, and `BLOOM_EXPERIMENT_LOCAL_ROOT`
- **THEN** each appears as an uncommented `VAR=` line with an empty value and an
  explanatory comment (mirroring the existing `LOCAL_LLM_URL`/`LOCAL_LLM_MODEL`
  "disabled — leave empty" convention), and a freshly generated `.env.dev` from
  `make init` carries them through still empty, requiring no un-commenting to
  discover or opt into

## ADDED Requirements

### Requirement: Externalized Local-Only Storage Backend Vars

The `bloommcp` service in `docker-compose.dev.yml` SHALL source
`BLOOM_STORAGE_BACKEND`, `BLOOM_STORAGE_LOCAL_ROOT`, and
`BLOOM_EXPERIMENT_LOCAL_ROOT` via `${VAR:-}` interpolation from the active env
file — not as literal, commented-out YAML — so enabling bloommcp's fully-local
(offline) mode never requires editing a tracked file.

#### Scenario: Toggling local mode requires no tracked-file edit

- **WHEN** a developer wants to run bloommcp in fully-local (offline) mode in dev
- **THEN** they set `BLOOM_STORAGE_BACKEND=local` (and optionally
  `BLOOM_STORAGE_LOCAL_ROOT` / `BLOOM_EXPERIMENT_LOCAL_ROOT`) in their own
  `.env.dev` — this does not require editing `docker-compose.dev.yml`

#### Scenario: Unset stays inert, matching today's default

- **WHEN** `BLOOM_STORAGE_BACKEND` is unset in both `.env.dev` and the shell
  environment
- **THEN** `${BLOOM_STORAGE_BACKEND:-}` resolves to an empty string, the
  `bloommcp` container sees no meaningful value for the var, and the server boots
  in the default Supabase-backed mode exactly as it does today with the line
  commented out

#### Scenario: Pre-set backend is announced at `dev-up` invocation time

- **WHEN** a developer runs plain `make dev-up` with `BLOOM_STORAGE_BACKEND`
  resolving non-empty from either the shell environment or `.env.dev`
- **THEN** a foreground NOTE is printed before the doctor preflight/build steps,
  naming the resolved value and pointing at `make dev-up-local` or unsetting the
  var to restore the default — so externalizing the toggle (making it newly
  overridable by a stray shell export or a forgotten `.env.dev` value) doesn't
  silently redirect a plain `dev-up` with the only cue buried in detached
  container logs

### Requirement: Discoverable `make dev-up-local` Entrypoint

The project SHALL provide a `make dev-up-local` target, listed in `make help`,
that starts the dev stack with `BLOOM_STORAGE_BACKEND=local` for that invocation
without persisting the change to `.env.dev`, by delegating to the existing
`dev-up` target rather than duplicating its recipe.

#### Scenario: `make dev-up-local` is discoverable and scoped to one invocation

- **WHEN** a developer runs `make help`
- **THEN** `dev-up-local` is listed alongside `dev-up`/`prod-up`
- **WHEN** a developer runs `make dev-up-local`
- **THEN** the `bloommcp` container boots with `BLOOM_STORAGE_BACKEND=local` in
  its environment, and the developer's `.env.dev` file on disk is unchanged

#### Scenario: Plain `dev-up` is unaffected

- **WHEN** a developer runs plain `make dev-up` (not `dev-up-local`)
- **THEN** `BLOOM_STORAGE_BACKEND` is empty/absent in the `bloommcp` container,
  regardless of any prior `make dev-up-local` invocation on the same machine
