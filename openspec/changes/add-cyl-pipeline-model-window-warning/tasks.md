# Tasks: add-cyl-pipeline-model-window-warning

## Conventions

**TDD.** Each group opens with tests.
- A **red** test fails today for the stated reason. Run it, see it fail, implement, see it pass. Record the red output in the commit body.
- A **guard** test passes before and after; it's labelled as such.

**Commits.** One commit per group 1–6, with its tests, implementation and the docs that describe it.

| # | Message | Group |
|---|---|---|
| C0 | `docs(openspec): propose add-cyl-pipeline-model-window-warning (bloom#971)` (exists) | — |
| C0r | `docs(openspec): revise add-cyl-pipeline-model-window-warning after review` (rounds 1 and 2) | — |
| C1 | `build(workflows): require sleap-roots-contracts 0.1.0a9 and add wandb` | 1 |
| C2 | `feat(workflows): list production model cards from the wandb registry` | 2 |
| C3 | `feat(workflows): serve GET /model-cards outside the shared rate limit` | 3 |
| C4 | `feat(deploy): give only the workflows service a WANDB_API_KEY` | 4 |
| C5a | `feat(web): classify pipeline parameter groups against the model windows` | 5 (5.1–5.2) |
| C5b | `feat(web): proxy the workflows model-card list` | 5 (5.3–5.6) |
| C6 | `feat(web): warn about past-window and no-model scans in the confirm dialog` | 6 |
| C7 | `docs(openspec): tick add-cyl-pipeline-model-window-warning tasks` | 7 |

**Push and PR.**
- After C1, push and open a **draft** PR against `staging`, for early pip-audit and Trivy feedback.
- **Never amend or rewrite a pushed commit.** A fix found later becomes a new commit; the PR is squash-merged.
- Tick tasks (C7) before the last push.

**Issue references.**
- Commit bodies, the PR title and the PR body say "Part of #971".
- None of them puts "close(s/d)", "fix(es/ed)" or "resolve(s/d)" directly before any `#<n>`, including in pasted red output and in /pr-description's Related list.

**Style.**
- Web: match the touched files' double quotes and semicolons. Don't run Prettier or `/fix-formatting` on `web/`. The pre-commit hooks aren't installed in this worktree; don't install them, and never skip hooks.
- Python: use the repo-pinned `uvx black@26.3.1` and `uvx ruff@0.9.9`.

**Paths.**
- Web paths are relative to `web/`; run `npx vitest run <paths>` from `web/`.
- Workflows tests run from `services/workflows/` with `uv run --frozen --extra test pytest tests/ -q`.
- Repo unit tests run from the root with `uv run --extra test pytest tests/unit/ -q`.

**Key handling.** Never put the wandb key on a command line, in a file in the repo, or in a log. Use `-e WANDB_API_KEY` with no value, so Docker takes it from the host environment.

## 0. Setup and gates (no commit)

- [ ] 0.1 Run `npm ci` at the worktree root, then `uv sync --extra test` in `services/workflows/`.
- [ ] 0.2 Baselines, all green before any change:
  - `npx vitest run components/cyl-pipeline lib/cyl-pipeline app/api/cyl`
  - the workflows tests
  - `tests/unit/`
  - Record any Windows-only failures that come from unrelated tests.
- [ ] 0.3 **Gate (user):** the GitHub secrets `PROD_WANDB_API_KEY` and `STAGING_WANDB_API_KEY` exist, ideally a service-account key with registry read access. They're needed before merge (design D7).
- [x] 0.4 Egress: from `bloom_v2_staging-workflows-1`, `https://api.wandb.ai` answered HTTP 404 at the API root on 2026-10-02 (user go-ahead). The prod container runs on the same host.

## 1. Workflows dependencies and image (C1)

