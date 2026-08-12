**Timing note:** #620 was filed 2026-08-06 referencing "demo Tuesday" — today may already be that
Tuesday. Treat this as immediate, not something to schedule later. Section 0 exists specifically so
the fallback is known-good before time is spent on anything else.

## 0. Confirm the fallback first

- [ ] 0.1 Before touching the OAuth flow at all, sanity-check that the existing fallback (Claude
      Code + `BLOOMMCP_API_KEY` against staging, per
      [connecting-claude-code.md](../../../bloommcp/docs/connecting-claude-code.md)) still works
      today: `claude mcp add --transport http bloommcp-staging
      https://staging.bloom.salk.edu:8443/bloommcp/mcp --header "Authorization: Bearer <token>"`,
      then one read-only tool call. Pass = a real result, not an auth or connection error. If this
      is already broken, escalate immediately — there is no time left to discover it later (see
      design.md, "re-confirm the fallback... first").

## 1. Prep

- [ ] 1.1 Confirm staging's actual state right now: re-check `GOTRUE_OAUTH_SERVER_ENABLED`,
      `BLOOMMCP_PUBLIC_URL`, `BLOOMMCP_OAUTH_AUTHORIZATION_SERVER`, `BLOOMMCP_OAUTH_JWKS_URI` on
      the deployed staging host (not just the committed `.env.staging.defaults`), since secrets and
      any host-level overrides aren't visible from the repo alone.
- [ ] 1.2 Run a fast, scriptable, unauthenticated reachability check before involving a browser or
      a human:
      `curl -i https://staging.bloom.salk.edu:8443/bloommcp/.well-known/oauth-protected-resource`
      (expect `200` + JSON containing `authorization_servers`/`resource`) and
      `curl -i https://staging.bloom.salk.edu:8443/bloommcp/mcp` with no auth header (expect `401`,
      not a hang or `5xx`). This only proves the route responds — it does not prove the consent
      screen itself works (see design.md Decision 2); it exists to fail fast on a network/deploy
      problem before spending the more expensive manual steps in Section 2 on it. **If this hangs
      instead of returning a response, suspect Salk wifi/VPN first** (per
      `connecting-claude-code.md`'s own warning that an unreachable host looks like a hang, not a
      clean error) — reconfirm connectivity before treating it as a server-side failure.
- [ ] 1.3 Set up a disposable, clearly-labeled test identity for the login/consent step — sign up
      via bloom-web on staging with an address like `oauth-verify-620+<yyyymmdd>@<your-domain>`; do
      not use a real Salk/SSO account. Record this exact identifier in the task 4.1 write-up so it
      can be located later for cleanup (task 4.6).

## 2. Run the flow

- [ ] 2.1 From a device on Salk wifi/VPN, run
      `npx -y mcp-remote https://staging.bloom.salk.edu:8443/bloommcp/mcp` and record the result of
      each step against these concrete pass criteria: discovery
      (`/.well-known/oauth-protected-resource`, expect `200` + JSON with `authorization_servers`/
      `resource`), the initial unauthenticated call (expect `401`), dynamic client registration
      (expect `201` + a `client_id` in the response), browser redirect to login, login, redirect to
      consent, consent decision, token exchange (expect `200` + an `access_token`). **Cap
      registration attempts at 3 total** — this route has no rate limiting (PR #613) — and if still
      failing after 3, stop and record it as failed rather than continuing to retry.
- [ ] 2.2 Once authenticated, issue exactly one **read-only** tool call —
      `list_available_experiments` — against staging through this connection and confirm it returns
      real staging data, not an auth error. **Do not call `remove_outliers`, `qc_clean`, or any tool
      that persists a run against a real experiment** (anything calling `ResultStore.commit()`) —
      this would leave a fake analysis run on real experiment data indistinguishable from a genuine
      one.
- [ ] 2.3 Decode the access token's JWT header from task 2.1 locally (do not paste the raw token
      anywhere — see task 2.4) and record its `alg`. Cross-check bloommcp's server logs at the run's
      timestamp for whether `SupabaseOAuthVerifier` accepted it via the JWKS/ES256 path or the
      `JWT_SECRET`/HS256 fallback (`bloommcp/src/bloom_mcp/auth.py`). This is the concrete evidence
      for whether staging currently signs ES256 — task 1.1's env-var check alone can't answer that.
- [ ] 2.4 If any step fails, capture the request/response detail needed to file a follow-up issue
      (status code, error body, which step) — don't just note "it failed." **Redact `client_secret`,
      bearer tokens, authorization codes, and refresh tokens before recording anything** — replace
      secret fields with `[REDACTED]`. This repository is public.

## 3. Claude Desktop cached-token check

- [ ] 3.1 With a token already cached under `~/.mcp-auth/` from task 2, configure Claude Desktop to
      use the same staging `mcp-remote` bridge and confirm whether it reuses that cached token
      without a fresh browser login.
- [ ] 3.2 Restart Claude Desktop and repeat the check — confirm whether the cached token survives an
      application restart or Desktop re-prompts for login on relaunch. Record Desktop's version/OS
      and whether a browser window opened on restart.

## 4. Record results and decide

- [ ] 4.1 Write up the verification record as a comment on #620, including for each step in
      Sections 0–3: timestamp, HTTP status (or "hung/timed out"), a one-line description of the
      response body (redacted per 2.4), the decoded JWT `alg` and which verifier path accepted it
      (2.3), the tool called and a one-line summary of what it returned (2.2), Desktop version/OS
      and restart behavior (3.1–3.2), the test identity used (1.3), whether any staging state was
      left behind and cleaned up (4.6), and whether the `.env.staging.defaults` comment was accurate
      or stale.
- [ ] 4.2 File a follow-up GitHub issue for any failed step, with enough detail to act on without
      re-running the flow (per Requirement: mcp-remote OAuth flow verified end-to-end against
      staging).
- [ ] 4.3 Apply the go/no-go criteria from design.md and record the recommendation on #620.
- [ ] 4.4 If the `.env.staging.defaults` comment above `GOTRUE_OAUTH_SERVER_ENABLED` is confirmed
      stale, correct or remove it in a small follow-up commit (config/comment fix, not a proposal —
      see `openspec/AGENTS.md`'s "Skip proposal for... Configuration changes").
- [ ] 4.5 Notify Evelyn of the go/no-go recommendation immediately — per the timing note above,
      there is likely no lead time left, so this should happen as soon as 4.3 is decided, not
      batched with other follow-up.
- [ ] 4.6 Query staging's `oauth_clients` (and session) tables for rows created by this run's
      disposable identity (1.3); delete them regardless of whether the run succeeded or failed
      (design.md: cleanup is unconditional, not contingent on failure).
