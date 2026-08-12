**Timing note:** #620 was filed 2026-08-06 referencing "demo Tuesday" (2026-08-11). As of this
revision it is 2026-08-12 — one day past that date — and #620 has zero comments: no run write-up,
no go/no-go call. Whether the demo already happened without this verification ever running, or
this has simply slipped unnoticed, cannot be determined from the repo alone. Treat this as
**overdue**, not "today is the day": run it immediately regardless, and task 4.5's notification
must say plainly that the recommendation is arriving after, not before, the date the issue itself
referenced. Section 0 exists specifically so the fallback is known-good before time is spent on
anything else.

## 0. Confirm the fallback first

- [ ] 0.1 Before touching the OAuth flow at all, sanity-check that the existing fallback (Claude
      Code + `BLOOMMCP_API_KEY` against staging, per
      [connecting-claude-code.md](../../../bloommcp/docs/connecting-claude-code.md)) still works
      today: `claude mcp add --transport http bloommcp-staging
      https://staging.bloom.salk.edu:8443/bloommcp/mcp --header "Authorization: Bearer <token>"`,
      then one read-only tool call. Pass = a real result, not an auth or connection error. **If
      this is already broken: STOP. Do not proceed to Section 1.** Escalate immediately instead —
      there is no time left to discover it later (see design.md, "re-confirm the fallback...
      first"), and there is no point verifying a new auth path while the only known-good fallback
      for the demo is unconfirmed.

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
      via bloom-web on staging with an address like `oauth-verify-620+<yyyymmdd>-a1@<your-domain>`
      (the `-a1` attempt suffix matters: if this run aborts and is re-attempted the same day,
      increment it to `-a2`, etc., so 4.6's cleanup and the 4.1 write-up can tell which attempt's
      state belongs to which signup instead of colliding on an identical address); do not use a
      real Salk/SSO account. Plus-addressing survives client-side (`LoginForm.tsx`'s
      `sanitizeLocalPart` allow-list includes `+`, and the concatenated address reaches
      `supabase.auth.signUp()` verbatim) — but that only covers this repo's code, not whatever
      Supabase Auth or the mail provider might do server-side, so still confirm the confirmation
      email actually arrives at the exact plussed address before relying on it. Perform this
      signup, and the login/consent step in task 2.1, in a private/incognito browser window on a
      profile with no existing bloom-web session — if a real account were already logged in there,
      consent could complete silently under that account instead of this disposable one, with no
      error to signal it. Record this exact identifier in the task 4.1 write-up so it can be
      located later for cleanup (task 4.6).

## 2. Run the flow

- [ ] 2.1 From a device on Salk wifi/VPN, run
      `npx -y mcp-remote https://staging.bloom.salk.edu:8443/bloommcp/mcp` and record the result of
      each step against these concrete pass criteria: discovery
      (`/.well-known/oauth-protected-resource`, expect `200` + JSON with `authorization_servers`/
      `resource`), the initial unauthenticated call (expect `401`), dynamic client registration
      (expect `201` + a `client_id` in the response), browser redirect to login, login, redirect to
      consent, consent decision, token exchange (expect `200` + an `access_token`). **Cap
      registration attempts at 3 total** — this route has no rate limiting (PR #613) — and if still
      failing after 3, stop and record it as failed rather than continuing to retry. **Record every
      `client_id` returned by a `201` in this task** (one per registration attempt, so up to 3) —
      dynamic registration creates the `oauth_clients` row before login even happens, so `client_id`
      is the actual lookup key task 4.6 uses to delete it, not the disposable identity from 1.3
      (that identity is unrelated to which client got registered). Carry these `client_id` values
      into the task 4.1 write-up even if the run later fails or aborts.
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
      bearer tokens, authorization codes, refresh tokens, session cookies, and PKCE `code_verifier`/
      `state` values before recording anything** — replace secret fields with `[REDACTED]`. The
      last two are easy to miss because they show up in dev-tools network captures of the
      login/consent redirects, not just in JSON response bodies. This repository is public.
- [ ] 2.5 As a secondary, informational check (not required for the go/no-go decision in Section
      4, and not counted against 2.1's 3-attempt retry cap — that cap is for isolating a failure,
      this is one deliberate, planned run): repeat registration and login once more, but **deny**
      consent at the consent screen instead of approving it. Confirm no token is issued and no tool
      call is possible afterward. Record the `client_id` this second registration returns (task 4.6
      needs to delete this row too) and fold the result into the 4.1 write-up.

## 3. Claude Desktop cached-token check

- [ ] 3.1 With a token already cached under `~/.mcp-auth/` from task 2, configure Claude Desktop to
      use the same staging `mcp-remote` bridge and confirm whether it reuses that cached token
      without a fresh browser login.
- [ ] 3.2 Restart Claude Desktop and repeat the check — confirm whether the cached token survives an
      application restart or Desktop re-prompts for login on relaunch. Record Desktop's version/OS
      and whether a browser window opened on restart.

## 4. Record results and decide

**Abort-safe:** if this run is stopped or crashes anywhere after 1.3 (identity created) or 2.1/2.5
(a `client_id` registered), tasks 4.1, 4.5, and 4.6 are still mandatory using whatever was captured
up to that point — an incomplete run is not an exemption from notifying Evelyn or cleaning up
staging state, it's a reason to say so explicitly in the write-up.

- [ ] 4.1 Write up the verification record as a comment on #620, including for each step in
      Sections 0–3: timestamp, HTTP status (or "hung/timed out"), a one-line description of the
      response body (redacted per 2.4), the decoded JWT `alg` and which verifier path accepted it
      (2.3), the tool called and a one-line summary of what it returned (2.2), the deny-consent
      result (2.5), Desktop version/OS and restart behavior (3.1–3.2), the test identity used (1.3)
      and every `client_id` registered (2.1, 2.5), whether any staging state was left behind and
      cleaned up (4.6), and whether the `.env.staging.defaults` comment was accurate or stale.
      **Before posting:** grep the draft for `secret`, `Bearer `, `access_token`, `refresh_token`,
      `code_verifier`, and `state=` as an independent check that 2.4's redaction actually caught
      everything — this is a public repo and a missed secret can't be un-posted after the fact; one
      remembered pass during drafting is not enough for something irreversible.
- [ ] 4.2 File a follow-up GitHub issue for any failed step, with enough detail to act on without
      re-running the flow (per Requirement: mcp-remote OAuth flow verified end-to-end against
      staging).
- [ ] 4.3 Apply the go/no-go criteria from design.md and record the recommendation on #620.
- [ ] 4.4 If the `.env.staging.defaults` comment above `GOTRUE_OAUTH_SERVER_ENABLED` is confirmed
      stale, correct or remove it in a small follow-up commit (config/comment fix, not a proposal —
      see `openspec/AGENTS.md`'s "Skip proposal for... Configuration changes").
- [ ] 4.5 Notify Evelyn of the go/no-go recommendation immediately, concretely: @-mention her
      GitHub handle (`@egao28`) directly on the #620 comment from 4.3 (not just posting and assuming
      she sees it), and — since a passive GitHub notification isn't "immediate" — also reach her
      through whatever direct channel (Slack/in-person) is fastest if this run is being executed on
      her behalf rather than by her. Per the timing note above, this is already overdue against the
      date #620 referenced, so say that plainly rather than presenting it as on-time, and send it as
      soon as 4.3 is decided, not batched with other follow-up.
- [ ] 4.6 Clean up every piece of state this run created on staging, unconditionally (design.md:
      cleanup does not depend on success or failure). Three separate things need deleting, keyed by
      three different identifiers captured above — none of them share a key with each other:
      1. **The OAuth client(s).** Dynamic registration creates the `oauth_clients` row before any
         login happens, keyed by `client_id` (2.1, 2.5) — **not** by the disposable identity from
         1.3. For each `client_id` recorded:
         `DELETE FROM auth.oauth_clients WHERE client_id = '<client_id>';`
         (run `\d auth.oauth_clients` first to confirm the exact column name on staging's deployed
         GoTrue version if this errors — the row is keyed by the client_id returned in the `201`,
         but the column name itself hasn't been directly confirmed against staging's schema.)
      2. **The session.** Keyed by the disposable identity's `auth.users.id`:
         `DELETE FROM auth.sessions WHERE user_id = (SELECT id FROM auth.users WHERE email =
         '<disposable email from 1.3>');`
      3. **The disposable user account itself.** This is the step design.md's "avoids polluting
         staging's user list" rationale for using a disposable identity actually depends on — a
         disposable identity that never gets deleted pollutes the user list exactly as much as a
         real account would:
         `DELETE FROM auth.users WHERE email = '<disposable email from 1.3>';`
      Record in the 4.1 write-up that all three were run, or which ones couldn't be (per the
      abort-safe note above) and why.
