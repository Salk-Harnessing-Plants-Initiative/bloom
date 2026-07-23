## ADDED Requirements

### Requirement: Local Input Root Resolution

When `BLOOM_STORAGE_BACKEND=local`, the local input root used by `LocalReader` (via
`resolve_experiment_local_root()`) SHALL resolve in this order: `BLOOM_EXPERIMENT_LOCAL_ROOT`
when explicitly set; otherwise `<BLOOM_LOCAL_ROOT>/input` when the single `BLOOM_LOCAL_ROOT`
variable is set; otherwise `BLOOM_TRAITS_DIR` (the pre-existing supported default).
`BLOOM_LOCAL_ROOT` SHALL take effect only when `BLOOM_STORAGE_BACKEND=local`; for the default
Supabase backend it SHALL be inert, and `SupabaseReader` SHALL continue to resolve its
raw-input path from `BLOOM_TRAITS_DIR` exactly as before. The resolved root SHALL be validated
at server boot (`validate_experiment_local_root`, called by `server.main()` in fully-local
mode): an explicitly-set `BLOOM_EXPERIMENT_LOCAL_ROOT`, or the `BLOOM_TRAITS_DIR` fallback,
SHALL be required to already exist as a readable directory (fail-fast, unchanged); the
`<BLOOM_LOCAL_ROOT>/input` default SHALL instead be created (`mkdir(parents=True,
exist_ok=True)`) if missing, after confirming the top-level `BLOOM_LOCAL_ROOT` itself exists
and is a writable directory.

This requirement documents `resolve_experiment_local_root` / `validate_experiment_local_root`
(`bloommcp/src/bloom_mcp/experiment_utils.py`) as they exist on `staging` today — shipped by
the not-yet-archived `add-bloommcp-local-experiment-reader` change (#390) — plus this change's
`BLOOM_LOCAL_ROOT` tier. It is filed as ADDED rather than MODIFIED because this capability's
archived spec predates #390 entirely (see the proposal's design.md, Migration Plan, for the
archive-ordering note).

#### Scenario: Explicit BLOOM_EXPERIMENT_LOCAL_ROOT always wins

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_EXPERIMENT_LOCAL_ROOT` is set, and
  `BLOOM_LOCAL_ROOT` is also set
- **THEN** `LocalReader` reads from `BLOOM_EXPERIMENT_LOCAL_ROOT`, not `<BLOOM_LOCAL_ROOT>/input`

#### Scenario: BLOOM_LOCAL_ROOT supplies the default input root

- **WHEN** `BLOOM_STORAGE_BACKEND=local`, `BLOOM_EXPERIMENT_LOCAL_ROOT` is unset, and
  `BLOOM_LOCAL_ROOT` is set to an existing writable directory
- **THEN** `LocalReader` reads from `<BLOOM_LOCAL_ROOT>/input`, creating that subfolder if it
  does not already exist

#### Scenario: Neither variable set falls back to BLOOM_TRAITS_DIR

- **WHEN** `BLOOM_STORAGE_BACKEND=local` and both `BLOOM_EXPERIMENT_LOCAL_ROOT` and
  `BLOOM_LOCAL_ROOT` are unset
- **THEN** `LocalReader` reads from `BLOOM_TRAITS_DIR`, exactly as before this change

#### Scenario: BLOOM_LOCAL_ROOT is inert on the default backend

- **WHEN** `BLOOM_STORAGE_BACKEND` is unset or `supabase`, and `BLOOM_LOCAL_ROOT` is set
- **THEN** `SupabaseReader`'s raw-input path still resolves from `BLOOM_TRAITS_DIR`, unaffected
  by `BLOOM_LOCAL_ROOT`
