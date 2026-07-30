## 1. The one fact this proposal deliberately ships without

- [x] 1.1 **Decision (2026-07-30, with the issue owner): ship an explicit, unmissable placeholder**
      for the `BLOOMMCP_API_KEY` distribution contact, rather than block on naming a real one. There
      is no self-service lookup or existing doc for this today (confirmed: `scripts/generate-secrets.sh`
      mints it once, `scripts/setup-env-secrets.sh` pushes it to GitHub Secrets, nothing publishes it
      to researchers). The placeholder must be visually unmistakable as unfinished (e.g.
      `<TODO: name the BLOOMMCP_API_KEY contact/process>`) — not a vague-but-plausible sentence like
      "ask the maintainers" that would pass a casual read as a real answer. This is a disclosed,
      deliberate gap (see proposal.md's Review History), not an oversight.

## 2. Write the guide

- [ ] 2.1 Create `bloommcp/docs/connecting-claude-code.md` covering, in order: the access-scope
      warning (before anything else — read access spans virtually every `public.*` table by
      default via a schema-wide grant, RLS can and does carve out exceptions, named example
      `gene_patents`; write access is insert/update/delete, confined to the `bloommcp-data` bucket),
      the Salk wifi/VPN prerequisite, the `claude mcp add` command for prod, the `claude mcp add`
      command for staging (with the explicit `:8443`/wrong-port-trap callout), where to get
      `<token>` (the placeholder from 1.1), and a pointer to `bloommcp/docs/storage-backends.md`'s
      `BLOOM_STORAGE_BACKEND=local` mode.
- [ ] 2.2 Add the Claude Desktop/Enterprise section, explicitly marked not-yet-written, forward-
      pointing to #522.
- [ ] 2.3 Search the repo (`bloommcp/README.md`, `_WIKI/BLOOMMCP/README.md`,
      `bloommcp/docs/storage-backends.md`, and a broad grep for existing mentions of "connect" /
      "Claude Code" / a placeholder link) for anything that already points at, or should point at,
      this guide — both an existing stale reference to repoint, and a natural place to add a new
      one-line cross-link. Fix both directions in the same pass, not just new links outward.
      **Also fix `_WIKI/BLOOMMCP/README.md`'s existing access-scope line** ("The role can read every
      `public.*` table but cannot write to any of them — writes go through the storage bucket
      above") to match this proposal's corrected claim (RLS exceptions exist; write includes
      delete) rather than leaving two docs with contradictory access-scope claims.

## 3. Verify against the spec

- [ ] 3.1 Check the shipped guide against each scenario in `specs/bloommcp-connect-guide/spec.md`
      individually — for each scenario, quote the specific sentence/section in the guide that
      satisfies it (not a single holistic re-read). Confirm in particular: the access-scope warning
      precedes all connection steps and does not overclaim unconditional universal read access; both
      environments' connect commands are present with the staging-port trap called out explicitly;
      the token placeholder is visually unmistakable, not a vague non-answer; the offline
      alternative is linked; the Desktop/Enterprise section defers to #522.

## 4. Housekeeping this change's own review surfaced

- [ ] 4.1 Once PR #563 (`bloommcp-caller-identity`, adds `bloommcp_usage`) merges to `staging`,
      re-check whether `bloommcp_usage`'s `bloom_agent` grant (INSERT/UPDATE on a `public.*` table,
      outside the `bloommcp-data` bucket) requires an update to this guide's write-scope warning —
      it will, unless the guide is rewritten to account for it first. Do not let this go stale
      silently; this is the specific, disclosed staleness risk named in design.md's Risks section.
- [ ] 4.2 (Suggestion, not required for this change) If a fully authoritative "what can `bloom_agent`
      actually read" map is ever wanted, do it as a live query against the deployed `pg_policies`
      view (and role-membership grants), not a migration-file text sweep — this proposal's own quick
      sweep (design.md Decision 5) produced false positives from multi-line `CREATE POLICY`
      statements, so a migration grep alone isn't a reliable enough method for a claim this
      security-sensitive.

## 5. The acceptance-gating check this change ships without

- [ ] 5.1 **Not performed by this change — decision (2026-07-30, with the issue owner): open the PR
      with this explicitly unverified**, called out plainly in the PR description (naming the
      specific unmet #553 acceptance criterion), rather than silently shipping a guide that looks
      complete. Whoever has Salk network access and a real deployed `BLOOMMCP_API_KEY` should, when
      they pick this up:
      - Run the guide's own connect command (or a lighter-weight `curl`/`httpie` JSON-RPC
        `tools/list` call against the MCP endpoint, as a faster pre-flight check before installing
        the `claude` CLI) against both `https://bloom.salk.edu/bloommcp/mcp` and
        `https://staging.bloom.salk.edu:8443/bloommcp/mcp`.
      - **Pass criterion:** a `tools/list` response listing at least one registered tool (e.g.
        `list_available_experiments`) from **both** environments independently. A pass on one
        environment and a failure on the other counts as a fail overall — the guide documents both
        as equally supported, so both must work.
      - Fix anything this surfaces (wrong header name/format, an undocumented redirect, a stale
        example) before considering the guide verified, and check this box only once both
        environments have passed.

## 6. Validation

- [ ] 6.1 `openspec validate add-bloommcp-connect-guide --strict` passes.
