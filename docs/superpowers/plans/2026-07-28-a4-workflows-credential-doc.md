# A4 `bloom_workflows` Credential Doc Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document how the A4 cluster pipeline consumes a dedicated `bloom_workflows` Supabase
credential, and point the existing `bloomcli` README stub + bloom #398 at that documentation —
closing the documentation half of `sleap-roots-pipeline#17`.

**Architecture:** Pure documentation change. One new doc under `docs/credentials/`, a one-line edit
to an existing `bloomcli/README.md` stub, and a drafted (not posted) GitHub comment. No code,
tests, or migrations — every DB grant this credential needs already exists (verified directly
against `supabase/migrations/20260716000000_create_workflows_role.sql`,
`20260720000000_grant_bloom_workflows_writeback_rpc.sql`,
`20260722000200_create_cyl_intermediates_bucket.sql`), and the consumption mechanism
(`bloomcli/src/bloomctl/credentials.py`'s `load_credentials`) already works via a plain file mount
with zero code changes.

**Tech Stack:** Markdown docs only, in the `salk-bloom` repo (branch
`eberrigan/a4-workflows-credential-doc`, based on `origin/staging`).

## Global Constraints

- No code, test, or migration changes in this plan — documentation only.
- Never post a GitHub comment without Elizabeth's explicit sign-off on the exact text (standing
  rule) — Task 3 produces a draft for her review; posting happens only after she approves it,
  outside this plan's automated steps.
- Do not specify a concrete secret-transmission mechanism (e.g. a specific secrets manager or
  channel) — the consumer-side Kubernetes Secret doesn't exist yet
  (`wire-a4-batch-stage-write-back` in `sleap-roots-pipeline` has zero diff from `main` as of this
  writing). State the constraint ("secure channel, never plaintext Slack/email"), not a mechanism.
- Match the dotenv format exactly as implemented in `bloomcli/src/bloomctl/credentials.py`:
  keys `BLOOM_API_URL`, `BLOOM_ANON_KEY`, `BLOOM_EMAIL`, `BLOOM_PASSWORD`; file
  `~/.bloom/credentials.txt` for the default `prod` profile; file mode `0600`.

---

## Task 1: Write the new credential-consumption doc

**Files:**
- Create: `docs/credentials/bloom-workflows-a4-pipeline.md`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the doc path `docs/credentials/bloom-workflows-a4-pipeline.md`, which Task 2 links to.

- [ ] **Step 1: Create the `docs/credentials/` directory and write the doc**

Write this exact content to `docs/credentials/bloom-workflows-a4-pipeline.md`:

```markdown
# `bloom_workflows` credential for the A4 cluster pipeline

The A4 cluster pipeline (`talmolab/sleap-roots-pipeline`'s Argo `images-downloader` and
`write-back` tasks) authenticates to Bloom as a dedicated Supabase Auth account flagged
`is_workflows: true`, which the existing JWT hook
(`supabase/migrations/20260703120000_add_bloom_workflows_jwt_hook_branch.sql`) routes into the
`bloom_workflows` Postgres role. All grants that role needs already exist:

- `SELECT` on `cyl_images` / `cyl_scans_extended`, and `SELECT` on the `images` Storage bucket —
  `supabase/migrations/20260716000000_create_workflows_role.sql`.
- `EXECUTE` on `insert_cyl_result_envelope` (the write-back RPC) —
  `supabase/migrations/20260720000000_grant_bloom_workflows_writeback_rpc.sql`.
- `SELECT`/`INSERT`/`UPDATE` on the `cyl-intermediates` Storage bucket (blob uploads from
  `bloomctl cyl ingest-result --predictions-dir`) —
  `supabase/migrations/20260722000200_create_cyl_intermediates_bucket.sql`.

**This is a dedicated account, not a reuse of #391's `services/workflows` video-generation
endpoint's account.** Both accounts share the `bloom_workflows` *role* (an accepted shared-role
tradeoff from bloom #470's review), but each has its own Auth *credentials* — so a leaked
cluster-pod Secret doesn't also compromise the video endpoint, and vice versa.

## Consumption shape

`bloomctl` reads credentials from a plain dotenv file — see
`bloomcli/src/bloomctl/credentials.py`'s `load_credentials`. No service-role-key path and no
env-var-only path exist; this is the only mechanism:

- Path: `~/.bloom/credentials.txt` (the default `prod` profile).
- Format: dotenv, four required keys —

  ```
  BLOOM_API_URL=https://bloom.salk.edu
  BLOOM_ANON_KEY=<anon key>
  BLOOM_EMAIL=bloom-pipeline-workflows@salk.edu
  BLOOM_PASSWORD=<password>
  ```
- Permissions: `0600`.

**This means a Kubernetes Secret volume-mounted at `~/.bloom/credentials.txt` inside the
`images-downloader` and `write-back` task containers works today, with zero `bloomctl` code
changes.** `bloomctl` doesn't need to know it's running non-interactively — it just reads the file
that's already there, exactly as `bloomctl login` would have written it interactively.

## Handing off the credential values

Once the account exists (email `bloom-pipeline-workflows@salk.edu`, password in the account
owner's password manager, plus the `BLOOM_API_URL`/`BLOOM_ANON_KEY` pair from
`https://bloom.salk.edu/api/client-info`), those four values must reach whoever wires the
cluster-side Kubernetes Secret **over a secure channel — never plaintext Slack or email** (same
rule used for the `bloom-pipeline-serviceaccount.yaml` token handoff). The exact channel isn't
pinned down here because the consumer-side Secret doesn't exist yet; decide it when
`sleap-roots-pipeline`'s Argo wiring (see the `wire-a4-batch-stage-write-back` branch, or its
successor) is ready to receive it.

