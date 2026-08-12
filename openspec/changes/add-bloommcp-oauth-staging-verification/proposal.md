## Why

[#613](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/613) ("OAuth 2.1 login for
external MCP clients") is merged into `staging`, and per its own description staging's
`GOTRUE_OAUTH_SERVER_ENABLED` is already `true` there — **confirmed directly against
`origin/staging:.env.staging.defaults`** rather than assumed from the PR body. That file's
`GOTRUE_OAUTH_SERVER_ENABLED=true` line sits directly under a comment reading `# OAuth 2.1 server
— off until the bloom-web consent route is deployed here`, which contradicts the value on the very
next line. Either the comment is stale (the consent route already deployed and nobody updated it)
or the flag was flipped ahead of the route actually landing — this change's own verification run
settles which, since a login that reaches the consent screen and fails there is a different result
than one that never reaches it. Benfica's OAuth verification (her comment on #613) confirmed the
full flow — discovery, dynamic registration, browser login, consent, token exchange, authorized
tool calls — but **only against her own dev stack**. Staging has never been exercised end-to-end,
and two related questions are explicitly still open:

- Whether a `mcp-remote` token cached under `~/.mcp-auth/` from a terminal session is picked up by
  Claude Desktop after a restart, or whether Desktop re-prompts for login.
- Whether an authenticated tool call against **real staging data** (not just the auth handshake)
  succeeds post-login.

[#620](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/620) tracks exactly this
verification. It matters because Evelyn has a demo Tuesday and wants to run bloommcp live against
staging through Claude Desktop; confirming this works — or confirming it doesn't, with enough lead
time to fall back to the existing Claude Code + `BLOOMMCP_API_KEY` path
([connecting-claude-code.md](../../../bloommcp/docs/connecting-claude-code.md), which already
works today) — is the point. **Timing note, updated while revising this proposal:** #620 was filed
2026-08-06 (a Thursday) referencing that Tuesday, 2026-08-11. As of this revision it is
2026-08-12 — one day past it — and #620 still has zero comments: no run write-up, no go/no-go call
posted. Whether the demo already happened without this verification ever running, or this has
simply slipped unnoticed, is not answerable from the repo alone. Whoever runs this needs to treat
it as **overdue**, not merely immediate — run it now regardless, and say plainly in the eventual
notification (tasks.md 4.5) that the result is arriving after the referenced date, not before it.
The fallback (below) should still be sanity-checked first, since there's no more lead time to
recover if it turns out to be broken too, whatever the calendar says at that point.

This change does **not** attempt to resolve [#616](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/616)
(the still-open network-path decision for Claude Desktop's in-app "Connectors" UX), and #616 does
identify `mcp-remote` as a local bridge that "works today" independent of that decision — but this
proposal's earlier draft overstated that point. #616's own "Readiness (independent of which option
above is chosen)" section says plainly **"not ready — regardless of network path,"** listing #265,
#617, #108, #618 — text that, read literally, includes option 4 (`mcp-remote`) along with the
in-app Connectors options. Two of those four (#617's sanitized-error-contract gap in
`sections/core/*`, and #618's schema-wide `bloom_agent` RLS) are transport-agnostic server/DB-role
properties that apply identically over `mcp-remote`. This change does not close either gap — it
proceeds anyway, on the basis that both are *pre-existing, already-disclosed* risk that the
currently-used Claude Code + `BLOOMMCP_API_KEY` path already carries today (see
`connecting-claude-code.md`'s own "what this token actually grants" disclosure), not new exposure
created by testing OAuth specifically. #265 (silent zero-auth on a missing API key) and #108 (no
rate limiting) are addressed directly in Non-Goals and design.md rather than waved away.

## What Changes

- Sanity-check the fallback (Claude Code + `BLOOMMCP_API_KEY` against staging, per
  `connecting-claude-code.md`) **first, before** touching the OAuth flow — recommending a fallback
  under demo-day time pressure without having actually confirmed it still works today is not a real
  safety net.
- Run an unauthenticated, scriptable pre-check against staging's discovery endpoint and the bare
  MCP endpoint before involving a browser or a human, to fail fast on a network/reachability problem
  before spending the more expensive manual steps on it.
- Run the full `mcp-remote` OAuth flow (discovery → 401 → dynamic client registration → browser
  login → consent → token exchange → authorized tool call) against staging
  (`https://staging.bloom.salk.edu:8443/bloommcp/mcp`, matching `BLOOMMCP_PUBLIC_URL` in
  `.env.staging.defaults`) using a disposable test identity and a **read-only** tool call only
  (never `remove_outliers`, `qc_clean`, or any tool that persists a run against a real experiment),
  and record the outcome of each step against concrete, pre-defined pass criteria (see design.md and
  tasks.md).
- Confirm, one way or the other, whether Claude Desktop reuses a `~/.mcp-auth/`-cached token from a
  prior terminal-run `mcp-remote` session after a Desktop restart, or re-prompts for login.
- Confirm an authenticated tool call against real staging data (not merely a successful token
  exchange) succeeds through this path.
- Check the stale-looking comment above `GOTRUE_OAUTH_SERVER_ENABLED=true` in
  `.env.staging.defaults` against what's actually observed (does login reach and pass the consent
  screen, or fail there) and correct or remove the comment if it no longer reflects reality.
- Clean up every piece of state the run itself creates on shared staging — each registered OAuth
  client, the test login session, and the disposable user account (`auth.users` row) itself —
  regardless of whether the run succeeds, fails, or is aborted partway.
- Redact credentials (bearer tokens, authorization codes, `client_secret`, session cookies, PKCE
  `code_verifier`/`state`) from anything recorded in the write-up below, and independently re-check
  the draft for these patterns immediately before posting — this repository is public and posting
  is not reversible.
- Record a go/no-go recommendation for Tuesday's demo: either this path is confirmed working, or
  the fallback (Claude Code + `BLOOMMCP_API_KEY` against staging) is confirmed as the path to use
  instead.
- File follow-up GitHub issue(s) for any step that fails, with enough detail (request/response,
  which step, staging vs. dev divergence) for someone to act on without re-running the whole flow.

## Non-Goals

- **No code change to `bloommcp`, `bloom-web`, or the OAuth flow itself.** This is a verification
  exercise against an already-shipped feature (#613). If verification surfaces a real bug, fixing
  it is out of scope for this change — filed as a follow-up issue instead (see What Changes).
- **No decision on the Claude Desktop "Connectors" in-app network path.** That is #616's scope, not
  this change's. This change only exercises the `mcp-remote` local-bridge path, which does not
  depend on #616's outcome.
- **No production verification.** `.env.prod.defaults` keeps `GOTRUE_OAUTH_SERVER_ENABLED` off
  (per #613); this change is staging-only, matching #620's own scope.
- **No new researcher-facing documentation.** `connecting-claude-code.md`'s "Claude Desktop / Claude
  Enterprise" section is explicitly left "Not yet written," pending #522/#616. Writing that section
  is out of scope here even if this verification succeeds — it can cite #616's still-open network
  question for the in-app path, and this change's findings are the input to that future doc, not a
  replacement for it.
- **No fix for #265, #617, #108, or #618** (the gaps #616's Readiness section lists). This change
  works around #108 (no rate limiting on the registration route) with a self-imposed retry cap
  (tasks.md), not a server-side fix, and treats #617/#618 as pre-existing accepted risk (see Why) —
  none of the four are touched by this change.

## Impact

- **Affected capability:** new `bloommcp-oauth-staging-verification` — a one-time verification
  record, not a system capability that bloommcp exposes. No existing spec is modified.
- **Affected code:** none expected. If verification uncovers a real defect, the fix is scoped to a
  follow-up issue/change, not folded into this one.
- **Affected docs:** the stale comment in `.env.staging.defaults` above `GOTRUE_OAUTH_SERVER_ENABLED`
  may be corrected as a side effect of this change's findings (see What Changes).
- **Execution model — disclosed explicitly, not just implied:** unlike this repo's other OpenSpec
  changes, most of this change's tasks (the browser-based login/consent, Claude Desktop's GUI and
  restart, and being physically on Salk wifi/VPN) require a human directly driving them — an AI
  agent cannot complete tasks.md Sections 1–3 unassisted. An agent's role is limited to: the
  unauthenticated discovery/reachability pre-check (scriptable, no browser needed); drafting the
  write-up, follow-up issues, and the `.env.staging.defaults` comment fix once a human supplies the
  real results; and opening the PR for this proposal itself.
- Refs: #620 (this issue, closes), #613 (merged, ships the OAuth flow this verifies), #616 (related;
  does not block this change proceeding, though its Readiness section's language is broader than an
  earlier draft of this proposal characterized it — see Why).
