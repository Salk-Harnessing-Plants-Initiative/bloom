# Tasks: add-cyl-pipeline-model-window-warning

## Conventions

**TDD.** Each group opens with tests.
- A **red** test fails today for the stated reason. Run it, see it fail, implement, and see it pass. Record the red output in the commit body.
- A **guard** or **characterisation** test passes before and after the change. Each one is labelled as such.

**Commits.**
- C0 is the OpenSpec scaffold: `docs(openspec): propose add-cyl-pipeline-model-window-warning (bloom#971)`. Review edits go in `docs(openspec): revise … after review`.
- Groups 1–7 are one commit each, with test and implementation together.
- Group 8 ends with a `docs(openspec): tick …` commit.
- Group 9 runs at and after merge.

**Issue references.**
- Commit bodies, the PR title and the PR body say "Part of #971".
- None of them puts "close(s/d)", "fix(es/ed)" or "resolve(s/d)" directly before any `#<n>`. `auto-close-issues-on-staging.yml` scans PR titles and bodies.

**Style.**
- Web: match the touched files' double quotes and semicolons. Don't run Prettier or `/fix-formatting` on `web/` files.
- Python: use the repo-pinned `uvx black@26.3.1` and `ruff@0.9.9`.

**Paths.**
- Web paths are relative to `web/`; run `npx vitest run <paths>` from `web/`.
- Workflows tests run from `services/workflows/` with `uv run --frozen --extra test pytest tests/ -q`.
- Repo unit tests run from the root with `uv run --extra test pytest tests/unit/ -q`.

## 0. Setup and gates (no commit)

- [ ] 0.1 Run `npm ci` at the worktree root. Then run `uv sync --extra test` in `services/workflows/`.
- [ ] 0.2 Baselines, all green before any change:
  - `npx vitest run components/cyl-pipeline lib/cyl-pipeline app/api/cyl`
  - the workflows tests
  - `tests/unit/test_env_defaults.py tests/unit/test_video_worker_containers.py tests/unit/test_env_dev_example.py`
  - Record any Windows-only failures that come from unrelated tests.
- [ ] 0.3 **Gate (user):** the GitHub repository secrets `PROD_WANDB_API_KEY` and `STAGING_WANDB_API_KEY` exist, ideally a wandb service-account key with registry read access. Required before merge (design D7); not needed to develop.
- [ ] 0.4 Note the current state of the predict and traits re-pin in sleap-roots-pipeline (PR number, whether `argo template update` has run). This is information only; task 9.1 is the gate.

## 1. Workflows dependencies (`services/workflows/pyproject.toml`, `uv.lock`)

