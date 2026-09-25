## Why

The trait extractor moves to `sleap-roots-contracts==0.1.0a9` (`talmolab/sleap-roots#269`, merged
2026-09-24, `e373b0f`) and stamps every envelope `contract_version = "0.1.0a9"`. The write-back RPC
`insert_cyl_result_envelope` accepts only `0.1.0a7`, so the cluster's traits template cannot take the
a9 image until Bloom accepts a9 (bloom#895). For the RPC and the result-envelope schema the bump is a
pure version restamp. The verification is in design.md.

## What Changes

- **Re-pin the write-back RPC to `0.1.0a9`** (`cyl-trait-writeback`). A new forward migration
  `CREATE OR REPLACE`s the live **2-arg** `insert_cyl_result_envelope(jsonb, text)`, byte-identical to
  `20260917140000_fix_cyl_redelivery_status_fallback.sql` except for the pinned literal (design:
  *Body source*). **BREAKING** (planned cutover): `0.1.0a7` and `v0.1.0a7` are no longer accepted.
- **No cutover guard; existing rows keep their stamp.** This becomes a new requirement and retires
  the a3/a7 retiring-version guard pattern (design: *No cutover guard*).
- **The rollback** restores the `20260917140000` a7 body, bloom#875 fallback included (design:
  *Rollback*).
- **Re-pin the vendored contract** from `v0.1.0a7` to `v0.1.0a9` (`contracts/`), with the TS
  regenerated (expected unchanged) and a8/a9 README notes.
- **The re-pin procedure** (`contract-pinning`): README step 5 changes from "if you add a guard" to
  "don't add a retiring-version guard; re-pin the RPC literal in the same change". A new CI check ties
  the RPC's live literal to `contracts/pin.json`.
- **Archive `repin-cyl-contract-a7` first, in this PR.** Its delta MODIFIES the same requirement, so
  a later a7 archive would revert the spec. This supersedes the open archive PR #779, which gets
  closed (design: *Archive a7 here*).
- **Tests:** `PINNED_VERSION` moves to a9. New tests cover a7 rejection, the migration's
  idempotency, the rollback, no-guard regression, a body-diff check, and the pin/RPC tie. Three tests
  that re-apply a7 bodies get an explicit `0.1.0a7`.

## Impact

- **Affected specs:**
  - `cyl-trait-writeback`: MODIFIED *Write-back validates the contract version*; ADDED *A contract
    re-pin leaves existing rows untouched*.
  - `contract-pinning`: MODIFIED *Pinned contract schema is vendored at an explicit version*; ADDED
    *The write-back RPC's pinned version matches the vendored pin*.
- **Affected code:**
  - `supabase/migrations/20260925120000_cyl_writeback_contract_a9.sql` and its rollback.
  - `contracts/{schema/result_envelope.schema.json,pin.json,README.md}`.
  - `tests/integration/{test_cyl_writeback_rpc.py,test_cyl_read_path.py,test_contract_migration_match.py}`
    and a new `tests/unit/test_cyl_writeback_a9_migration_files.py`.
  - `bloomcli/tests/test_cyl_ingest.py`, which only needs the mocked error string changed.
  - The `repin-cyl-contract-a7` archive move and the `openspec/specs/` updates.
- **Operational:**
  - This opens a **rejection window**: from the moment it applies to the staging Supabase until the
    traits template bump lands in `talmolab/sleap-roots-pipeline`, a7 write-backs are rejected.
  - The rejections are loud: Workflows go red and `bloomctl` reports a `contract_version` mismatch.
  - They are also recoverable: the first run after the bump recomputes the affected scans. Nothing is
    replayed (design: *Rejection window*).
  - Staging deploys are currently stuck on a pending environment approval (run 35657797607), so the
    migration applies only after that approval is given.
- **Out of scope:**
  - The pipeline template bump, which follows the verified apply.
  - `bloomctl` source, pins and image. Its image builds `--frozen` against a7 and needs nothing.
  - `services/workflows` (uses `hashing.py` only) and `bloommcp`.
  - A compatibility set (bloom#895 option (b), declined).
  - The stale 1-arg signature in `database.types.ts`, which predates this change (since
    `20260912110000`).
