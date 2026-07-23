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
- [ ] 7.2 `make dev-up-local` brings the stack up with `BLOOM_STORAGE_BACKEND=local` in the
      running `bloommcp` container's environment (`docker compose exec bloommcp env | grep
      BLOOM_STORAGE_BACKEND`) and in its boot log (task 4.1's print line), while a plain
      `make dev-up` (no `.env.dev` overrides) shows it empty/absent — both in the container env
      and the boot log. **Not run**: this repo's shared dev machine already has a live
      `bloom_v2_dev` compose project running (3+ days uptime) under the same fixed project name;
      bringing up another stack against it here would rebuild/restart those containers. Run this
      manually once the live stack is safe to cycle.
- [ ] 7.3 Setting `BLOOM_STORAGE_BACKEND=local` directly in a scratch `.env.dev` (no
      `dev-up-local`) and running plain `make dev-up` also picks it up — proving the `.env.dev`
      path and the `make dev-up-local` path are both live, not just the latter. **Not run** — same
      live-stack collision risk as 7.2.
- [ ] 7.4 Running `make dev-up-local` does not modify `.env.dev` on disk — hash or `mtime` the
      file before and after and assert it's unchanged. **Not run** — same live-stack collision
      risk as 7.2.
- [x] 7.5 `make help` lists `dev-up-local`.
- [x] 7.6 `openspec validate update-dev-local-mode-toggle --strict` passes.