- [ ] 1.1 **Characterisation,** green before and after. New `tests/test_contracts_pin.py` asserts that `compute_param_hash({"species": "canola", "mode": "cylinder", "age": 2})` equals the literal it returns at a5, computed before the bump.
- [ ] 1.2 **Red,** same file. Inside the test function: `from sleap_roots_contracts import ModelCard, Selector`, then validate a card with `selectors=[{"species": "arabidopsis", "mode": "cylinder", "age_min": 2, "age_max": 14}]`. Fails at a5, where there's no `Selector` (it was added in a8). The import stays inside the function so 1.1 still collects.
- [ ] 1.3 Raise the floor to `sleap-roots-contracts>=0.1.0a9`, add `wandb>=0.21.3`, run `uv lock --upgrade-package sleap-roots-contracts`, review the lock diff for unrelated bumps, and keep `python scripts/check-uv-locks.py` green.
- [ ] 1.4 Run `uv export --frozen --no-hashes | uvx pip-audit@2.10.0 -r /dev/stdin`, as CI does. Report any finding to the user before going further.
- [ ] 1.5 **Red.** New `tests/unit/test_workflows_dockerfile_shape.py`, modelled on `test_bloommcp_dockerfile_shape.py`. It asserts:
  - an `ENV` sets `WANDB_CONFIG_DIR`, `WANDB_CACHE_DIR`, `WANDB_DATA_DIR` and `WANDB_DIR` under `/tmp`, plus `WANDB_SILENT=true` and `WANDB_ERROR_REPORTING=false`;
  - the `RUN` that contains `uv pip install` also contains `rm -r` of wandb's `bin` directory (located via `importlib.util.find_spec`, never `import wandb`), with no `-f`;
  - that `RUN` comes before `USER bloom`.
  - Fails: none of these exist.
- [ ] 1.6 Edit the Dockerfile per design D1b, and 1.5 goes green.
- [ ] 1.7 **Container smoke test, before committing C1.**
  - Build: `docker build -t workflows:local services/workflows`.
  - Run: `docker run --rm --read-only --tmpfs /tmp:mode=1777,size=512m --cap-drop ALL --security-opt no-new-privileges -e WANDB_API_KEY -e WANDB_SILENT=false workflows:local python -c "<inline listing>"`. The inline script mirrors design D1: it lists collections, keeps `production`, and validates each as `ModelCard`.
  - It prints `8`, and its stderr is empty.
  - **If it fails because `bin/` is missing,** drop the removal from 1.6, adjust 1.5, and record why in the commit body.
  - After C2, re-run it with `python -m model_cards_fetch` (task 7.4).
- [ ] 1.8 Trivy: `trivy image --severity CRITICAL --exit-code 1 --ignorefile .trivyignore workflows:local`. Report any finding to the user, and never add a `.trivyignore` entry without their sign-off (it would need its own PR).

## 2. Card listing: `model_cards_fetch.py` (child) and `model_cards.py` (parent) (C2)

**Child tests** go in `tests/test_model_cards_fetch.py`. They run `model_cards_fetch.main()` in-process, with a fake `wandb` module installed via `monkeypatch.setitem(sys.modules, "wandb", fake)`, and capture stdout and stderr with `capsys`.

- [ ] 2.1 **Red.** Two collections, with artifacts aliased `["production"]`, `["latest"]` and `["production", "v0"]` → exit 0, and stdout JSON `{"cards": [...]}` with the two production cards.
  - Assert `fake.Api` was called with `api_key=<env value>` and `timeout=5`.
  - Assert `artifact_collections(project_name="eberrigan-salk-institute-for-biological-studies-org/wandb-registry-sleap-roots-models", type_name="model")` and `artifacts(type_name="model", name=f"{project}/{collection.name}")`.
  - Each card has exactly `{root_type, registry_id, version, selectors}`, each selector exactly `{species, mode, age_min, age_max}`, and `registry_id` is the qualified name before `:`.
  - No artifact's `download` or `files` is called.
  - Fails: there's no module.
- [ ] 2.2 **Red.** One flat-metadata production artifact plus one valid one → one card, and stderr names the bad artifact.
- [ ] 2.3 **Red.** All invalid → exit 2, with no stdout JSON. Zero production artifacts → exit 0 and `{"cards": []}`. `fake.Api` raising → exit 1, with the message on stderr.

**Parent tests** go in `tests/test_model_cards.py`:
- the child is replaced through the seam `model_cards._run_child(env) -> CompletedProcess`, which may raise `subprocess.TimeoutExpired`;
- the clock through `model_cards._monotonic` and `model_cards._utcnow`;
- the key through `monkeypatch.setenv`/`delenv`;
- an autouse fixture clears the cache.

- [ ] 2.4 **Red.** Configuration:
  - `WANDB_API_KEY` unset or `"  "` → `ModelCatalogNotConfigured`, and `_run_child` isn't called.
  - With `" key\n"`, the child's env carries the value unchanged, plus `WANDB_HTTP_TIMEOUT="5"`.
  - The real `_run_child` calls `subprocess.run` with `[sys.executable, "-m", "model_cards_fetch"]`, `timeout=20` and `capture_output=True`; assert by patching `model_cards.subprocess.run`.
