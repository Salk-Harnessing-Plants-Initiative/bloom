## ADDED Requirements

### Requirement: Intermediates blob bytes storage bucket and access control

A `cyl-intermediates` Supabase Storage bucket SHALL exist to hold the `.slp`
bytes referenced by `cyl_scan_intermediates.s3_location`. Unlike the
`cyl_scan_intermediates` TABLE (whose direct writes are restricted to
`bloom_admin` — see "Intermediates table role-based access control": all table
writes go through the write-back RPC's `SECURITY DEFINER`), there is no
RPC-mediated path for Supabase Storage byte writes, so this bucket's
`storage.objects` RLS SHALL grant `bloom_writer` and `bloom_workflows` direct
`SELECT`, `INSERT`, and `UPDATE` (scoped to `bucket_id = 'cyl-intermediates'`),
mirroring the existing `bloom_workflows`/`videos`-bucket precedent. `bloom_admin`
SHALL have `FOR ALL`; `bloom_agent` and `bloom_user` SHALL have `SELECT`-only.
No role SHALL have `DELETE`.

#### Scenario: bloom_writer can upload and read back

- **WHEN** a session assumes `bloom_writer` and uploads then reads back an
  object in the `cyl-intermediates` bucket
- **THEN** both operations succeed

#### Scenario: bloom_workflows can upload and read back

- **WHEN** a session assumes `bloom_workflows` and uploads then reads back an
  object in the `cyl-intermediates` bucket
- **THEN** both operations succeed (mirrors its existing `videos`-bucket access)

#### Scenario: Read-only roles cannot write

- **WHEN** a session assumes `bloom_agent` or `bloom_user` and attempts to
  `INSERT` or `UPDATE` an object in the `cyl-intermediates` bucket
- **THEN** the write is rejected

#### Scenario: No role can delete

- **WHEN** any non-admin role attempts to `DELETE` an object in the
  `cyl-intermediates` bucket
- **THEN** the delete is rejected
