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
- `GRANT USAGE ON SCHEMA storage TO bloom_workflows` (schema grants are a `supabase db push`
  no-op, so this one lives outside the migrations, in `supabase/grants/schema_grants.sql`
  instead) — a prerequisite for the storage grants above.

**This is a dedicated account, not a reuse of #391's `services/workflows` video-generation
endpoint's account.** Both accounts share the `bloom_workflows` *role* (an accepted shared-role
tradeoff from bloom #470's review), but each has its own Auth *credentials* — so a leaked
cluster-pod Secret doesn't also compromise the video endpoint, and vice versa.

## Consumption shape

`bloomctl` reads credentials from a plain dotenv file — see
`bloomcli/src/bloomctl/credentials.py`'s `load_credentials`. No service-role-key path and no
env-var-only path exist; this is the only mechanism for how `bloomctl` *reads* credentials.
(`bloomctl login --email ... --password ... --api-url ... --anon-key ...` is a second, fully
non-interactive way to *produce* the same file — it just runs the same write path a human
running `bloomctl login` interactively would. Either approach is valid; the file-mount approach
documented below needs no code changes and no login step.)

- Path: `~/.bloom/credentials.txt` (the default `prod` profile).
- Format: dotenv, four required keys —

  ```
  BLOOM_API_URL=https://bloom.salk.edu/api
  BLOOM_ANON_KEY=<anon key>
  BLOOM_EMAIL=bloom-pipeline-workflows@salk.edu
  BLOOM_PASSWORD=<password>
  ```
- Permissions: as hygiene, set `defaultMode: 0600` on the Secret volume — `bloomctl` doesn't
  check the file's mode when reading it (only `save_credentials`, the interactive `bloomctl
  login` write path, calls `chmod(0o600)`), so a Kubernetes Secret volume mount defaulting to
  `0644` would still work, but `0600` is good practice for a file holding a password.

**This means a Kubernetes Secret volume-mounted at `~/.bloom/credentials.txt` inside the
`images-downloader` and `write-back` task containers works today, with zero `bloomctl` code
changes.** `bloomctl` doesn't need to know it's running non-interactively — it just reads the file
that's already there, exactly as `bloomctl login` would have written it interactively.

## Handing off the credential values

Once the account exists (email `bloom-pipeline-workflows@salk.edu`, password in the account
owner's password manager, plus the `BLOOM_API_URL`/`BLOOM_ANON_KEY` pair from
`https://bloom.salk.edu/api/client-info`), those four values must reach whoever wires the
cluster-side Kubernetes Secret **over a secure channel — never plaintext Slack or email** (same
rule used for the `talmolab/sleap-roots-pipeline` repo's `bloom-pipeline-serviceaccount.yaml`
token handoff). The exact channel isn't
pinned down here because the consumer-side Secret doesn't exist yet; decide it when
`sleap-roots-pipeline`'s Argo wiring (see the `wire-a4-batch-stage-write-back` branch, or its
successor) is ready to receive it.

## Related

- [talmolab/sleap-roots-pipeline#17](https://github.com/talmolab/sleap-roots-pipeline/issues/17) —
  tracks this credential's provisioning end-to-end.
- [bloom #398](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/398) — originally
  scoped for non-interactive `bloomctl` auth; the file-mount approach above already satisfies that
  need with no `bloomctl` code changes.
- `docs/superpowers/specs/2026-07-28-a4-workflows-credential-design.md` — the fuller design doc
  with the reasoning behind the decisions summarized here.
