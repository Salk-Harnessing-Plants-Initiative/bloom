## ADDED Requirements

### Requirement: X-Bloom-Identity Header Verification

bloommcp SHALL accept an optional `X-Bloom-Identity` request header on every HTTP request it
serves (the combined surface and every mounted per-section sub-app). When present, it SHALL be
verified as a JWT using `algorithms=["HS256"]` against the `JWT_SECRET` environment variable
with `audience="authenticated"`, mirroring `langchain/deps.py:get_current_user()`'s
verification exactly. The resolved caller identity SHALL be the token's `sub` claim. When the
header is absent, the request SHALL proceed as anonymous with no change to today's behavior.
When the header is present but fails verification (invalid signature, wrong audience, expired,
or missing `sub`), the request SHALL be rejected with a `401` response rather than silently
treated as anonymous.

#### Scenario: Absent header proceeds as anonymous

- **WHEN** a request carries no `X-Bloom-Identity` header
- **THEN** the request proceeds exactly as it does today, attributed to the anonymous caller

#### Scenario: Valid header resolves the caller identity

- **WHEN** a request carries an `X-Bloom-Identity` header containing a JWT signed with the
  configured `JWT_SECRET`, `algorithms=["HS256"]`, `audience="authenticated"`, and a `sub` claim
- **THEN** the request proceeds, and the resolved caller identity is that `sub` value

#### Scenario: Expired token is rejected, not downgraded to anonymous

- **WHEN** a request carries an `X-Bloom-Identity` header whose JWT is otherwise validly signed
  but expired
- **THEN** the request is rejected with a `401` response
- **AND** the request does not proceed as anonymous

#### Scenario: Malformed or wrong-audience token is rejected

- **WHEN** a request carries an `X-Bloom-Identity` header that is not a validly-signed JWT, or
  whose `aud` claim is not `"authenticated"`, or that decodes with no `sub` claim
- **THEN** the request is rejected with a `401` response

#### Scenario: Verification covers every mounted surface, not only the combined app

- **WHEN** an `X-Bloom-Identity` header is sent to the combined surface (`/mcp`) or to any
  per-section mount (e.g. `/core/mcp`, `/sleap_roots/mcp`, `/phenotyping_segmentation/mcp`)
- **THEN** the same verification applies uniformly, with no section requiring its own wiring

### Requirement: Caller Identity Never Grants Database or Storage Authority

A resolved `X-Bloom-Identity` caller identity SHALL NOT be used as an authorization principal for
any database or Storage operation, and SHALL NOT be forwarded to PostgREST or Supabase Storage in
any form. Every such operation SHALL continue to run as the `bloom_agent` role, authenticated via
`BLOOM_AGENT_KEY`, exactly as it does today. This holds regardless of whether the caller identity
verified successfully, failed verification, or was absent.

#### Scenario: A verified identity does not change which role performs a DB/Storage call

- **WHEN** a request carrying a valid `X-Bloom-Identity` header triggers a tool that reads or
  writes Postgres/Storage
- **THEN** the resulting `get_postgrest_client()` call is authenticated with `BLOOM_AGENT_KEY`,
  unaffected by the resolved identity

#### Scenario: The identity token itself is never transmitted to PostgREST or Storage

- **WHEN** any DB/Storage call is made during a request that carried an `X-Bloom-Identity` header
- **THEN** the raw header value or decoded token is not present in that call's credentials,
  headers, or parameters

### Requirement: bloommcp_usage Records Caller Activity

bloommcp SHALL record usage of every request in a `bloommcp_usage` table: `identity` (the
resolved caller identity, or the literal `anonymous` when no header was present), `first_seen`,
`last_seen`, a monotonically incrementing `request_count`, and `last_action` (the mounted
section/path that served the request). Recording SHALL upsert atomically keyed on `identity`,
incrementing `request_count` and refreshing `last_seen`/`last_action` on repeat activity from the
same identity. A failure while recording usage SHALL be caught and logged, and SHALL NOT cause
the underlying request to fail.

#### Scenario: A new identity's first request creates a row

- **WHEN** an identity with no prior `bloommcp_usage` row makes a request
- **THEN** a row is created with `request_count = 1`, `first_seen` and `last_seen` set to the
  request time, and `last_action` set to the section/path served

#### Scenario: A repeat request from the same identity increments the count

- **WHEN** an identity with an existing `bloommcp_usage` row makes another request
- **THEN** its `request_count` increments by exactly 1, `last_seen` and `last_action` update, and
  `first_seen` is unchanged

#### Scenario: Anonymous requests collapse into one aggregate row

- **WHEN** two requests with no `X-Bloom-Identity` header are made (from any callers)
- **THEN** both upsert against the same `identity = 'anonymous'` row, incrementing its
  `request_count` rather than creating two rows

#### Scenario: A usage-recording failure does not fail the request

- **WHEN** the `bloommcp_usage` upsert raises (e.g. a transient DB error)
- **THEN** the triggering request still completes and returns its normal result
- **AND** the failure is logged

### Requirement: JWT_SECRET Is Validated Lazily, Only When Needed

bloommcp SHALL NOT require the `JWT_SECRET` environment variable at import time or at
unconditional server boot. It SHALL be read only when a request actually carries an
`X-Bloom-Identity` header. If such a request arrives and `JWT_SECRET` is unset, bloommcp SHALL
reject the request with a `5xx` response naming the missing variable, rather than crashing the
process or silently treating the caller as anonymous.

#### Scenario: Import and boot succeed with JWT_SECRET unset

- **WHEN** `bloom_mcp` is imported, or the server boots, with `JWT_SECRET` unset and no request
  has yet carried an `X-Bloom-Identity` header
- **THEN** import and boot succeed with no error raised for `JWT_SECRET`

#### Scenario: A header arrives with JWT_SECRET unset

- **WHEN** a request carries an `X-Bloom-Identity` header while `JWT_SECRET` is unset
- **THEN** bloommcp responds with a `5xx` error naming `JWT_SECRET` as missing
- **AND** the caller is not silently treated as anonymous
