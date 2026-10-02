# Tasks: add-cyl-pipeline-model-window-warning

## Conventions

**TDD.** Each group opens with tests.
- A **red** test fails today for the stated reason. Run it, see it fail, implement, see it pass. Record the red output in the commit body.
- A **guard** test passes before and after; it's labelled as such.

**Commits.** One commit per group 1–6, with its tests, implementation and the docs that describe it.

| # | Message | Group |
|---|---|---|
| C0 | `docs(openspec): propose add-cyl-pipeline-model-window-warning (bloom#971)` (exists) | — |
| C0r | `docs(openspec): revise add-cyl-pipeline-model-window-warning after review` | — |
| C1 | `build(workflows): require sleap-roots-contracts 0.1.0a9 and add wandb` | 1 |
| C2 | `feat(workflows): list production model cards from the wandb registry` | 2 |
| C3 | `feat(workflows): serve GET /model-cards outside the shared rate limit` | 3 |
| C4 | `feat(deploy): give only the workflows service a WANDB_API_KEY` | 4 |
| C5a | `feat(web): add the past-window rule for pipeline parameter groups` | 5 (5.1–5.2) |
| C5b | `feat(web): proxy the workflows model-card list` | 5 (5.3–5.6) |
| C6 | `feat(web): warn in the confirm dialog about scans past their models' validated age` | 6 |
| C7 | `docs(openspec): tick add-cyl-pipeline-model-window-warning tasks` | 7 |

**Push and PR.**
- After C0r and C1, push and open a **draft** PR against `staging`. That gets pip-audit and Trivy feedback on the wandb image early.
- Push later commits as they land.
- Tick tasks (C7) before the last push, so the full suite isn't run twice.

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

## 0. Setup and gates (no commit)

- [ ] 0.1 Run `npm ci` at the worktree root, then `uv sync --extra test` in `services/workflows/`.
- [ ] 0.2 Baselines, all green before any change:
  - `npx vitest run components/cyl-pipeline lib/cyl-pipeline app/api/cyl`
  - the workflows tests
  - `tests/unit/`
  - Record any Windows-only failures that come from unrelated tests (doctor, env and deploy scripts, `os.geteuid`).
- [ ] 0.3 **Gate (user):** the GitHub repository secrets `PROD_WANDB_API_KEY` and `STAGING_WANDB_API_KEY` exist, ideally a wandb service-account key with registry read access. They aren't needed to develop. They are needed before merge: without them every staging deploy and the next prod promotion abort at env validation (design D7).
- [x] 0.4 Egress: from `bloom_v2_staging-workflows-1`, `urllib.request.urlopen("https://api.wandb.ai", timeout=5)` connected and got HTTP 404 from the API root on 2026-10-02 (user go-ahead). The prod container runs on the same host.

## 1. Workflows dependencies and image (C1)

- [ ] 1.1 **Characterisation,** green before and after. New `tests/test_contracts_pin.py` asserts that `compute_param_hash({"species": "canola", "mode": "cylinder", "age": 2})` equals the literal it returns at a5. Compute and paste that value before the bump.
- [ ] 1.2 **Red,** same file. Inside the test function (not at module level, so 1.1 still collects at a5): `from sleap_roots_contracts import ModelCard, Selector`, and validate a card with `selectors=[{"species": "arabidopsis", "mode": "cylinder", "age_min": 2, "age_max": 14}]`. Fails at a5, where `Selector` doesn't exist (it was added in a8).
- [ ] 1.3 Raise the floor to `sleap-roots-contracts>=0.1.0a9`, add `wandb>=0.21.3` (an open floor), run `uv lock --upgrade-package sleap-roots-contracts`, review the lock diff for unrelated bumps, and keep `python scripts/check-uv-locks.py` green.
- [ ] 1.4 Run `uv export --frozen --no-hashes | uvx pip-audit@2.10.0 -r /dev/stdin`, as CI does. Report any finding to the user before going further.
- [ ] 1.5 Dockerfile:
  - add the `ENV` block from design D1b: `WANDB_CONFIG_DIR`, `WANDB_CACHE_DIR`, `WANDB_DATA_DIR`, `WANDB_DIR`, `WANDB_SILENT=true`, `WANDB_ERROR_REPORTING=false`;
  - after `uv pip install`, add a `RUN` that deletes `wandb/bin/wandb-core` and `wandb/bin/gpu_stats`, locating the package with `python -c "import wandb, os; print(os.path.dirname(wandb.__file__))"`.
