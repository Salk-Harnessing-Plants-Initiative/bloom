# Design — foreign-catalog read guard (#573)

## Context

#395 established that mixing storage backends for one experiment splits its
version history into two physically disjoint catalogs, and that full
cross-backend detection is infeasible without contacting the inactive backend.
PR #572 (archived change `2026-08-09-add-bloommcp-backend-mixing-guard`) added
the locally-available signals: a `storage_backend` sentinel stamped on every
`manifest.json` write (from `active_backend_name()`, i.e. derived from the
resolved backend object) and a one-time fresh-catalog info log. Its design.md
names the residual risk verbatim: consumer "latest" resolution stays silently
split on every read after the one-time log — tracked as #573 (this change) and
#574 (tool-facing surfacing, out of scope here).

**Terminology.** A *foreign catalog* is a manifest whose `storage_backend`
sentinel names a backend other than the one currently serving the read.

## Goals / Non-Goals

- Goals:
  - Every manifest read fails loudly, by default, when it resolves a foreign
    catalog — at `get_run`/`list_runs`/`create_run`/`commit` and at the
    `require_clean` cleaned-tier resolution, not just at one call site.
  - The failure is structurally distinguishable (its own exception type in each
    layer's taxonomy), names both backends, carries a remedy, and leaks no host
    path.
  - A deliberate foreign read (offline inspection of a copied bucket) has a
    sanctioned, explicit, logged escape hatch.
- Non-Goals:
  - Detecting the disjoint-split staleness case (A → B → A flips): each
    backend's own catalog is self-consistent, so the sentinel it serves always
    matches itself. Undetectable from local information — unchanged non-goal
    from #395/#572. The docs updated here say so explicitly.
  - Surfacing `storage_backend` in tool-facing provenance output (#574).
  - Any manifest schema change. The guard reads the existing v5 field.
  - Migration tooling for legitimately moving a catalog between backends.

## When can the guard actually fire? (honest trigger analysis)

The sentinel is stamped at write time from the same `active_backend_name()` the
guard compares against, and each backend writes only into its own store — so in
the pure flip scenario the two catalogs are each self-consistent and the guard
is silent. The guard fires exactly when catalog bytes and serving backend
disagree:

- a Supabase bucket (or prefix) downloaded/synced to disk and served via
  `BLOOM_STORAGE_BACKEND=local` — the plausible "inspect prod results offline"
  flow;
- a local catalog uploaded into Supabase Storage as a hand-rolled "migration"
  (the docs' "a backend is not a migration" trap, now enforced instead of only
  documented);
- a restored backup or shared/overlapping root where both backends resolve to
  the same physical objects;
- manual tampering with `manifest.json`'s sentinel (fails on next read instead
  of silently steering resolution).

This is narrower than #573's title scenario but is precisely the guard the
issue proposes ("it only needs to compare against itself"), and it converts the
sentinel from a passive forensic field into an active integrity check at every
read. The A → B → A staleness case remains covered only by the fresh-catalog
log + sentinel forensics (#572) and by #574's surfacing.

## Decisions

- **Decision: guard at the `read_manifest` chokepoint, not per caller.**
  `bloom_mcp.manifest.read_manifest` is the single function every manifest read
  passes through (`AnalysisDir.get_version`/`list_versions`/`read_manifest` →
  `read_manifest`); guarding there covers `get_run`, `list_runs`, `create_run`,
  `commit`'s allocation/re-check reads, `get_download_links`,
  `_resolve_one_class`, and the staleness helpers structurally — no call site
  can forget it. Symmetric with the stamp, which lives in the sibling
  `write_manifest`.
  - Alternatives considered: (a) guard only `ResultStore.get_run` — misses the
    `require_clean` path entirely, which reads `AnalysisDir` directly, and
    misses commit-time reads (a commit would silently *re-stamp and take over*
    a foreign catalog, since `write_manifest` overwrites the sentinel);
    (b) guard in `AnalysisDir.get_version` — misses `list_versions` and direct
    `read_manifest` callers. Both rejected as leaving silent paths.
- **Decision: comparison uses `active_backend_name()`** — the same
  isinstance-derived function the stamp uses (PR #572 review moved it off
  `selected_backend_name()`), so stamp and check cannot disagree about naming.
- **Decision: an absent sentinel passes.** Manifests written before schema v5
  carry no `storage_backend`; failing them would brick every pre-#572 catalog.
  Documented limitation: the guard protects only catalogs written after #572.
  (First post-upgrade commit re-stamps the manifest, closing the window.)
- **Decision: fail-closed by default; explicit env-var opt-out.**
  `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` downgrades the raise to a
  warning-level log (per read — it is opt-in and abnormal, so the
  #572 "info, not warning" paging argument does not apply) and returns the
  manifest. Accepted values: unset, `0`, `1`; anything else fails
  `validate_storage_backend()` at boot, mirroring `BLOOM_STORAGE_BACKEND`'s
  fail-fast discipline. Read lazily at guard time, never at import (Tier-0
  side-effect-free import contract). Without the hatch, a deliberately copied
  catalog is unreadable with no sanctioned remedy except hand-editing every
  `manifest.json` in a bucket.
  - Alternative considered: no escape hatch (rely on hand-editing manifests) —
    rejected; it punishes the one legitimate flow (offline inspection) and
    invites operators to blunt the sentinel itself.
- **Decision: two-layer error taxonomy, mirroring the existing pattern.**
  Manifest layer raises `ManifestBackendMismatchError` (sibling of
  `ManifestSchemaError` in `bloom_mcp/manifest/manifest.py`, exported from
  `bloom_mcp.manifest`); `_guarded_manifest_read` maps it to
  `CatalogBackendMismatchError(ManifestReadError)` in `result_store/ports.py` —
  exactly how `ManifestSchemaError` maps to
  `ManifestIncompatibleError(ManifestReadError)` today, so every existing
  `except ManifestReadError` / `except ResultStoreError` handler keeps working
  and a caller that needs to distinguish can `isinstance()`-check.
- **Decision: hard error in cleaned-tier resolution, handled explicitly.**
  `_resolve_one_class` already catches `ManifestSchemaError` explicitly and has
  a generic `except Exception` fallback that would technically catch the new
  error too (as a hard error — its soft-miss/hard-error contract already
  prevents fall-through). We still add an explicit
  `except ManifestBackendMismatchError` branch so the returned error string is
  the precise both-backends message, not a generic "could not read manifest"
  wrapper, and so the contract is pinned by test rather than by accident of the
  fallback branch. The error must never surface as
  `CleanedVersionRequiredError`: the "run `qc_clean` first" remedy would send
  an agent straight into committing new runs against the foreign catalog
  (which the commit-time guard would then refuse — safe, but a misleading
  two-step failure).
- **Decision: message content.** Both backend names, the logical
  `<tool_class>/<stem>` catalog identity, and the remedy (stop mixing backends;
  for a deliberate offline copy set `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1`).
  Logical storage keys only — never absolute host paths (matching the local
  backend's existing no-path-leak rule).

## Risks / Trade-offs

- **A single foreign catalog could brick multi-experiment listings.**
  `list_existing_analyses` iterates many experiments' manifests; if one read's
  raise aborts the whole listing, one bad catalog hides every healthy one.
  → Mitigation: implementation task verifies the listing path's per-experiment
  error isolation and adds a regression test (report the mismatch for that
  experiment's entry; keep listing the rest). If isolation does not exist
  today, it is added for this error type as part of this change.
- **Fakes are blind to the guard.** `FakeResultStore` and `FakeReader` never
  construct a `Manifest` and have no backend concept (called out in #572's
  design), so fake-based suites can never exercise the guard. → Tests are
  written against the real manifest path: the local backend on a temp root
  (write a manifest, flip/patch the sentinel, read) and the
  `_FakeSbStorageClient` harness in `tests/test_storage_backend.py` — the one
  fixture that runs real backend dispatch. The parity-suite exemption is
  recorded in the spec deltas so it cannot be mistaken for missing coverage.
- **Pre-v5 manifests pass silently.** Accepted (see Decisions); the alternative
  bricks all history written before #572. Window closes on first re-commit.
- **The guard can be mistaken for full mixing detection.** → The docs section
  and both spec deltas state what it cannot catch (A → B → A) in the same
  breath as what it can.
- **New optional env var vs. env-parity checks.** The deploy-env-parity gate
  compares committed env-defaults files. → Implementation task runs the parity
  checker; the variable is optional-with-default so it should not need
  defaults-file entries, but this is verified, not assumed.

## Migration Plan

No schema change, no data migration. Deploys pick up the guard on restart;
supported (unmixed) deployments see no behavior change. If a deployment is
unknowingly serving a foreign catalog today, its reads start failing loudly
with the remedy in the message — that is the intended surfacing, and
`BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` restores service (with warnings) while
the operator untangles the catalogs. Rollback: revert the commit; the sentinel
field itself is untouched.

## Open Questions

None blocking. The `list_existing_analyses` isolation behavior is a verify-task
rather than an open design question (both outcomes have a defined resolution).
