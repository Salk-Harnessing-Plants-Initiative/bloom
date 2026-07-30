## ADDED Requirements

### Requirement: Claude Code Connection Guide Exists and Is Complete

`bloommcp/docs/connecting-claude-code.md` SHALL exist and SHALL be a complete, start-to-finish
guide a researcher can follow to connect Claude Code to bloommcp, without needing to read any
other document first. It SHALL cover, at minimum: the Salk wifi/VPN prerequisite, the connect
command for both the staging and production environments, where to obtain the shared token, and a
pointer to `bloommcp/docs/storage-backends.md`'s fully-local/offline alternative for researchers
who'd rather not connect to the shared server at all.

#### Scenario: A researcher with no prior context can follow the guide start to finish

- **WHEN** a researcher on Salk wifi/VPN with Claude Code installed reads
  `bloommcp/docs/connecting-claude-code.md` top to bottom
- **THEN** they reach a working `claude mcp add` invocation for their target environment without
  needing to consult `bloommcp/README.md`, `_WIKI/BLOOMMCP/README.md`, or the GitHub issue

#### Scenario: The offline alternative is discoverable from the guide

- **WHEN** a researcher reads the guide and decides they'd rather not connect to the shared server
- **THEN** the guide points them to `bloommcp/docs/storage-backends.md`'s `BLOOM_STORAGE_BACKEND=local`
  mode as the alternative

### Requirement: Access-Scope Warning Precedes Connection Steps

The guide SHALL state, in plain language and before any connection instructions, that the token
behind either environment grants read access to virtually the entire scientific database by
default (every `public.*` table, including tables created after the guide is written, via a
schema-wide grant), not merely the researcher's own data. It SHALL NOT phrase this as unconditional
universal access — it SHALL note that row-level security can and does carve out exceptions to that
default (naming the confirmed example: `gene_patents` returns no rows to the shared role despite
the schema-wide grant, because its only policy targets a different role). It SHALL state that
write access is confined to the `bloommcp-data` Storage bucket only, and SHALL name insert, update,
and delete as the covered write operations (not merely "write" without enumerating delete). This
statement SHALL NOT be placed after the connection steps or as a footnote.

#### Scenario: The warning appears before the first connection instruction

- **WHEN** the guide is read top to bottom
- **THEN** the access-scope warning appears before the first `claude mcp add` command or any other
  connection instruction

#### Scenario: The warning names both the read breadth and the write confinement

- **WHEN** the access-scope warning is read on its own
- **THEN** it states both that read access spans virtually the whole database by default (not
  scoped per-user/per-lab/per-experiment) and that write access — insert, update, and delete — is
  confined to the `bloommcp-data` bucket

#### Scenario: The warning does not overclaim unconditional universal read access

- **WHEN** the access-scope warning is read on its own
- **THEN** it does not state or imply that every table is guaranteed readable with no exceptions —
  it names row-level security as a mechanism that can (and, for at least one table today, does)
  override the default

### Requirement: Both Environments Are Documented, Including the Staging-Port Trap

The guide SHALL document connect commands for both the production endpoint
(`https://bloom.salk.edu/bloommcp/mcp`) and the staging endpoint
(`https://staging.bloom.salk.edu:8443/bloommcp/mcp`), and SHALL explicitly call out that omitting
staging's `:8443` port lands on production's Caddy instance instead (same host, prod's wildcard TLS
cert still matches, connection succeeds, but the request silently returns a blank `200` rather than
reaching staging).

#### Scenario: The staging port trap is stated explicitly, not left implicit in the URL

- **WHEN** the guide documents the staging connect command
- **THEN** it explicitly warns that dropping `:8443` silently connects to production instead of
  erroring, rather than relying on the reader to notice the port in the example command

#### Scenario: The production connect command is present, not only staging's

- **WHEN** the guide is read top to bottom
- **THEN** a complete `claude mcp add` invocation for the production endpoint
  (`https://bloom.salk.edu/bloommcp/mcp`) is present, not only staging's

### Requirement: Token-Contact Placeholder Is Unmissable, Not a Vague Non-Answer

If the real contact/process for obtaining `BLOOMMCP_API_KEY` is not yet determined at the time the
guide is written, the guide SHALL mark that spot with an explicit, visually unmistakable
placeholder (e.g. a `<TODO: ...>`-style marker), rather than a sentence that reads as genuine
information without actually naming a specific person, role, or channel.

#### Scenario: An unresolved token contact is marked as a placeholder, not disguised as an answer

- **WHEN** the real token-distribution contact has not been determined
- **THEN** the guide's token-source spot contains a marker that is immediately recognizable as
  unfinished (e.g. `<TODO: ...>`), not a plausible-sounding but non-specific phrase like "ask the
  maintainers"

### Requirement: Live-Verification Status Is Stated, Not Silently Assumed

Whether the guide's connect commands have been verified end-to-end with a real deployed
`BLOOMMCP_API_KEY` against both staging and production (a live `tools/list` round-trip, not merely
the unauthenticated health/401 checks) SHALL be stated explicitly in the change's own tracking
(tasks.md and the PR that introduces the guide) — it SHALL NOT be left implicit, silently assumed
complete, or represented only by an unchecked checkbox with no accompanying statement of what,
specifically, remains unverified.

#### Scenario: An unverified live round-trip is stated plainly, not just unchecked

- **WHEN** the live authenticated round-trip has not been performed
- **THEN** the PR introducing the guide states this plainly (naming the specific unmet acceptance
  criterion from the tracking issue), rather than relying on a reader to notice an unchecked
  tasks.md item

#### Scenario: A verified live round-trip requires both environments independently

- **WHEN** the live round-trip is eventually run
- **THEN** it is considered passed only if both staging and production each independently return a
  `tools/list` response listing at least one registered tool — a pass on one environment and a
  failure on the other is a fail overall, not a partial pass

### Requirement: Claude Desktop/Enterprise Section Is Marked Not-Yet-Written

The guide SHALL include a section for Claude Desktop/Enterprise that states plainly it is not yet
written, and SHALL forward-point to
[#522](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/522) as the tracking issue
whose resolution unblocks it, rather than omitting the topic entirely or attempting a guide that
would need a rewrite once #522 resolves.

#### Scenario: A reader looking for Desktop/Enterprise instructions finds a clear deferral

- **WHEN** a researcher using Claude Desktop or Claude Enterprise reads the guide looking for
  connection instructions
- **THEN** they find a section stating this path is not yet documented, with a link to #522 for
  status, rather than no mention at all or an incomplete/incorrect attempt
