## Context

This is a manual verification task against a live, security-relevant auth flow (OAuth 2.1 login,
#613) on a shared staging deployment, with a hard external deadline (Evelyn's demo Tuesday — and,
found while drafting this proposal, #620 was filed 2026-08-06, meaning "Tuesday" is very likely
today; there is no banked lead time). It warrants a short design.md for two reasons: the run
touches real staging infrastructure (a mis-run could leave stray OAuth client registrations or
confusing log noise on a shared stack), and the go/no-go call for the demo needs written criteria
decided _before_ the run, not improvised after — otherwise "did it work" risks becoming a judgment
call made under time pressure.

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
  rows in staging's Supabase (`oauth_clients`/session tables). Using a throwaway or clearly-labeled
  account avoids polluting staging's user list with a run that exists only to verify plumbing.
  Alternative considered: use a real account — rejected, since a demo-prep verification run isn't
  worth attributing to a real person's usage history.
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
- **Decision: cap OAuth registration retries at 3 total attempts, not open-ended.** PR #613's own
  description discloses no rate limiting on `/auth/v1/oauth/*` (the dynamic-registration route) —
  every attempt, success or failure, persists a new row. "Retries only to isolate a failure" (Goals,
  above) needs a concrete number or it's unenforceable under time pressure. Alternative considered:
  no explicit cap, trusting judgment — rejected, since "isolate a failure" is exactly the kind of
  judgment that erodes under a same-day deadline.
- **Decision: cleanup of run-created staging state is unconditional, not contingent on failure.**
  An earlier draft of this section scoped cleanup to "if the failure mode allows it," but dynamic
  registration creates an `oauth_clients` row on *every* run, success or failure — there is nothing
  about success that makes cleanup unnecessary. tasks.md has an explicit, unconditional cleanup task
  for this reason.
- **Decision: redact credentials from every written record.** Bearer tokens, authorization codes,
  and `client_secret` values appear in real responses during this flow. Given this repository is
  public (see Context), every task that instructs capturing request/response detail also instructs
  redacting those fields — status codes and error bodies are useful evidence; raw secrets are not
  needed to make the record useful and must not be pasted anywhere.
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
    the flow fails against staging, or if a fix would be needed and there isn't lead time to land
    and re-verify it before Tuesday.
  - No partial-credit "probably fine" outcome — an untested step defaults to no-go for that step,
    consistent with #620's own framing ("enough lead time to fall back").

## Risks / Trade-offs

- **Shared-stack risk:** any run — successful or not — leaves state (a registered OAuth client, a
  session) on staging. Mitigation: use a disposable identity (above); the verification record notes
  whether anything was left behind, and it is deleted unconditionally as its own task (deleting a
  test-created `oauth_clients` row is a config/data cleanup, not a code change, so it stays in scope
  for this change even though fixing a code defect does not).
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

- If the flow fails specifically at the consent step, is the fix (deploying/fixing the bloom-web
  consent route on staging) small enough to land before Tuesday, or does it immediately become the
  no-go case? Left for whoever runs this to judge against the actual failure once observed — not
  answerable in advance.
