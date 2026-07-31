## Context

#389 introduced the `BLOOM_STORAGE_BACKEND` seam (`supabase` / `local`) as a
backend-agnostic object-storage abstraction. `next_version_id` (in
`manifest/versioning.py`) allocates version ids by reading only the *active*
backend's manifest. Since each backend physically owns a disjoint manifest
location — Supabase Storage (bucket `bloommcp-data`) vs. a local-filesystem
root (`BLOOM_STORAGE_LOCAL_ROOT` / `BLOOM_OUTPUT_DIR`) — a single
`manifest.json` can never itself contain entries written by both backends.
The hazard is purely at the catalog level: an experiment can end up with
*two* independent, disjoint manifests (one per backend), each unaware of the
other, both willing to allocate colliding `v<N>` ids for different bytes.

#395 (item 2) asks for this to be made observable via (a) a within-root
sentinel recording which backend produced a manifest, and/or (b) a warning
when a fresh catalog (`v1`) is allocated into a root that may already hold
history from another backend.

## Goals / Non-Goals

- Goal: make backend mixing for one experiment observable — a log line at the
  moment it may be happening, plus a durable clue in the manifest itself
  (which backend last wrote this catalog) — so a human auditing either store
  (or comparing the two) has more to go on than the undocumented caveat in
  `storage-backends.md` alone. This is deliberately partial: the manifest's
  `storage_backend` field records which backend most recently wrote the file,
  not which specific version was a suspected fresh-catalog split — that
  signal exists only transiently, in the log line at commit time (see Risks).
- Goal: keep the change additive and low-complexity — a version-bumped but
  fully backward-compatible schema change, no new cross-process
  coordination.
- Non-goal: automatically detect or prevent mixing. Detection would require
  the active backend to contact the *inactive* one (e.g. `local` reaching out
  to Supabase to check for prior history), which defeats `local`'s offline
  design and re-introduces the exact Supabase dependency #395's item 1 (now
  shipped) removed. This proposal only makes the local signal available —
  it does not close the gap fully.
- Non-goal: #395 item 3 (Windows/NTFS atomicity hardening for the local
  backend's `os.replace`). Confirmed still optional/low-priority per the
  maintainer's 2026-07-30 comment on #395, with no change in circumstances
  since the original issue — the local backend remains a dev-only convenience
  and production stays on Supabase. Left for a future change if that changes.

## Decisions

