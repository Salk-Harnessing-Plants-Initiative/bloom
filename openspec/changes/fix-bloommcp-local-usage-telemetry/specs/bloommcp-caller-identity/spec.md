## ADDED Requirements

### Requirement: Usage Telemetry Is Skipped Entirely for the Local Storage Backend

When `storage_backend.is_local_backend()` is true (`BLOOM_STORAGE_BACKEND=local`),
`IdentityMiddleware` SHALL NOT invoke `record_usage_async` for any request, qualifying or
not. No `bloommcp_usage` RPC attempt SHALL be made, no failure SHALL occur, and no
recording-related log line SHALL be emitted. This gate is independent of, and evaluated
before, the existing `/health`-path and downstream-`401` gates. Behavior for the `supabase`
backend (the default) is unchanged by this requirement.

#### Scenario: A qualifying request under the local backend is never recorded

- **WHEN** a non-`/health` request completes with a non-`401` status while
  `BLOOM_STORAGE_BACKEND=local`
- **THEN** `record_usage_async` is not called for that request
- **AND** no `bloommcp_usage`-recording failure is logged

#### Scenario: The supabase backend records exactly as before

- **WHEN** the same request completes under the default `supabase` backend
- **THEN** `record_usage_async` is called as this capability already specifies

## MODIFIED Requirements

### Requirement: bloommcp_usage Records Caller Activity Per Mounted Surface

bloommcp SHALL record usage of every qualifying HTTP request in a `bloommcp_usage` table:
`identity` (the resolved caller identity, or the literal `anonymous` when none was resolved),
`first_seen`, `last_seen`, a monotonically incrementing `request_count`, and `last_action` (the
mounted surface that served the request — one of the registered section names, or `combined` for
the root/combined surface). The resolved caller identity SHALL come from the first of the
following sources that yields one:

1. A verified `X-Bloom-Identity` header (see the "X-Bloom-Identity Header Verification"
   requirement).
2. Otherwise, the `subject` of the `AccessToken` FastMCP's own bearer-auth layer verified for this
   request (available via `scope["user"].access_token.subject` once that request has been
   dispatched), when that credential's verifier populates one. A shared-API-key credential (via
   `ApiKeyVerifier`) SHALL NOT be treated as naming an individual and SHALL NOT supply an identity
   by this source, regardless of its own validity.
3. Otherwise, the literal `anonymous`.

**This requirement applies only when the `supabase` storage backend is active** — see "Usage
Telemetry Is Skipped Entirely for the Local Storage Backend" above for the `local` backend, where
none of the below occurs at all. A request qualifies only if it is not to the `/health` endpoint
AND the downstream response is not a `401` — a `401` from the wrapped app (e.g. FastMCP's own
bearer-auth check) indicates the caller was never authenticated to use bloommcp at all, and SHALL
NOT be recorded; recording SHALL happen only after the downstream response is known, not
unconditionally before it. Recording SHALL upsert atomically keyed on `identity`, incrementing
`request_count` and refreshing `last_seen`/`last_action` on repeat activity from the same
identity, and SHALL run without blocking or adding latency to the request it is attributed to.
Recording attempts SHALL be bounded: beyond a fixed number of concurrently in-flight recording
attempts, further attempts SHALL be dropped (logged) rather than queued or blocked on. **A failure
while recording usage — including a dropped or unschedulable attempt — SHALL be caught and logged
as a warning naming the underlying exception's message, not a full stack trace**, and SHALL NOT
cause the underlying request to fail. This table records the most recent state per identity; it
is not an append-only history (repeat activity from the same identity overwrites `last_action`
and does not preserve the previous one), and `request_count` counts qualifying HTTP requests, not
MCP tool invocations specifically (protocol-level messages such as `initialize` also count — see
design.md Risks).

Usage is recorded at the granularity of which mounted surface handled a request, not the specific
MCP tool invoked — a caller's `bloommcp_usage` row reflects "used the `sleap_roots` surface,
9 times, most recently," not "ran `qc_clean`." A per-tool design was attempted and reverted: it
depended on a value threaded via a `ContextVar` into the MCP tool-dispatch code path, which cannot
reliably reach that code for a reused `streamable-http` session (the common real-world case) —
see `add-bloommcp-caller-identity` design.md Decision 4. Reading the OAuth `AccessToken.subject`
off `scope["user"]` (source 2 above) is a different mechanism from that rejected one and does not
share its limitation — see `add-bloommcp-oauth-usage-attribution` design.md Decision 1.

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

- **WHEN** two qualifying requests resolve no identity from either source (no `X-Bloom-Identity`
  header and no OAuth `AccessToken.subject`), from any callers
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
- **AND** the failure is logged as a warning with the exception's message, not a full traceback

#### Scenario: Usage recording does not add latency to the request it is attributed to

- **WHEN** the `bloommcp_usage` upsert is slow (e.g. a slow or momentarily unresponsive database)
- **THEN** the triggering request's response is not delayed waiting for it

#### Scenario: Requests to /health are never recorded

- **WHEN** a request is made to the `/health` endpoint, with or without a valid
  `X-Bloom-Identity` header
- **THEN** no `bloommcp_usage` row is created or updated as a result

#### Scenario: A request the downstream app rejects with 401 is not recorded

- **WHEN** a request (with a valid, absent, or otherwise-acceptable `X-Bloom-Identity` header)
  reaches the wrapped app, and that app's own response is a `401`
- **THEN** no `bloommcp_usage` row is created or updated as a result — recording is gated on the
  downstream response, not fired unconditionally beforehand

#### Scenario: A non-401 downstream rejection is still recorded

- **WHEN** the wrapped app's response is some status other than `401` (including other error
  statuses unrelated to authentication)
- **THEN** the request is still recorded normally

#### Scenario: Recording attempts beyond the in-flight bound are dropped, not queued

- **WHEN** the number of concurrently in-flight recording attempts already equals the configured
  bound
- **THEN** a further recording attempt is dropped (logged) immediately, rather than queued
  indefinitely or blocking the triggering request

#### Scenario: An OAuth-authenticated caller with no identity header is attributed to their own subject

- **WHEN** a request carries no `X-Bloom-Identity` header, and FastMCP's bearer-auth layer
  verified an `AccessToken` for this request whose `subject` is set (issued by
  `SupabaseOAuthVerifier`)
- **THEN** the request is recorded under that `subject`, not `anonymous`

#### Scenario: A shared-API-key-authenticated caller is still recorded as anonymous

- **WHEN** a request carries no `X-Bloom-Identity` header, and is authenticated via the shared
  `BLOOMMCP_API_KEY` (through `ApiKeyVerifier`, whose issued `AccessToken` never sets `subject`)
- **THEN** the request is recorded as `anonymous`, not under the shared key or any value derived
  from it

#### Scenario: A verified identity header takes precedence over a simultaneously-present OAuth subject

- **WHEN** a request carries both a verified `X-Bloom-Identity` header and an `AccessToken` with a
  different, non-empty `subject`
- **THEN** the request is recorded under the header's resolved identity, not the `AccessToken`'s
  `subject`

#### Scenario: No authentication configured at all resolves to anonymous, not an error

- **WHEN** bloommcp has no `auth` provider configured (dev mode — neither `BLOOMMCP_API_KEY` nor
  OAuth env vars set), so `scope` carries no `"user"` key at all
- **THEN** the request is recorded as `anonymous`, and no error is raised for the missing key
