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
  - A deliberate foreign **read** (offline inspection of a copied bucket) has a
    sanctioned, explicit, logged escape hatch that is actually reachable in the
    standard dev flow (compose passthrough + `.env.dev.example`).
  - A foreign catalog is never **written to** — not extended, not re-stamped —
    under any configuration.
- Non-Goals:
  - Detecting the disjoint-split staleness case (A → B → A flips): each
    backend's own catalog is self-consistent, so the sentinel it serves always
    matches itself. Undetectable from local information — unchanged non-goal
    from #395/#572. The docs updated here say so explicitly.
  - Surfacing `storage_backend` in tool-facing provenance output (#574).
  - Any manifest schema change. The guard reads the existing v5 field.
  - Migration tooling for legitimately moving a catalog between backends.
  - Prod/staging compose passthrough for the escape hatch (see Decisions).

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

The same physics constrains **tests**: flipping the backend between write and
read can never produce a mismatch, because the other backend sees no manifest
at all. A foreign manifest is manufactured only by hand-patching the stored
sentinel (edit the JSON under the local root, or mutate/seed the in-memory
Supabase store's bytes) — every guard test uses that recipe, never
flip-and-read.

## Decisions

- **Decision: guard at the `read_manifest` chokepoint, not per caller.**
  `bloom_mcp.manifest.read_manifest` is the single function every manifest read
  passes through (`AnalysisDir.get_version`/`list_versions`/`read_manifest` →
  `read_manifest`); guarding there covers `get_run`, `list_runs`, `create_run`,
  `commit`'s allocation/re-check reads, `get_download_links`,
  `_resolve_one_class`, and the staleness helpers structurally — no call site
  can forget it. Symmetric with the stamp, which lives in the sibling
  `write_manifest`. The comparison runs after `validate_schema` +
  `Manifest.model_validate`, so `ManifestSchemaError` keeps precedence and the
  guard reads the validated model, not the raw dict.
  - Alternatives considered: (a) guard only `ResultStore.get_run` — misses the
    `require_clean` path entirely, which reads `AnalysisDir` directly;
    (b) guard in `AnalysisDir.get_version` — misses `list_versions` and direct
    `read_manifest` callers. Both rejected as leaving silent paths.
- **Decision: comparison uses `active_backend_name()`** — the same
  isinstance-derived function the stamp uses (PR #572 review moved it off
  `selected_backend_name()`), so stamp and check cannot disagree about naming.
  An absent, `None`, or empty sentinel passes (pre-v5 manifests; failing them
  would brick all pre-#572 history — window closes on the catalog's next
  re-stamping commit).
- **Decision: the escape hatch sanctions reads only; the write path checks the
  sentinel unconditionally.** `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1`
  downgrades the manifest-layer raise to a warning-level log *per guarded read*
  (it is opt-in and abnormal, so #572's "info, not warning" paging argument
  does not apply) and returns the manifest. But `create_run` and `commit`
  perform their own comparison on the manifest object they just read —
  independent of the hatch — and raise `CatalogBackendMismatchError` before any
  staging, upload, or manifest write. Without this, hatch=1 would let a commit
  proceed to `write_manifest`, which re-stamps the sentinel with the active
  backend's name — a silent *take-over* of the foreign catalog. Reads-only is
  also exactly the hatch's stated use case (inspection).
- **Decision: env-var semantics.** Accepted values: unset, empty/whitespace
  (≡ unset — mandatory because the dev-compose `${VAR:-}` passthrough pattern
  delivers `""` inside the container, exactly as `_selected_backend_name`
  already treats `BLOOM_STORAGE_BACKEND`), `0`, and `1`. Anything else fails
  `validate_storage_backend()` at boot, mirroring `BLOOM_STORAGE_BACKEND`'s
  fail-fast discipline. At guard time only the exact value `1` enables the
  hatch — an invalid value that escaped boot validation keeps the guard closed.
  Read lazily on every call, never memoized (unlike `_active`, which memoizes
  the backend object in the same module — a copy-pasted memo would break the
  per-read warning contract and test isolation), never at import (Tier-0
  side-effect-free import contract; CI's clean-env wheel-import gate enforces
  this).
- **Decision: reachability — dev yes, prod/staging no.** The variable is passed
  through `docker-compose.dev.yml` via the existing `${VAR:-}` pattern and
  documented as an empty opt-in line in `.env.dev.example` (both conventions
  are specced in `development-environment`, hence that capability's MODIFIED
  delta). Staging/prod compose files keep their closed `environment:` lists:
  adding the var there would drag in the full defaults-file cascade (entries in
  both `.env.prod.defaults` and `.env.staging.defaults`, non-empty values for
  `validate_env.sh`) for a knob that should be a deliberate, rare operator
  action. The docs and the error remedy say plainly: in containerized
  staging/prod the hatch requires a compose edit + redeploy (or a revert);
  host-run and dev-compose processes can set the env var directly.
- **Decision: two-layer error taxonomy, mirroring the existing pattern.**
  Manifest layer raises `ManifestBackendMismatchError` (sibling of
  `ManifestSchemaError` in `bloom_mcp/manifest/manifest.py`, exported from
  `bloom_mcp.manifest`); `_guarded_manifest_read` maps it to
  `CatalogBackendMismatchError(ManifestReadError)` in `result_store/ports.py` —
  exactly how `ManifestSchemaError` maps to
  `ManifestIncompatibleError(ManifestReadError)` today, so every existing
  `except ManifestReadError` / `except ResultStoreError` handler keeps working
  and a caller that needs to distinguish can `isinstance()`-check. On the
  commit path the error surfaces as itself with do-not-retry semantics,
  mirroring the existing `KeyScopeGuardError` branch — never wrapped into the
  generic `CommitFailedError`, whose "transient — retry" message would mislead
  about a permanent condition.
- **Decision: typed propagation through the reader layer.** Today
  `_resolve_one_class` stringifies unknown failures and both reader adapters
  *discard* the string: `LocalReader.load_experiment` demotes every
  resolution failure under `require_clean=True` to
  `CleanedVersionRequiredError` ("run the QC workflow first") and
  `SupabaseReader.load_experiment` to `ExperimentNotFoundError` — so a
  string-level fix would change nothing observable. Instead
  `ManifestBackendMismatchError` propagates out of `_resolve_one_class`
  (excluded from its generic `except Exception`, alongside the explicit
  `ManifestSchemaError` branch), and both readers surface it as
  `ForeignCatalogError(ExperimentReadError)` (new, in `data_access/ports.py`)
  naming both backends. Consumer tools already declare
  `errors=(ExperimentReadError, CommitFailedError, ManifestReadError)`
  (verified in `pca_analysis.py:274` / `qc_clean.py:281`), so the message
  passes through the `@as_mcp_tool` envelope as a structured `tool_error` with
  **zero per-tool code changes** — and `pca_analysis`'s explicit
  `except CleanedVersionRequiredError` re-raise (the "run qc_clean first"
  remedy) is never hit, because `ForeignCatalogError` is a sibling, not a
  subclass, of it. Remaining `load_experiment_data` /
  `_resolve_versioned_cleaned` callers are audited during implementation so no
  path lets the raw manifest-layer error escape undeclared.
- **Decision: message content.** Both backend names, the logical catalog
  identity as the manifest's storage prefix (`bloommcp_output/qc_<stem>` — the
  only identity `read_manifest(prefix)` has), and the remedy (stop mixing
  backends; for a deliberate offline copy set
  `BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1`, noting the containerized-deploy
  caveat). Logical storage keys only — never absolute host paths (matching the
  local backend's existing no-path-leak rule).

## Risks / Trade-offs

- **A single foreign catalog and multi-class listings.** Verified during
  review: `list_existing_analyses` already isolates failures **per tool class
  for one experiment** (`except Exception → errors.append → continue`), so the
  new error cannot abort a listing; the foreign class contributes an error
  entry and healthy classes still list. The risk is regression, not a gap →
  pinned by a characterization test (mind the module's 30s `_RESPONSE_CACHE`
  and that `trim_staleness`'s own manifest reads may add a second error entry).
- **Fakes are blind to the guard.** `FakeResultStore` and `FakeReader` never
  construct a `Manifest` and have no backend concept (called out in #572's
  design), so fake-based suites can never exercise the guard. → Tests are
  written against the real manifest path: the local backend on a temp root and
  the in-memory Supabase boundary (`fake_supabase_storage` patches the manifest
  module's storage helpers but *not* `active_backend_name()`, so the guard is
  exercisable there too), with the foreign sentinel hand-patched (see trigger
  analysis). The parity-suite exemptions are recorded in both parity files and
  in the spec deltas so the gap is a documented boundary, not missing coverage.
- **Test hygiene.** There is no repo-wide autouse backend reset: tests that
  flip `BLOOM_STORAGE_BACKEND` or touch the memoized `_active` must use the
  opt-in `local_manifest_backend` fixture or call `reset_backend_for_tests()`
  in setup+teardown, and every test asserting default (fail-closed) behavior
  must `monkeypatch.delenv("BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST",
  raising=False)` so an ambient export can't flip it. The new var joins
  `test_package_baseline.py`'s env scrub list.
- **Pre-v5 manifests pass silently.** Accepted (see Decisions); the alternative
  bricks all history written before #572. Window closes on first re-commit.
- **The guard can be mistaken for full mixing detection.** → The docs section
  and both spec deltas state what it cannot catch (A → B → A) in the same
  breath as what it can.
- **A staging/prod bucket that is already foreign would start failing on
  deploy, where the hatch is not reachable without a compose edit.** Risk is
  low (prod/staging never run `local`; a foreign sentinel there would require a
  hand-uploaded catalog), and the failure would be the intended surfacing — but
  it is verified, not assumed: a one-time pre-merge audit queries the
  staging/prod `bloommcp-data` buckets for any `manifest.json` whose
  `storage_backend` is present and ≠ `supabase`.

## Migration Plan

No schema change, no data migration, no new required env var. Deploys pick up
the guard on restart; supported (unmixed) deployments see no behavior change.
If a deployment is unknowingly serving a foreign catalog today, its reads start
failing loudly with the remedy in the message — that is the intended surfacing.
Restoring read access: host-run or dev-compose processes set
`BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1` (warning trail per read);
containerized staging/prod require a compose `environment:` edit + redeploy, or
reverting the merge commit. The pre-merge bucket audit (Risks) makes the
staging/prod case a verified non-event rather than a hope. Rollback: revert the
commit; the sentinel field itself is untouched, and
`CatalogBackendMismatchError ⊂ ManifestReadError` /
`ForeignCatalogError ⊂ ExperimentReadError` mean downstream handlers are valid
both before and after.

## Open Questions

None blocking.
