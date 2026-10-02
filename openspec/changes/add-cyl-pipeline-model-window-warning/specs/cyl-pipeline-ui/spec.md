## ADDED Requirements

### Requirement: Model-card proxy `GET /api/cyl/pipeline/model-cards` SHALL forward the caller's session to the workflows service and return only a well-formed card list
`web/app/api/cyl/pipeline/model-cards/route.ts` SHALL export only `GET`. Its helpers live in `web/lib/cyl-pipeline/model-cards.ts`, not in the route module. It SHALL, in this order:

1. Respond `503` with the trigger proxy's not-enabled text when `isPipelineTriggerEnabled()` is false.
2. Respond `401` when `getSession()` has no access token.
3. Call `GET ${WORKFLOWS_URL ?? "http://workflows:5100"}/model-cards` with `Authorization: Bearer <access token>`, `redirect: "manual"` and a 10-second timeout.

It SHALL pass through a `200` whose body is `{cards: [...]}`, where each card has a string `root_type`, `registry_id` and `version` and a `selectors` array of `{species: string, mode: string, age_min: integer, age_max: integer}`.

It SHALL respond `502` with a fixed detail to any other upstream status, a body of another shape, or a network error, and `504` to a timeout. It SHALL NOT require an `Origin` header (same-origin browser GETs send none). The trigger proxy `POST /api/cyl/pipeline` stays POST-only.

#### Scenario: A signed-in user gets the card list
- **WHEN** the switch is on, the caller has a session, and upstream answers `200` with a well-formed card list
- **THEN** the route answers `200` with that list
- **AND** upstream received the caller's bearer token

#### Scenario: Switched off, the route makes no upstream call
- **WHEN** `CYL_PIPELINE_TRIGGER_ENABLED` is not `"true"`
- **THEN** the route answers `503` and does not call the workflows service

#### Scenario: Upstream failures become fixed caller-safe errors
- **WHEN** upstream answers `503`, answers `200` with a card whose `selectors` is not an array, or does not answer within 10 seconds
- **THEN** the route answers `502`, `502` and `504` respectively, with a fixed detail that does not echo upstream text

### Requirement: Confirm dialog SHALL warn about scan groups past their models' validated age, and SHALL NOT block confirm on that check
The confirm dialog SHALL read the production model cards through `GET /api/cyl/pipeline/model-cards`, alongside its other checks, with a 10-second timeout. Confirm SHALL stay disabled until that read has also settled, in addition to the reads named in "Confirm dialog shows read-only resolved params and a pre-check, without predicting skips".

**Failure.** A failed, malformed or timed-out card read SHALL NOT put the dialog in its failed state and SHALL NOT disable confirm. The dialog SHALL instead show the muted line "*Couldn't check the models' age ranges.*"

**Rule.** For each resolved-params group `(species, mode, age)` (as defined by that requirement), let `max` be the largest `age_max` among every selector, on every card, whose `species` and `mode` equal the group's. A group is **past its window** when `max` exists and `age > max`. This matches sleap-roots-predict's `past_window_age` without overrides. Groups whose age is below every window, and groups whose species and mode have no selector, are not past their window.

**Display.** When at least one group is past its window, the dialog SHALL show one warning block directly after the stage-in warning lines and before the concurrent runs. It lists every such group, uncollapsed, in the resolved-params order:
- Heading: "*N scans are past their models' validated age and are predicted with the nearest models:*" (N = the summed counts). At N = 1: "*1 scan is past its models' validated age and is predicted with the nearest models:*".
- One line per group: "*<species> · day <age> — models validated up to day <max> (<count>)*".

The warning SHALL NOT disable confirm. When the read succeeds and no group is past its window, nothing is shown. The phrases "will run", "will be skipped" and "reused" remain forbidden in the dialog.

#### Scenario: A day-28 arabidopsis group is named with the window's top
- **WHEN** the target holds 60 arabidopsis scans at day 21, 30 at day 28 and 120 at day 14, and the cards' arabidopsis·cylinder selectors reach `age_max` 14
- **THEN** the dialog shows "*90 scans are past their models' validated age and are predicted with the nearest models:*"
- **AND** the lines "*arabidopsis · day 21 — models validated up to day 14 (60)*" and "*arabidopsis · day 28 — models validated up to day 14 (30)*"
- **AND** the day-14 group is not listed
- **AND** confirm is enabled once the other checks pass

#### Scenario: Rice past both windows is named with the higher window
- **WHEN** the target holds 5 rice scans at day 18 and the rice·cylinder selectors are 2–5 and 6–10
- **THEN** the dialog shows "*5 scans are past their models' validated age and are predicted with the nearest models:*"
- **AND** the line "*rice · day 18 — models validated up to day 10 (5)*"

#### Scenario: One past-window scan uses the singular heading
- **WHEN** the target holds 1 soybean scan at day 10 and the soybean·cylinder selectors reach `age_max` 8
- **THEN** the dialog shows "*1 scan is past its models' validated age and is predicted with the nearest models:*"
- **AND** the line "*soybean · day 10 — models validated up to day 8 (1)*"

#### Scenario: Younger scans and species without cards get no warning
- **WHEN** the target holds canola scans at day 0 and sorghum scans at day 10, and the cards have canola selectors 2–13 and no sorghum selector
- **THEN** no past-window block is shown

#### Scenario: A failed card read does not block the run
- **WHEN** the card read answers `502` or does not settle within 10 seconds
- **THEN** the dialog shows "*Couldn't check the models' age ranges.*"
- **AND** confirm is enabled once the other checks pass, and no failed-state alert is shown

#### Scenario: Confirm waits for the card read
- **WHEN** enumeration, the pre-check and the concurrent-run query have settled but the card read has not
- **THEN** confirm is disabled
