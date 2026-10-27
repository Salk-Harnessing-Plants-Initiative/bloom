# cyl-model-catalog Specification

## Purpose
The workflows service's read-only view of the production model cards in the wandb registry (`sleap-roots-models`, alias `production`), served as `GET /model-cards` so the cylinder pipeline confirm dialog can warn about scans past their models' validated age or with no model (bloom#971).

## Requirements
### Requirement: `GET /model-cards` SHALL require a Supabase user and SHALL apply its own per-user rate limit, not the shared one
`services/workflows/main.py` SHALL provide `GET /model-cards`, reachable externally as `GET /workflows/model-cards` once Caddy strips the prefix.

- **Auth.** It SHALL require a valid Supabase user JWT (`Depends(require_supabase_user)`) and SHALL reject an unauthenticated request before any registry call.
- **Rate limit.** It SHALL NOT spend the shared per-user limit. It SHALL apply its own: 60 requests per user per 60-second window, counted under the scope `model-cards` (`WORKFLOWS_MODEL_CARDS_RATE_LIMIT`, default `60`), answering `429` beyond it.
- **Handler.** It SHALL be a synchronous function.

#### Scenario: An unauthenticated request is rejected before any registry call
- **WHEN** `GET /model-cards` is called without a valid Supabase user JWT
- **THEN** the response is `401`
- **AND** the card listing is not called

#### Scenario: Dialog opens never consume the trigger's budget
- **WHEN** an authenticated user calls `GET /model-cards` 6 times within 60 seconds
- **THEN** every call answers `200`
- **AND** the user's shared per-user hit count is unchanged

#### Scenario: Its own limit refuses a flood
- **WHEN** one user calls `GET /model-cards` 61 times within 60 seconds
- **THEN** the 61st call answers `429` without calling the card listing

### Requirement: `GET /model-cards` SHALL return the validated production model cards from the wandb registry, read with a bounded direct GraphQL query, or `503` when they cannot be read
**Request.** A listing SHALL send `POST https://api.wandb.ai/graphql` with HTTP Basic auth, user `api` and password the value of `WANDB_API_KEY`, and SHALL NOT import the `wandb` library.

**Time limits.** Each request SHALL be bounded to 5 seconds per network phase. The whole listing SHALL be abandoned 15 seconds after it started, including while a response body is still arriving. No request SHALL be retried within a listing.

**Query.** It SHALL page through the `model` artifact collections of project `wandb-registry-sleap-roots-models` of entity `eberrigan-salk-institute-for-biological-studies-org`, 100 per page, requesting each collection's `artifactMembership(aliasName: "production")` with its `versionIndex` and artifact `metadata`. A page that reports `hasNextPage` without an `endCursor`, or more than 20 pages, SHALL fail the listing.

**Card build.** Each collection with a production membership SHALL be built as a `sleap_roots_contracts.ModelCard` (contracts `>=0.1.0a9`) from its metadata, which is parsed when it arrives as a JSON string, plus:
- `registry_id` = `<entity>/<project>/<collection name>`;
- `version` = `v<versionIndex>`.

**Response.** The route SHALL respond `200` with exactly the keys `cards`, `fetched_at` and `skipped`:
- `fetched_at` is the ISO-8601 UTC time the served listing was read;
- `skipped` is the number of production memberships that could not be built into a card;
- each card has exactly the keys `root_type`, `registry_id`, `version` and `selectors`;
- each selector has exactly the keys `species`, `mode`, `age_min` and `age_max`.

**Bad or missing cards.**
- A membership whose metadata fails to parse or validate SHALL be skipped, counted in `skipped`, and logged.
- If at least one collection has a production membership and none validates, the listing SHALL fail.
- No production membership at all SHALL give `200` with an empty `cards` list and `skipped: 0`.

**Errors.**
- When `WANDB_API_KEY` is unset or contains only whitespace, the route SHALL respond `503` "*The model catalog isn't configured in this environment.*" without sending a request.
- When no card list can be served, the route SHALL respond `503` "*Couldn't read the model catalog.*". That includes:
  - a non-2xx status;
  - a GraphQL `errors` array;
  - an unparseable or unexpectedly shaped body;
  - a timeout;
  - the 15-second limit;
  - the page guards.
- Every cause SHALL be logged and SHALL NOT appear in the response body. The key SHALL NOT be logged.

#### Scenario: An authenticated caller gets the production cards
- **WHEN** the registry has a collection `arabidopsis-lateral` whose production membership is version 0 with metadata `root_type: "lateral"` and `selectors: [{species: "arabidopsis", mode: "cylinder", age_min: 2, age_max: 14}]`, and another collection with no production membership
- **THEN** the response is `200` with `skipped: 0`
- **AND** `cards` holds exactly one card, `{root_type: "lateral", registry_id: "eberrigan-salk-institute-for-biological-studies-org/wandb-registry-sleap-roots-models/arabidopsis-lateral", version: "v0", selectors: [{species: "arabidopsis", mode: "cylinder", age_min: 2, age_max: 14}]}`
- **AND** `fetched_at` parses as an ISO-8601 time in UTC

#### Scenario: The request authenticates the way wandb's client does
- **WHEN** a listing is made with `WANDB_API_KEY` set to `k`
- **THEN** each request is a `POST` to `https://api.wandb.ai/graphql` with HTTP Basic credentials `api` / `k`

#### Scenario: Collections are read page by page
- **WHEN** the first page reports `hasNextPage: true` with an `endCursor`
- **THEN** a second request is sent with that cursor, and cards from both pages are returned

#### Scenario: A non-conforming card is skipped and counted
- **WHEN** two collections have production memberships and one has flat (pre-selector) metadata
- **THEN** the response is `200` with the one valid card and `skipped: 1`
- **AND** the skipped collection is named in the log

#### Scenario: No readable card is an error, not an empty catalog
- **WHEN** every production membership's metadata fails `ModelCard` validation and no earlier listing is held
- **THEN** the response is `503` "*Couldn't read the model catalog.*"

#### Scenario: An empty registry is an empty list
- **WHEN** no collection has a production membership
- **THEN** the response is `200` with `cards: []`, `skipped: 0` and a `fetched_at`

#### Scenario: A missing key is reported without a request
- **WHEN** `WANDB_API_KEY` is unset, or is only whitespace, and an authenticated caller requests `GET /model-cards`
- **THEN** the response is `503` "*The model catalog isn't configured in this environment.*"
- **AND** no request is sent

#### Scenario: A response that trickles in is cut off
- **WHEN** wandb sends a page's body slowly enough that the listing passes 15 seconds while it is still arriving
- **THEN** the listing fails at the 15-second limit, not when the body ends

#### Scenario: A registry error is logged, not returned
- **WHEN** wandb answers with a GraphQL `errors` array whose message is `secret-detail` and no earlier listing is held
- **THEN** the response is `503` "*Couldn't read the model catalog.*" and does not contain `secret-detail`
- **AND** the log contains `secret-detail`

### Requirement: `GET /model-cards` SHALL serve a cached listing, refresh it one at a time in the background, back off after failures, and never hold a request waiting on wandb for more than 6 seconds
**Fresh.** A successful listing SHALL be served without contacting wandb for 300 seconds after it was read.

**Stale.**
- From 300 seconds until 3600 seconds after it was read, the listing SHALL still be served immediately. If no refresh is running and none is backing off, one SHALL be started in the background.
- After 3600 seconds it SHALL NOT be served.

**One refresh at a time.** Refreshes SHALL run on a single background worker, so at most one listing is in progress.

**Cold requests.** A request with no listing to serve SHALL start a refresh, if none is running and none is backing off, and wait for it at most 6 seconds. It SHALL be served the result if the refresh succeeds in time, and SHALL otherwise respond `503` "*Couldn't read the model catalog.*" while the refresh continues.

**Backoff.**
- After a failed refresh, no new refresh SHALL start for 60 seconds, or 300 seconds after a `401`, `403` or `429` from wandb.
- During a backoff, a request with no listing to serve SHALL respond `503` at once.
- A failed refresh SHALL NOT replace a held listing.

**Startup.** On startup the service SHALL begin one refresh, without delaying startup or `/health`. It SHALL skip that refresh when `WANDB_API_KEY` is unset or blank.

#### Scenario: The cache answers repeat requests
- **WHEN** two requests arrive 60 seconds apart
- **THEN** the registry is listed once and both responses carry the same `fetched_at`

#### Scenario: A stale listing is served while one refresh runs
- **WHEN** a request arrives 301 seconds after the listing was read
- **THEN** it is served that listing at once, with its original `fetched_at`
- **AND** one background refresh starts, and a second request during that refresh starts no other

#### Scenario: A failed refresh keeps the last good listing
- **WHEN** the listing is 301 seconds old and the background refresh fails
- **THEN** later requests are still served that listing until it is 3600 seconds old
- **AND** no new refresh starts for 60 seconds

#### Scenario: An authentication failure backs off longer
- **WHEN** a refresh fails because wandb answers `401`
- **THEN** no new refresh starts for 300 seconds

#### Scenario: Concurrent cold requests share one listing
- **WHEN** 5 requests arrive together with no listing held, and the listing succeeds within 6 seconds
- **THEN** the registry is listed once and all 5 receive the same cards

#### Scenario: A slow registry doesn't hold requests
- **WHEN** no listing is held and the refresh has not finished 6 seconds after a request arrived
- **THEN** that request answers `503` at 6 seconds, and the refresh keeps running in the background

#### Scenario: During a backoff a cold request fails at once
- **WHEN** no listing is held and the last refresh failed 10 seconds ago
- **THEN** a request answers `503` without contacting wandb

#### Scenario: A very old listing is not served
- **WHEN** the only held listing is 3601 seconds old and refreshes keep failing
- **THEN** the response is `503`

#### Scenario: Startup warms the cache
- **WHEN** the service starts with `WANDB_API_KEY` set and the registry readable
- **THEN** a listing runs in the background and the first request is served from it without listing again

