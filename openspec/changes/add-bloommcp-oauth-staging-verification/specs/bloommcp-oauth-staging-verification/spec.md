## ADDED Requirements

### Requirement: mcp-remote OAuth flow verified end-to-end against staging

The full `mcp-remote` OAuth 2.1 flow — discovery, dynamic client registration, browser login,
consent, token exchange, and an authenticated tool call — SHALL be exercised against staging
(`https://staging.bloom.salk.edu:8443/bloommcp/mcp`) using a disposable, clearly-labeled test
identity, and the outcome of each step SHALL be recorded.

#### Scenario: Full flow succeeds against staging

- **WHEN** a researcher runs `npx -y mcp-remote https://staging.bloom.salk.edu:8443/bloommcp/mcp`
  against staging
- **THEN** discovery, dynamic registration, browser login, consent, and token exchange each
  succeed, and a subsequent tool call against real staging data returns a real result (not an
  auth error)

#### Scenario: A step fails against staging

- **WHEN** any step in the flow fails against staging
- **THEN** the failing step, the observed error, and how it differs (if at all) from Benfica's
  dev-stack verification are recorded, and a follow-up GitHub issue is filed with that detail

#### Scenario: The tool call is restricted to a read-only tool

- **WHEN** the authenticated tool call in this verification is chosen
- **THEN** it is a read-only tool (`list_available_experiments`), never a tool that persists a run
  against a real experiment (e.g. `remove_outliers`, `qc_clean`, or any other tool that calls
  `ResultStore.commit()`)

#### Scenario: Credentials are redacted from every recorded artifact

- **WHEN** request/response detail from any step (registration, consent, token exchange) is
  recorded for the write-up or a follow-up issue
- **THEN** bearer tokens, authorization codes, and `client_secret` values are replaced with
  `[REDACTED]` before being recorded — this repository is public

#### Scenario: Staging state created by the run is cleaned up

- **WHEN** the run completes, whether it succeeds or fails
- **THEN** any `oauth_clients`/session row created by the run's disposable test identity is
  identified and deleted, and the write-up records that the cleanup happened

### Requirement: Existing fallback path re-confirmed before being relied upon

The existing Claude Code + `BLOOMMCP_API_KEY` path against staging SHALL be sanity-checked as
still working, before the OAuth flow is exercised — a fallback that is only assumed to work,
recommended under demo-day time pressure, is not an actual safety net.

#### Scenario: Fallback confirmed working

- **WHEN** `claude mcp add` is used to connect to staging with `BLOOMMCP_API_KEY` and a read-only
  tool call is issued
- **THEN** the call returns real staging data, confirming the fallback is available before the
  OAuth flow is attempted

#### Scenario: Fallback itself is broken

- **WHEN** the fallback sanity check fails
- **THEN** this is escalated immediately, before spending further time on the OAuth verification —
  with no lead time banked (see design.md), discovering this later leaves no path to recover either
  way

### Requirement: Claude Desktop cached-token behavior determined

Whether a `mcp-remote` OAuth token cached under `~/.mcp-auth/` from a prior terminal-run session
against staging is reused by Claude Desktop after an application restart, or whether Desktop
re-prompts for a fresh login, SHALL be determined and recorded — either outcome is an acceptable
result, as long as it is known.

#### Scenario: Desktop reuses a terminal-cached token

- **WHEN** Claude Desktop is restarted after a terminal `mcp-remote` login against staging already
  succeeded and cached a token under `~/.mcp-auth/`
- **THEN** it is recorded that Desktop reconnects using that cached token without re-prompting

#### Scenario: Desktop requires a fresh login after restart

- **WHEN** Claude Desktop is restarted after the same prior terminal-run login
- **THEN** it is recorded that Desktop re-prompts for a fresh browser login instead of reusing the
  cached token — this is a valid, actionable outcome (log in fresh immediately before the demo), not
  a failure of the verification itself

### Requirement: Go/no-go recommendation recorded for the Tuesday demo

A go/no-go recommendation for using `mcp-remote` + Claude Desktop live in Tuesday's demo SHALL be
recorded, based on the fixed criteria in this change's `design.md` (full flow success including a
real tool call, and a known answer to the cached-token question) rather than decided ad hoc after
the run.

#### Scenario: Verification succeeds — go

- **WHEN** the full flow (including a real tool call against staging data) succeeds and the
  cached-token behavior is known
- **THEN** the recommendation is "go" — proceed with `mcp-remote` + Claude Desktop against staging
  for the demo

#### Scenario: Verification fails or cannot complete in time — no-go

- **WHEN** any step in the flow fails against staging, or a needed fix cannot land and be
  re-verified before Tuesday
- **THEN** the recommendation is "no-go" — fall back to the existing Claude Code +
  `BLOOMMCP_API_KEY` path against staging
  (see [connecting-claude-code.md](../../../../../bloommcp/docs/connecting-claude-code.md))
