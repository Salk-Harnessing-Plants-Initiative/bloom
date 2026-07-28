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
