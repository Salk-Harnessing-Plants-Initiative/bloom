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

- [x] 2.1 Create `bloommcp/docs/connecting-claude-code.md` covering, in order: the access-scope
      warning (before anything else — read access spans virtually every `public.*` table by
      default via a schema-wide grant, RLS can and does carve out exceptions, named example
      `gene_patents`; write access is insert/update/delete, confined to the `bloommcp-data` bucket),
      the Salk wifi/VPN prerequisite, the `claude mcp add` command for prod, the `claude mcp add`
      command for staging (with the explicit `:8443`/wrong-port-trap callout), where to get
      `<token>` (the placeholder from 1.1), and a pointer to `bloommcp/docs/storage-backends.md`'s
      `BLOOM_STORAGE_BACKEND=local` mode.
- [x] 2.2 Add the Claude Desktop/Enterprise section, explicitly marked not-yet-written, forward-
      pointing to #522.
- [x] 2.3 Searched the repo (`git grep` for "connecting-claude-code"/"connect-claude-code") —
      no pre-existing stale reference to this guide anywhere; nothing to repoint. Added new
      cross-links: a one-line pointer from `bloommcp/README.md`'s intro to the new guide, and fixed
      `_WIKI/BLOOMMCP/README.md`'s "Supabase data access" section, which previously claimed
      `bloom_agent` "can read every `public.*` table" unconditionally — now states the schema-wide
      default plus the `gene_patents`-style RLS-exception caveat, and lists delete alongside
      insert/update for the storage-bucket write grant, with a link to the new guide.

## 3. Verify against the spec

- [x] 3.1 Checked the shipped guide against every scenario in `specs/bloommcp-connect-guide/spec.md`:
      - *Complete/self-contained* — the guide's own content (warning → prerequisite → prod command →
        staging command → token placeholder → offline pointer → Desktop deferral) reaches a working
        `claude mcp add` invocation with no external doc needed.
      - *Offline alternative discoverable* — "No shared server? Run bloommcp fully locally" section
        links `storage-backends.md` and names `BLOOM_STORAGE_BACKEND=local`.
      - *Warning precedes connection steps* — "Before you connect: what this token actually grants"
        is the first section after the one-paragraph intro, ahead of the Prerequisite/Connecting
        sections.
      - *Warning names read breadth + write confinement* — "Read access spans virtually every table
        ... by default" / "no per-user/per-lab/per-experiment filtering" and "Write access is
        confined to one Storage bucket ... insert, update, and delete."
      - *Warning doesn't overclaim* — "Row-level security can, and in at least one confirmed case
        does, narrow that default" + named `gene_patents` example + "Don't treat ... as an
        unconditional guarantee."
      - *Both environments, staging-port trap explicit* — separate "Connecting to production"/
        "Connecting to staging" sections, plus a dedicated paragraph: "The `:8443` is not optional —
        don't drop it," explaining the silent-200-from-prod failure mode.
      - *Token placeholder unmissable* — the token section is the literal string
        `` `<TODO: name the BLOOMMCP_API_KEY contact/process ...>` `` — not a plausible-sounding
        non-answer.
      - *Desktop/Enterprise deferred* — "**Not yet written.**" as the section's first words, with a
        link to #522.
      - *Live-verification status stated, not silently assumed* — covered by tasks.md section 5
        below and the PR description, not by the guide file itself (this requirement targets the
        change's own tracking, per its wording).

## 4. Housekeeping this change's own review surfaced

- [x] 4.1 PR #563 (`bloommcp-caller-identity`, adds `bloommcp_usage`) merged to `staging` on
      2026-07-30, ~90 minutes after this branch forked. Re-checked: `bloommcp_usage`'s `bloom_agent`
      grant (`INSERT`, `UPDATE` on `public.bloommcp_usage` — a `public.*` table, outside the
      `bloommcp-data` bucket) did require a guide update, as flagged. Resolved: both
      `bloommcp/docs/connecting-claude-code.md` and `_WIKI/BLOOMMCP/README.md` now disclose this as
      a narrow, non-scientific-data exception to the Storage-bucket-only write claim.
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

- [x] 6.1 `openspec validate add-bloommcp-connect-guide --strict` passes.
