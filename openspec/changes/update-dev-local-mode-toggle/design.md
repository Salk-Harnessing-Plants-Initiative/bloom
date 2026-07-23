## Context

`docker-compose.dev.yml`'s `bloommcp` service env block hand-embeds three commented-out literal
YAML lines to opt into `BLOOM_STORAGE_BACKEND=local` (fully-local/offline mode). Every other var
in that block comes from `.env.dev` via `${VAR}` interpolation. #478 asks for two independent
changes: (1) move the toggle out of tracked YAML, (2) add a discoverable command to invoke it —
the issue names two possible mechanisms for (2) (compose `profiles:` or an env-prefixed
invocation) without picking one, and that choice is the one thing here worth writing down before
coding.

## Goals / Non-Goals

- Goals:
  - `BLOOM_STORAGE_BACKEND` / `BLOOM_STORAGE_LOCAL_ROOT` / `BLOOM_EXPERIMENT_LOCAL_ROOT` reach the
    `bloommcp` container the same way every other configurable var in the file does.
  - A developer can discover and toggle fully-local mode without hand-editing a tracked file.
- Non-Goals:
  - Changing what `BLOOM_STORAGE_BACKEND=local` *does* (resolution/precedence logic in
    `storage_backend.py` / `experiment_utils.py` is untouched).
  - A general-purpose "run any dev-stack variant" mechanism — this is a single-var toggle.

## Decisions

- **Decision: `dev-up-local` delegates to `dev-up` via `$(MAKE)`, it does not duplicate the
  recipe.** Rather than copying `dev-up`'s full body (the `ensure-bloommcp-data-dirs`
  prerequisite, `doctor.sh` call, npm-install check, echo lines) and swapping only the `docker
  compose up` line, `dev-up-local`'s recipe is exactly:
  ```makefile
  .PHONY: dev-up-local
  dev-up-local:
  	BLOOM_STORAGE_BACKEND=local $(MAKE) dev-up
  ```
  The exported var is inherited by the sub-make's own `docker compose up`, since Compose's
  shell-env precedence applies regardless of which Make target invokes it. This means `dev-up-local`
  can never drift out of sync with `dev-up` (new prerequisite, changed install logic, new step) —
  there is exactly one recipe body to maintain.
- **Decision: env-var passthrough, not compose `profiles:`.** Docker Compose's interpolation
  precedence is shell env > `--env-file` > `.env`, so the `BLOOM_STORAGE_BACKEND=local` prefix
  above overrides whatever (if anything) `.env.dev` has for that one var, for that one invocation,
  without writing to the file.
  - Alternatives considered: Compose `profiles:` would require restructuring the `bloommcp`
    service (either a second profile-gated service definition, duplicating ports/build/volumes, or
    marking existing service(s) with a profile list that changes which containers a bare `docker
    compose up` includes). That's a bigger, cross-cutting shape change to a compose file used by
    CI (`DOCTOR_SKIP=1 make dev-up`) and by every other dev workflow in this repo, for a payoff
    (declarative service grouping) this single-var, single-service toggle doesn't need. No compose
    file in this repo uses `profiles:` today, so adopting it here would be a new pattern introduced
    for one issue rather than reuse of an existing one. Per the project's "Simplicity First" bias
    (boring, proven patterns; avoid frameworks without clear justification), the shell-env-prefix
    recipe wins.
- **Decision: `.env.dev.example` documents the three vars as empty-by-default live lines, not
  commented-out lines.** The issue's wording suggested a "commented-out example entry," but no
  commented-out `VAR=value` line exists anywhere in `.env.dev.example` today — the file's actual
  established convention for an opt-in/disabled-by-default feature is an uncommented var left
  empty with an explanatory comment (`LOCAL_LLM_URL` / `LOCAL_LLM_MODEL`: "Local LLM (disabled in
  dev — leave empty)"). `scripts/init_dev.py`'s `render()` only substitutes `CHANGEME` on
  non-comment lines and copies everything else through verbatim — either style would survive
  `make init` unchanged — so the empty-live-line style was chosen to match the file's one existing
  precedent for this exact situation rather than invent a second convention. It also composes
  correctly with `${VAR:-}` compose interpolation: an empty env value and an absent one are
  provably equivalent to both consumers (`os.environ.get(...) or _DEFAULT_BACKEND` in
  `storage_backend.py:306`; `if explicit:` in `storage_backend.py:342` and
  `experiment_utils.py:47`), so there is no behavioral difference between "line commented out" and
  "line present, empty."

