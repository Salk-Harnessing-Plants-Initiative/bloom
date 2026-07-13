## ADDED Requirements

### Requirement: bloom_user holds UPDATE only on experiment_progress_logs

The `bloom_user` role SHALL NOT hold table-level `UPDATE` privilege on any
`public`-schema table, present or future, except `public.experiment_progress_logs`.
`SELECT` and `INSERT` access SHALL be unchanged by this requirement. Because the
privilege is removed at the table-grant layer (not merely via RLS policy), an
`UPDATE` attempt on a non-exempt table is rejected at the privilege check
(`SQLSTATE 42501`, before RLS is evaluated).

#### Scenario: Update on a general public table is denied at the privilege layer

- **WHEN** a session running as `bloom_user` runs `UPDATE public.species` (or any
  `public` table other than `experiment_progress_logs`)
- **THEN** the statement raises `SQLSTATE 42501` (insufficient privilege) and,
  verified in a separate transaction, the target row is unchanged

#### Scenario: The one previously-permitted UPDATE (accessions) is now denied

- **WHEN** a session running as `bloom_user` runs `UPDATE public.accessions` (the
  only one of the five dropped policies that was `USING (true)`, so the only real
  capability removed — `bloom_user` could update `accessions` before this change)
- **THEN** the statement raises `SQLSTATE 42501` and the row is unchanged

#### Scenario: Update on experiment_progress_logs is allowed

- **WHEN** a session running as `bloom_user` runs `UPDATE
public.experiment_progress_logs` permitted by its `USING (true)` policy
- **THEN** the update succeeds, and `has_table_privilege('bloom_user',
'public.experiment_progress_logs', 'UPDATE')` is true

#### Scenario: Read and insert access is preserved (the change is no-UPDATE, not no-write)

- **WHEN** the grants of `bloom_user` on `public.species` are inspected, and a
  `bloom_user` session inserts a row
- **THEN** `has_table_privilege` reports `SELECT` true, `INSERT` true, `UPDATE`
  false, and `DELETE` false; and the `INSERT` succeeds — confirming the revoke was
  UPDATE-only and INSERT is intentionally retained (#341 scopes the cleanup to
  UPDATE)

#### Scenario: Future tables do not silently re-grant UPDATE

- **WHEN** a new `public` table is created (by the migration-applying role) after
  this change
- **THEN** `bloom_user` holds `SELECT` and `INSERT` but **not** `UPDATE` on it,
  because the default privileges for that role were revoked of `UPDATE`

#### Scenario: bloom_writer, bloom_admin, and bloom_agent are unaffected

- **WHEN** the grants of `bloom_writer`, `bloom_admin`, and `bloom_agent` on
  `public.species` are inspected after this change
- **THEN** `bloom_writer` retains `UPDATE` (writes still flow through it — the
  central premise that nothing real regresses), `bloom_admin` retains `UPDATE`
  (full CRUD), and `bloom_agent` remains read-only (no `UPDATE`) — none of their
  privileges changed

### Requirement: Inert bloom_user UPDATE policies are removed, not merely dormant

The five `bloom_user` `user_update_*` RLS policies on `public` tables SHALL be
dropped — four gate on `created_by = auth.uid()` and are inert only because
`bloom_user` lacks `auth` USAGE (#341); the fifth, `user_update_accessions`, is
`USING (true)` and was the only live one — so the role's no-UPDATE design is
enforced by its actual privilege set rather than by a withheld schema grant. The
`user_update_experiment_progress_logs` policy SHALL be retained.

#### Scenario: Dropped policies are absent, the retained one present

- **WHEN** `pg_policies` is inspected after this change
- **THEN** `user_update_accessions`, `user_update_chat_threads`,
  `user_update_cyl_experiments`, `user_update_gene_candidates`, and
  `user_update_species` are absent
- **AND** `user_update_experiment_progress_logs` is present

#### Scenario: A later auth USAGE grant cannot re-enable writes

- **WHEN** the post-change schema is inspected for the structural conditions a
  hypothetical future `GRANT USAGE ON SCHEMA auth TO bloom_user` would need to
  re-enable writes
- **THEN** none exist: `bloom_user` holds no blanket `public` `UPDATE` grant **and**
  no `created_by = auth.uid()` UPDATE policy remains — so write re-activation is
  impossible without a new, deliberate policy + grant (verified from schema state;
  the test does NOT grant `auth` USAGE)

#### Scenario: Migration is idempotent

- **WHEN** the migration is applied a second time (or to a fresh database where a
  policy never existed)
- **THEN** it completes without error (`DROP POLICY IF EXISTS`, idempotent
  `REVOKE`/`GRANT`) and leaves the same end state
