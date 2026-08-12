## Context

This is a manual verification task against a live, security-relevant auth flow (OAuth 2.1 login,
#613) on a shared staging deployment, with a hard external deadline (Evelyn's demo Tuesday). #620
was filed 2026-08-06 referencing that Tuesday, which is 2026-08-11. As of this revision it is
2026-08-12 — one day past it — and #620 still has zero comments: no run write-up, no go/no-go call
posted. Whether the demo already happened without this verification, or this has simply slipped
unnoticed, isn't answerable from the repo alone. This is now **overdue**, not merely urgent — it
warrants a short design.md for two reasons: the run touches real staging infrastructure (a mis-run
could leave stray OAuth client registrations, an undeleted test user, or confusing log noise on a
shared stack), and the go/no-go call for the demo needs written criteria decided _before_ the run,
not improvised after — otherwise "did it work" risks becoming a judgment call made under time
pressure.

This repository is **public**. Anything recorded as part of this verification (GitHub issue
comments, follow-up issues) is world-readable — see the redaction decision below.

## Goals / Non-Goals

- Goals: exercise the full `mcp-remote` OAuth flow against staging exactly once (plus retries only
  to isolate a failure, not to keep re-trying past a clear go/no-go signal); answer the
  Desktop-restart cached-token question definitively; leave a written record usable by someone who
  wasn't present for the run.
- Non-Goals: fixing anything found broken (see proposal.md Non-Goals); deciding the #616 network
  path; writing the Desktop section of `connecting-claude-code.md`.

## Decisions

- **Decision: run against staging with a disposable, clearly-named test identity, not a real
  researcher account.** Dynamic client registration and the browser login/consent step create real
  rows in staging's Supabase (`oauth_clients`/session tables, and the `auth.users` row itself from
  the bloom-web signup). Using a throwaway or clearly-labeled account avoids polluting staging's
  user list with a run that exists only to verify plumbing — but only if the `auth.users` row is
  actually deleted afterward (tasks.md 4.6); a disposable identity that's created but never removed
  pollutes the user list exactly as much as a real account would, so "disposable" is a claim this
  change's cleanup step has to make true, not a property of the email address alone. Alternative
  considered: use a real account — rejected, since a demo-prep verification run isn't worth
  attributing to a real person's usage history.
- **Decision: treat the `.env.staging.defaults` comment/value mismatch as a signal to read, not a
  precondition to fix first.** The issue's own checklist item 1 says "once staging's OAuth flag is
  flipped" — the flag already reads `true`. Rather than treating that as ambiguous and blocking on
  a clarification round-trip, this change proceeds on the observed value (which is what the running
  server actually uses) and uses the run's own outcome as the *authoritative* signal for whether the
  comment or the value is the stale one — a 200 from a lightweight reachability check (tasks.md 1.2)
  on the consent route only proves the route responds at all, not that consent actually completes
  correctly, so it doesn't replace the live run's own result. If the flow fails specifically at the
  consent step in a way consistent with the bloom-web consent route not actually being deployed to
  staging, that's the answer; if it succeeds, the comment is simply stale and gets corrected.
- **Decision: also exercise the consent-denial path once, as a secondary check (tasks.md 2.5).**
  This is the first time this OAuth surface has been exercised against anything other than a dev
  stack, and every scenario tried so far is the happy path. Confirming that denying consent
  actually stops the flow (no token, no tool access) costs one extra registration + login during
  the same live session — near-zero marginal effort against a real, if unlikely, gap: an auth
  surface that only fails safe on the path someone bothered to test. This is informational, not a
  go/no-go input — the demo doesn't depend on how consent-denial behaves.
- **Decision: cap OAuth registration retries at 3 total attempts, not open-ended.** PR #613's own
  description discloses no rate limiting on `/auth/v1/oauth/*` (the dynamic-registration route) —
  every attempt, success or failure, persists a new row. "Retries only to isolate a failure" (Goals,
  above) needs a concrete number or it's unenforceable under time pressure. Alternative considered:
  no explicit cap, trusting judgment — rejected, since "isolate a failure" is exactly the kind of
  judgment that erodes under a same-day deadline.
- **Decision: cleanup of run-created staging state is unconditional, not contingent on failure, and
  covers three separately-keyed rows, not one.** An earlier draft of this section scoped cleanup to
  "if the failure mode allows it," but dynamic registration creates an `oauth_clients` row on
  *every* run, success or failure — there is nothing about success that makes cleanup unnecessary.
  A later draft also conflated the three things this run creates under one key ("the disposable
  identity"), but they aren't all found the same way: the `oauth_clients` row is keyed by `client_id`
  (created at registration, before login — unrelated to which identity later logs in), while the
  session and the `auth.users` row are keyed by the disposable identity's email/id (created at
  signup and login). tasks.md 4.6 deletes all three, each by its own key, and is explicitly marked
  abort-safe — mandatory even if the run stops partway, using whatever identifiers were captured up
  to that point, not only on a clean finish.
