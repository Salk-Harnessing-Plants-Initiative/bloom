## Context

Documentation-only change; the design surface is small, but a few decisions are worth pinning down
so implementation doesn't drift from what this proposal actually reviewed.

## Decision 1: File location — `bloommcp/docs/connecting-claude-code.md`, not top-level `docs/`

Top-level `docs/` in this repo is a grab-bag (`docs/issues/` — CI/CD planning docs; `docs/superpowers/`
— an unrelated planning-tool convention used by at least one other branch) with no established
"researcher guide" pattern yet. `bloommcp/docs/` already holds one audience-appropriate,
evergreen how-to (`storage-backends.md`, no date prefix, unlike the dated design-snapshot docs
alongside it) that this guide will directly cross-reference. Co-locating keeps both bloommcp-facing
docs discoverable from the same place and lets the new guide link to `storage-backends.md` with a
relative path that won't need updating if `docs/` is ever reorganized.

**Alternative considered:** a new `docs/guides/` top-level directory. Rejected for this change — it
would be a new convention decided unilaterally inside a docs-only proposal; if the team wants a
general researcher-guide location later, that's a separate, small decision, not bundled here.

## Decision 2: Spec-back the guide's required content, not just write prose

This repo backs every issue-driven change with an OpenSpec capability, including doc-adjacent ones
(e.g. `bloommcp-storage-backend` covers `storage-backends.md`'s documented precedence contract).
The three things #553's Acceptance section actually gates on — the access-scope warning's
placement, both-environment coverage (including the staging-port trap), and Part 2's explicit
deferral — are concrete enough to write as checkable requirements with scenarios, so a reviewer (or
a future spec-conformance pass) can verify the shipped file against them instead of re-reading the
whole issue by hand.

**What's deliberately not spec'd:** exact prose wording, tone, or section ordering beyond "warning
before connection steps." Those are editorial, not requirements.

## Decision 3: The authenticated round-trip is a task-time gate, not something this proposal can do

#553 explicitly says whoever picks this up should obtain a real `BLOOMMCP_API_KEY` and confirm
`tools/list` actually returns tool data against both staging and prod before publishing. That needs
Salk network access and a live secret, neither of which is available while drafting a proposal.
This is tracked as an explicit, unchecked task (see tasks.md) rather than quietly assumed
equivalent to the unauthenticated health/401 checks already done in the issue — those checks
prove auth is *enforced*, not that a valid token actually works end-to-end.

## Decision 4: Where to get `<token>` is a real-world fact, not a design choice

`scripts/generate-secrets.sh` mints `BLOOMMCP_API_KEY` once per environment; `scripts/setup-env-secrets.sh`
pushes it to GitHub Secrets. Nothing in the repo documents a self-service lookup or names who to
ask — this is genuinely undocumented process knowledge, not a gap in this proposal's research.
Implementation must pin this down with an actual maintainer/contact before the guide can honestly
claim to be "complete, followable, start-to-finish" per the issue's acceptance criteria.

## Risks / Trade-offs

- **Risk:** the guide could ship with a placeholder-y "ask someone" for the token, quietly failing
  the issue's own acceptance bar. Mitigated by making it an explicit tasks.md item, not folded into
  "write the doc."
- **Risk:** the staging-port trap or access-scope figures could drift from the migrations they cite
  if those migrations change before this ships. Mitigated by citing exact migration filenames/line
  numbers in proposal.md so a future reader can re-check rather than trust a paraphrase.