- **Decision: stamp `storage_backend` on `Manifest`, not per-`VersionEntry`.**
  Because one manifest file is always owned by exactly one backend at a time
  (see Context), a top-level field on `Manifest` fully captures "which
  backend last wrote this catalog." A per-version field would imply a single
  manifest could hold mixed-backend entries, which cannot happen — it would
  overstate what the field can detect.
  - Alternative considered: per-`VersionEntry` field (mirroring
    `input_validation`'s precedent). Rejected as over-fitted to a case that
    can't occur; adds a field to every version for no additional signal.

- **Decision: stamp inside `write_manifest`, not at each call site.** A
  single line in `manifest.py` (`manifest.storage_backend =
  selected_backend_name()` before serialization) guarantees every writer —
  `SupabaseResultStore.commit` today, any future result-store adapter — stamps
  correctly with no per-call-site duty to remember.

- **Decision: log in `SupabaseResultStore.commit`'s existing `fresh is None`
  branch**, not in `create_run`. `create_run`'s version-id allocation is
  provisional (re-derived in `commit` per the existing collision-retry
  comment); `commit`'s `fresh = adir.read_manifest()` check immediately before
  building a brand-new `Manifest` is the authoritative, single place a fresh
  catalog is actually decided, for both `supabase` and `local` (this class
  backs both — `BLOOM_STORAGE_BACKEND` only changes what
  `bloom_mcp.supabase_client`'s helpers resolve to underneath it).
  - `FakeResultStore` (the in-memory test double) is out of scope: it has no
    real `BLOOM_STORAGE_BACKEND` concept and nothing to stamp a name from.

- **Decision: `logger.info`, not `logger.warning` and not an exception.**
  Mixing is a provenance hazard, not a correctness failure of the run being
  committed — the commit must still succeed, so raising is off the table.
  `logger.warning` was the initial instinct (and mirrors the existing
  precedent at `storage_backend._resolve_local_root`'s `BLOOM_OUTPUT_DIR`
  fallback), but this call site fires on *every* first commit for *any*
  brand-new experiment/tool_class — the overwhelmingly common case is a
  genuinely new experiment, not a mixing event. Logging that at WARNING in
  production, where log/alerting pipelines commonly page on WARNING-and-above,
  would mean every researcher's first analysis of a new experiment pages
  on-call. `logger.info` keeps the line fully greppable (for anyone
  deliberately auditing a specific experiment's history) without generating
  alert-fatigue noise for a near-always-benign event.

- **Decision: bump `CURRENT_SCHEMA_VERSION` 4 → 5 — not just precedent, an
  actual correctness improvement.** This codebase's own precedent is
  inconsistent on whether an additive, optional field needs a version bump:
  `VersionEntry.input_validation` (#403) was added within v3 with no bump,
  but the very next change, `VersionEntry.source_id`/`source_name`, bumped
  v3 → 4 despite being equally additive/optional. Tracing `read_manifest`
  (`manifest.py`) shows *why* the bump is the better choice, not just the
  more recent one: `validate_schema(raw)` runs **before**
  `Manifest.model_validate(raw)`, and it raises the clean
  `ManifestSchemaError` whenever `manifest_schema_version > KNOWN_SCHEMA_VERSION`
  (`KNOWN_SCHEMA_VERSION` is this process's own `CURRENT_SCHEMA_VERSION`).
  Bumping to 5 means an old process (still at `KNOWN_SCHEMA_VERSION = 4`)
  that reads a manifest a newer deploy already stamped with
  `storage_backend` hits that clean, expected error *before* ever reaching
  the strict Pydantic parse — closing the "uncaught `ValidationError` on a
  rolling deploy" hazard, rather than merely inheriting it. (This also means
  `input_validation`'s original no-bump choice left that same hazard latent
  for pre-#403 code reading a post-#403 manifest — a pre-existing gap this
  proposal doesn't need to fix, but the version-gate mechanism is why
  bumping here is worth doing properly.)

## Risks / Trade-offs

- The log line fires on *every* first commit for a new experiment/tool_class
  under a given backend — including the common, non-mixing case of a
  genuinely brand-new experiment. This is an accepted false-positive: there is
  no local way to distinguish "genuinely new" from "new to this backend, but
  has history elsewhere," and the issue explicitly asks for the fresh-catalog
  moment to be observable rather than silent. Logging at `info` (see
  Decisions) keeps this from becoming an alerting/paging problem; the message
  itself is worded as a reminder to check, not an assertion that mixing
  occurred.
- The sentinel and log line only ever reflect the *active* backend's own
  view; they cannot themselves join the two catalogs. Closing that gap fully
  is out of scope (see Non-Goals).
- **Both signals require server log or storage access to see.** A bench
  scientist driving bloommcp through Claude Desktop/Code has no path to
  either the log line or the `manifest.json` field — only someone who can
  read server logs or the storage backend directly would notice. Tracked as
  a follow-up in #574 (surface `storage_backend` in tool-facing provenance
  output) rather than expanding this proposal's scope.
- **Downstream "latest" resolution stays silently split — not just the
  operator-facing signal.** `ResultStore.get_run(run_ref="latest")` resolves
  from `manifest.latest`, scoped entirely to whichever backend answers the
  read. Consumer tools that gate on the certified-latest run (`qc_clean`'s
  `require_clean` contract, `pca_analysis`) will silently accept whichever
  backend's "latest" they're pointed at after a mixing event, with a
  different `output_sha256` than the other backend's "latest" — and this
  happens on every read after the one-time, transient fresh-catalog log line,
  not just the moment of the split. This proposal does not close that gap
  (see Non-Goals); tracked as a follow-up in #573 rather than left only as
  prose here, since this doc is archived on merge.
- **Known residual gap: flip A → B → A is silent.** The log line only fires
  when `commit` finds no existing manifest at all (`fresh is None`). If an
  experiment is committed under `supabase`, flipped to `local` (logs, since
  `local`'s catalog starts fresh), then flipped back to `supabase`,
  `supabase`'s original manifest still exists — so the return trip logs
  nothing, even though a `local`-backed run happened in between and
  `supabase`'s catalog is now silently stale relative to it. This proposal
  does not close that gap (it would require the same infeasible cross-backend
  contact ruled out in Non-Goals); it is called out here and in
  `storage-backends.md` as an explicit limitation, not silently left
  undocumented.
- `FakeResultStore` (the in-memory test double) never constructs a real
  `Manifest` and has no `BLOOM_STORAGE_BACKEND` concept, so tests and dev
  flows that exercise only the fake will never see the log line or the
  sentinel — consistent with its stated non-goal, but worth knowing if a
  future test tries to assert on it against the fake.

## Migration Plan

Purely additive: existing manifests (v2/v3/v4-without-`storage_backend`)
continue to validate unchanged; `storage_backend` is populated, and
`manifest_schema_version` written as `5`, starting from the next write after
this change deploys. No backfill, no data migration. Because
`CURRENT_SCHEMA_VERSION` bumps to 5 (see Decisions), an old process still at
`KNOWN_SCHEMA_VERSION = 4` that reads a manifest a newer deploy already wrote
gets the clean, existing `ManifestSchemaError` (`validate_schema` runs before
the strict Pydantic parse) rather than an uncaught `ValidationError` — a
rolling-deploy read is a normal, handled case, not a new hazard.

## Open Questions

None — scope was narrowed with the maintainer's 2026-07-29 triage comment on
#395 (item 1 done, item 3 deferred as optional/low-priority), leaving item 2
as this proposal's sole implementation target.
