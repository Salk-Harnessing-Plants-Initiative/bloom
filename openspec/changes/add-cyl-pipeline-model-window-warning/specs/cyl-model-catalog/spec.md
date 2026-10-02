## ADDED Requirements

### Requirement: `GET /model-cards` SHALL require a Supabase user and SHALL NOT spend the shared per-user rate limit
`services/workflows/main.py` SHALL provide `GET /model-cards`, reachable externally as `GET /workflows/model-cards` once Caddy strips the prefix. It SHALL require a valid Supabase user JWT (`Depends(require_supabase_user)`) and SHALL reject an unauthenticated request before any registry call. It SHALL NOT call `enforce_rate_limit`. Its handler SHALL be a synchronous function, so waiting for a listing happens in the threadpool rather than on the event loop.

#### Scenario: An unauthenticated request is rejected before any registry call
- **WHEN** `GET /model-cards` is called without a valid Supabase user JWT
- **THEN** the response is `401`
- **AND** the card listing is not called

#### Scenario: Repeated calls are never rate limited and never consume the trigger's budget
- **WHEN** an authenticated user calls `GET /model-cards` 6 times within 60 seconds with the real limiter in place
- **THEN** every call answers `200`
- **AND** `enforce_rate_limit` is never called for these requests, so the user's per-user hit count is unchanged

### Requirement: `GET /model-cards` SHALL return the validated production model cards from the wandb registry, read with a bounded direct GraphQL query, or `503` when they cannot be read
**Request.** The service SHALL send `POST https://api.wandb.ai/graphql` with HTTP Basic auth, user `api` and password the value of `WANDB_API_KEY`. Each request SHALL time out after 5 seconds, the whole listing SHALL be abandoned after 15 seconds, and no request SHALL be retried.

**Query.** It SHALL page through the `model` artifact collections of project `wandb-registry-sleap-roots-models` of entity `eberrigan-salk-institute-for-biological-studies-org`, 100 per page, requesting each collection's `artifactMembership(aliasName: "production")` with its `versionIndex` and artifact `metadata`. It SHALL NOT import the `wandb` library.

**Card build.** Each collection with a production membership SHALL be built as a `sleap_roots_contracts.ModelCard` (contracts `>=0.1.0a9`) from its metadata, which is parsed when it arrives as a JSON string, plus:
- `registry_id` = `<entity>/<project>/<collection name>`;
- `version` = `v<versionIndex>`.

**Response.** The route SHALL respond `200` with exactly the keys `cards` and `fetched_at`:
- `fetched_at` is the ISO-8601 UTC time the served listing was read;
- each card has exactly the keys `root_type`, `registry_id`, `version` and `selectors`;
- each selector has exactly the keys `species`, `mode`, `age_min` and `age_max`.

**Bad or missing cards.**
- A collection whose metadata fails validation SHALL be skipped and logged.
- If at least one collection has a production membership and none validates, the route SHALL respond `503`.
- No production membership at all SHALL give `200` with an empty `cards` list.

**Errors.**
- When `WANDB_API_KEY` is unset or contains only whitespace, the route SHALL respond `503` "*The model catalog isn't configured in this environment.*" without sending a request.
- Any of these SHALL produce `503` "*Couldn't read the model catalog.*": a non-2xx status, a GraphQL `errors` array, an unparseable or unexpectedly shaped body, a timeout, the 15-second deadline, or more than 20 pages.
- The cause SHALL be logged and SHALL NOT appear in the response body. The key SHALL NOT be logged.

#### Scenario: An authenticated caller gets the production cards
- **WHEN** the registry has a collection `arabidopsis-lateral` whose production membership is version 0 with metadata `root_type: "lateral"` and `selectors: [{species: "arabidopsis", mode: "cylinder", age_min: 2, age_max: 14}]`, and another collection with no production membership
- **THEN** the response is `200`
- **AND** `cards` holds exactly one card, `{root_type: "lateral", registry_id: "eberrigan-salk-institute-for-biological-studies-org/wandb-registry-sleap-roots-models/arabidopsis-lateral", version: "v0", selectors: [{species: "arabidopsis", mode: "cylinder", age_min: 2, age_max: 14}]}`
- **AND** `fetched_at` parses as an ISO-8601 time in UTC