- [ ] 1.6 **Container smoke test (local; a key is needed only for the listing step).**
  - Build: `docker build -t workflows:local services/workflows`.
  - Run: `docker run --rm --read-only --tmpfs /tmp -e WANDB_API_KEY=… workflows:local python -c "import model_cards; print(len(model_cards.list_production_cards()[0]))"`. It must print `8`. Run this after C2, then amend C1's Dockerfile part if needed.
  - **If it fails because a binary is missing,** drop the deletion line from 1.5, record why in the commit body, and rely on 1.7.
  - Never pass the key on a command line that gets logged or committed.
- [ ] 1.7 Trivy: `trivy image --severity CRITICAL --exit-code 1 --ignorefile .trivyignore workflows:local`. Report any finding to the user, and never add a `.trivyignore` entry without their sign-off (such an entry needs its own PR, per `lint_cve_isolation.sh`).

## 2. Card listing, `services/workflows/model_cards.py` (C2)

The tests go in `tests/test_model_cards.py`:
- a fake API object is injected by monkeypatching `model_cards._api`;
- the clock is faked with `model_cards._monotonic` and `model_cards._utcnow`, never `time.monotonic`;
- the key is set with `monkeypatch.setenv`/`delenv("WANDB_API_KEY")`;
- an autouse fixture clears the cache.

- [ ] 2.1 **Red.** Two collections, with artifacts aliased `["production"]`, `["latest"]` and `["production", "v0"]` → only the two production cards come back.
  - The fake records its calls. Assert `artifact_collections(project_name="eberrigan-salk-institute-for-biological-studies-org/wandb-registry-sleap-roots-models", type_name="model")` and `artifacts(type_name="model", name=f"{project}/{collection.name}")`.
  - Each output dict has exactly the keys `{root_type, registry_id, version, selectors}`, and each selector exactly `{species, mode, age_min, age_max}`.
  - `registry_id` is the qualified name before `:`.
  - `_api` was called with the stripped key and builds the client with `timeout=5`.
  - Fails: there's no module.
- [ ] 2.2 **Red.** One production artifact with flat a5-style metadata plus one valid one → one card, and `caplog` names the bad artifact.
- [ ] 2.3 **Red.** Every production artifact invalid → raises `ModelCatalogUnavailable`. Zero production artifacts → `([], fetched_at)`.
- [ ] 2.4 **Red.** `WANDB_API_KEY` unset or `"  "` → raises `ModelCatalogNotConfigured`, and `_api` is never called. With `"key\n"`, `_api` receives `"key"`.
- [ ] 2.5 **Red.** Cache:
  - two calls 60 s apart → `_api` is called once, with the same `fetched_at`;
  - at exactly 300.0 s → listed again, with a new `fetched_at` that parses with `datetime.fromisoformat` and has UTC `tzinfo`;
  - all-invalid followed by valid → the second call lists again;
  - a generic exception followed by success → listed again;
  - at 301 s with a failing refresh → raises, and doesn't return the old list.
- [ ] 2.6 **Red.** Concurrency:
  - 5 threads start behind a `threading.Barrier(5)` on a cold cache. The fake `_api` sets an `entered` event and waits on `release.wait(timeout=5)`. The main thread waits `entered.wait(5)` and releases.
  - `_api` is entered once, and all threads get the same list.
  - Every `join(timeout=5)` is followed by `assert not t.is_alive()`.
  - The commit body notes that the red is probabilistic, while the green assertion is deterministic.
- [ ] 2.7 **Red.** Bounded wait: one thread holds a refresh blocked inside `_api` (via an event), and a second caller raises `ModelCatalogUnavailable` within the lock-wait bound. The bound is a module constant, `LOCK_WAIT_SECONDS = 8`, patched to `0.2` in the test.
- [ ] 2.8 **Red.** `subprocess.run([sys.executable, "-c", "import main, model_cards, sys; assert 'wandb' not in sys.modules"], cwd=<service dir>)` exits 0. It runs in a subprocess because other tests may already have imported wandb.
- [ ] 2.9 Implement `model_cards.py` per design D1 and D3: constants, `_api(key)`, `_monotonic`, `_utcnow`, the lock and its state, and `list_production_cards() -> tuple[list[dict], str]`.
- [ ] 2.10 Docs in the same commit: `contracts/README.md`. Its line "Bloom imports neither `ModelCard` nor `Selector`" becomes: "Bloom's `services/workflows` validates production model cards as `ModelCard` (with `Selector`) to serve `GET /model-cards`; it needs `>=0.1.0a9`, because the `selectors` shape arrived in a8."

## 3. Route, `services/workflows/main.py` (C3)

Tests go in `tests/test_main.py`, following its `dependency_overrides` and monkeypatch pattern.

