# Design: add-cyl-pipeline-model-window-warning

## Context

bloom#971 phase 1 was decided with the user on 2026-10-01 ([decisions](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/971#issuecomment-5937500723), [update](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/971#issuecomment-5938030166)):

- A scan older than every model window for its species and mode runs with that species' highest-age window, for models and traits pipeline alike. There's no upper limit.
- These scans are included **by default**, with a warning in the confirm dialog.
- Younger-than-window scans and species with no cards are unchanged (#994, #993).
- No new provenance marking.
- No user param overrides.

**Cluster side (live):**
- **predict `choose_models`** (talmolab/sleap-roots-predict#50, `79939ee`). `past_window_age(params, cards, overrides)` returns the largest `age_max` among selectors with the scan's species and mode, taken across all cards. It returns that value only when the scan's age is above it and some non-overridden root type has a card for that species and mode. Matching then uses that age, while the params keep the real age.
- **traits `choose_pipeline`** (talmolab/sleap-roots#272, `426ad4d`). The same rule, over `pipeline_selection.yaml`.
- Both are pinned in sleap-roots-pipeline (#112 traits, #113 predict). `argo template update` ran for both, traits first, per #115.

**Bloom side today:**
- Prod's trigger is on (`CYL_PIPELINE_TRIGGER_ENABLED=true` on main).
- So past-window scans already run from Bloom with nothing on screen saying so. This change adds that warning.

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
- The dialog names every parameter group the cluster will predict past its window, from the same card data and rule as predict.
- Defaults stay one click. Neither the warning nor a failed check ever disables Confirm.
- A slow or broken registry can't degrade the workflows service.

**Non-Goals**
- Choosing models (phase 2, #897) and suggestions for species with no cards (#993).
- Younger-than-window scans (#994).
- A shared matcher in sleap-roots-contracts (#13, #14).
- Provenance stamping.
- A server-side preview (#898).
- Changes to the trigger request, Argo submission or stage-in.

## Decisions

### D1. Read the registry with the `wandb` library, bounded in time (user decision)

`services/workflows/model_cards.py` reads the registry the way predict's `WandbRegistrySource.list_cards` does:

- **Where:** project `"eberrigan-salk-institute-for-biological-studies-org/wandb-registry-sleap-roots-models"`, with alias `production`. These are module constants with no environment override.
- **How:** `api.artifact_collections(project_name=PROJECT, type_name="model")`, then `api.artifacts(type_name="model", name=f"{PROJECT}/{collection.name}")`. Keep only artifacts whose `aliases` include `production`.
- **Card build:** `ModelCard.model_validate({**metadata, "registry_id": qualified_name.split(":")[0], "version": artifact.version, "weights_checksum": artifact.digest})`.
- **One bad card:** an artifact that fails validation is skipped and logged.
- **All bad:** if alias-carrying artifacts exist and none validates, the module raises `ModelCatalogUnavailable`. This mirrors predict's `NoReadableModelCardsError`.
- **None at all:** zero alias-carrying artifacts returns `[]`. The route serves it, and the dialog treats it as unknown (D5).
- **Construction:** `_api(key)` is the only place that imports `wandb`. It returns `wandb.Api(api_key=key, timeout=5)`. The key is `os.environ["WANDB_API_KEY"].strip()`; unset or blank raises `ModelCatalogNotConfigured` before `_api` is called.
- **No network at import:** importing `model_cards` or `main` never imports `wandb`.
- **Why the 5 s timeout:** wandb's public-API default is 19 s per call, with retries for up to 20 s. A cold listing makes about 3 + N calls (login check, viewer, collections, artifacts). The timeout caps each call.

**Alternatives rejected with the user:**
- Calling wandb's GraphQL API through `httpx`: undocumented, and we'd re-implement the alias and pinning logic.
- Having the cluster publish the list: a new step in the pipeline repos.

### D1b. Image: wandb directories, telemetry and bundled binaries

All of these go in `services/workflows/Dockerfile`, not in compose. They apply to all 7 containers that share the image and cause no compose churn.

- **Directories.** `ENV WANDB_CONFIG_DIR=/tmp/wandb/config WANDB_CACHE_DIR=/tmp/wandb/cache WANDB_DATA_DIR=/tmp/wandb/data WANDB_DIR=/tmp`. The `workflows` container is `read_only: true`, with only a `/tmp` tmpfs, and runs as a system user (`adduser --system bloom`) whose home isn't writable.
- **Quiet.** `ENV WANDB_SILENT=true`.
- **Telemetry.** `ENV WANDB_ERROR_REPORTING=false`. `wandb.Api()` otherwise starts a Sentry reporter tagged with the entity and the viewer's email.
- **Binaries (user decision).** After `uv pip install`, delete wandb's `bin/wandb-core` (Go, about 43 MB) and `bin/gpu_stats` (Rust, about 8 MB). They serve `wandb.init` and GPU metrics; registry listing over `wandb.Api()` is plain HTTP from Python.
  - **Why:** CI's Trivy step fails on any CRITICAL in the `workflows` image (`pr-checks.yml`, `exit-code: '1'`, on every PR). Go stdlib CRITICALs in bundled binaries are common; `.trivyignore` already carries one for the Supabase CLI binary. A `.trivyignore` edit must be its own PR (`lint_cve_isolation.sh`).
  - **Condition:** the deletion is kept **only if** the container smoke test (task 1.6) lists the cards with the binaries removed. If it fails, the binaries stay and task 1.7's Trivy result decides what happens next, with the user.

### D2. Response shape and contracts version

`GET /model-cards` returns, always with both keys:

```
{"cards": [{"root_type": "...", "registry_id": "...", "version": "...",
            "selectors": [{"species": "...", "mode": "...", "age_min": 2, "age_max": 14}, ...]}, ...],
 "fetched_at": "<ISO-8601 UTC, when the cached listing was read>"}
```

- **Left out:** no other card fields.
- **Contracts floor:** raised from `>=0.1.0a5` to `>=0.1.0a9` and re-locked, because `ModelCard.selectors` arrived in a8.
- **Why the bump is safe:** the service's other contracts use, `compute_param_hash` in `pipeline.py`, comes from `hashing.py`, which is unchanged from a5 to a9. Task 1.1 pins the hash value across the bump.
- **Prod already runs a newer version.** The Dockerfile installs from `pyproject.toml`, not `uv.lock`, so the deployed image likely already resolves contracts a9. The bump realigns CI's lock with that.

### D3. Cache, bounded waiting, auth and rate limit

**Cache.**
- A module-level entry `(fetched_monotonic, cards, fetched_at)` with a TTL of 300 s; a refresh is due when `now - fetched_monotonic >= 300`.
- A `threading.Lock` makes refreshes single-flight.
- Time comes from injectable `_monotonic` and `_utcnow`, so tests never patch `time.monotonic`.
- The service runs one uvicorn worker (`tests/unit/test_workflows_single_worker.py`), so it's one cache per container.

**Bounded waiting.**
- A caller that finds the cache cold or expired acquires the lock with `timeout=8`. If it can't get the lock within 8 s, it raises `ModelCatalogUnavailable`, which becomes a 503. Once it has the lock, it re-checks the cache before listing.
- Combined with the 5 s per-call wandb timeout, a slow or hung registry can't pile requests up in the shared anyio threadpool. That pool is 40 threads, and every sync route uses it, `/health` included.

**Failure.**
- A failed listing isn't cached.
- An expired listing isn't served after a failed refresh: the dialog fails soft (D5), and serving a stale catalog would hide a broken key.

**Auth.** `Depends(require_supabase_user)`, like every route. The endpoint sits behind Caddy's public `/workflows/*`, and auth keeps it from being an anonymous way to make the server call wandb.

**Rate limit.** Not applied.
- The precedent is the plate-video progress route (`main.py:127-139`).
- The shared limiter is 5 calls per 60 s per user across every route (`auth.py:24-25`), so spending it on each dialog open would cause 429s on Confirm.
- The cache bounds the upstream cost: at most one listing per 5 minutes per container.

**Errors (503, with a fixed detail; the cause goes to the log, never the body).**
- `ModelCatalogNotConfigured` → "The model catalog isn't configured in this environment."
- Anything else → "Couldn't read the model catalog."

**The trigger switch.** The upstream route doesn't check `CYL_PIPELINE_TRIGGER_ENABLED`. Cards aren't sensitive and the cost is cache-bounded, so an authenticated direct call while the trigger is off is harmless. The web proxy (D4) does check the switch.

### D4. Web proxy route, server/client split

`web/app/api/cyl/pipeline/model-cards/route.ts` exports only `GET`, with `dynamic = "force-dynamic"` and `runtime = "nodejs"`.

**Check order:**
1. `isPipelineTriggerEnabled()` → 503 with the trigger route's text; `getSession` isn't called.
2. `getSession()` without an access token → 401.
3. `fetch(`${WORKFLOWS_URL ?? "http://workflows:5100"}/model-cards`)` with the bearer token, `redirect: "manual"`, and a 10 s timeout from `AbortController` + `setTimeout`. That timer can be driven by vitest's fake timers, unlike `AbortSignal.timeout`.

**Responses:**
- A 200 whose JSON passes `isCardList` (D5) and has a string `fetched_at` is passed through.
- Any other status, a 3xx, non-JSON, a bad shape or a network error → 502 with a fixed detail.
- A timeout → 504.

**File layout:**
- Server-only helpers (forward, map) live in `lib/cyl-pipeline/model-cards-proxy.ts`, mirroring `trigger-proxy.ts`.
- Client-safe code (`isCardList`, `fetchModelCards`) lives in `lib/cyl-pipeline/model-cards.ts`, which the dialog imports. The client bundle never pulls in server code.

**No Origin check, unlike the trigger proxy.**
- Browsers don't send `Origin` on a same-origin GET, so `isSameOrigin` would reject every real request.
- The route only reads non-sensitive data, changes nothing and returns nothing per user. The Cell Ranger logs GET proxy is the precedent.
- `route.test.ts` pins the trigger route to POST only, which is why this is a sibling route.

### D5. Dialog behaviour

**Fetch.**
- `fetchModelCards()` calls the proxy with a 10 s `AbortController` timeout, clearing its timer in `finally`. The timeout also covers reading the body.
- It returns the card array on success and `null` otherwise. It never throws.
- An **empty** array is returned as `[]`, and the dialog treats it the same as `null` (user decision: zero production cards means a registry problem).

**Load and settle.**
- The dialog starts the read in its load effect, outside the `Promise.all` that drives the failed state, behind the same `active` unmount guard.
- Confirm stays disabled until enumeration, the pre-check, the concurrent-run query and the card read have all settled.
- The card read always settles within 10 s and never sets the failed state. The precedent is `getSession().catch(() => null)`.
- **Tests:** the dialog tests mock this module with `vi.mock` and a `vi.hoisted` controllable function, so their `fetchSpy` assertions, which expect exactly one POST, are unchanged.

**Rule.**
- `pastWindowGroups(groups, cards)` in `lib/cyl-pipeline/model-windows.ts` is pure. It's separate from `params-summary.ts`, whose shape is pinned by its test and documented as "throwaway … #897 must not extend it here".
- For each `ParamsGroup` `{species, mode, age, count}`:
  - `max` = the largest `age_max` over every selector of every card with `selector.species === group.species && selector.mode === group.mode`;
  - the group is past its window iff `max` exists and `group.age > max`;
  - the result keeps `{species, age, count, max}` in the groups' order (count descending, as `paramsSummary` sorts).
- This equals predict's `past_window_age` with no overrides; Bloom sends none.

**Which scans count (user decision).**
- The dialog calls `paramsSummary` a second time, over **scans with images only** (`withImages`), and passes those groups to `pastWindowGroups`.
- Scans with no images fail at stage-in and never reach predict, so they aren't described as "predicted with the nearest models". The no-images line already counts them.
- Scans with metadata problems are already absent from `paramsSummary`'s groups.
- The Parameters list itself still uses every scan, as today.

**Display.** One block, `data-testid="past-window"`, placed as the last part of the warnings item, after the stage-in and no-images lines and before the concurrent runs:

> `<n> scans are past their models' validated age and are predicted with the nearest models:` (at n = 1: `1 scan is past its models' validated age and is predicted with the nearest models:`)
> `<species> · day <age> — models validated up to day <max> (<count>)`, one line per group

Notes:
- Every group is listed, uncollapsed, in `text-amber-800` like the neighbouring warnings.
- "Validated up to day N" names only the window's top, because that's all the rule uses.
- It avoids the dialog's banned phrases (`/will run|will be skipped|reused/i`).

**When no warning is possible.** When the card read gave `null` or `[]`, **and** at least one parameter group exists, a muted `text-stone-500` line shows instead: "*Couldn't check the models' age ranges.*" (`data-testid="past-window-unknown"`). With no parameter groups at all, nothing is shown. While the read is pending, neither testid is shown.

**Caption (user decision).** The Parameters caption becomes "*Parameters come from each scan's metadata. Choosing models isn't supported yet*", still linking bloom#897, which is now phase 2 of #971 (choosing models). The old text, "overrides aren't supported yet", promised param overrides, which #971 dropped.

**Relation to #898.** The parameter groups are client-side and documented as throwaway once a server-side preview exists. This warning is a function of those groups plus the card list, so it moves server-side with them. Until then it duplicates predict's rule in TypeScript, pinned by a cross-check against predict's own test table (task 5.2). Phase 2 replaces both copies with contracts' shared matcher (#13).

### D6. Spec deltas: ADDED only, with explicit cross-references

**Dialog.** The confirm-dialog content is specified by `cyl-pipeline-ui` "Confirm dialog shows read-only resolved params and a pre-check, without predicting skips". It has a closed list, "It SHALL display, in this order: 1–7", plus the caption in item 6. The unarchived `fix-cyl-pipeline-runs-ui-955` MODIFIES that requirement, and two unarchived MODIFYs of one requirement silently revert each other at archive (`--strict` can't see it).

So this change ADDS "Confirm dialog SHALL warn about scan groups past their models' validated age; neither the warning nor a failed check SHALL disable confirm", which says explicitly:
- the block **extends item 3**, as its last part, and the list's numbering and other items are unchanged;
- the card read joins the settle set;
- it **supersedes item 6's caption text**, with item 6's other content unchanged.

Task 9.6 files a follow-up issue to fold the two requirements into one once both changes are archived.

**Endpoint.** It gets a new capability, `cyl-model-catalog`, rather than the POST-only `cyl-pipeline-trigger`. The catalog is its own concern, and phase 2 will grow it. Its three ADDED requirements are split by concern: access, listing and errors, cache.

### D7. Secret wiring (workflows only; required in both environments)

| File | Change |
|---|---|
| `docker-compose.prod.yml` `workflows.environment` | `WANDB_API_KEY: ${WANDB_API_KEY}`. `scripts/validate_env.sh` requires every `${VAR…}` in this file to be non-blank, even with a `:-` default, so the key is **required in prod and staging**, which both deploy from this file (user decision). |
| `docker-compose.dev.yml` `workflows.environment` | `WANDB_API_KEY: ${WANDB_API_KEY:-}`. Optional in dev, where the dialog shows the muted line. `.env.dev.example` gets a blank `WANDB_API_KEY=` with a comment, in the same form as `WORKFLOWS_K8S_TOKEN=`. |
| `.github/workflows/deploy.yml` | Add `WANDB_API_KEY=${{ secrets.PROD_WANDB_API_KEY }}` to the prod heredoc and `…STAGING_WANDB_API_KEY` to the staging heredoc. `scripts/verify_env_parity.py` checks they match. |
| `tests/unit/test_env_defaults.py` | Add `WANDB_API_KEY` to `SENSITIVE_INVENTORY`. Never add it to `.env.*.defaults`. |
| `tests/unit/test_video_worker_containers.py` | The worker-parity test drops `WANDB_API_KEY` from the expected (`workflows`) side, next to `WORKFLOWS_CORS_ORIGINS`. That asserts the video workers don't have it. |
| new `tests/unit/test_wandb_key_scope.py` | Three checks: `WANDB_API_KEY` appears only in `workflows.environment` in both compose files; it has the exact values above; and both `deploy.yml` heredocs contain their line. Nothing checks that today, and `verify_env_parity.py` only checks prod/staging symmetry. |

**What a missing secret does.** It aborts that environment's deploy at validation, before containers change, so the running stack is untouched. Until both secrets exist, every staging deploy is blocked (anyone's), and so is the next prod promotion. The user adds both before merge (task 0.3; gate 9.2).

**Egress.** Outbound HTTPS from `bloom_v2_staging-workflows-1` to `api.wandb.ai` was checked on 2026-10-02 (HTTP 404 from the API root, so the connection was made). `bloom_v2_prod-workflows-1` runs on the same host.

**Docs.**
- `services/workflows/README.md`: the endpoint, the key in Provisioning and Configuration, and corrections to Auth model Layer 1 ("rate limit shared across every route") and Layer 2 ("no privileged credential").
- `DEV_SETUP.md`: optional dev keys.
- `.env.prod.defaults` header and `scripts/setup-env-secrets.sh` comment: replace their hand-kept, already-stale secret lists with a pointer to the `deploy.yml` heredocs, rather than adding a seventh drifting entry.
- `PROD_SETUP.md` secrets table: the same pointer, plus "service-specific secrets are in each service's README".
- `.env.staging.defaults` has no secret list and isn't touched.

### D8. Merge gates

1. **The cluster re-pin is live.** Satisfied: srp #112, #113 and #115 (task 9.1 records it).
2. **Both GitHub secrets exist.** The user confirms; names are checked with `gh secret list` (task 9.2).

The PR is opened as a **draft** and marked ready only after both gates. Merging is the user's.

## Risks / Trade-offs

- **A cold read after a restart may be slow.** A cold listing is several wandb calls. Usually the dialog's 10 s budget covers it; when it doesn't, the user sees the muted line once and the next open finds the cache warm. Task 9.3 measures the cold read on staging. If it's above about 5 s, warming the cache at startup becomes a follow-up (drafted, not posted).
- **Image size and audit surface.**
  - New packages: requests, urllib3, charset-normalizer, protobuf, sentry-sdk, gitpython, gitdb, smmap and platformdirs. A `pip-audit` finding fails CI until resolved.
  - wandb caps `protobuf<7`, so an advisory fixed only in 7.x would have no fix path.
  - Deleting the binaries (D1b) removes most of the size.
- **The Dockerfile installs from `pyproject.toml`, not the lock.**
  - So the deployed image can get a newer `wandb` than CI's lock, and the unit tests fake `_api`, so an API change in a new wandb release would surface only at runtime (the dialog's muted line).
  - This is existing behaviour for every dependency here and stays out of scope; a lock-based install would change all 7 containers' builds.
  - Task 9.3's live check, and the muted line, make a break visible.
- **Traits windows could diverge from model windows.** The dialog reads model cards only, and today `pipeline_selection.yaml` has the same windows. Phase 2's shared catalog removes the gap.
- **Rebase hotspot.** An in-flight `feat/rnaseq-s3-folder-service` edits `services/workflows/main.py` and `README.md`; whichever merges second rebases.

## Migration

- No database change.
- **Rollback:** revert the squash commit. Compose, `deploy.yml` and the code revert together; leftover GitHub secrets are harmless.
- **No-revert off switch:** set the secret to an invalid value. The endpoint answers 503, the dialog shows the muted line, and Confirm keeps working. A blank value can't be used, because validation rejects it.
