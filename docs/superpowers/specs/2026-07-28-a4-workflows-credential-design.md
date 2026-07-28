# A4 `bloom_workflows` Cluster Credential — Provisioning + Documentation

## Purpose

Close [talmolab/sleap-roots-pipeline#17](https://github.com/talmolab/sleap-roots-pipeline/issues/17)
("A4 infra: provision scoped Supabase credential for cluster stage-in + write-back") — the last
blocker for A4's Argo `images-downloader`/`write-back` tasks to call `bloomctl` unattended from a
cluster pod.

## Background

A4's cluster pipeline (in `talmolab/sleap-roots-pipeline`) runs `bloomctl cyl
batch-download-for-predict` / `batch-ingest-result` (merged
[bloom #532](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/532)) from Argo
Workflow pods, and needs a Supabase credential to authenticate as. The issue's framing ("provision
a scoped credential") suggested new DB grants might be needed, but reading the migrations directly
shows otherwise:

- `supabase/migrations/20260716000000_create_workflows_role.sql` +
  `20260703120000_add_bloom_workflows_jwt_hook_branch.sql` — the `bloom_workflows` role, its
  Storage-read grant on the `images` bucket, and the `is_workflows` JWT-hook branch that routes an
  Auth user into that role all already exist and are live in production (backing #391's
  `services/workflows` video-generation endpoint since 2026-07-17).
- `20260720000000_grant_bloom_workflows_writeback_rpc.sql` — `EXECUTE` on
  `insert_cyl_result_envelope` is already granted to `bloom_workflows` (merged via bloom #470,
  2026-07-20).
- `20260722000200_create_cyl_intermediates_bucket.sql` — the blob-upload bucket grants
  (`cyl-intermediates`) are already in place too, per that migration's own comment anticipating this
  exact gap.

So **every DB-side grant `bloom_workflows` needs for A4 already exists.** What's actually missing is
narrower than the issue title implies:

1. An Auth *account* for the cluster pipeline to sign in as.
2. Documentation of the credential's consumption shape, for whoever wires the cluster-side
   Kubernetes Secret (the `sleap-roots-pipeline` side, branch `wire-a4-batch-stage-write-back` —
   currently zero diff from `main`, i.e. not yet built).

`bloomcli/src/bloomctl/{auth,credentials}.py` confirm the consumption side is already simple: a
dotenv file at `~/.bloom/credentials.txt` (`BLOOM_EMAIL`/`BLOOM_PASSWORD`/`BLOOM_API_URL`/
`BLOOM_ANON_KEY`, mode `0600`), read via `dotenv_values` and authenticated via plain
`sign_in_with_password` — no service-role-key path, no env-var-only path. A Kubernetes Secret
volume-mounted at that path works **today, with zero `bloomctl` code changes.**

## Decisions

**Dedicated account, not reuse.** `bloom_workflows` is a shared *role* — bloom #470's review already
had Benfica (@blm3886, who owns the role and the `services/workflows` video endpoint) accept it as
non-A4-dedicated at the grants level. But the *Auth account* is a separate trust boundary: reusing
#391's video-endpoint account would mean a leaked cluster-pod Secret also compromises video
generation, and vice versa. Decision: provision a **second, dedicated** Supabase Auth account,
flagged `is_workflows: true` (so it inherits the same `bloom_workflows` role via the existing JWT
hook), used only by the A4 cluster pipeline.

- **Email:** `bloom-pipeline-workflows@salk.edu` — distinguishes it from #391's account by naming
  its specific consumer.
- **Password:** generated and stored by Elizabeth in her own password manager — never enters this
  session/transcript.
- **Who provisions it:** Elizabeth, directly in the Supabase dashboard (has admin access).

**No migration, no `bloomctl` code change.** All grants exist; the consumption path already works
via file mount. This is an ops action (create one Auth account) + documentation, not a feature —
per this repo's "tiny changes skip OpenSpec but still state intent" convention, this stays a design
note rather than a full OpenSpec proposal.

**Documentation location:** a new doc, `docs/credentials/bloom-workflows-a4-pipeline.md` (this repo
has no existing `docs/ops/` or `docs/credentials/` convention to match, so this is a new small
directory). Covers:
- The dotenv shape and file path/permissions the K8s Secret mount must produce.
- That this works via a plain file mount with no `bloomctl` code changes required.
- That handing the 4 credential values to whoever wires the cluster Secret must go over a secure
  channel (never plaintext Slack/email) — same standing rule as the
  `bloom-pipeline-serviceaccount.yaml` token handoff. The exact transmission mechanism is left
  unspecified here since the consumer-side Secret doesn't exist yet (`wire-a4-batch-stage-write-back`
  has no commits) — pick it when that side is ready to receive it.
- A pointer back to `sleap-roots-pipeline#17` and this design doc for context.

Also update the existing stub in `bloomcli/README.md` (currently: "Non-interactive / scoped
credentials for cluster/CI use are tracked separately (#398)") to link the new doc.

**Bloom #398:** still open, but scoped assuming a code change was needed — it isn't; the file-mount
approach already satisfies non-interactive cluster auth with zero `bloomctl` changes. Post a comment
on #398 noting this (narrows its remaining scope) without closing or re-scoping it unilaterally —
that call belongs to whoever's driving that issue.

## Scope

**In scope (this change):**
- `docs/credentials/bloom-workflows-a4-pipeline.md` (new)
- `bloomcli/README.md` — update the #398 stub to link the new doc
- A drafted (not yet posted) comment for bloom #398

**Out of scope:**
- Creating the actual Supabase Auth account — an ops action Elizabeth performs directly in the
  Supabase dashboard, not a code change tracked by this PR.
- Transmitting the credential to the cluster-side Secret — deferred until
  `wire-a4-batch-stage-write-back` (or its successor) actually defines the Secret.
- Any `bloomctl` code change — none needed.
- Re-scoping or closing bloom #398 — a comment only.

## Testing

Documentation-only change; no automated tests apply. Verification is a self-review read-through
confirming the doc accurately describes `credentials.py`'s actual dotenv format and permissions
(already verified against the live source during this design's background research).