- [ ] 2.5 **Red.** Child results:
  - exit 0 with valid JSON → `(cards, fetched_at)`, with `fetched_at` UTC ISO-8601;
  - exit 1 or 2 with stderr `secret-detail` → `ModelCatalogUnavailable`, and `caplog` contains `secret-detail`;
  - unparseable stdout → `ModelCatalogUnavailable`;
  - `TimeoutExpired` → `ModelCatalogUnavailable`.
- [ ] 2.6 **Red.** Real kill: patch `model_cards.CHILD_COMMAND` to `[sys.executable, "-c", "import time; time.sleep(30)"]` and `model_cards.CHILD_TIMEOUT_SECONDS` to `1` → `ModelCatalogUnavailable` within 5 s of wall clock. Also assert `CHILD_TIMEOUT_SECONDS == 20` in an unpatched test.
- [ ] 2.7 **Red.** Cache:
  - two calls 60 s apart → one child run, with the same `fetched_at`;
  - at exactly 300.0 s → a second run, with a new `fetched_at`;
  - a failure followed by success → listed again;
  - at 301 s with a failing refresh → raises, and doesn't return the old list.
- [ ] 2.8 **Red.** Single flight:
  - 5 threads start behind a `threading.Barrier(5)` on a cold cache. The fake child sets an `entered` event, the main thread waits for it, sleeps `0.2` so the other four queue on the lock, then releases.
  - Exactly 1 child run, and all threads get the same cards.
  - Every `join(timeout=5)` is followed by `assert not t.is_alive()`.
- [ ] 2.9 **Red.** Bounded wait:
  - assert `LOCK_WAIT_SECONDS == 6`;
  - with it patched to `0.2` and a leader blocked in the child, a second caller raises `ModelCatalogUnavailable` in under 2 s of wall clock;
  - after the leader is released and succeeds, a third caller is served from the cache without a new run;
  - release and join every thread.
- [ ] 2.10 **Red.** `warm()`:
  - key unset → no child run, no exception;
  - child failure → no exception, a log line, and the cache stays empty;
  - success → the cache is filled, and the next call doesn't run the child.
- [ ] 2.11 **Red.** `subprocess.run([sys.executable, "-c", "import main, model_cards, sys; assert 'wandb' not in sys.modules"], cwd=<service dir>)` exits 0.
- [ ] 2.12 Implement `model_cards_fetch.py` and `model_cards.py` per design D1 and D3.
- [ ] 2.13 Docs in the same commit: in `contracts/README.md`, the line "Bloom imports neither `ModelCard` nor `Selector`" becomes "Bloom's `services/workflows` validates production model cards as `ModelCard` (with `Selector`) to serve `GET /model-cards`; it needs `>=0.1.0a9`, because the `selectors` shape arrived in a8."

## 3. Route and startup warm-up, `services/workflows/main.py` (C3)

Tests go in `tests/test_main.py`, following its `dependency_overrides` and monkeypatch pattern. The route calls `model_cards.list_production_cards` through the module attribute, so patching works.

- [ ] 3.1 **Red.** `GET /model-cards`, with auth overridden and the listing faked → 200 `{"cards": cards, "fetched_at": ts}`, with exactly those keys. An empty list → 200 `{"cards": [], "fetched_at": ts}`. Fails: 404.
- [ ] 3.2 **Red.** Rate limit:
  - with the real `enforce_rate_limit` and the override returning `"user-1"`, 6 calls → all 200, and `auth._hits.get("user-1", [])` is empty;
  - with `main.enforce_rate_limit` patched to record calls, it's never called.
- [ ] 3.3 **Red.** Errors:
  - `ModelCatalogNotConfigured` → 503 with the exact not-configured text;
  - `ModelCatalogUnavailable`, or `RuntimeError("secret-detail")` → 503 "Couldn't read the model catalog.";
  - the body doesn't contain `secret-detail`, and `caplog` does.
- [ ] 3.4 **Red.** Auth:
  - an override raising `HTTPException(401)` → 401, and the listing isn't called;
  - with `auth.SUPABASE_URL` and `auth.SUPABASE_ANON_KEY` patched and no override, a request without `Authorization` → 401.
