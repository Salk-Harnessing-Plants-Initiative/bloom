## ADDED Requirements

### Requirement: Model-card proxy `GET /api/cyl/pipeline/model-cards` SHALL forward the caller's session to the workflows service and return only a well-formed card list
`web/app/api/cyl/pipeline/model-cards/route.ts` SHALL export only `GET`, with `dynamic = "force-dynamic"` and `runtime = "nodejs"`. Its server-only helpers live in `web/lib/cyl-pipeline/model-cards-proxy.ts`, and the client-safe shape check in `web/lib/cyl-pipeline/model-cards.ts`. It SHALL, in this order:

1. Respond `503` with the trigger proxy's not-enabled text when `isPipelineTriggerEnabled()` is false, without reading the session.
2. Respond `401` when `getSession()` has no access token.
3. Call `GET ${WORKFLOWS_URL ?? "http://workflows:5100"}/model-cards` with `Authorization: Bearer <access token>`, `redirect: "manual"` and a 10-second timeout.

**Pass-through.** It SHALL pass through a `200` whose JSON body has a string `fetched_at` and a `cards` array in which every card has a string `root_type`, `registry_id` and `version`, and a `selectors` array of `{species: string, mode: string, age_min: integer, age_max: integer}`.

**Failures.** It SHALL respond `502` with a fixed detail to:
- any other status, including a redirect;
- a non-JSON body or a body of another shape;
- a network error.

It SHALL respond `504` to a timeout. No error response SHALL include upstream text.

**Origin.** It SHALL NOT require an `Origin` header, because same-origin browser GETs send none. The trigger proxy `POST /api/cyl/pipeline` stays POST-only.

#### Scenario: A signed-in user gets the card list
- **WHEN** the switch is on, the caller has a session, and upstream answers `200` with a well-formed card list
- **THEN** the route answers `200` with that list
- **AND** upstream received the caller's bearer token
- **AND** the request carried no `Origin` header

#### Scenario: Switched off, the route reads nothing
- **WHEN** `CYL_PIPELINE_TRIGGER_ENABLED` is not `"true"`
- **THEN** the route answers `503`, does not read the session, and does not call the workflows service

#### Scenario: No session is refused before any upstream call
- **WHEN** the switch is on and `getSession()` returns no access token
- **THEN** the route answers `401` and does not call the workflows service

#### Scenario: Upstream failures become fixed caller-safe errors
- **WHEN** upstream answers `503` with detail `upstream-text`, answers `200` with a card whose `selectors` is not an array, the fetch rejects with a network error, or upstream does not answer within 10 seconds
- **THEN** the route answers `502`, `502`, `502` and `504` respectively
- **AND** no response body contains `upstream-text`

### Requirement: Confirm dialog SHALL warn about scan groups past their models' validated age; neither the warning nor a failed check SHALL disable confirm
The confirm dialog SHALL extend "Confirm dialog shows read-only resolved params and a pre-check, without predicting skips" as stated here. That requirement's numbered display list and its other items are otherwise unchanged.

**Reading the cards.** The dialog SHALL read the production model cards through `GET /api/cyl/pipeline/model-cards`, alongside its other checks, with a 10-second timeout. Confirm SHALL stay disabled until that read has also settled. The read SHALL settle within 10 seconds and SHALL NOT put the dialog in its failed state.

**Which scans count.** Past-window groups SHALL be computed from the resolved-params groups (as that requirement defines them) of the enumerated scans that have at least one image. Scans with no images are counted only by the existing no-images line.

**Rule.** For such a group `(species, mode, age)`, let `max` be the largest `age_max` among every selector, on every card, whose `species` and `mode` equal the group's. The group is **past its window** when `max` exists and `age > max`. This matches sleap-roots-predict's `past_window_age` without overrides. Not past their window:
- a group whose age equals `max`;
- a group whose age is below every window;
- a group whose species and mode have no selector, even if the species has selectors in another mode.