#### Scenario: The request authenticates the way wandb's client does
- **WHEN** a listing is made with `WANDB_API_KEY` set to `k`
- **THEN** each request is a `POST` to `https://api.wandb.ai/graphql` with HTTP Basic credentials `api` / `k`

#### Scenario: Collections are read page by page
- **WHEN** the first page reports `hasNextPage: true` with an `endCursor`
- **THEN** a second request is sent with that cursor, and cards from both pages are returned

#### Scenario: One non-conforming card is skipped
- **WHEN** two collections have production memberships and one has flat (pre-selector) metadata
- **THEN** the response is `200` with the one valid card
- **AND** the skipped collection is named in the log

#### Scenario: No readable card is an error, not an empty catalog
- **WHEN** every production membership's metadata fails `ModelCard` validation
- **THEN** the response is `503` "*Couldn't read the model catalog.*"

#### Scenario: An empty registry is an empty list
- **WHEN** no collection has a production membership
- **THEN** the response is `200` with `cards: []` and a `fetched_at`

#### Scenario: A missing key is reported without a request
- **WHEN** `WANDB_API_KEY` is unset, or is only whitespace, and an authenticated caller requests `GET /model-cards`
- **THEN** the response is `503` "*The model catalog isn't configured in this environment.*"
- **AND** no request is sent

#### Scenario: A slow registry is cut off
- **WHEN** a request to wandb does not answer within 5 seconds, or the listing has run for 15 seconds
- **THEN** the request answers `503` "*Couldn't read the model catalog.*"

#### Scenario: A registry error is logged, not returned
- **WHEN** wandb answers with a GraphQL `errors` array whose message is `secret-detail`
- **THEN** the response is `503` "*Couldn't read the model catalog.*" and does not contain `secret-detail`
- **AND** the log contains `secret-detail`

### Requirement: `GET /model-cards` SHALL cache a successful listing for 300 seconds, warm it at startup, and answer `503` rather than wait more than 6 seconds for another request's refresh
**Cache.** A successful listing SHALL be served from an in-process cache until 300 seconds after it was read; at or after 300 seconds the next request SHALL refresh it.

**Startup.** On startup the service SHALL begin one refresh in a background thread without delaying startup or `/health`. It SHALL skip that refresh when `WANDB_API_KEY` is unset or blank, and a failed startup refresh SHALL leave the cache empty.

**One refresh at a time.** At most one listing SHALL run at a time. Requests that wait on a refresh that succeeds SHALL be served from it. A request that cannot start a refresh, or be served by one, within 6 seconds SHALL respond `503` "*Couldn't read the model catalog.*".

**Failures.** A failed listing SHALL NOT be cached. After a failed refresh, an expired listing SHALL NOT be served.

#### Scenario: The cache answers repeat requests
- **WHEN** two requests arrive 60 seconds apart
- **THEN** the registry is listed once and both responses carry the same `fetched_at`
- **AND** a request 300 or more seconds after the listing lists the registry again and carries a new `fetched_at`

#### Scenario: Startup warms the cache
- **WHEN** the service starts with `WANDB_API_KEY` set and the registry readable
- **THEN** a listing runs in the background and the first request is served from the cache without listing again

#### Scenario: Concurrent cold requests share one listing
- **WHEN** 5 requests arrive together on a cold cache and the listing succeeds
- **THEN** the registry is listed once and all 5 receive the same cards

#### Scenario: A request does not wait long for another's refresh
- **WHEN** one refresh is still running and a second request arrives
- **THEN** the second request is served from that refresh if it succeeds within 6 seconds, and otherwise answers `503` within 6 seconds

#### Scenario: A failure is retried, not cached
- **WHEN** a listing fails and the next request finds the registry readable
- **THEN** the next request lists the registry again and answers `200`

#### Scenario: An expired listing is not served after a failed refresh
- **WHEN** the cached listing is 301 seconds old and the refresh fails
- **THEN** the response is `503`, not the old listing
