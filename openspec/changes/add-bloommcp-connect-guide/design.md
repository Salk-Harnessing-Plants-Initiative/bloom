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

## Decision 3: The authenticated round-trip ships unverified — disclosed, not silently skipped

#553 explicitly says whoever picks this up should obtain a real `BLOOMMCP_API_KEY` and confirm
`tools/list` actually returns tool data against both staging and prod before publishing. That needs
Salk network access and a live secret, neither available in the environment implementing this
change. **Decision (2026-07-30, with the issue owner):** rather than block on this indefinitely,
ship without it and say so plainly — the PR description states this specific acceptance criterion
from #553 is unmet, and tasks.md's corresponding item stays unchecked with the same note. This is
explicitly *not* equivalent to the unauthenticated health/401 checks already done in the issue —
those prove auth is enforced, not that a valid token round-trips successfully — and the gap must
not be papered over as "verified" by a future reader skimming a checked box. Whoever has real
staging/prod network access and the deployed key can close this out as a fast follow-up; it does
not require touching any other part of this change.

**If/when someone does run it:** success means a `tools/list` JSON-RPC response listing at least
one registered tool (e.g. `list_available_experiments`) for *both* environments independently — a
pass on one environment and a failure on the other is a fail overall, not a partial pass, since the
guide documents both as equally supported. A lightweight `curl`/`httpie` JSON-RPC call against the
MCP endpoint is a reasonable pre-flight smoke test before installing the `claude` CLI, if that's
faster for whoever picks this up.

## Decision 4: Where to get `<token>` ships as an explicit placeholder, not a real answer

`scripts/generate-secrets.sh` mints `BLOOMMCP_API_KEY` once per environment; `scripts/setup-env-secrets.sh`
pushes it to GitHub Secrets. Nothing in the repo documents a self-service lookup or names who to
ask — this is genuinely undocumented process knowledge, not a gap in this proposal's research, and
not something available in the environment implementing this change either. **Decision (2026-07-30,
with the issue owner):** ship the guide with a single, unmissable placeholder in that one spot
(e.g. `<TODO: name the BLOOMMCP_API_KEY contact/process>`) rather than block the rest of an
otherwise-complete, accurate guide on it, and rather than write a vague-but-plausible-looking
non-answer that would quietly fail the issue's "complete, followable" bar without looking like a
gap. The placeholder must be visually unmistakable as unfinished — not a sentence that reads as
real information.

## Decision 5: The access-scope disclosure needed a real RLS audit, not just a grant/policy grep

An earlier draft of this proposal verified the access-scope claim by citing the schema-wide
`GRANT SELECT` plus every `agent_read_*` policy in the two migrations that create them — thorough
against those two files, but insufficient on its own: a table can carry a schema-wide `GRANT SELECT`
and *still* return zero rows to `bloom_agent` if its RLS policy targets a different role and
`bloom_agent` isn't a member of that role. A 5-agent review of that draft caught exactly this case
(`gene_patents`, `FOR SELECT TO authenticated`, `bloom_agent` not a member of `authenticated`) by
independently searching for `GRANT authenticated TO <role>` across every migration, not just the
two the draft had cited. This proposal now states the access-scope claim with that caveat built in,
but a full audit (every table, every policy, every role-membership grant) was not re-run beyond
confirming this one case — a further sweep during this same fix pass (below) found the same
pattern is more widespread than this one table. A fully authoritative version of that audit,
against the live deployed schema rather than migration files, is left as a follow-up
(tasks.md 4.2), not attempted here.

**A follow-up sweep while fixing this proposal found the gap is broader than one table, in the
safer direction.** Diffing every RLS-enabled table (`ALTER TABLE ... ENABLE ROW LEVEL SECURITY`,
~65 tables on `origin/staging`) against every `CREATE POLICY` mentioning `bloom_agent` found
~30 candidates with no `bloom_agent`-targeting policy. Most were false positives from this
codebase's own formatting — several `CREATE POLICY ... ON <table>` statements wrap the `TO
bloom_agent` clause onto a second line (e.g. `gravi_scanners`), which a naive single-line grep
misses entirely; re-checking those individually found the policy present. After removing those
false positives, **~19 tables genuinely have zero policy mentions at all** — not for `bloom_agent`,
not for `bloom_user`, not for `bloom_admin`, not for anyone (spot-checked: `assemblies`, `genes`,
`gene_orthologs`, `gene_progress_notes`, `gene_candidate_scientists`, `gene_candidate_support`,
`ortho_gene_id_map`, `phenotypers`, `plate_plant_traits_list`, `plates_exp`, `plates_scan_trait`,
`plates_trait_source`, `scrna_de`, `translation_candidates`, `translation_lines`,
`translation_project_users`, `translation_projects`, `video_jobs`, `cyl_scan_videos`). With RLS
enabled and no policy at all, Postgres denies all rows to every role by default (none of
`bloom_agent`/`bloom_user`/`bloom_admin` has `BYPASSRLS` or table ownership — confirmed: all three
are plain `NOLOGIN` roles created without that attribute) — so in practice these tables look
unreachable via PostgREST for *any* bloom role today, not merely `bloom_agent`.

**This is disclosed here, not folded into the shipped guide's content.** Two reasons: (1) this was
a migration-file text sweep, not a live query against the deployed `pg_policies` view — the
gravi_* false-positive above already proves that method under-catches real policies, so it could
equally over-catch or miss something on the deployed schema (Supabase Studio also allows direct,
un-migrated policy changes). (2) whether these ~19 tables are legacy/unused, intentionally
locked down, or an unnoticed gap is a separate question from what a bloommcp connection guide needs
to answer, and asserting a specific table inventory in a researcher-facing doc risks going stale or
wrong the moment either is true. The guide keeps `gene_patents` as its one named, fully-verified
example (RLS policy targets a different role **and** `bloom_agent` isn't a member of that role —
both confirmed); it does not claim precision it doesn't have about the other ~19. If the team wants
an authoritative "what can `bloom_agent` actually read" map, that's a separate, live-DB-verified
piece of work, tracked as a suggestion in tasks.md, not attempted here.

## Risks / Trade-offs

- **Risk:** the guide ships with an unfilled placeholder for the token contact and an unverified
  live-connection claim — both are real, disclosed gaps against #553's own acceptance criteria, not
  silently glossed over. Mitigated by naming both explicitly in the PR description and in this
  proposal, rather than letting a merged PR imply full completion.
- **Risk:** the staging-port trap or access-scope figures could drift from the migrations they cite
  if those migrations change before this ships. Mitigated by citing exact migration filenames/line
  numbers in proposal.md so a future reader can re-check rather than trust a paraphrase.
- **Risk:** this guide is itself a roadmap for exactly what the shared key can read and write —
  publishing precise connection instructions plus a precise access-scope map in a repo file makes
  that map available to anyone who later obtains the shared key, not only to well-intentioned
  researchers. #553 already implicitly accepts this trade-off (the alternative — vague or omitted
  disclosure — is worse for a legitimate researcher's ability to make an informed choice), but it
  wasn't previously named as a risk anywhere in this proposal; naming it here doesn't change the
  decision, just makes it visible.
- **Risk:** `bloommcp_usage` (landing with PR #563, not yet merged) will add a `public.*` table
  write grant for `bloom_agent` outside the `bloommcp-data` bucket the moment it merges, making the
  guide's write-scope disclosure stale on that day. Mitigated by tasks.md 4.1, an explicit
  re-verification task tied to that merge rather than an assumption this proposal's snapshot stays
  accurate indefinitely.
