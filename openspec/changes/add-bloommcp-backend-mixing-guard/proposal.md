## Why

#389 (opt-in local storage backend) documented, as a deliberate non-goal, that
flipping `BLOOM_STORAGE_BACKEND` mid-experiment silently splits an
experiment's version history: `next_version_id` only ever reads the *active*
backend's manifest, so switching backends starts a fresh catalog and
re-allocates a colliding `v1` with different bytes / `output_sha256`. #395
tracks this as a HIGH-severity follow-up. Issue #395's item 1 (backend-aware
boot) has since shipped via `add-bloommcp-local-experiment-reader`; item 2
(this proposal) is confirmed still open — grepping `bloom_mcp` turns up no
sentinel or warning, only the caveat in `bloommcp/docs/storage-backends.md`.

A cross-backend check isn't feasible without contacting the inactive backend
(the `local` backend is meant to run fully offline, and `supabase` has no
reason to probe a local filesystem it doesn't own), so this can't be
*prevented* from purely local information. It can be made *observable*: stamp
which backend produced a manifest, and log loudly at the exact moment a fresh
catalog starts — the only local signal that a split may be occurring.

## What Changes

- Add an optional `storage_backend` field to `Manifest`, bumping
  `CURRENT_SCHEMA_VERSION` 4 → 5 (additive — following the more recent
  `VersionEntry.source_id`/`source_name` precedent of bumping for additive
  fields, rather than the older no-bump `input_validation` precedent; see
  design.md), stamped with `storage_backend.selected_backend_name()` every
  time `write_manifest` runs.
- In `SupabaseResultStore.commit`, when the manifest read at commit time is
  `None` (a fresh catalog is about to be created, i.e. `v1` is being
  allocated), emit a `logger.info` naming the experiment, tool class, and
  active backend, and noting that any history under a different backend is
  now invisible from this catalog. `info`, not `warning`, because this fires
  on every brand-new experiment's first commit — the common, non-mixing case
  — and `warning`-level would page on-call for routine new-experiment
  onboarding in any environment with WARNING-and-above alerting.
- Update `bloommcp/docs/storage-backends.md`'s "Do not mix backends" section
  to describe the sentinel field and the fresh-catalog info log line, while
  keeping the existing caution that mixing is still not automatically
  prevented or cross-checked.
- **Non-goals** (explicitly out of scope, carried over from #395):
  - Automatic cross-backend detection — would require contacting the inactive
    backend, defeating the `local` backend's offline design.
  - Item 3 from #395 (POSIX-only atomicity hardening / Windows guarantees) —
    per the maintainer's 2026-07-30 triage comment on #395 this remains
    optional/low-priority with no new information; left for a separate
    change if the local backend ever becomes more than a dev convenience on
    Windows.

## Impact

- Affected specs: `bloommcp-storage-backend` (MODIFIED: Backend Parity and
  Provenance Integrity)
- Affected code:
  - `bloommcp/src/bloom_mcp/manifest/schema.py` (`Manifest.storage_backend`)
  - `bloommcp/src/bloom_mcp/manifest/manifest.py` (`write_manifest` stamps the
    active backend)
  - `bloommcp/src/bloom_mcp/result_store/supabase_store.py` (`commit`'s
    fresh-catalog branch logs the info-level message)
  - `bloommcp/docs/storage-backends.md`
- Closes GitHub issue #395 (items 2; item 3 explicitly deferred, see above).
