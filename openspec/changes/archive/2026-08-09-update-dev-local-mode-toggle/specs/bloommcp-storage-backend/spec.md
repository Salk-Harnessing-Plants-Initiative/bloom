## ADDED Requirements

### Requirement: Backend Selection Boot Visibility

The server SHALL print which object-storage backend is active (`local` or `supabase`) at
startup, alongside the existing authentication-mode message. This is an observability addition
only — it SHALL NOT alter backend selection, fail-fast validation, or resolution/precedence
behavior defined by `Backend Selection via BLOOM_STORAGE_BACKEND`.

#### Scenario: Active backend is printed at boot

- **WHEN** `main()` starts the server, in either backend mode
- **THEN** a log line states which backend is active (`local` or `supabase`) before the server
  begins accepting requests

#### Scenario: No change to selection or fail-fast behavior

- **WHEN** `BLOOM_STORAGE_BACKEND` is unset, `supabase`, `local`, or an unrecognized value
- **THEN** the boot-visibility print does not change which backend is selected, whether startup
  validation fails fast, or any behavior described by `Backend Selection via
  BLOOM_STORAGE_BACKEND` — it only adds a message describing the outcome already determined by
  that requirement
