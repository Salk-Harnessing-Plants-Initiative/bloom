## ADDED Requirements

### Requirement: `GET /model-cards` SHALL list the production model cards from the wandb registry, cached, without the shared rate limit
`services/workflows/main.py` SHALL provide `GET /model-cards`, reachable externally as `GET /workflows/model-cards` once Caddy strips the prefix. The route SHALL require a valid Supabase user JWT (`Depends(require_supabase_user)`). It SHALL NOT call `enforce_rate_limit`.

**Listing.** It SHALL list the wandb model registry `sleap-roots-models` of entity `eberrigan-salk-institute-for-biological-studies` with the `wandb` library:
- keep only `model` artifacts whose aliases include `production`;
- build each as a `sleap_roots_contracts.ModelCard` (contracts `>=0.1.0a9`) from the artifact's metadata plus `registry_id` (its qualified name without `:version`), `version` and `weights_checksum` (its digest).

**Response.** It SHALL respond `200` with `{"cards": [...], "fetched_at": <ISO-8601 UTC>}`. Each card is `{root_type, registry_id, version, selectors: [{species, mode, age_min, age_max}]}`, and no other card field.

**Bad cards.** An artifact that fails validation SHALL be skipped and logged. If at least one alias-carrying artifact exists and none validates, the route SHALL respond `503`. Zero alias-carrying artifacts SHALL give `{"cards": []}`.

**Missing key.** When `WANDB_API_KEY` is unset or blank, the route SHALL respond `503` "*The model catalog isn't configured in this environment.*" without calling wandb.

**Registry failure.** Any other failure to read the registry SHALL respond `503` "*Couldn't read the model catalog.*" and log the cause.

**Cache.** A successful listing SHALL be cached in-process for 300 seconds; requests within that time make no registry call. Concurrent requests on a cold cache SHALL make at most one registry call. A failed listing SHALL NOT be cached, and an expired listing SHALL NOT be served after a failed refresh.

#### Scenario: An authenticated caller gets the production cards
- **WHEN** an authenticated request is made to `GET /workflows/model-cards` and the registry holds an artifact aliased `production` whose metadata has `root_type: "lateral"` and `selectors: [{species: "arabidopsis", mode: "cylinder", age_min: 2, age_max: 14}]`
- **THEN** the response is `200` and `cards` contains `{root_type: "lateral", registry_id, version, selectors: [{species: "arabidopsis", mode: "cylinder", age_min: 2, age_max: 14}]}`
- **AND** artifacts without the `production` alias are absent

#### Scenario: The cache answers repeat requests
- **WHEN** two requests arrive 60 seconds apart
- **THEN** wandb is listed once
- **AND** a request more than 300 seconds after the listing lists wandb again

#### Scenario: Opening the dialog never spends the trigger's rate limit
- **WHEN** a user calls `GET /model-cards` more than 5 times within 60 seconds
- **THEN** every call is answered without `429`
- **AND** the user's next `POST /pipeline` is not refused for rate limit because of them

#### Scenario: A missing key is reported without calling wandb
- **WHEN** `WANDB_API_KEY` is unset and an authenticated caller requests `GET /model-cards`
- **THEN** the response is `503` with "*The model catalog isn't configured in this environment.*"
- **AND** no wandb API object is created

#### Scenario: One non-conforming artifact is skipped
- **WHEN** the registry holds two `production` artifacts and one has flat (pre-selector) metadata
- **THEN** the response is `200` with the one valid card

#### Scenario: No readable card is an error, not an empty catalog
- **WHEN** every `production` artifact fails `ModelCard` validation
- **THEN** the response is `503` and the failure is not cached

#### Scenario: An unauthenticated request is rejected before any registry call
- **WHEN** `GET /model-cards` is called without a valid Supabase user JWT
- **THEN** the response is `401` and wandb is not called
