## ADDED Requirements

### Requirement: X-Bloom-Identity Header Verification

bloommcp SHALL accept an optional `X-Bloom-Identity` request header on every HTTP request it
serves (the combined surface and every mounted per-section sub-app). Exactly one occurrence of
the header SHALL be accepted; a request carrying it more than once SHALL be rejected. When
present, it SHALL be verified as a JWT using `algorithms=["HS256"]` against the `JWT_SECRET`
environment variable with `audience="authenticated"`, mirroring
`langchain/deps.py:get_current_user()`'s verification exactly. The resolved caller identity
SHALL be the token's `sub` claim, normalized to lowercase, which SHALL additionally be required
to match a standard UUID shape in its entirety (not merely as a prefix) and SHALL NOT
case-insensitively equal the reserved literal `anonymous`. When the header is absent, the request
SHALL proceed as anonymous with no change to today's behavior. When the header is present but
fails verification (invalid signature, disallowed algorithm, wrong audience, expired, missing
`sub`, malformed `sub`, `sub` equal to the reserved literal, or duplicated), the request SHALL be
rejected with a `401` response rather than silently treated as anonymous or silently resolved to
one occurrence.

#### Scenario: Absent header proceeds as anonymous

- **WHEN** a request carries no `X-Bloom-Identity` header
- **THEN** the request proceeds exactly as it does today, attributed to the anonymous caller

#### Scenario: Valid header resolves the caller identity

- **WHEN** a request carries an `X-Bloom-Identity` header containing a JWT signed with the
  configured `JWT_SECRET`, `algorithms=["HS256"]`, `audience="authenticated"`, and a UUID-shaped
  `sub` claim
- **THEN** the request proceeds, and the resolved caller identity is that `sub` value

#### Scenario: Expired token is rejected, not downgraded to anonymous

- **WHEN** a request carries an `X-Bloom-Identity` header whose JWT is otherwise validly signed
  but expired
- **THEN** the request is rejected with a `401` response
- **AND** the request does not proceed as anonymous

#### Scenario: Malformed, wrong-audience, or wrong-algorithm token is rejected

- **WHEN** a request carries an `X-Bloom-Identity` header that is not a validly-signed JWT, or
  whose `aud` claim is not `"authenticated"`, or that decodes with no `sub` claim, or whose
  header specifies an algorithm other than `HS256` (including `none`) regardless of any other
  claim's validity
- **THEN** the request is rejected with a `401` response

#### Scenario: A sub claim that isn't UUID-shaped, or equals the reserved sentinel, is rejected

- **WHEN** a request carries an otherwise-validly-signed `X-Bloom-Identity` header whose `sub`
  claim is not shaped like a UUID, or case-insensitively equals `anonymous`
- **THEN** the request is rejected with a `401` response
- **AND** no value from this token is ever written to `bloommcp_usage.identity`

#### Scenario: A sub claim with a trailing character after a valid UUID is rejected

- **WHEN** a request carries an otherwise-validly-signed `X-Bloom-Identity` header whose `sub`
  claim is a valid UUID immediately followed by any additional character (e.g. a trailing
  newline)
- **THEN** the request is rejected with a `401` response — the UUID-shape check matches the
  entire claim, not merely a prefix of it

#### Scenario: A differently-cased UUID normalizes to the same resolved identity

- **WHEN** two requests carry validly-signed `X-Bloom-Identity` headers whose `sub` claims are the
  same UUID in different letter casing
- **THEN** both resolve to the identical (lowercased) caller identity, and `bloommcp_usage`
  accumulates them under one row, not two

#### Scenario: A duplicated header is rejected

- **WHEN** a request carries the `X-Bloom-Identity` header more than once
- **THEN** the request is rejected with a `401` response, regardless of whether the repeated
  values are identical or different

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

- **WHEN** a request carrying a valid `X-Bloom-Identity` header triggers any currently-registered
  tool that reads or writes Postgres/Storage
- **THEN** the resulting `get_postgrest_client()` call is authenticated with `BLOOM_AGENT_KEY`,
  unaffected by the resolved identity, for every such tool — not merely a single sampled example

#### Scenario: The identity token itself is never transmitted to PostgREST or Storage

- **WHEN** any DB/Storage call is made during a request that carried an `X-Bloom-Identity` header
- **THEN** the raw header value or decoded token is not present in that call's credentials,
  headers, or parameters

