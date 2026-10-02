# Design: add-cyl-pipeline-model-window-warning

## Context

bloom#971 phase 1 was decided with the user on 2026-10-01 ([decisions](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/971#issuecomment-5937500723), [update](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/971#issuecomment-5938030166)):

- A scan older than every model window for its species and mode runs with that species' highest-age window, for models and traits pipeline alike. There's no upper limit.
- These scans are included **by default**, with a warning in the confirm dialog.
- Younger-than-window scans and species with no cards are unchanged on the cluster (#994, #993).
- No new provenance marking.
- No user param overrides.

**Cluster side (live):**
- **predict `choose_models`** (talmolab/sleap-roots-predict#50, `79939ee`). `past_window_age(params, cards, overrides)` returns the largest `age_max` among selectors with the scan's species and mode, taken across all cards. It returns that value only when the scan's age is above it and some non-overridden root type has a card for that species and mode. Matching then uses that age, while the params keep the real age.
  - A scan matching no card for any root type fails in `_predict_one` ("no models resolved").
  - Write-back then closes it out as failed with the generic "no result produced for this scan by write-back" (`bloomctl/cyl/ingest.py`, `fail_cyl_pipeline_run_scans_without_result`).
- **traits `choose_pipeline`** (talmolab/sleap-roots#272, `426ad4d`). The same rule, over `pipeline_selection.yaml`.
- Both are pinned in sleap-roots-pipeline (#112 traits, #113 predict). `argo template update` ran, traits first, per #115.

**Bloom side today:**
- Prod's trigger is on (`CYL_PIPELINE_TRIGGER_ENABLED=true` on main).
- Past-window scans already run from Bloom with nothing on screen saying so.
- Runs on species with no models fail scan by scan, again with nothing on screen.
- An experiment has exactly one species (`cyl_experiments.species_id`; `cyl_scans_extended` takes species from the experiment), so a scan, wave, experiment or accession run is single-species.

Production registry (`sleap-roots-models`, alias `production`, read 2026-09-30): 8 cards, all `v0`.

| Species | Window (days) |
|---|---|
| soybean | 2–8 |
| canola | 2–13 |
| pennycress | 2–14 |
| arabidopsis | 2–14 (also multiplant cylinder) |
| rice | 2–5 and 6–10 |

## Goals / Non-Goals

**Goals**
- The dialog names every parameter group the cluster will predict past its window, and every group it can't predict at all, from the same card data and rule as predict.
- A run where no scan can produce results is stopped before it reaches the cluster.
- Defaults stay one click. Warnings never disable Confirm, and failing to check never does either.
- A slow or broken registry can't degrade the workflows service.

**Non-Goals**
- Choosing models (phase 2, #897) and suggesting models for species with none (#993).
- Running younger-than-window scans (#994).
- A shared matcher in sleap-roots-contracts (#13, #14).
- Provenance stamping.
- A server-side preview, or leaving no-model scans out of runs (#898).
- Changes to the trigger request, Argo submission or stage-in.
- Marking past-window results in trait exports (#865).

## Decisions

### D1. Read the registry with the `wandb` library, in a child process with a hard limit (user decisions)

**Why a child process.** wandb's public `Api(timeout=…)` doesn't bound a listing:
- `api.artifacts(...)` builds its own `InternalApi()` (`apis/public/artifacts.py`, `sdk/artifacts/_graphql_fragments.py:18`).
- That client's `_retry_gql` retries timeouts, connection errors, 5xx and 429 for `retry_timedelta = 7 days` by default (`sdk/internal/internal_api.py:239-241`), with backoff up to 300 s.
- The constructor's login check also uses `InternalApi` (20 s).
- A Python thread stuck in that loop can't be stopped, and it would hold the refresh lock until the container restarts.

**How it works.**
- `model_cards.py`, in the API process, runs `subprocess.run([sys.executable, "-m", "model_cards_fetch"], capture_output=True, timeout=20, env={**os.environ, "WANDB_HTTP_TIMEOUT": "5"})`. On `TimeoutExpired`, `subprocess.run` kills the child.
- `model_cards_fetch.py` is the child, the only module that imports `wandb`. It:
  - lists `artifact_collections(project_name=PROJECT, type_name="model")`, then `artifacts(type_name="model", name=f"{PROJECT}/{collection.name}")`, with `PROJECT = "eberrigan-salk-institute-for-biological-studies-org/wandb-registry-sleap-roots-models"`;
  - keeps artifacts aliased `production`;
  - validates `ModelCard.model_validate({**metadata, "registry_id": qualified_name.split(":")[0], "version": artifact.version, "weights_checksum": artifact.digest})`;
  - skips and logs invalid ones on stderr;
  - writes `{"cards": [...]}` to stdout and exits 0.
  - All invalid → exit 2. Any other error → exit 1.
  - It never calls `artifact.download()` or `.files()`, which would start `wandb-core` (D1b).
  - The client is `wandb.Api(api_key=os.environ["WANDB_API_KEY"], timeout=5)`.
- `WANDB_HTTP_TIMEOUT` is read when wandb's API classes are defined, so it has to be in the child's environment. It caps `InternalApi`'s 20 s too.

**What the parent checks.**
- `WANDB_API_KEY` unset or whitespace-only → `ModelCatalogNotConfigured`, and no child is started.
- The key is **not** stripped: wandb's internal clients re-read the raw environment value, so a stripped copy would authenticate some calls and not others.
- Non-zero exit, unparseable stdout or a timeout → `ModelCatalogUnavailable`, with the child's stderr logged (never returned).

**Cost.** A healthy cold listing is about 4 + 2N wandb calls (verify, viewer, server info, collections, then an introspection call and an artifacts query per collection; N ≈ 8), plus about 1–2 s of interpreter and wandb start-up. That's one per 5 minutes per container, at most.

**Side benefit.** wandb's global singleton, background threads and the in-memory key never live in the uvicorn process.

**Alternatives rejected with the user:**
- Calling GraphQL through `httpx`: undocumented, and we'd re-implement the alias and pinning logic.
- Having the cluster publish the list: a new step in the pipeline repos.
- Tuning retries in-process: relies on internals that change.
- A give-up thread: leaks a stuck thread per refresh during an outage.

### D1b. Image: wandb environment and bundled binaries

All in `services/workflows/Dockerfile`, for all 7 containers that share the image. Only `workflows` ever imports wandb. All 7 are `read_only: true` with a `/tmp` tmpfs.

- **Environment.** `ENV WANDB_CONFIG_DIR=/tmp/wandb/config WANDB_CACHE_DIR=/tmp/wandb/cache WANDB_DATA_DIR=/tmp/wandb/data WANDB_DIR=/tmp WANDB_SILENT=true WANDB_ERROR_REPORTING=false`.
  - `workflows` runs read-only as a system user with an unwritable home.
  - `WANDB_ERROR_REPORTING=false` turns off the Sentry reporter that `wandb.Api()` otherwise starts, tagged with the entity and the viewer's email.
- **Binaries (user decision).** Remove wandb's `bin/` directory (`wandb-core`, Go, about 43 MB; `gpu_stats`, Rust, about 8 MB) **in the same `RUN` as `uv pip install`**. A later `RUN` would leave them in the install layer.
  - The package is located without importing it: `d=$(python -c "import importlib.util,pathlib;print(pathlib.Path(importlib.util.find_spec('wandb').origin).parent)") && rm -r "$d/bin"`.
  - There's no `-f`, so a moved directory fails the build loudly.
  - `wandb-core` is reached only through `util.get_core_path()`, from `ensure_service()`, which runs on `wandb.init` or an artifact download. `gpu_stats` isn't referenced from Python. Listing with `wandb.Api()` touches neither.
  - **Why:** CI's Trivy step fails on any CRITICAL in the `workflows` image (`pr-checks.yml`, `exit-code: '1'`, every PR). Go stdlib CRITICALs in bundled binaries are common, and `.trivyignore` already carries one for the Supabase CLI binary. A `.trivyignore` edit must be its own PR (`lint_cve_isolation.sh`).
  - **Condition:** kept only if the read-only container smoke test (task 1.5) lists the cards with `bin/` removed. Otherwise `bin/` stays, and Trivy's result (task 1.6) is taken to the user.
- **Guard.** A new `tests/unit/test_workflows_dockerfile_shape.py` pins the ENV block, and that the removal sits in the install `RUN`, before `USER bloom`. The precedent is `test_bloommcp_dockerfile_shape.py`.

### D2. Response shape and contracts version

`GET /model-cards` returns, always with both keys:

```
{"cards": [{"root_type": "...", "registry_id": "...", "version": "...",
            "selectors": [{"species": "...", "mode": "...", "age_min": 2, "age_max": 14}, ...]}, ...],
 "fetched_at": "<ISO-8601 UTC, when the cached listing was read>"}
```

- **Contracts floor:** raised from `>=0.1.0a5` to `>=0.1.0a9` and re-locked, because `ModelCard.selectors` arrived in a8.
- **Why the bump is safe:** the service's other contracts use, `compute_param_hash` in `pipeline.py`, comes from `hashing.py`, which is unchanged from a5 to a9 (task 1.1 pins the value).
- **Prod already runs a newer version.** The Dockerfile installs from `pyproject.toml`, not the lock, so the deployed image likely already resolves a9.

### D3. Cache, startup warm-up, bounded waiting, auth and rate limit

**Cache.**
- A module-level entry `(fetched_monotonic, cards, fetched_at)` with a TTL of 300 s; a refresh is due at `now - fetched_monotonic >= 300`.
- Time comes from injectable `_monotonic` and `_utcnow`; tests never patch `time.monotonic`.
- The service runs one uvicorn worker, so it's one cache per container.

**Single flight.**
- A `threading.Lock` ensures at most one listing runs at a time.
- A request that finds the cache cold or expired acquires the lock with `timeout=LOCK_WAIT_SECONDS` (6), read at call time.
- With the lock held, it re-checks the cache, so it's served if another request's refresh just succeeded, and only lists if the cache is still cold.
- Not getting the lock in time → `ModelCatalogUnavailable` → 503.
- After a *failed* refresh, the next lock holder lists again. That's sequential, never concurrent, and each attempt is bounded at 20 s.

**Startup warm-up (user decision).**
- `main.py` gains a FastAPI `lifespan` that starts one `threading.Thread(target=model_cards.warm, daemon=True)`.
- `warm()` returns immediately when the key is unset or blank, and swallows and logs failures.
- Startup and `/health` never wait for it.
- Tests construct `TestClient(main.app)` without entering the lifespan, and the key is unset in CI, so the warm-up doesn't run in tests unless a test asks for it.

**Timing budget.**

| Stage | Limit |
|---|---|
| child listing | killed at 20 s |
| waiting for another request's refresh | 6 s |
| web proxy → workflows | 8 s (`AbortSignal.timeout`) |
| dialog → proxy | 10 s |

- A leader whose proxy has given up keeps running in its threadpool thread, because sync routes aren't cancelled. It finishes within 20 s and warms the cache for the next open.
- At most two threadpool threads are ever tied up by this route for a meaningful time: the leader, plus waiters for at most 6 s. The other sync routes, `/health` included, share the 40-thread pool.

**Failure.** A failed listing isn't cached, and an expired listing isn't served after a failed refresh.

**Auth.** `Depends(require_supabase_user)`, like every route.

**Rate limit.** Not applied.
- The precedent is the plate-video progress route (`main.py:127-139`).
- The shared limiter is 5 per 60 s per user across routes (`auth.py:24-25`).
- The cache and the single-flight lock bound the upstream cost.

**Errors.** Both are 503 with a fixed detail; the cause goes to the log only.
- `ModelCatalogNotConfigured` → "The model catalog isn't configured in this environment."
- Anything else → "Couldn't read the model catalog."

**The trigger switch.** The upstream route doesn't check `CYL_PIPELINE_TRIGGER_ENABLED`: the cards aren't sensitive and the cost is bounded. The web proxy (D4) does check it.

### D4. Web proxy route, server/client split

`web/app/api/cyl/pipeline/model-cards/route.ts` exports only `GET`, with `dynamic = "force-dynamic"` and `runtime = "nodejs"`.

**Check order:**
1. `isPipelineTriggerEnabled()` → 503 with the trigger route's text; `getSession` isn't called.
2. `getSession()` without an access token → 401.
3. `fetch(`${WORKFLOWS_URL ?? "http://workflows:5100"}/model-cards`)` with the bearer token, `redirect: "manual"` and `signal: AbortSignal.timeout(8_000)`.

**Responses:**
- A 200 whose JSON passes `isCardList` and has a string `fetched_at` is passed through.
- Any other status, a 3xx, non-JSON, a bad shape or a network error → 502 with a fixed detail.
- `TimeoutError` → 504.
- `AbortSignal.timeout` rejects with `TimeoutError`, which keeps the trigger proxy's mapping (`trigger-proxy.ts:176`) faithful. Tests inject that rejection, as the trigger tests do.

**File layout.**
- Server-only helpers (forward, map) live in `lib/cyl-pipeline/model-cards-proxy.ts`.
- Client-safe code (`isCardList`, `fetchModelCards`) lives in `lib/cyl-pipeline/model-cards.ts`, imported by the dialog.

**No Origin check.**
- Browsers send none on a same-origin GET.
- The route only reads non-sensitive data, changes nothing and returns nothing per user. The Cell Ranger logs GET proxy is the precedent.
- The trigger route stays POST-only.

### D5. Dialog behaviour

**Fetch.**
- `fetchModelCards()` calls the proxy with an `AbortController` aborted by `setTimeout(10_000)`, so it can be driven by vitest's fake timers, unlike `AbortSignal.timeout`.
- It races the body read against the abort signal, and clears the timer in `finally`.
- It returns the card array, or `null` on any failure. It never throws.
- The dialog treats `[]` like `null` (user decision: zero production cards means a registry problem).

**Load and settle.**
- The dialog starts the read in its load effect, outside the `Promise.all` that drives the failed state, behind the same `active` guard.
- Confirm waits for it, too. It always settles within 10 s and never sets the failed state.
- The precedent is `getSession().catch(() => null)`.
- **Tests:** the dialog tests mock this module with `vi.mock` and a `vi.hoisted` controllable function; the existing `fetchSpy` POST assertions are unchanged.

**Model groups (user decision: only scans with images).**
- `paramsSummary` is called a second time, over the scans in `withImages`. Its groups, sorted by count descending, then species, then age, are the **model groups**.
- Scans with no images fail at stage-in and never reach predict, so they're described only by the no-images line.
- Scans with metadata problems are already absent from `paramsSummary`'s groups.
- The Parameters list itself still uses every scan.

**Rule.** `classifyModelGroups(groups, cards)` in `lib/cyl-pipeline/model-windows.ts` is pure. It's separate from `params-summary.ts`, whose shape is pinned by its test and documented as "throwaway … #897 must not extend it here". For each group it takes the selectors on every card whose `species` and `mode` equal the group's:
- **past its window** when such selectors exist and `age` > their largest `age_max` (kept as `max`). This equals predict's `past_window_age` with no overrides.
- **no model** when it isn't past its window and no such selector has `age_min <= age <= age_max`. That covers species and modes with no selector, and ages below or between windows: exactly where predict selects no model for any root type.
- **covered** otherwise.

**Display** (spec item 3, after the stage-in and no-images lines):

| Situation | What's shown |
|---|---|
| Some groups are no model (and the block doesn't apply) | `data-testid="no-model"`: "*N scans have no production model for their species and age and will fail:*" (singular at N = 1), then `<species> · day <age> (<count>)` per group |
| Some groups are past their window | `data-testid="past-window"`: "*N scans are past their models' validated age and are predicted with the nearest models:*" (singular at N = 1), then `<species> · day <age> — models validated up to day <max> (<count>)` per group |
| The card read gave `null` or `[]`, and at least one model group exists | `data-testid="past-window-unknown"`, in `text-stone-500`: "*Couldn't check the models' age ranges.*" |
| The read is pending, or no model group exists | Nothing |

Both warnings list every group, uncollapsed, in `text-amber-800`.

**Block (user decision).** When the card read returned cards, at least one model group exists, and **every** model group is no model, a blocking reason disables Confirm:
- "*None of these scans has a production model for its species and age, so the pipeline can't produce results.*"
- The no-model warning isn't shown in addition.
- Since an experiment has one species, this stops unsupported-species runs, while a supported species with a few too-young scans still runs with the warning.
- A failed or empty card read never blocks.

**Copy.**
- "Validated up to day N" names only the window's top, because that's all the rule uses.
- All the copy avoids the banned phrases (`/will run|will be skipped|reused/i`).

**Caption (user decision).** "*Parameters come from each scan's metadata. Choosing models isn't supported yet*", still linking bloom#897, now phase 2 of #971.

**Relation to #898.** The groups are client-side and documented as throwaway once a server-side preview exists. These warnings and the block are functions of those groups plus the card list, so they move server-side with them. Until then the rule duplicates predict's in TypeScript, pinned by a hand-copied cross-check against predict's own tables at `79939ee` (task 5.2), which won't detect later drift. Phase 2 replaces both copies with contracts' shared matcher (#13).

### D6. Spec deltas

- **Dialog.** A MODIFIED of `cyl-pipeline-ui` "Confirm dialog shows read-only resolved params and a pre-check, without predicting skips".
  - It restates the archived text (with `fix-cyl-pipeline-runs-ui-955`'s singular wording, archived on staging in #1023 on 2026-10-02).
  - It adds: the card read and the four-read settle rule; the model-group definitions; the no-model blocker in item 2; item 3 becoming "Stage-in and model warnings"; and the new caption in item 6.
  - No other active change modifies this requirement (checked after rebasing onto `2c17ca98`), so there's no ordering gate.
- **Proxy.** An ADDED requirement in `cyl-pipeline-ui`.
- **Endpoint.** A new capability, `cyl-model-catalog`, rather than the POST-only `cyl-pipeline-trigger`, with three ADDED requirements: access, listing and errors, cache.
  - Archive creates its spec with "Purpose: TBD"; task 9.5 replaces that.

### D7. Secret wiring (workflows only; required in both environments)

| File | Change |
|---|---|
| `docker-compose.prod.yml` `workflows.environment` | `WANDB_API_KEY: ${WANDB_API_KEY}`. `scripts/validate_env.sh` requires every `${VAR…}` here to be non-blank, even with `:-`, so the key is **required in prod and staging**, which both deploy from this file (user decision). |
| `docker-compose.dev.yml` `workflows.environment` | `WANDB_API_KEY: ${WANDB_API_KEY:-}`, optional. `.env.dev.example` gets a blank `WANDB_API_KEY=` with a comment. |
| `.github/workflows/deploy.yml` | `WANDB_API_KEY=${{ secrets.PROD_WANDB_API_KEY }}` in the prod heredoc, and `…STAGING_WANDB_API_KEY` in the staging heredoc. `scripts/verify_env_parity.py` checks they match. |
| `tests/unit/test_env_defaults.py` | Add `WANDB_API_KEY` to `SENSITIVE_INVENTORY`. |
| `tests/unit/test_video_worker_containers.py` | Pop `WANDB_API_KEY` from the expected (`workflows`) side, next to `WORKFLOWS_CORS_ORIGINS`. |
| new `tests/unit/test_wandb_key_scope.py` | Only `workflows` has the key, in both compose files, with the exact values above; both `deploy.yml` heredocs carry their line. |

**What a missing secret does.** It aborts that environment's deploy at validation, before containers change. Until both secrets exist, every staging deploy is blocked (anyone's), and so is the next prod promotion. The user adds both before merge (task 0.3; gate 9.2).

**Egress.** From `bloom_v2_staging-workflows-1`, `https://api.wandb.ai` answered HTTP 404 at the API root on 2026-10-02, so the connection works. `bloom_v2_prod-workflows-1` runs on the same host.

**Docs.**
- `services/workflows/README.md`: the endpoint; the key in Provisioning and Configuration; Auth model Layer 1 corrected ("rate limit shared across every route" now names the two exempt routes); Layer 2 corrected ("no privileged credential" becomes no privileged Supabase credential, plus `WANDB_API_KEY`).
- `DEV_SETUP.md`: optional keys.
- `.env.prod.defaults` header, `scripts/setup-env-secrets.sh` comment and `PROD_SETUP.md` table: replace the hand-kept, already-stale secret lists with a pointer to the `deploy.yml` heredocs and each service's README.

### D8. Merge gates

1. **The cluster re-pin is live.** Satisfied: srp #112, #113 and #115 (task 9.1).
2. **Both GitHub secrets exist.** The user confirms; names are checked with `gh secret list` (task 9.2).

The PR is opened as a draft and marked ready only after both gates. Merging is the user's.

## Risks / Trade-offs

- **The first open after a cache expiry may wait.** The warm-up covers the first open after startup. After a 5-minute expiry, the first dialog's request leads a refresh. If that takes over 8 s, that one dialog shows the muted line and the next is served warm. Task 9.3 measures it.
- **Image size and audit surface.**
  - New packages: requests, urllib3, charset-normalizer, protobuf, sentry-sdk, gitpython, gitdb, smmap and platformdirs. A `pip-audit` finding fails CI until resolved.
  - wandb caps `protobuf<7`, so a fix only in 7.x would have no path.
  - Removing `bin/` in the install layer removes most of the size.
- **The Dockerfile installs from `pyproject.toml`, not the lock.**
  - The image can get a newer wandb than CI's lock, and the unit tests fake the child, so an API change would surface only at runtime, as the muted line.
  - This is existing behaviour for every dependency here and stays out of scope.
  - Task 9.3's live check and the muted line make a break visible.
- **Traits windows could diverge from model windows.** The dialog reads model cards only. Phase 2's shared catalog removes the gap.
- **Past-window results are unmarked in exports.** A day-28 arabidopsis result has the same recipe key as a day-10 one, because the weights are the same. The #865 export doesn't mark it. It's out of scope here and was raised with the user.
- **Rebase hotspot.** An in-flight `feat/rnaseq-s3-folder-service` edits `services/workflows/main.py` and `README.md`.

## Migration

- No database change.
- **Rollback:** revert the squash commit. Compose, `deploy.yml` and the code revert together; leftover secrets are harmless.
- **No-revert off switch:** set the secret to an invalid value. The endpoint answers 503, the dialog shows the muted line, and Confirm keeps working. A blank value can't be used, because validation rejects it.