## Related

- [talmolab/sleap-roots-pipeline#17](https://github.com/talmolab/sleap-roots-pipeline/issues/17) —
  tracks this credential's provisioning end-to-end.
- [bloom #398](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/398) — originally
  scoped for non-interactive `bloomctl` auth; the file-mount approach above already satisfies that
  need with no `bloomctl` code changes.
```

- [ ] **Step 2: Verify the doc's factual claims against the live source**

Run these to confirm nothing has drifted since this plan was written:

```bash
git -C c:/repos/salk-bloom show HEAD:supabase/migrations/20260716000000_create_workflows_role.sql | grep -c "bloom_workflows"
git -C c:/repos/salk-bloom show HEAD:supabase/migrations/20260720000000_grant_bloom_workflows_writeback_rpc.sql | grep -c "insert_cyl_result_envelope"
git -C c:/repos/salk-bloom show HEAD:bloomcli/src/bloomctl/credentials.py | grep -n "BLOOM_API_URL\|BLOOM_ANON_KEY\|BLOOM_EMAIL\|BLOOM_PASSWORD\|0o600\|credentials.txt"
```

Expected: each grep returns matches (non-zero counts / non-empty output). If anything is empty,
the doc's claims are stale — fix the doc's text before continuing, don't fix the grep.

- [ ] **Step 3: Commit**

```bash
git add docs/credentials/bloom-workflows-a4-pipeline.md
git commit -m "docs(credentials): document A4 bloom_workflows cluster credential"
```

---

## Task 2: Link the new doc from `bloomcli/README.md`

**Files:**
- Modify: `bloomcli/README.md:192-194`

**Interfaces:**
- Consumes: the doc path produced by Task 1
  (`docs/credentials/bloom-workflows-a4-pipeline.md`, relative to repo root; from `bloomcli/`
  that's `../docs/credentials/bloom-workflows-a4-pipeline.md`).
- Produces: nothing further downstream.

- [ ] **Step 1: Read the current stub to confirm line numbers haven't shifted**

Run: `grep -n "Non-interactive / scoped credentials" bloomcli/README.md`

Expected output (line number may vary slightly, text must match):
```
193:(`bloom_writer` / `bloom_admin`). Non-interactive / scoped credentials for
```

- [ ] **Step 2: Replace the stub**

Find this exact text in `bloomcli/README.md` (currently spanning two lines):

```
Auth: uses your saved login profile, which must have write access
(`bloom_writer` / `bloom_admin`). Non-interactive / scoped credentials for
cluster/CI use are tracked separately (#398).
```

Replace with:

```
Auth: uses your saved login profile, which must have write access
(`bloom_writer` / `bloom_admin`). For non-interactive / scoped credentials for
cluster/CI use, see
[`docs/credentials/bloom-workflows-a4-pipeline.md`](../docs/credentials/bloom-workflows-a4-pipeline.md)
(tracked by #398).
```

- [ ] **Step 3: Verify the replacement**

Run: `grep -n "bloom-workflows-a4-pipeline" bloomcli/README.md`
Expected: one match, on the line just edited.

- [ ] **Step 4: Commit**

```bash
git add bloomcli/README.md
git commit -m "docs(bloomcli): link README's #398 stub to the new credential doc"
```

---

## Task 3: Draft the bloom #398 comment for Elizabeth's review

**Files:**
- Create: `docs/credentials/398-comment-draft.md` (temporary — deleted in Step 4 of this task,
  never committed)

**Interfaces:**
- Consumes: the doc path from Task 1.
- Produces: nothing — this task's output is a message shown to Elizabeth in-conversation, not a
  file that ships in the PR.

- [ ] **Step 1: Write the draft comment to a scratch file**

Write this exact content to `docs/credentials/398-comment-draft.md`:

```markdown
While provisioning A4's cluster credential (sleap-roots-pipeline#17), found that this issue's
original scope may be narrower than written: `bloomctl` already reads credentials from a plain
dotenv file (`~/.bloom/credentials.txt`, see `bloomcli/src/bloomctl/credentials.py`) with no
service-role-key path and no interactive-only requirement. A Kubernetes Secret volume-mounted at
that path works today, with **zero `bloomctl` code changes** — `bloomctl` doesn't need to know
it's running non-interactively, it just reads whatever file is already there.

Documented the full consumption shape + the dedicated-account decision at
`docs/credentials/bloom-workflows-a4-pipeline.md`.

Not closing this — leaving the call on whether to re-scope, narrow, or close #398 to whoever's
driving it. Flagging so the remaining scope (if any) is considered against what's already covered
by the file-mount approach.
```

- [ ] **Step 2: Show the draft to Elizabeth for approval**

Print the contents of `docs/credentials/398-comment-draft.md` in the conversation and ask
explicitly: "Here's the draft comment for bloom #398 — post it as-is, edit it, or hold off?" Do
not post to GitHub until she gives explicit approval of this specific action.

- [ ] **Step 3: Post the comment (only after explicit approval)**

Once approved, post via:

```bash
gh api repos/Salk-Harnessing-Plants-Initiative/bloom/issues/398/comments -f body="$(cat docs/credentials/398-comment-draft.md)"
```

- [ ] **Step 4: Delete the scratch draft file (it must not ship in the PR)**

```bash
rm docs/credentials/398-comment-draft.md
```

Confirm it's gone: `git status docs/credentials/398-comment-draft.md` should show nothing (file
was never staged/committed, so nothing to unstage).

---

## Task 4: Open the pull request

**Files:** none (this task only pushes the branch and opens a PR; Tasks 1–2 are the code content).

**Interfaces:**
- Consumes: commits from Tasks 1 and 2 (Task 3's file is deleted, not part of the PR).

- [ ] **Step 1: Confirm the working tree is clean and only intended commits are present**

```bash
git status
git log --oneline origin/staging..HEAD
```

Expected: clean working tree; exactly two commits ahead of `origin/staging` (Task 1's and Task
2's), no `398-comment-draft.md` present anywhere (tracked or untracked).

- [ ] **Step 2: Push the branch**

```bash
git push -u origin eberrigan/a4-workflows-credential-doc
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --base staging --title "docs: document A4 bloom_workflows cluster credential (sleap-roots-pipeline#17)" --body "$(cat <<'EOF'
## Summary
- Documents how the A4 cluster pipeline's dedicated `bloom_workflows` credential is consumed (`docs/credentials/bloom-workflows-a4-pipeline.md`) — all DB grants already exist (#391/#470/#407's migrations), so this is documentation only, no code/migration change.
- Links `bloomcli/README.md`'s existing #398 stub to the new doc.

## Context
Closes the documentation half of [talmolab/sleap-roots-pipeline#17](https://github.com/talmolab/sleap-roots-pipeline/issues/17). The actual Supabase Auth account (`bloom-pipeline-workflows@salk.edu`, dedicated — not reused from #391's video endpoint) is provisioned directly in the Supabase dashboard, outside this PR. See `docs/superpowers/specs/2026-07-28-a4-workflows-credential-design.md` for the full design/brainstorm.

## Test plan
- [ ] Doc's factual claims re-verified against current `supabase/migrations/**` and `bloomcli/src/bloomctl/credentials.py` (see Task 1, Step 2 of the implementation plan)
- [ ] README link renders correctly and points at the right relative path
EOF
)"
```

- [ ] **Step 4: Report the PR URL back to Elizabeth**

The `gh pr create` output includes the PR URL — share it directly, don't fetch or re-derive it.

---

## Self-Review Notes

- **Spec coverage:** design doc's three deliverables (new credential doc, README stub link, #398
  comment draft) each map to Task 1, Task 2, and Task 3 respectively. The design doc's explicit
  "out of scope" items (Supabase account creation, Secret transmission, bloomctl code, OpenSpec)
  have no corresponding task, matching the spec.
- **No placeholders:** every task step includes literal file content or exact commands; no "add
  appropriate docs" or "TBD" language.
- **Consistency:** the doc path (`docs/credentials/bloom-workflows-a4-pipeline.md`) and the account
  email (`bloom-pipeline-workflows@salk.edu`) are identical across Tasks 1–3, matching the design
  doc.