- [ ] 1.1 **Characterisation,** green before and after. New `tests/test_contracts_pin.py` asserts that `compute_param_hash({"species": "canola", "mode": "cylinder", "age": 2})` equals the literal it returns at a5 today. Compute that literal before the bump and paste it in. This guards the dedup preview's hash across the version change.
- [ ] 1.2 **Red,** same file. `from sleap_roots_contracts import ModelCard, Selector`, then validate a card with `selectors=[{"species": "arabidopsis", "mode": "cylinder", "age_min": 2, "age_max": 14}]`. Fails at a5: `Selector` can't be imported, and a5's `ModelCard` has flat fields.
- [ ] 1.3 Raise the floor to `sleap-roots-contracts>=0.1.0a9`, add `wandb>=0.21.3` (an open floor, matching predict's lock), run `uv lock`, and keep `scripts/check-uv-locks.py` green.
- [ ] 1.4 Run `uv export --frozen --no-hashes | uvx pip-audit@2.10.0 -r /dev/stdin` as CI does (`pr-checks.yml:240`). It must be clean; any finding goes to the user before going further.

## 2. Card listing (`services/workflows/model_cards.py`)

The tests (`tests/test_model_cards.py`) use a fake API object injected by monkeypatching `model_cards._api` (the one place that does `import wandb; return wandb.Api()`), a fake `time.monotonic`, and `monkeypatch.setenv/delenv("WANDB_API_KEY")`. An autouse fixture clears the cache.

- [ ] 2.1 **Red.** Two collections; artifacts aliased `["production"]`, `["latest"]` and `["production", "v0"]` → only the two production cards are returned. Their `registry_id` is the qualified name before `:`, plus `version` and `selectors`. `weights_checksum` is not in the output dicts. Fails: there's no module.
- [ ] 2.2 **Red.** One production artifact with flat a5-style metadata plus one valid one → one card, and a warning is logged naming the bad artifact.
- [ ] 2.3 **Red.** Every production artifact invalid → raises `ModelCatalogUnavailable`. Zero production artifacts → `[]`.
- [ ] 2.4 **Red.** `WANDB_API_KEY` unset or `"  "` → raises `ModelCatalogNotConfigured`, and `_api` is never called.
- [ ] 2.5 **Red.** Cache:
  - two calls 60 s apart → `_api` is called once;
  - a call at 301 s → called again;
  - a failing listing followed by a good one → the second call lists again, so the failure wasn't cached;
  - after expiry, a failing refresh raises rather than returning the old list.
- [ ] 2.6 **Red.** Concurrency: 5 threads call on a cold cache, with a fake `_api` that blocks on an event → `_api` is entered once, and every thread gets the same list.
- [ ] 2.7 Implement: module constants (entity, registry, alias, TTL 300), a `threading.Lock`, `(fetched_monotonic, cards, fetched_at)` state, and `list_production_cards() -> tuple[list[dict], str]`. The listing logic mirrors predict's `WandbRegistrySource.list_cards` / `_collect_cards` (design D1).

## 3. Route (`services/workflows/main.py`)

Tests go in `tests/test_main.py`, following its `dependency_overrides` and monkeypatch pattern.

- [ ] 3.1 **Red.** `GET /model-cards` with auth overridden and `model_cards.list_production_cards` faked → 200 `{"cards": [...], "fetched_at": "..."}`. Fails: 404, there's no route.
- [ ] 3.2 **Red.** `main.enforce_rate_limit` patched to raise 429 → `GET /model-cards` still answers 200, and the patch was never called.
- [ ] 3.3 **Red.** `ModelCatalogNotConfigured` → 503 with the exact not-configured text. `ModelCatalogUnavailable` or any other exception → 503 "Couldn't read the model catalog." The exception text isn't in the body.
- [ ] 3.4 **Guard.** No auth override and no bearer token → 401, and the listing fake was never called.
- [ ] 3.5 Implement the route. It's a sync `def`, so FastAPI runs the blocking wandb call in its threadpool.

## 4. Secret wiring (design D7)

- [ ] 4.1 Add `WANDB_API_KEY: ${WANDB_API_KEY}` to `docker-compose.prod.yml` `workflows.environment`. Then:
  - **Red:** `tests/unit/test_env_defaults.py::test_all_compose_vars_are_sourced` fails, because the variable has no source.
  - **Red:** `tests/unit/test_video_worker_containers.py::test_the_worker_carries_the_services_own_credentials` fails, because the workers' environment now differs from `workflows`'.
- [ ] 4.2 Add `WANDB_API_KEY` to `SENSITIVE_INVENTORY`. In the worker-parity test, drop `WANDB_API_KEY` from the `workflows` environment next to the existing `WORKFLOWS_CORS_ORIGINS` pop, with a comment: workers don't get the key. Both go green, and `test_no_overlap_with_sensitive_inventory` stays green.
- [ ] 4.3 Add `WANDB_API_KEY: ${WANDB_API_KEY:-}` to `docker-compose.dev.yml` `workflows.environment`, and a blank `WANDB_API_KEY=` with a comment to `.env.dev.example`. `tests/unit/test_env_dev_example.py` stays green.
- [ ] 4.4 In `.github/workflows/deploy.yml`, add `WANDB_API_KEY=${{ secrets.PROD_WANDB_API_KEY }}` to the prod heredoc and `…STAGING_WANDB_API_KEY` to the staging heredoc. `python scripts/verify_env_parity.py` must be green.
- [ ] 4.5 Run the whole of `tests/unit/` and confirm it's green, apart from the Windows-only failures recorded in 0.2.

## 5. Web: rule, client and proxy (`lib/cyl-pipeline/model-windows.ts`, `lib/cyl-pipeline/model-cards.ts`, `app/api/cyl/pipeline/model-cards/route.ts`)

- [ ] 5.1 **Red.** New `lib/cyl-pipeline/model-windows.test.ts`, using the live-registry fixture `__fixtures__/model-cards.ts` (the 8 production cards of 2026-09-30, reduced to their selectors):
  - `pastWindowGroups(groups, cards)` returns `{species, age, count, max}` for arabidopsis 21 and 28 (max 14), rice 18 (max 10), soybean 10 (max 8), canola 14 (max 13) and pennycress 15 (max 14).
  - It returns nothing for arabidopsis 14, rice 8, rice 3, canola 0 and sorghum 10.
  - The groups' order is kept.
  - A selector with another `mode` (multiplant cylinder) doesn't count toward cylinder's `max`.
  - Fails: there's no module.
- [ ] 5.2 **Cross-check,** same file. The five phase-1 decision cases (arabidopsis 28, rice 18, soybean 10, canola 14, pennycress 15) equal the clamp ages that predict's merged tests expect. Copy the expected values from talmolab/sleap-roots-predict `tests/test_model_selection.py` at `79939ee`, and cite the file and commit in a comment.
- [ ] 5.3 **Red.** `lib/cyl-pipeline/model-cards.test.ts`:
  - `isCardList` accepts the fixture, and rejects a non-integer `age_max`, a missing `selectors`, and a non-array `cards`.
  - `fetchModelCards()` returns the cards on 200. It returns `null` on a non-200, on a bad shape, on a rejected fetch, and when the 10 s timeout fires (fake timers). It never throws.
- [ ] 5.4 **Red.** `app/api/cyl/pipeline/model-cards/route.test.ts`, modelled on the trigger proxy's `route.test.ts`:
  - switch off → 503, and no upstream fetch;
  - no session → 401;
  - upstream 200 with a valid list → 200 pass-through, with the bearer token sent;
  - upstream 503 or a bad shape → 502 with a fixed detail;
  - timeout → 504;
  - it exports `GET` and no other method;
  - a request with no `Origin` header is served.
- [ ] 5.5 **Guard.** The existing `app/api/cyl/pipeline/route.test.ts` "exports POST and no other method" stays green, so the trigger route is untouched.
- [ ] 5.6 Implement the three modules. `no-provenance-joins.test.ts` must stay green.

## 6. Dialog (`components/cyl-pipeline/RunPipelineDialog.tsx`)

`RunPipelineDialog.test.tsx` adds `vi.mock("@/lib/cyl-pipeline/model-cards", …)` with a controllable `fetchModelCards`. It resolves to the fixture by default, can be held (deferred) or resolve to `null`, and the existing `fetchSpy` POST assertions stay unchanged.

- [ ] 6.1 **Red.** Target: 60 arabidopsis scans at day 21, 30 at day 28 and 120 at day 14.
  - `data-testid="past-window"` shows "90 scans are past their models' validated age and are predicted with the nearest models:" and the two group lines, exactly as in the spec scenario.
  - It appears after the stage-in lines and before `concurrent-runs` (check document order).
  - Confirm is enabled.
- [ ] 6.2 **Red.** Singular: 1 soybean scan at day 10 → "1 scan is past its models' validated age and is predicted with the nearest models:" plus "soybean · day 10 — models validated up to day 8 (1)".
- [ ] 6.3 **Red.** `fetchModelCards` resolves `null` → `past-window-unknown` shows "Couldn't check the models' age ranges.", there's no `role="alert"`, and Confirm is enabled.
- [ ] 6.4 **Red.** `fetchModelCards` is held while the other reads have settled → Confirm is disabled. Releasing it enables Confirm.
- [ ] 6.5 **Guard.**
  - No past-window group (the default pennycress day-14 fixture) → neither testid is present.
  - The existing banned-phrase tests (`/will run|will be skipped|reused/i`) still pass, now with a past-window target added to their cases.
  - The query-failure tests for the five tables still show the failed state.
- [ ] 6.6 Implement: start `fetchModelCards()` in the load effect, outside the `Promise.all` that drives the failed state. Store its result with the other checks, gate `canConfirm` on it having settled, and render the block per design D5.

## 7. Docs

- [ ] 7.1 `services/workflows/README.md`: document `GET /model-cards` (auth, no rate limit, cache, 503s). Add `WANDB_API_KEY` to the Provisioning and Configuration tables.
- [ ] 7.2 `PROD_SETUP.md` secrets table, the `.env.prod.defaults` / `.env.staging.defaults` header comments, and `scripts/setup-env-secrets.sh`: add `WANDB_API_KEY`, used by `workflows` only.
- [ ] 7.3 `contracts/README.md`: correct "Bloom imports neither `ModelCard` nor `Selector`". The workflows service now reads `ModelCard`/`Selector` (a9) to list production cards.

## 8. Pre-merge

- [ ] 8.1 Run `openspec validate add-cyl-pipeline-model-window-warning --strict`.
- [ ] 8.2 Run `/pre-merge`: lint, the full web and Python suites, and the self-review. Fix anything until green.
- [ ] 8.3 Open the PR against `staging` with `/pr-description`. The title and body say "Part of #971" and state the merge gate (9.1, 9.2). Then tick the tasks.

## 9. Merge gate and verification

- [ ] 9.1 **Gate.** The sleap-roots-pipeline re-pin of the predictor template (predict image from `c25ef52`) and the trait-extractor template (`sha-426ad4d`) has merged, and the user has run `argo template update`. Record the srp PR and the date.
- [ ] 9.2 **Gate.** Task 0.3's secrets exist, which the user confirms. Merge only after 9.1 and 9.2, and only by the user.
- [ ] 9.3 After the staging deploy (read-only), `GET /workflows/model-cards` with a staging session returns 8 cards matching design's Context table. Record the cold-read time from the workflows logs or a timed request. If it's above about 5 s, file the cache-warming follow-up (design Risks) as a draft for the user; don't post it.
- [ ] 9.4 On staging, open the confirm dialog for a target with a past-window group, for example an arabidopsis experiment with day-20/21 scans. Confirm the block and its counts; don't submit. Any GPU run, for example one past-window scan end to end, needs the user's yes for that specific run.
- [ ] 9.5 Archive with `/openspec:archive add-cyl-pipeline-model-window-warning` after 9.3–9.4. If `fix-cyl-pipeline-runs-ui-955` is still unarchived, either order is safe, because this change only ADDs (design D6).
