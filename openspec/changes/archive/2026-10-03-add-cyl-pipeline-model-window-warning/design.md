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
- A slow or broken registry can't degrade the workflows service, and the image gains no new dependency.

**Non-Goals**
- Choosing models (phase 2, #897) and suggesting models for species with none (#993).
- Running younger-than-window scans (#994).
- A shared matcher in sleap-roots-contracts (#13, #14).
- Provenance stamping.
- A server-side preview, or leaving no-model scans out of runs (#898).
- Changes to the trigger request, Argo submission or stage-in.
- Marking past-window results in trait exports (#865).

## Decisions

### D1. Read the registry with one direct GraphQL query over `httpx` (user decision)

**Why not the `wandb` library.** Implementation found that the version the image would actually install (wandb 0.30.0, since the Dockerfile installs from `pyproject.toml`) routes every public-API GraphQL call through `ServiceApi`, a connection to the bundled Go `wandb-core` service. `Api()` has started that service since 0.26. That would mean:
- shipping the Go binary, with its Trivy CRITICAL risk;
- 73 MB plus an OpenTelemetry stack in the image;
- killing a grandchild process on timeout;
- behaviour changing with every wandb release.

Older releases (predict's lock is 0.21.3) instead retry inside `InternalApi` for up to 7 days, ignoring `Api(timeout=...)`.

**What the service does instead.** It makes the same HTTP request wandb's own client makes:
- **Request:** `POST https://api.wandb.ai/graphql`, with HTTP Basic auth `("api", WANDB_API_KEY)` (wandb `apis/public/api.py`, `sdk/internal/internal_api.py`), sent by the `httpx` client the service already depends on.
- **Query**, one per page:

  ```graphql
  query ($entity: String!, $project: String!, $cursor: String) {
    project(name: $project, entityName: $entity) {
      artifactType(name: "model") {
        artifactCollections(after: $cursor, first: 100) {
          pageInfo { endCursor hasNextPage }
          edges { node { name
            artifactMembership(aliasName: "production") { versionIndex artifact { metadata } } } }
        }
      }
    }
  }
  ```

  with `entity = "eberrigan-salk-institute-for-biological-studies-org"` and `project = "wandb-registry-sleap-roots-models"`. These are module constants.
  - `artifactMembership(aliasName: ...)` is the field wandb's own generated operations use to resolve `name:alias`, in both 0.21.3 and 0.30.0.
- **Card build.** For every collection whose membership is non-null: `ModelCard.model_validate({**metadata, "registry_id": f"{entity}/{project}/{collection}", "version": f"v{versionIndex}"})`.
  - `metadata` is parsed if it arrives as a JSON string.
  - The `registry_id` form matches what predict records in provenance (checked on staging source 264).
  - `weights_checksum` isn't requested; the response doesn't carry it.
- **Bad cards.** A collection whose metadata fails validation is skipped and logged. Alias-carrying collections that all fail → `ModelCatalogUnavailable`. None aliased → `[]`.
- **Errors.** A GraphQL `errors` array, a non-2xx status, unparseable JSON, a missing expected key, or more than `MAX_PAGES = 20` pages → `ModelCatalogUnavailable`.
- **Measured.** Live and read-only on 2026-10-02, with the user's key: 108 collections, 8 production cards, identical to the `wandb` library's listing, in 2 requests and 0.7 s. The library's per-collection route took 109 requests and 13.7 s.

**Alternatives considered with the user:**
- The `wandb` library with its Go binary and a process-group kill.
- The library capped at `<0.26`: an upper bound on an aging line.
- Having the cluster publish the list: a new step in the pipeline repos.

**Trade-off.** We own one query against wandb's GraphQL API, which isn't formally documented. It's the same field and auth that wandb's clients from 0.21 to 0.30 use. A live comparison against the library (task 1.6) is recorded once, and the dialog's muted line makes a runtime break visible.

### D2. Response shape and contracts version

`GET /model-cards` returns, always with both keys:

```
{"cards": [{"root_type": "...", "registry_id": "...", "version": "...",
            "selectors": [{"species": "...", "mode": "...", "age_min": 2, "age_max": 14}, ...]}, ...],
 "fetched_at": "<ISO-8601 UTC, when the served listing was read>",
 "skipped": 0}
```

- **Contracts floor:** raised from `>=0.1.0a5` to `>=0.1.0a9` and re-locked, because `ModelCard.selectors` arrived in a8.
- **Why the bump is safe:** the service's other contracts use, `compute_param_hash` in `pipeline.py`, comes from `hashing.py`, which is unchanged from a5 to a9 (task 1.1 pins the value).
- **Prod already runs a newer version.** The Dockerfile installs from `pyproject.toml`, not the lock, so the deployed image likely already resolves a9. No other dependency is added.

### D3. Cache, background refresh, backoff, rate limit and auth (revised after the PR #1028 review; user decision C)

The first version waited on a lock and never served an expired listing. The PR review found three problems with it:
- httpx's 5 s timeout is per network phase, not per request, so a probe held the lock for 18.8 s with a trickling response;
- a request waiting behind a failed refresh ran its own listing, answering after 12.8 s instead of 6;
- with open email signup in prod (`.env.prod.defaults`: `ENABLE_EMAIL_SIGNUP=true`, autoconfirm), anyone could tie up the 40-thread pool, `/health` included, and hammer the key while wandb was failing.

**State.** One module-level entry, `(fetched_monotonic, cards, fetched_at, skipped)`; a `_backoff_until` monotonic time; and a single-thread `ThreadPoolExecutor` that runs refreshes, holding the running refresh's `Future`. Time comes from injectable `_monotonic` and `_utcnow`. The service runs one uvicorn worker, so it's one cache per container.

**Serving.**

| Situation | What happens |
|---|---|
| Fresh (under 300 s) | Served without contacting wandb. |
| Stale (300–3600 s) | Served at once with its original `fetched_at`. If no refresh is running and none is backing off, one is submitted to the worker. |
| Over 3600 s, or nothing held | A cold request submits a refresh (unless one is running or backing off) and waits on its `Future` for at most `COLD_WAIT_SECONDS` (6), then answers 503. The refresh keeps going on the worker, so the next request finds the result. |
| Cold during a backoff | 503 at once, without contacting wandb. |

**Backoff.** A failed refresh sets `_backoff_until` to now + 60 s, or + 300 s on a wandb 401, 403 or 429, and never replaces a held listing.

**Bounded refresh.** The listing streams each response and checks the 15 s deadline (`REFRESH_DEADLINE_SECONDS`) before each page and as each body chunk arrives. httpx's 5 s per-phase timeout covers connect and write, and stalls between chunks. So a trickling body is cut off at the deadline, which closes the probe's 18.8 s case.
- A hung worker can still occupy that one thread, but never a request thread.
- DNS lookup isn't covered; that's a stated limit.

**Waiting.** Requests never wait on a lock: they wait on a `Future` with a timeout, or not at all. At most one request thread per cold request is held, for at most 6 s, and stale or fresh requests hold none.

**Startup warm-up (user decision).** The FastAPI `lifespan` submits one refresh without waiting. It's skipped when the key is unset or blank.

**Key handling.** `WANDB_API_KEY` that's unset or whitespace-only → `ModelCatalogNotConfigured`, and no request is made. The value is sent as-is, because wandb's own clients use the raw value. A 401 is logged with a hint to check the key.

**Rate limit (user decision C).** The route doesn't spend the shared limit (5 per 60 s), which would cause 429s on Confirm. It has its own: `auth.enforce_rate_limit(user_id, limit=MODEL_CARDS_RATE_LIMIT, scope="model-cards")`, 60 per 60 s by default (`WORKFLOWS_MODEL_CARDS_RATE_LIMIT`). This follows #1004's `folder-check` scope. A 429 reaches the dialog as the proxy's 502, so the dialog shows the muted line.

**Timing budget.**

| Stage | Limit |
|---|---|
| one network phase | 5 s |
| whole listing | 15 s |
| a cold request waiting for a refresh | 6 s |
| web proxy → workflows | 8 s |
| dialog → proxy | 10 s |

The nesting that matters is 6 s < 8 s < 10 s. The 15 s limit caps the worker, not any request.

**Auth.** `Depends(require_supabase_user)`, like every route.

**Errors.** Both are 503 with a fixed detail; causes, never the key, go to the log only.
- `ModelCatalogNotConfigured` → "The model catalog isn't configured in this environment."
- Anything else → "Couldn't read the model catalog."

**The trigger switch.** The upstream route doesn't check `CYL_PIPELINE_TRIGGER_ENABLED`; the web proxy does.

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

**Block (user decision).** When the card read returned cards with `skipped` = 0, at least one model group exists, and **every** model group is no model, a blocking reason disables Confirm:
- "*None of these scans has a production model for its species and age, so the pipeline can't produce results.*" (singular at N = 1: "*This scan has no production model …*")
- When `skipped` > 0 (user decision, PR #1028 review), the list may be incomplete: Bloom's contracts version can drift from predict's `==0.1.0a9`, because the image installs from `pyproject.toml`. The no-model warning is shown instead of the block, so the check fails open.
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

- **The first open after a cache expiry leads a refresh.** That's under 1 s when healthy (measured 0.7 s); up to the 15 s deadline when wandb is slow, in which case that dialog shows the muted line.
- **We own the GraphQL query.** If wandb changes the `artifactMembership` field or Basic auth, the endpoint answers 503 and the dialog shows the muted line until the query is updated. Task 9.3's live check covers each deploy.
- **"Covered" means some root type has a model, not that traits can run.** A traits pipeline needs every root type it requires (Dicot: primary + lateral; YoungerMonocot: primary + crown). If one root type's card is demoted or narrowed, or a species gains model cards but no `PipelineCard`, the dialog shows nothing while traits fail. None of this applies to today's cards; phase 2's shared matcher (#13, #14) would check it.
- **Traits windows could diverge from model windows.** The dialog reads model cards only. Phase 2's shared catalog removes the gap.
- **Past-window results are unmarked in exports.** A day-28 arabidopsis result has the same recipe key as a day-10 one, because the weights are the same. The #865 export doesn't mark it. It's out of scope here and was raised with the user.
- **Rebase hotspot.** An in-flight `feat/rnaseq-s3-folder-service` edits `services/workflows/main.py` and `README.md`.

## Migration

- No database change.
- **Rollback:** revert the squash commit. Compose, `deploy.yml` and the code revert together; leftover secrets are harmless.
- **No-revert off switch:** set the secret to an invalid value. The endpoint answers 503, the dialog shows the muted line, and Confirm keeps working. A blank value can't be used, because validation rejects it.
