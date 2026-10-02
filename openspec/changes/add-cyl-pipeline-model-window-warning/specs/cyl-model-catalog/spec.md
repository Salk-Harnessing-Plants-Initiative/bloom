## ADDED Requirements

### Requirement: `GET /model-cards` SHALL require a Supabase user and SHALL NOT spend the shared per-user rate limit
`services/workflows/main.py` SHALL provide `GET /model-cards`, reachable externally as `GET /workflows/model-cards` once Caddy strips the prefix. It SHALL require a valid Supabase user JWT (`Depends(require_supabase_user)`) and SHALL reject an unauthenticated request before any registry call. It SHALL NOT call `enforce_rate_limit`. Its handler SHALL be a synchronous function, so the blocking registry call runs in the threadpool rather than on the event loop.

#### Scenario: An unauthenticated request is rejected before any registry call
- **WHEN** `GET /model-cards` is called without a valid Supabase user JWT
- **THEN** the response is `401`
- **AND** the card listing is not called

#### Scenario: Repeated calls are never rate limited and never consume the trigger's budget
- **WHEN** an authenticated user calls `GET /model-cards` 6 times within 60 seconds with the real limiter in place
- **THEN** every call answers `200`
- **AND** `enforce_rate_limit` is never called for these requests, so the user's per-user hit count is unchanged

### Requirement: `GET /model-cards` SHALL return the validated production model cards from the wandb registry, or `503` when they cannot be read
The route SHALL list the wandb model registry `sleap-roots-models` of entity `eberrigan-salk-institute-for-biological-studies` with the `wandb` library:
- create the API client with the stripped `WANDB_API_KEY` and a per-request timeout of 5 seconds;
- keep only `model` artifacts whose aliases include `production`;
- build each as a `sleap_roots_contracts.ModelCard` (contracts `>=0.1.0a9`) from the artifact's metadata plus `registry_id` (its qualified name without `:version`), `version` and `weights_checksum` (its digest).

**Response.** It SHALL respond `200` with exactly the keys `cards` and `fetched_at`:
- `fetched_at` is the ISO-8601 UTC time the served listing was read;
- each card has exactly the keys `root_type`, `registry_id`, `version` and `selectors`;
- each selector has exactly the keys `species`, `mode`, `age_min` and `age_max`.

**Bad or missing cards.**
- An artifact that fails validation SHALL be skipped and logged.
- If at least one alias-carrying artifact exists and none validates, the route SHALL respond `503`.
- Zero alias-carrying artifacts SHALL give `200` with an empty `cards` list.

**Errors.**
- When `WANDB_API_KEY` is unset or blank, the route SHALL respond `503` "*The model catalog isn't configured in this environment.*" without creating a wandb client.
- Any other failure SHALL respond `503` "*Couldn't read the model catalog.*". It SHALL log the cause and SHALL NOT include it in the response body.
- Importing the service SHALL NOT import `wandb`.

#### Scenario: An authenticated caller gets the production cards
- **WHEN** the registry holds an artifact aliased `production` whose metadata has `root_type: "lateral"` and `selectors: [{species: "arabidopsis", mode: "cylinder", age_min: 2, age_max: 14}]`, and another artifact aliased only `latest`
- **THEN** the response is `200`
- **AND** `cards` holds exactly one card, `{root_type: "lateral", registry_id, version, selectors: [{species: "arabidopsis", mode: "cylinder", age_min: 2, age_max: 14}]}`
- **AND** `fetched_at` parses as an ISO-8601 time in UTC

#### Scenario: One non-conforming artifact is skipped
- **WHEN** the registry holds two `production` artifacts and one has flat (pre-selector) metadata
- **THEN** the response is `200` with the one valid card
- **AND** the skipped artifact is named in the log

#### Scenario: No readable card is an error, not an empty catalog
- **WHEN** every `production` artifact fails `ModelCard` validation
- **THEN** the response is `503` "*Couldn't read the model catalog.*"

#### Scenario: An empty registry is an empty list
- **WHEN** no artifact carries the `production` alias
- **THEN** the response is `200` with `cards: []` and a `fetched_at`

#### Scenario: A missing key is reported without calling wandb
- **WHEN** `WANDB_API_KEY` is unset, or is only whitespace, and an authenticated caller requests `GET /model-cards`
- **THEN** the response is `503` "*The model catalog isn't configured in this environment.*"
- **AND** no wandb client is created

#### Scenario: A registry error is logged, not returned
- **WHEN** listing the registry raises an error whose message is `secret-detail`
- **THEN** the response is `503` "*Couldn't read the model catalog.*" and does not contain `secret-detail`
- **AND** the log contains `secret-detail`

### Requirement: `GET /model-cards` SHALL cache a successful listing for 300 seconds, refresh it at most once at a time, and never wait more than 8 seconds for a refresh
A successful listing SHALL be served from an in-process cache until 300 seconds after it was read; at or after 300 seconds the next request SHALL refresh it.

**One refresh at a time.** Concurrent requests that find the cache cold or expired SHALL cause at most one registry listing. A request that cannot begin or join a refresh within 8 seconds SHALL respond `503` "*Couldn't read the model catalog.*".

**Failures.** A failed listing SHALL NOT be cached. After a failed refresh, an expired listing SHALL NOT be served.

#### Scenario: The cache answers repeat requests
- **WHEN** two requests arrive 60 seconds apart
- **THEN** the registry is listed once and both responses carry the same `fetched_at`
- **AND** a request 300 or more seconds after the listing lists the registry again and carries a new `fetched_at`

#### Scenario: Concurrent cold requests share one listing
- **WHEN** 5 requests arrive together on a cold cache
- **THEN** the registry is listed once and all 5 receive the same cards

#### Scenario: A hung registry does not hold requests indefinitely
- **WHEN** one refresh is blocked inside the registry call and a second request arrives
- **THEN** the second request answers `503` within 8 seconds rather than waiting for the first

#### Scenario: A failure is retried, not cached
- **WHEN** a listing fails and the next request finds the registry readable
- **THEN** the next request lists the registry again and answers `200`

#### Scenario: An expired listing is not served after a failed refresh
- **WHEN** the cached listing is 301 seconds old and the refresh fails
- **THEN** the response is `503`, not the old listing