- [ ] 3.1 **Red.** `GET /model-cards`, with auth overridden and `model_cards.list_production_cards` faked to return `(cards, ts)` → 200 `{"cards": cards, "fetched_at": ts}`, and the body has exactly those keys. An empty list → 200 `{"cards": [], "fetched_at": ts}`. Fails: 404.
- [ ] 3.2 **Red.** Rate limit:
  - with the real `enforce_rate_limit` in place and the override returning `"user-1"`, 6 calls → all 200, and `auth._hits.get("user-1", [])` is empty;
  - separately, with `main.enforce_rate_limit` patched to record calls, it's never called.
- [ ] 3.3 **Red.** Errors:
  - `ModelCatalogNotConfigured` → 503 with the exact not-configured text;
  - `ModelCatalogUnavailable`, or `RuntimeError("secret-detail")` → 503 "Couldn't read the model catalog.";
  - `"secret-detail"` isn't in the body, and is in `caplog`.
- [ ] 3.4 **Red.** Auth:
  - override `require_supabase_user` with a function that raises `HTTPException(401)`, as `test_get_run_requires_auth` does → 401, and the listing fake was never called;
  - separately, with `monkeypatch.setattr(auth, "SUPABASE_URL", "http://kong:8000")` and `SUPABASE_ANON_KEY` set, and no override, a request without `Authorization` → 401.
- [ ] 3.5 **Red.** `assert not inspect.iscoroutinefunction(main.model_cards_route)`.
- [ ] 3.6 Implement the route (a sync `def`).
- [ ] 3.7 Docs in the same commit, in `services/workflows/README.md`:
  - a row in the Endpoints table, and a short section next to "Pipeline trigger": auth, no rate limit, 300 s cache, 8 s wait bound, the two 503 texts;
  - correct Auth model Layer 1 and the `WORKFLOWS_RATE_LIMIT` row, which say the limit is shared by "every application route", to name the two exempt routes.

## 4. Secret wiring (C4)

- [ ] 4.1 **Red.** New `tests/unit/test_wandb_key_scope.py`:
  - in `docker-compose.prod.yml`, `services.workflows.environment["WANDB_API_KEY"] == "${WANDB_API_KEY}"`; in `docker-compose.dev.yml`, it's `"${WANDB_API_KEY:-}"`;
  - no other service in either compose file has `WANDB_API_KEY`;
  - `.github/workflows/deploy.yml`'s prod heredoc contains `WANDB_API_KEY=${{ secrets.PROD_WANDB_API_KEY }}`, and the staging heredoc the `STAGING_` line.
  - Fails: none of these exist.
- [ ] 4.2 Add the two compose entries. Observe red in `tests/unit/test_env_defaults.py::test_all_compose_vars_are_sourced` (no source for the variable) and in `tests/unit/test_video_worker_containers.py::test_the_worker_carries_the_services_own_credentials`, for both prod and dev.
- [ ] 4.3 Add `WANDB_API_KEY` to `SENSITIVE_INVENTORY`. In the worker-parity test, pop `WANDB_API_KEY` from the `workflows` side next to `WORKFLOWS_CORS_ORIGINS`, with the comment "workers don't get the key". Add the blank `WANDB_API_KEY=` with a comment to `.env.dev.example`. Then:
  - `tests/unit/test_env_dev_example.py` and `test_no_overlap_with_sensitive_inventory` stay green.
- [ ] 4.4 Add the two `deploy.yml` heredoc lines. `python scripts/verify_env_parity.py .github/workflows/deploy.yml` is green, and 4.1 is green.
- [ ] 4.5 Docs in the same commit:
  - `services/workflows/README.md`: Provisioning step 7, which sets the deploy secrets `PROD_/STAGING_WANDB_API_KEY` (ideally a service-account key with registry read access; only `workflows` gets it). Add a Configuration table row. Correct Auth model Layer 2's "no privileged credential": no privileged *Supabase* credential, and one third-party credential, `WANDB_API_KEY`, for `GET /model-cards`.
  - `DEV_SETUP.md` "optional keys": `WANDB_API_KEY` shows the dialog's past-window warning locally; without it, the dialog shows "Couldn't check the models' age ranges."
  - `.env.prod.defaults` header, `scripts/setup-env-secrets.sh` comment and `PROD_SETUP.md` secrets table: replace their hand-kept lists with a pointer to the two `deploy.yml` heredocs (kept in sync by `scripts/verify_env_parity.py`) and to each service's README for service-specific secrets. `.env.staging.defaults` isn't touched.