### Requirement: bloommcp_usage Records Caller Activity Per Mounted Surface

bloommcp SHALL record usage of every qualifying HTTP request in a `bloommcp_usage` table:
`identity` (the resolved caller identity, or the literal `anonymous` when no header was present),
`first_seen`, `last_seen`, a monotonically incrementing `request_count`, and `last_action` (the
mounted surface that served the request — one of the registered section names, or `combined` for
the root/combined surface). Recording SHALL upsert atomically keyed on `identity`, incrementing
`request_count` and refreshing `last_seen`/`last_action` on repeat activity from the same
identity, and SHALL run without blocking or adding latency to the request it is attributed to. A
failure while recording usage — including a failure to schedule the recording itself — SHALL be
caught and logged, and SHALL NOT cause the underlying request to fail. Requests to the `/health`
endpoint SHALL NOT be recorded. This table records the most recent state per identity; it is not
an append-only history (repeat activity from the same identity overwrites `last_action` and does
not preserve the previous one).

Usage is recorded at the granularity of which mounted surface handled a request, not the specific
MCP tool invoked — a caller's `bloommcp_usage` row reflects "used the `sleap_roots` surface,
9 times, most recently," not "ran `qc_clean`." A per-tool design was attempted and reverted: it
depended on a value threaded via a `ContextVar` into the MCP tool-dispatch code path, which cannot
reliably reach that code for a reused `streamable-http` session (the common real-world case) —
see design.md Decision 4.

#### Scenario: A new identity's first qualifying request creates a row

- **WHEN** an identity with no prior `bloommcp_usage` row makes a qualifying request (any mounted
  surface other than `/health`)
- **THEN** a row is created with `request_count = 1`, `first_seen` and `last_seen` set to the
  request time, and `last_action` set to the surface that served it

#### Scenario: A repeat request from the same identity increments the count

- **WHEN** an identity with an existing `bloommcp_usage` row makes another qualifying request
  (the same mounted surface or a different one)
- **THEN** its `request_count` increments by exactly 1, `last_seen` and `last_action` update to
  reflect the new request, and `first_seen` is unchanged

#### Scenario: Anonymous requests collapse into one aggregate row

- **WHEN** two qualifying requests with no `X-Bloom-Identity` header are made (from any callers)
- **THEN** both upsert against the same `identity = 'anonymous'` row, incrementing its
  `request_count` rather than creating two rows

#### Scenario: Concurrent first-time requests from the same new identity do not lose an update

- **WHEN** two qualifying requests from the same, previously-unseen identity are made
  concurrently
- **THEN** the resulting row has `request_count = 2`, not `1` (a lost update) and not a
  duplicate-row or constraint-violation error

#### Scenario: A usage-recording failure does not fail the request

- **WHEN** the `bloommcp_usage` upsert raises (e.g. a transient DB error), or recording cannot
  even be scheduled
- **THEN** the triggering request still completes and returns its normal result
- **AND** the failure is logged

#### Scenario: Usage recording does not add latency to the request it is attributed to

- **WHEN** the `bloommcp_usage` upsert is slow (e.g. a slow or momentarily unresponsive database)
- **THEN** the triggering request's response is not delayed waiting for it

#### Scenario: Requests to /health are never recorded

- **WHEN** a request is made to the `/health` endpoint, with or without a valid
  `X-Bloom-Identity` header
- **THEN** no `bloommcp_usage` row is created or updated as a result

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

### Requirement: Transport-Level Bearer Auth Is Unaffected

The existing `BLOOMMCP_API_KEY` bearer-auth check (enforced independently by FastMCP's
`TokenVerifier` on every mounted surface) SHALL remain enforced exactly as it does today,
regardless of whether an `X-Bloom-Identity` header is present, absent, valid, or invalid. Neither
check substitutes for the other.

#### Scenario: A missing or invalid bearer token is still rejected regardless of identity header

- **WHEN** a request carries a valid `X-Bloom-Identity` header but a missing or invalid
  `Authorization` bearer token (with `BLOOMMCP_API_KEY` configured)
- **THEN** the request is still rejected by FastMCP's existing bearer-auth check

#### Scenario: A valid bearer token does not bypass identity-header verification

- **WHEN** a request carries a valid `Authorization` bearer token but an invalid
  `X-Bloom-Identity` header
- **THEN** the request is rejected (per the header-verification requirement above), independent
  of the bearer token's validity
