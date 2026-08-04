## Why

[#478](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/478) — enabling
bloommcp's fully-local/offline mode (`BLOOM_STORAGE_BACKEND=local`, the Claude-Desktop-offline
path from #386/PR #389 and #390/PR #405) today means hand-editing three commented-out lines
directly in the **tracked** `docker-compose.dev.yml` (the `bloommcp` service's env block):

```yaml
      # BLOOM_STORAGE_BACKEND: local
      # BLOOM_STORAGE_LOCAL_ROOT: /app/data/ANALYSIS_OUTPUT
      # BLOOM_EXPERIMENT_LOCAL_ROOT: /app/data/TRAITS_DIR
```

(The issue's own snippet quotes only the first two lines, at "lines 147-148" — `BLOOM_EXPERIMENT_LOCAL_ROOT` was added afterward by #390's `LocalReader` work. All three are still literal, tracked YAML today, so this change treats all three, not just the two the issue text quotes.)

Every user-supplied/environment-specific var in this compose file's `bloommcp` block (the
Supabase keys, `BLOOMMCP_API_KEY`, `BLOOM_PLOTS_URL`, …) is externalized via `${VAR}`
interpolation from `.env.dev` — that's the file's own convention for "how you configure this."
These three break the pattern: they're literal YAML baked into checked-in source, not a
`.env.dev.example` entry. Toggling local mode means editing a tracked file: easy to
accidentally `git add`/commit the uncomment, and easy to silently lose after a `git pull` or
stash conflict touches the file. It's also undiscoverable — `BLOOM_STORAGE_BACKEND` /
`BLOOM_STORAGE_LOCAL_ROOT` / `BLOOM_EXPERIMENT_LOCAL_ROOT` appear nowhere in `make help` or
`.env.dev.example`, and the only way to find the toggle is reading raw compose YAML comments.

## What Changes

- **Externalize the toggle.** In `docker-compose.dev.yml`'s `bloommcp` service, replace the three
  commented-out literal lines with `${VAR:-}` interpolation (empty-string default, so unset
  behaves exactly like "commented out" does today):
  ```yaml
      BLOOM_STORAGE_BACKEND: ${BLOOM_STORAGE_BACKEND:-}
      BLOOM_STORAGE_LOCAL_ROOT: ${BLOOM_STORAGE_LOCAL_ROOT:-}
      BLOOM_EXPERIMENT_LOCAL_ROOT: ${BLOOM_EXPERIMENT_LOCAL_ROOT:-}
  ```
  Confirmed safe: both consumers read with a falsy-string check (`os.environ.get(...) or
  _DEFAULT_BACKEND` in `storage_backend.py:306`; `if explicit:` in `storage_backend.py:342` and
  `experiment_utils.py:47`), so an empty-string env value is indistinguishable from unset.
- **Document the three vars in `.env.dev.example`,** disabled-by-default (empty value, not a
  commented-out line — matching this file's existing convention for an opt-in/disabled feature,
  `LOCAL_LLM_URL`/`LOCAL_LLM_MODEL`: "disabled in dev — leave empty"). `make init`
  (`scripts/init_dev.py`) copies uncommented lines through verbatim aside from `CHANGEME`
  substitution, so these stay empty in a freshly generated `.env.dev` until a developer opts in.
  The comment block SHALL cross-reference `bloommcp/docs/storage-backends.md`'s existing
  "do not mix backends for one experiment" warning, so the lower-friction entry point doesn't
  drop that caution.
- **Add `make dev-up-local`.** A discoverable entrypoint, listed in `make help` alongside
  `dev-up`/`prod-up`, that delegates to the existing `dev-up` recipe with
  `BLOOM_STORAGE_BACKEND=local` prefixed for that one invocation (`BLOOM_STORAGE_BACKEND=local
  $(MAKE) dev-up` — no duplicated recipe body, so a future change to `dev-up` can't silently drift
  out of sync here) via a shell-env override (`docker compose` interpolation precedence: shell env
  > `--env-file`) — it does not write to or mutate `.env.dev`. `BLOOM_STORAGE_LOCAL_ROOT` /
  `BLOOM_EXPERIMENT_LOCAL_ROOT` are left unset, which — per `bloommcp/docs/storage-backends.md` —
  already fall back to the mounted `BLOOM_OUTPUT_DIR`/`BLOOM_TRAITS_DIR` dev paths, so no second
  var is needed for the common dev case.
- **Boot-time backend visibility.** `bloommcp/src/bloom_mcp/server.py`'s `main()` already computes
  `fully_local = is_local_backend()` before wiring the reader/store ports; add one line printing
  which backend is active. Externalizing the toggle makes it newly overridable by a leftover
  shell-exported `BLOOM_STORAGE_BACKEND=local` (impossible before this change, since no
  interpolation token existed for it) — today there is no way to tell which backend a running
  container picked without manually `docker compose exec`-ing in and grepping its environment.
  This is observability only; it does not touch backend-selection or resolution logic.
- **Docs.** Update `bloommcp/docs/storage-backends.md`'s "To enable it in dev" instructions (it
  currently says to uncomment lines in `docker-compose.dev.yml`) to point at `.env.dev` /
  `make dev-up-local` instead.