- [ ] 4.6 `tests/unit/` is green, apart from the Windows-only failures recorded in 0.2.

## 5. Web: rule, client and proxy (C5a, C5b)

- [ ] 5.1 **Red (C5a).** New `lib/cyl-pipeline/model-windows.test.ts`, using `lib/cyl-pipeline/__fixtures__/model-cards.ts` (the 8 production cards of 2026-09-30, selectors only):
  - `pastWindowGroups(groups, cards)` returns `{species, age, count, max}` for arabidopsis 21 and 28 (max 14), rice 18 (max 10), soybean 10 (max 8), canola 14 (max 13) and pennycress 15 (max 14);
  - nothing for arabidopsis 14 (equal), rice 8, rice 3, canola 0 and sorghum 10;
  - order is kept;
  - the synthetic pair canola·cylinder 2–13 plus canola·multiplant cylinder 2–20, day 15 → max 13;
  - disjoint windows canola 2–5 and 10–13, day 7 → not past;
  - a species with cards only in multiplant cylinder → not past for cylinder;
  - empty cards → `[]`; zero groups → `[]`.
  - Fails: there's no module.
- [ ] 5.2 **Cross-check (C5a),** same file:
  - The five phase-1 cases equal the clamp ages in talmolab/sleap-roots-predict `tests/test_model_selection.py` `_PAST_WINDOW` at `79939ee`: 28→14, 18→10, 10→8, 14→13, 15→14.
  - The fixture's selectors equal predict's `_PRODUCTION` table in `tests/card_builders.py` at `79939ee`.
  - Cite both files and the commit in a comment.
- [ ] 5.3 **Red (C5b).** `lib/cyl-pipeline/model-cards.test.ts`.
  - `isCardList` accepts the fixture and rejects each of these: a non-integer or boolean `age_max`, `NaN`, a `null` card, a selector missing `species`, a missing `selectors`, a non-array `cards`, and a missing `fetched_at`.
  - `fetchModelCards()`:
    - 200 → the cards;
    - non-200, a bad shape, a rejected fetch → `null`;
    - a fetch that settles only when `init.signal` aborts: `advanceTimersByTimeAsync(9_999)` leaves it pending, and `advanceTimersByTimeAsync(1)` resolves `null`;
    - a 200 whose `json()` never settles → `null` after 10 s;
    - after a successful 200, `vi.getTimerCount() === 0`;
    - it never throws.
  - The timeout comes from `AbortController` plus `setTimeout`, never `AbortSignal.timeout`, which ignores fake timers.
