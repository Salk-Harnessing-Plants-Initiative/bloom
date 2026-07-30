## 1. Pin down the one fact this proposal couldn't determine

- [ ] 1.1 Identify and name the actual process for a researcher to obtain the shared
      `BLOOMMCP_API_KEY` today (who to ask, how it's handed over) — there is no self-service
      lookup or existing doc for this (confirmed: `scripts/generate-secrets.sh` mints it once,
      `scripts/setup-env-secrets.sh` pushes it to GitHub Secrets, nothing publishes it to
      researchers). Write the real answer into the guide — not a placeholder.

## 2. Write the guide

- [ ] 2.1 Create `bloommcp/docs/connecting-claude-code.md` covering, in order: the access-scope
      warning (before anything else), the Salk wifi/VPN prerequisite, the `claude mcp add` command
      for prod, the `claude mcp add` command for staging (with the explicit `:8443`/wrong-port-trap
      callout), where to get `<token>` (from 1.1), and a pointer to
      `bloommcp/docs/storage-backends.md`'s `BLOOM_STORAGE_BACKEND=local` mode.
- [ ] 2.2 Add the Claude Desktop/Enterprise section, explicitly marked not-yet-written, forward-
      pointing to #522.
- [ ] 2.3 Add a one-line cross-link to the new guide from `bloommcp/README.md` and/or
      `_WIKI/BLOOMMCP/README.md`, wherever a researcher landing on the dev-facing docs would most
      plausibly look next — only if not already obviously discoverable.

## 3. Verify against the spec

- [ ] 3.1 Re-read the shipped guide against each requirement in
      `specs/bloommcp-connect-guide/spec.md` — confirm the access-scope warning precedes the
      connection steps, both environments are covered with the staging-port trap called out
      explicitly, the offline alternative is linked, and the Desktop/Enterprise section defers to
      #522 rather than attempting content.

## 4. The acceptance-gating check this proposal could not run

- [ ] 4.1 **On Salk wifi/VPN, with a real deployed `BLOOMMCP_API_KEY` in hand:** run the guide's
      own connect command against both
      `https://bloom.salk.edu/bloommcp/mcp` and `https://staging.bloom.salk.edu:8443/bloommcp/mcp`,
      and confirm `tools/list` actually returns tool data (not just the unauthenticated
      health/401 checks already recorded in #553) — per the issue's explicit acceptance criterion.
      Do not mark the guide "done" without this — the unauthenticated checks prove auth is
      enforced, not that a valid token round-trips successfully.
- [ ] 4.2 Fix anything the live check in 4.1 surfaces (wrong header name/format, an undocumented
      redirect, a stale example) before publishing.

## 5. Validation

- [ ] 5.1 `openspec validate add-bloommcp-connect-guide --strict` passes.