- [ ] 3.5 **Red.** `assert not inspect.iscoroutinefunction(main.model_cards_route)`.
- [ ] 3.6 **Red.** Lifespan: with `model_cards.warm` patched to set an event, `with TestClient(main.app):` → the event is set within 2 s, and `/health` answers 200 while `warm` is still blocked.
- [ ] 3.7 Implement the route (a sync `def`) and the `lifespan`, which starts `model_cards.warm` in a daemon thread.
- [ ] 3.8 Docs in the same commit, in `services/workflows/README.md`:
  - an Endpoints table row and a short section: auth, no rate limit, 300 s cache, startup warm-up, 6 s wait, 20 s child limit, the two 503 texts;
  - correct Auth model Layer 1 and the `WORKFLOWS_RATE_LIMIT` row ("every application route") to name the two exempt routes.

## 4. Secret wiring (C4)

- [ ] 4.1 **Red.** New `tests/unit/test_wandb_key_scope.py`:
  - prod compose `services.workflows.environment["WANDB_API_KEY"] == "${WANDB_API_KEY}"`; dev compose's is `"${WANDB_API_KEY:-}"`;
  - no other service in either compose file has the key;
  - the `deploy.yml` prod heredoc contains `WANDB_API_KEY=${{ secrets.PROD_WANDB_API_KEY }}`, and the staging heredoc the `STAGING_` line.
- [ ] 4.2 Add the two compose entries. Observe red in `test_env_defaults.py::test_all_compose_vars_are_sourced` and in `test_video_worker_containers.py::test_the_worker_carries_the_services_own_credentials`, for prod and dev.
- [ ] 4.3 Add `WANDB_API_KEY` to `SENSITIVE_INVENTORY`. Pop it from the `workflows` side of the worker-parity test, next to `WORKFLOWS_CORS_ORIGINS` ("workers don't get the key"). Add the blank `WANDB_API_KEY=` with a comment to `.env.dev.example`. `test_env_dev_example.py` and `test_no_overlap_with_sensitive_inventory` stay green.
- [ ] 4.4 Add the two `deploy.yml` heredoc lines. `python scripts/verify_env_parity.py .github/workflows/deploy.yml` and 4.1 go green.
- [ ] 4.5 Docs in the same commit:
  - `services/workflows/README.md`: Provisioning step 7 (the deploy secrets; ideally a service-account key; only `workflows` gets it), a Configuration row, and the Auth model Layer 2 correction ("no privileged Supabase credential", plus `WANDB_API_KEY` for `GET /model-cards`).
  - `DEV_SETUP.md` "optional keys": `WANDB_API_KEY` shows the dialog's model warnings locally; without it the dialog shows "Couldn't check the models' age ranges."
  - `.env.prod.defaults` header, `scripts/setup-env-secrets.sh` comment and `PROD_SETUP.md` secrets table: replace the hand-kept lists with a pointer to the two `deploy.yml` heredocs (kept in sync by `scripts/verify_env_parity.py`) and each service's README. `.env.staging.defaults` isn't touched.
- [ ] 4.6 `tests/unit/` is green, apart from the Windows-only failures recorded in 0.2.

## 5. Web: classification, client and proxy (C5a, C5b)

- [ ] 5.1 **Red (C5a).** New `lib/cyl-pipeline/model-windows.test.ts`, using `lib/cyl-pipeline/__fixtures__/model-cards.ts` (the 8 production cards of 2026-09-30, selectors only). `classifyModelGroups(groups, cards)` returns `{pastWindow: [...], noModel: [...]}` in the groups' order:
  - **past its window** (with `max`): arabidopsis 21 and 28 (max 14), rice 18 (max 10), soybean 10 (max 8), canola 14 (max 13), pennycress 15 (max 14);
  - **no model:** sorghum 10, canola 0, soybean 1;
  - **in neither list:** arabidopsis 14 (equal to the max), rice 8, rice 3;
  - with the synthetic canola·cylinder 2–13 plus canola·multiplant cylinder 2–20, day 15 → past its window with max 13;
  - with disjoint canola windows 2–5 and 10–13, day 7 → no model;
  - a species with cards only in multiplant cylinder → no model for cylinder;
  - empty cards → everything no model (the dialog never calls it with an empty list); zero groups → both lists empty.
  - Fails: there's no module.
