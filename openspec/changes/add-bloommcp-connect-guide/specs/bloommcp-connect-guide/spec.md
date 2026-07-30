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
behind either environment grants read access to essentially the entire scientific database (every
`public.*` table, including tables created after the guide is written), not merely the
researcher's own data, and that write access is confined to the `bloommcp-data` Storage bucket
only. This statement SHALL NOT be placed after the connection steps or as a footnote.

#### Scenario: The warning appears before the first connection instruction

- **WHEN** the guide is read top to bottom
- **THEN** the access-scope warning appears before the first `claude mcp add` command or any other
  connection instruction

#### Scenario: The warning names both the read breadth and the write confinement

- **WHEN** the access-scope warning is read on its own
- **THEN** it states both that read access spans the whole database (not scoped per-user/per-lab/
  per-experiment) and that write access is confined to the `bloommcp-data` bucket

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