- [ ] 5.4 **Red (C5b).** `app/api/cyl/pipeline/model-cards/route.test.ts`, modelled on the trigger proxy's tests:
  - switch off → 503, and neither `getSession` nor fetch is called;
  - no session → 401, and no fetch;
  - upstream 200 with a valid list → 200 pass-through, with the bearer token sent and no `Origin` required;
  - `WORKFLOWS_URL` is used when set, and `http://workflows:5100` otherwise;
  - upstream 503 with detail `upstream-text`, a 3xx, a non-JSON 200, a bad shape, or a `TypeError` rejection → 502, and no body contains `upstream-text`;
  - a `DOMException("t", "TimeoutError")` rejection → 504 (the trigger tests' precedent);
  - it exports `GET` only, with `dynamic = "force-dynamic"` and `runtime = "nodejs"`.
- [ ] 5.5 **Guard.** `app/api/cyl/pipeline/route.test.ts` "exports POST and no other method" stays green.
- [ ] 5.6 Implement:
  - `lib/cyl-pipeline/model-windows.ts` in C5a;
  - `lib/cyl-pipeline/model-cards.ts` (client-safe), `lib/cyl-pipeline/model-cards-proxy.ts` (server-only) and `app/api/cyl/pipeline/model-cards/route.ts` in C5b.
  - `no-provenance-joins.test.ts` stays green.

## 6. Dialog, `components/cyl-pipeline/RunPipelineDialog.tsx` (C6)

`RunPipelineDialog.test.tsx` adds `vi.mock("@/lib/cyl-pipeline/model-cards", …)`, with the controllable `fetchModelCards` created through `vi.hoisted`. It resolves to the fixture by default, and can be held (deferred), or resolve to `null` or `[]`. The existing `fetchSpy` POST assertions are unchanged. Existing tests that use pennycress day 21 will now also render the block; they assert with `toContain` or `within(params)`, so they stay green.

- [ ] 6.1 **Red.**
  - **Target:** 60 arabidopsis scans at day 21, 30 at day 28 and 120 at day 14, all with images, plus one stage-in-problem scan, one no-images scan and one concurrent run.
  - **Text:** `past-window` shows the spec's heading and two lines, exactly, with the day-21 line before the day-28 line, and no day-14 line.
  - **Position:** document order is stage-in line, then no-images line, then `past-window`, then `concurrent-runs`.
  - **Confirm:** enabled.
- [ ] 6.2 **Red.** Order: 30 at day 21 and 60 at day 28 → the day-28 line comes first.
- [ ] 6.3 **Red,** as `it.each`:
  - singular: 1 soybean scan at day 10 → "1 scan is past its models' validated age and is predicted with the nearest models:" and "soybean · day 10 — models validated up to day 8 (1)";
  - rice: 5 at day 18 → "5 scans are…" and "rice · day 18 — models validated up to day 10 (5)".
- [ ] 6.4 **Red.** No images: 4 arabidopsis scans at day 28, 1 of them without images → the block counts 3, and the no-images line counts 1.
- [ ] 6.5 **Red.** `fetchModelCards` resolves `null`, then `[]` → `past-window-unknown` shows "Couldn't check the models' age ranges.", there's no `role="alert"`, and Confirm is enabled. With zero parameter groups (every scan has a stage-in problem) and `null` → no muted line.
- [ ] 6.6 **Red.** `fetchModelCards` is held while the other reads have settled → Confirm is disabled and neither testid is shown. Releasing it enables Confirm.
- [ ] 6.7 **Red.** Caption: `params` contains "Parameters come from each scan's metadata. Choosing models isn't supported yet" with the bloom#897 link. Update the existing assertion of the old caption text.
- [ ] 6.8 **Guard** (meaningful once 6.9 renders the block; re-run after it):
  - pennycress day 14, canola day 0 and sorghum day 10 → neither testid;
  - the banned-phrase tests (`/will run|will be skipped|reused/i`) pass with a past-window target added to their cases;
  - card read `null` plus a table failure → the failed alert is shown;
  - closing the dialog while the card read is held, then resolving it → no `console.error`.
- [ ] 6.9 Implement per design D5: start the read outside the `Promise.all`, store its result, gate `canConfirm` on it having settled, compute the groups from scans with images, render the block or the muted line, and update the caption.

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
- [ ] 7.4 Re-run 1.4 (pip-audit) and 1.6–1.7 (container smoke test and Trivy) on the final image.
- [ ] 7.5 Run `/pre-merge` and fix anything until green.
- [ ] 7.6 Tick 0–7, write the PR body with `/pr-description` ("Part of #971"; the gates 9.1 and 9.2; a note that dev stacks must rebuild the workflows image), and update the draft PR.

## 8. Rebase watch

- [ ] 8.1 Before marking the PR ready, rebase onto `origin/staging`. Expect conflicts with any change to `docker-compose.prod.yml`, `deploy.yml`, or `services/workflows/{main.py,README.md}` (for example `feat/rnaseq-s3-folder-service`). Re-run 7.1–7.3 after.

## 9. Merge gates and verification

- [ ] 9.1 **Gate (satisfied).** The cluster re-pin is live: srp #112 (traits `sha-426ad4d`) and #113 (predict `sha-79939ee`), with `argo template update` run, traits first, per #115. Confirm that the srp changes didn't alter the canonical `sleap-roots-pipeline.yaml` that Bloom vendors.
- [ ] 9.2 **Gate.** Both secrets exist. Record the names from `gh secret list` (read-only; names only). Mark the PR ready only after 9.1 and 9.2; merging is the user's.
- [ ] 9.3 After the staging deploy, read-only checks:
  - `GET /workflows/model-cards` with a staging session returns 8 cards matching design's Context table;
  - record the cold-read time;
  - container logs show no wandb filesystem warnings.
  - If the cold read is above about 5 s, draft the cache-warming follow-up for the user (don't post it).
  - Repeat the endpoint check against prod after the next promotion, with the user's go-ahead.
- [ ] 9.4 On staging, open the confirm dialog for a target with a past-window group (for example an arabidopsis experiment with day-20/21 scans). Check the block and its counts; don't submit. Any GPU run needs the user's yes for that run.
- [ ] 9.5 Archive with `/openspec:archive add-cyl-pipeline-model-window-warning` after 9.3–9.4. Either order relative to `fix-cyl-pipeline-runs-ui-955` is safe, because this change only ADDs (design D6).
- [ ] 9.6 Draft (don't post) a follow-up issue: once both changes are archived, fold this change's dialog requirement into "Confirm dialog shows read-only resolved params and a pre-check, without predicting skips", covering its item-3 extension and the item-6 caption.