- [ ] 5.2 **Cross-check (C5a; a hand-copied snapshot that won't detect later drift),** same file:
  - the five past-window cases equal talmolab/sleap-roots-predict `tests/test_model_selection.py` `_PAST_WINDOW` at `79939ee`: 28→14, 18→10, 10→8, 14→13, 15→14;
  - the fixture's selectors equal `tests/card_builders.py` `_PRODUCTION` at `79939ee`.
  - Cite both in a comment.
- [ ] 5.3 **Red (C5b).** `lib/cyl-pipeline/model-cards.test.ts`.
  - `isCardList` accepts the fixture and rejects each of: a non-integer or boolean `age_max`, `NaN`, a `null` card, a selector missing `species`, a missing `selectors`, a non-array `cards`, and a missing `fetched_at`.
  - `fetchModelCards()` returns:
    - the cards on 200;
    - `null` on non-200, a bad shape, or a rejected fetch;
    - `null` from a fetch that settles only when `init.signal` aborts (rejecting with the signal's reason) once `advanceTimersByTimeAsync(10_000)` passes, while it's still pending at 9_999;
    - `null` from a 200 whose `json()` never settles, after 10 s, because the body read races the signal;
    - and after a successful 200, `vi.getTimerCount() === 0`.
  - It never throws.
- [ ] 5.4 **Red (C5b).** `app/api/cyl/pipeline/model-cards/route.test.ts`:
  - switch off → 503, and neither `getSession` nor fetch is called;
  - no session → 401, and no fetch;
  - upstream 200 with a valid list, on a request with no `Origin` header → 200 pass-through, with the bearer token sent and `init.signal` an `AbortSignal`;
  - `WORKFLOWS_URL` when set, else `http://workflows:5100`;
  - upstream 503 with detail `upstream-text`, a 3xx, a non-JSON 200, a bad shape, or a `TypeError` rejection → 502, and no body contains `upstream-text`;
  - a `DOMException("t", "TimeoutError")` rejection → 504;
  - exports `GET` only, with `dynamic = "force-dynamic"` and `runtime = "nodejs"`.
- [ ] 5.5 **Guard.** `app/api/cyl/pipeline/route.test.ts` "exports POST and no other method" stays green.
- [ ] 5.6 Implement:
  - `model-windows.ts` in C5a;
  - `model-cards.ts` (client-safe: `isCardList`, and `fetchModelCards` with `AbortController` + `setTimeout`), `model-cards-proxy.ts` (server-only, `AbortSignal.timeout(8_000)`) and the route in C5b.
  - `no-provenance-joins.test.ts` stays green.

## 6. Dialog, `components/cyl-pipeline/RunPipelineDialog.tsx` (C6)

`RunPipelineDialog.test.tsx` adds `vi.mock("@/lib/cyl-pipeline/model-cards", …)`, with the controllable `fetchModelCards` created through `vi.hoisted`:
- it resolves to the fixture by default, and can be held, or resolve to `null` or `[]`;
- the existing `fetchSpy` POST assertions are unchanged;
- existing tests that use pennycress day 21 will now also render the past-window block, and stay green because they assert with `toContain` or `within(params)`.

- [ ] 6.1 **Red.**
  - **Target:** 60 arabidopsis scans at day 21, 30 at day 28 and 120 at day 14, all with images, plus one stage-in-problem scan, one no-images scan and one concurrent run.
  - **Text:** `past-window` shows the spec's heading and two lines exactly, day 21 before day 28, with no day-14 line.
  - **Position:** document order is stage-in line, no-images line, `past-window`, `concurrent-runs`.
  - **Confirm:** enabled.
- [ ] 6.2 **Red.** Order: 30 at day 21 and 60 at day 28 → the day-28 line comes first.
- [ ] 6.3 **Red,** as `it.each`:
  - singular soybean day 10 (1) → "1 scan is past its models' validated age and is predicted with the nearest models:" and "soybean · day 10 — models validated up to day 8 (1)";
  - rice day 18 (5) → "5 scans are…" and "rice · day 18 — models validated up to day 10 (5)".
- [ ] 6.4 **Red.** No model:
  - 100 canola scans at day 7 and 12 at day 0 → `no-model` shows "12 scans have no production model for their species and age and will fail:" and "canola · day 0 (12)", and Confirm is enabled;
  - singular: 1 canola scan at day 0 → "1 scan has no production model for its species and age and will fail:".
- [ ] 6.5 **Red.** Block: 50 sorghum scans at day 10 → the blocker "None of these scans has a production model for its species and age, so the pipeline can't produce results." is in `blockers`, `no-model` isn't shown, and Confirm is disabled. The same target with `fetchModelCards` resolving `null` → no blocker, the muted line, and Confirm enabled.
- [ ] 6.6 **Red.** Images: 4 arabidopsis scans at day 28, 1 without images → `past-window` counts 3, and the no-images line counts 1. Only sorghum scans, none with images → the no-images line only; no model warning, muted line or blocker.
- [ ] 6.7 **Red.** Card read `null`, then `[]` → `past-window-unknown` shows "Couldn't check the models' age ranges.", with no `role="alert"`, no blocker and Confirm enabled. With zero model groups (every scan has a stage-in problem) and `null` → no muted line.
- [ ] 6.8 **Red.** Card read held while the other reads have settled → Confirm disabled, and none of `past-window`, `no-model` or `past-window-unknown` is shown. Releasing it enables Confirm.
- [ ] 6.9 **Red.** Caption: `params` contains "Parameters come from each scan's metadata. Choosing models isn't supported yet", with the bloom#897 link. Update the existing assertion of the old text.
- [ ] 6.10 **Guard** (meaningful once 6.11 renders the warnings; re-run after it):
  - pennycress day 14 → no model warning;
  - the banned-phrase tests (`/will run|will be skipped|reused/i`) pass with past-window, no-model and blocked targets added;
  - card read `null` plus a table failure → the failed alert is shown.
- [ ] 6.11 Implement per design D5: start the read outside the `Promise.all`, store it, gate `canConfirm` on it having settled, build the model groups from scans with images, classify them, and render the blocker, the warnings or the muted line. Update the caption.

## 7. Pre-merge and PR (C7)

- [ ] 7.1 `openspec validate add-cyl-pipeline-model-window-warning --strict`.
- [ ] 7.2 Python checks:
  - `uvx black@26.3.1 --check services/workflows tests/unit`
  - `uvx ruff@0.9.9 check services/workflows tests/unit`
  - `python scripts/check-uv-locks.py`
  - the workflows tests
  - `uv run --extra test pytest tests/unit/ -q`
  - `python scripts/verify_env_parity.py .github/workflows/deploy.yml`
- [ ] 7.3 Web checks: from `web/`, run `npx tsc --noEmit`, `npm run lint`, `npm run build` (with CI's placeholder `NEXT_PUBLIC_*` env; restore `web/tsconfig.json` afterwards) and `npm run test:unit`.
- [ ] 7.4 Re-run 1.4, then 1.7 using `python -m model_cards_fetch` (prints the 8 cards; empty stderr with `WANDB_SILENT=false`), then 1.8, on the final image.
- [ ] 7.5 Run `/pre-merge` and fix anything until green.
- [ ] 7.6 Tick 0–7, write the PR body with `/pr-description` ("Part of #971"; gates 9.1 and 9.2; dev stacks must rebuild the workflows image), and update the draft PR.

## 8. Rebase watch

- [ ] 8.1 Before marking the PR ready, rebase onto `origin/staging`. Expect conflicts with changes to `docker-compose.prod.yml`, `deploy.yml` or `services/workflows/{main.py,README.md}`, for example from `feat/rnaseq-s3-folder-service`. Re-run 7.1–7.3 after. Re-check that no new active change modifies "Confirm dialog shows read-only resolved params and a pre-check, without predicting skips".

## 9. Merge gates and verification

- [ ] 9.1 **Gate (satisfied).** The cluster re-pin is live: srp #112 (traits `sha-426ad4d`) and #113 (predict `sha-79939ee`), with `argo template update` run, traits first, per #115. Confirm that those srp changes didn't alter the canonical `sleap-roots-pipeline.yaml` that Bloom vendors.
- [ ] 9.2 **Gate.** Both secrets exist; record the names from `gh secret list` (read-only). Mark the PR ready only after 9.1 and 9.2; merging is the user's.
- [ ] 9.3 After the staging deploy, read-only checks:
  - `GET /workflows/model-cards` with a staging session returns 8 cards matching design's Context table;
  - the warm-up logged a successful listing at startup;
  - a request just after the 300 s expiry: record its time.
  - If the refresh takes over about 8 s, draft a follow-up for background refresh before expiry, for the user; don't post it.
  - Repeat the endpoint check on prod after the next promotion, with the user's go-ahead.
- [ ] 9.4 On staging, open the confirm dialog without submitting:
  - an arabidopsis experiment with day-20/21 scans → the past-window block;
  - an experiment of a species with no cards (for example sorghum) → the blocker.
  - Any GPU run needs the user's yes for that run.
- [ ] 9.5 Archive with `/openspec:archive add-cyl-pipeline-model-window-warning` after 9.3–9.4. Replace the new `cyl-model-catalog` spec's "Purpose: TBD" with one sentence.