- **CI regression coverage.** `.github/workflows/pr-checks.yml`'s `dev-stack-smoke` job already
  runs `DOCTOR_SKIP=1 make dev-up` on every relevant PR (line 981) — add one assertion step right
  after it confirming the `bloommcp` container's `BLOOM_STORAGE_BACKEND` is empty/absent, so a
  future change that accidentally makes plain `dev-up` inherit `local` mode fails CI instead of
  going unnoticed.

## Impact

- **Affected specs:**
  - `development-environment` — MODIFY `Committed Local Environment Template` (documents the
    disabled-by-default-empty-var convention this change relies on) and ADD
    `Externalized Local-Only Storage Backend Vars` and
    `Discoverable make dev-up-local Entrypoint`.
  - `bloommcp-storage-backend` — ADD `Backend Selection Boot Visibility` (the one-line boot log;
    does not modify the existing `Backend Selection via BLOOM_STORAGE_BACKEND` requirement's
    selection/fail-fast behavior).
- **Affected code:**
  - `docker-compose.dev.yml` (`bloommcp` service env block).
  - `.env.dev.example`.
  - `Makefile` (`dev-up-local` target, `help` listing).
  - `bloommcp/docs/storage-backends.md`.
  - `bloommcp/src/bloom_mcp/server.py` (`main()` — one boot-log line, no logic change).
  - `.github/workflows/pr-checks.yml` (`dev-stack-smoke` job — one regression-check step).
  - `tests/unit/test_init_dev.py` (new passthrough assertion for the three template lines).

## Scope / Non-Goals

- **Does not touch `docker-compose.prod.yml`** — the local backend is a local/dev-only opt-in;
  prod and staging never set it, and this change doesn't add prod tooling for it.
- **No compose `profiles:` mechanism introduced.** `make dev-up-local`'s recipe sets
  `BLOOM_STORAGE_BACKEND=local` as a plain shell-env prefix on the one `docker compose up`
  invocation (POSIX `VAR=value cmd`, no new Make variable, no change to `dev-up`) — boring and
  proven, versus introducing compose `profiles:`, a mechanism unused anywhere else in this
  project's compose files today. See `design.md` for the considered alternative.
- **Does not change resolution/precedence logic** in `storage_backend.py` / `experiment_utils.py`
  — only how the three env vars reach the container in dev. `BLOOM_STORAGE_LOCAL_ROOT` /
  `BLOOM_EXPERIMENT_LOCAL_ROOT` explicit-override semantics are unchanged.
- **Independent of, but a literal merge hazard with,
  [#479](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/479)** (the
  `BLOOM_LOCAL_ROOT` single-var proposal, `add-bloommcp-local-root`, branch
  `egao28/bloommcp-local-root-479`) — that proposal's own Scope section calls out #478 as
  "compatible either way," and its "What Changes" section says it will "add `BLOOM_LOCAL_ROOT`
  alongside the existing commented local-mode block." Both proposals edit the identical lines in
  `docker-compose.dev.yml`'s `bloommcp` env block, so whichever branch merges second WILL hit a
  literal git merge conflict there (not a semantic incompatibility — the two changes are
  compatible in what they do, just not textually disjoint). Whoever merges second resolves it by
  rebasing onto the first and applying the same `${VAR:-}` treatment to `BLOOM_LOCAL_ROOT` (or the
  three vars here, whichever landed first) — no separate follow-up change needed.
- **`SLEAP_OUT_CSV`/`ANALYSIS_OUTPUT` naming** was
  [#477](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/477), a separate,
  already-merged change (PR #495, staging) — not addressed here and no longer outstanding.