- **Decision: redact credentials from every written record, and check the redaction independently
  before posting.** Bearer tokens, authorization codes, `client_secret` values, session cookies, and
  PKCE `code_verifier`/`state` values all appear in real responses or browser network captures
  during this flow. Given this repository is public (see Context), every task that instructs
  capturing request/response detail also instructs redacting those fields — status codes and error
  bodies are useful evidence; raw secrets are not needed to make the record useful and must not be
  pasted anywhere. Because posting to a public repo is irreversible, tasks.md 4.1 also requires a
  second, independent grep of the draft immediately before posting — one remembered redaction pass
  made while drafting isn't a strong enough gate for a mistake that can't be taken back.
- **Decision: re-confirm the fallback path before relying on it, and do so first.** The go/no-go
  criteria below only make sense if the "no-go" side (Claude Code + `BLOOMMCP_API_KEY`) is actually
  known-good *today*, not merely documented as working in the past. Given there is no lead time left
  (see Context), discovering the fallback itself is broken only after the OAuth path also fails
  would leave no time to recover either way — so tasks.md checks the fallback first, before spending
  time on the OAuth flow.
- **Decision: go/no-go criteria for the Tuesday demo, fixed here rather than decided after the run:**
  - **Go** (use `mcp-remote` + Desktop live in the demo) only if: the full flow succeeds end-to-end
    including a real tool call against staging data, AND the Desktop-restart cached-token behavior
    is known (either answer is fine — "always re-prompts" just means log in fresh before the demo).
  - **No-go** (fall back to Claude Code + `BLOOMMCP_API_KEY`, per
    [connecting-claude-code.md](../../../bloommcp/docs/connecting-claude-code.md)) if any step in
    the flow fails against staging. Full stop — this run does not attempt to land and re-verify a
    fix in the same session to salvage a "go." An earlier draft's no-go clause left that as a
    judgment call ("...or if a fix would be needed and there isn't lead time..."), which is exactly
    the kind of improvised, under-pressure call these criteria exist to eliminate (see Context). A
    failed step is unconditionally a no-go for this run; any fix is filed as a follow-up issue
    (tasks.md 4.2) and re-verified as its own later run, not squeezed into this one.
  - No partial-credit "probably fine" outcome — an untested step defaults to no-go for that step,
    consistent with #620's own framing ("enough lead time to fall back").

## Risks / Trade-offs

- **Shared-stack risk:** any run — successful or not — leaves state (a registered OAuth client, a
  session, the disposable `auth.users` row itself, and a `bloommcp_usage` row from the read-only
  tool call) on staging. Mitigation: use a disposable identity (above); the verification record
  notes whether anything was left behind, and the OAuth client, session, and user rows are deleted
  unconditionally as their own task, each by its own key (deleting test-created rows is a
  config/data cleanup, not a code change, so it stays in scope for this change even though fixing a
  code defect does not). The `bloommcp_usage` row is accepted, not cleaned up — it's the same
  usage-tracking write any real request makes (`IdentityMiddleware` upserts on every non-`/health`,
  non-`401` request via `record_usage_async`, keyed by `identity` — a rolling `first_seen`/
  `last_seen`/`request_count` counter, not a new row per call), not pollution specific to this run,
  and there is no existing per-row deletion or TTL path for that table (only a full-table-drop
  migration rollback, which is not a cleanup mechanism this task should reach for). A rolling
  counter row for a disposable identity is a much smaller concern than an undeleted login account.
- **Mutating-tool risk:** the "real tool call against staging data" step could, if unconstrained,
  land on a tool that persists a new run against a real experiment (`remove_outliers`, `qc_clean`,
  and anything else that calls `ResultStore.commit()`) — indistinguishable afterward from a genuine
  analysis. Mitigation: tasks.md restricts this step to an explicitly read-only tool
  (`list_available_experiments`).
- **Time pressure vs. thoroughness:** the Tuesday deadline could tempt stopping at "the handshake
  worked" without actually confirming a tool call against real data. Mitigation: the go/no-go
  criteria above make the tool-call check a hard requirement, not an optional nice-to-have.
- **Non-reproducibility:** unlike the codebase's audit-script precedents (`add-bloommcp-outliers-staleness-audit`,
  `add-bloommcp-outliers-fit-audit`), this isn't a re-runnable script — it's a one-time manual
  session. If the same question comes up again later (e.g., after a future OAuth-related change),
  this change's record is historical context, not a tool to re-invoke. Accepted: #620 scopes this
  as a one-time check, not ongoing monitoring.

## Migration Plan

Not applicable — no code or schema changes ship with this change.

## Open Questions

None remaining — the question this section previously posed ("if the flow fails at the consent
step, is the fix small enough to land before Tuesday?") is resolved by the tightened go/no-go rule
above: any failure is a no-go for this run, full stop, regardless of how small the fix might be.
That removes the judgment call this section used to defer.