**Warning block.** When at least one group is past its window, the dialog SHALL show one warning block as the last part of that requirement's item 3, after the stage-in and no-images lines and before item 4. It lists every such group, uncollapsed, in the resolved-params order:
- Heading: "*N scans are past their models' validated age and are predicted with the nearest models:*" (N = the summed counts). At N = 1: "*1 scan is past its models' validated age and is predicted with the nearest models:*".
- One line per group: "*<species> · day <age> — models validated up to day <max> (<count>)*".

**When the check can't be made.** When the card read fails, times out or returns an empty `cards` list, and at least one resolved-params group exists, the dialog SHALL show the muted line "*Couldn't check the models' age ranges.*" in place of the block.

**Otherwise.** Nothing is shown when the read succeeds with cards and no group is past its window, when no resolved-params group exists, or while the read is pending.

**Confirm.** Neither the block nor the muted line SHALL disable confirm.

**Caption.** That requirement's item 6 caption SHALL read "*Parameters come from each scan's metadata. Choosing models isn't supported yet*", still linking bloom#897; item 6 is otherwise unchanged. The phrases "will run", "will be skipped" and "reused" remain forbidden in the dialog.

#### Scenario: Past-window arabidopsis groups are named with the window's top
- **WHEN** the target holds, all with images, 60 arabidopsis scans at day 21, 30 at day 28 and 120 at day 14, and the cards' arabidopsis·cylinder selectors reach `age_max` 14
- **THEN** the dialog shows "*90 scans are past their models' validated age and are predicted with the nearest models:*"
- **AND** the lines "*arabidopsis · day 21 — models validated up to day 14 (60)*" then "*arabidopsis · day 28 — models validated up to day 14 (30)*"
- **AND** the day-14 group is not listed
- **AND** confirm is enabled once the other checks pass

#### Scenario: Lines follow the resolved-params order, not age order
- **WHEN** the target holds 30 arabidopsis scans at day 21 and 60 at day 28, all with images
- **THEN** the day-28 line is listed before the day-21 line

#### Scenario: Rice past both windows is named with the higher window
- **WHEN** the target holds 5 rice scans at day 18 with images, and the rice·cylinder selectors are 2–5 and 6–10
- **THEN** the dialog shows "*5 scans are past their models' validated age and are predicted with the nearest models:*"
- **AND** the line "*rice · day 18 — models validated up to day 10 (5)*"

#### Scenario: One past-window scan uses the singular heading
- **WHEN** the target holds 1 soybean scan at day 10 with images, and the soybean·cylinder selectors reach `age_max` 8
- **THEN** the dialog shows "*1 scan is past its models' validated age and is predicted with the nearest models:*"
- **AND** the line "*soybean · day 10 — models validated up to day 8 (1)*"

#### Scenario: Scans with no images are not counted as predicted
- **WHEN** the target holds 4 arabidopsis scans at day 28, of which 1 has no images
- **THEN** the past-window block counts 3 scans
- **AND** the no-images line counts 1 scan

#### Scenario: Ages at the window's top, younger scans and other species get no warning
- **WHEN** the target holds pennycress scans at day 14, canola scans at day 0 and sorghum scans at day 10, the cards have pennycress selectors reaching 14, canola selectors 2–13 and no sorghum selector
- **THEN** no past-window block and no muted line are shown

#### Scenario: Another mode's window does not count
- **WHEN** the cards have canola·cylinder 2–13 and canola·multiplant cylinder 2–20, and the target holds canola scans at day 15
- **THEN** the dialog lists "*canola · day 15 — models validated up to day 13 (…)*"

#### Scenario: A failed or empty card read does not block the run
- **WHEN** the card read answers `502`, does not settle within 10 seconds, or returns an empty `cards` list
- **THEN** the dialog shows "*Couldn't check the models' age ranges.*"
- **AND** confirm is enabled once the other checks pass, and no failed-state alert is shown

#### Scenario: Confirm waits for the card read
- **WHEN** enumeration, the pre-check and the concurrent-run query have settled but the card read has not
- **THEN** confirm is disabled
- **AND** neither the past-window block nor the muted line is shown

#### Scenario: The caption points at model choice
- **WHEN** the dialog shows its Parameters section
- **THEN** the caption reads "*Parameters come from each scan's metadata. Choosing models isn't supported yet*", linking bloom#897