## Risks / Trade-offs

- **New risk this change introduces: shell-env leakage.** Today the three vars are literal
  YAML with no `${...}` token in `docker-compose.dev.yml`, so a developer's shell-exported
  `BLOOM_STORAGE_BACKEND=local` (e.g. promoted from a one-off `make dev-up-local` run into a
  shell profile for convenience) has zero effect on `docker compose up` — Compose only
  substitutes tokens that exist in the file. After this change, the token exists, so that same
  leftover export silently redirects *every* subsequent `make dev-up` (or bare `docker compose
  up`) into fully-local/offline mode, on a per-developer/per-shell basis. Mitigations (both
  needed — added after PR #513 review):
  - The boot-time backend-visibility print now runs BEFORE `validate_data_env()`/
    `validate_supabase_env()`, not after, so it fires even on a fail-fast boot, not just the
    happy path.
  - Because `dev-up`/`dev-up-local` both run `docker compose up -d` (detached), a container-log
    print alone is invisible without a deliberate `make dev-logs`/`docker compose logs` — so
    `dev-up-local`'s Makefile recipe also prints a foreground `@echo` banner at invocation time,
    and `.env.dev.example`'s comment now explicitly recommends the one-shot `make dev-up-local`
    form over setting the var in `.env.dev`/a shell profile, precisely because the latter has this
    silent-leakage failure mode and the former does not (it's scoped to one invocation).
  - `docker-compose.dev.yml` uses a single fixed compose project name (`bloom_v2_dev`) for the
    whole dev stack, shared with plain `dev-up` — `dev-up-local` is not an isolated instance. This
    was already true before this change (see the `development-environment` spec's "Canonical Local
    Stack Path" requirement — one dev stack per machine is intentional), so it's not a new risk
    this change introduces, but the Makefile comment and docs now call it out explicitly.
- `BLOOM_STORAGE_LOCAL_ROOT` / `BLOOM_EXPERIMENT_LOCAL_ROOT` are left unset by `make
  dev-up-local`, relying on the existing (already-documented, already-implemented) fallback to
  `BLOOM_OUTPUT_DIR` / `BLOOM_TRAITS_DIR`. If a developer wants a different local root, they set it
  in `.env.dev` directly (unaffected by this change) rather than passing it through the make
  target — acceptable since the target's job is discoverability of the common case, not a general
  config-override CLI.
- Empty-string interpolation for a var Compose then hands to the container as an explicit
  empty-string env var (rather than truly absent) is already how `LOCAL_LLM_URL` /
  `LOCAL_LLM_MODEL` behave in the `langchain-agent` service in this same compose file, so this
  isn't a new pattern for the container-boundary either — confirmed compatible above.
- `LOCAL_LLM_URL`/`LOCAL_LLM_MODEL` use bare `${VAR}` (no default) because `.env.dev.example`
  always defines them; this change deliberately uses `${VAR:-}` instead, because a developer's
  pre-existing `.env.dev` (generated before this change ships) won't yet have these three new
  keys at all — `${VAR:-}` resolves a truly-absent key to the same empty string without Compose's
  "variable is not set" stderr warning that bare `${VAR}` would print in that case.

## Migration Plan

No migration — this is additive to `docker-compose.dev.yml`/`​.env.dev.example`/`Makefile` and
does not change any running system's persisted state. Existing local `.env.dev` files without
the three new keys continue to work: an interpolated `${VAR:-}` with the key entirely absent from
`.env.dev` still resolves to the empty-string default, identical to today's "commented out"
behavior.

## Open Questions

None — the issue's two asks (correctness fix, discoverability polish) are both addressed and the
one design choice (profiles vs. env-passthrough) is resolved above.
