## Context

bloommcp's persistence is split behind two ports at the tools boundary
([\_ports.py](../../../bloommcp/src/bloom_mcp/tools/_ports.py)): the `ExperimentReader`
(inputs) and the `ResultStore` (outputs). #389 made the **output** side swappable by
selecting a `StorageBackend` beneath `supabase_client`'s five object-storage helpers,
driven by `BLOOM_STORAGE_BACKEND` (default `supabase`, opt-in `local`). It deliberately
left inputs and boot untouched, and its own docs flag the gap:

> `local` changes only where outputs are written. bloommcp still boots through
> `validate_supabase_env()` and still reads inputs and database tables via Supabase …
> A fully offline mode would also need inputs sourced locally.

This change closes that gap on the **input** side and fixes the boot gate. It is
constrained by four things already true on `staging`:

1. Consumers already depend only on the `ExperimentReader` port — `qc_tools`,
   `storage_tools`, and the workflows read through `_ports.reader()` / `_ports.load_frame`.
   So a new adapter is transparent to them.
2. Post-#389, `SupabaseReader`'s cleaned/legacy reads already resolve through the active
   storage backend, so under `BLOOM_STORAGE_BACKEND=local` they already read local files;
   its raw read already comes from local `BLOOM_TRAITS_DIR`. The residual Supabase
   couplings on the input path are the **boot gate** and the **class's identity / deprecation
   nudge** — not the byte path. (Verified against source: the store commit path touches
   only the five backend-routed helpers; no PostgREST/table call on the offline path.)
3. Two call sites still bypass the port — but one layer deeper than it looks: the
   cross-experiment correlation reads happen in
   `cross_experiment_correlations.load_and_align_experiments` (which takes filesystem
   **paths**, fed a hardcoded `EXPERIMENTS` dict by `correlation_tools.py`), and
   `start_run` (`src = _eu.TRAITS_DIR / filename`). `list_available_experiments` already
   routes through the port.
4. `LocalReader` reuses `load_experiment_data`'s resolution, whose **legacy tier** reads
   `OUTPUT_DIR/qc_<stem>/<stem>_cleaned.csv` directly (bypassing the backend) — a
   staleness vector for `require_clean` (see Decision 5).

## Goals / Non-Goals

- **Goals:** a first-class Supabase-free `LocalReader` behind the existing port; a single
  ergonomic fully-local switch; a backend-aware boot gate so a fully-local run needs no
  Supabase; consistent port routing for the stragglers **without weakening input
  provenance**; docs + a real offline end-to-end test.
- **Non-Goals:** packaging/distribution for non-technical users; per-user identity/RLS;
  PostgREST/table-read locality; any change to the deployed Supabase default.

## Decisions

### Decision 1 — Selection: reuse `BLOOM_STORAGE_BACKEND=local` as the single fully-local switch

When `BLOOM_STORAGE_BACKEND=local`, the composition root wires **both** the `LocalReader`
(input) and #389's `LocalStorageBackend` (output), and the boot gate skips Supabase. One
knob means "fully local." Unset / `supabase` is unchanged. Selection reads the backend via a
new **public** accessor on `storage_backend.py` (`selected_backend_name()` /
`is_local_backend()`) — `server.main()` and `_ports` must not import the private
`_selected_backend_name()`.

- **Alternatives considered:**
  - _Dedicated `BLOOM_LOCAL_MODE=1` / `BLOOM_OFFLINE=1`_ implying both backends local and
    dropping the boot gate, leaving `BLOOM_STORAGE_BACKEND` output-only. Keeps #389's
    documented "output-only" contract intact and reads as explicit intent, but adds a third
    overlapping concept and needs precedence rules for the contradictory combination
    (`BLOOM_LOCAL_MODE=1` + `BLOOM_STORAGE_BACKEND=supabase`).
  - _Separate input selector `BLOOM_EXPERIMENT_BACKEND=local`_ parallel to storage, with the
    boot gate keyed off "both local." Fully symmetric and allows independent selection, but
    requires setting **two** vars for the common fully-local case — exactly the ergonomics the
    issue warns against.
- **Rationale:** the issue explicitly prefers "a single fully-local switch." Reusing the
  existing var adds no new concept and is maximally symmetric with #389. The combination it
  gives up — _local output + Supabase-table input_ — is niche and already a partial state
  (post-#389 `local` output already reads raw inputs from local `BLOOM_TRAITS_DIR`). The cost
  is that #389's `storage-backends.md` "not a fully-offline mode" caveat must be **updated**,
  which this change does deliberately (not silently).

### Decision 2 — Local input root: `BLOOM_EXPERIMENT_LOCAL_ROOT` → `BLOOM_TRAITS_DIR`

`LocalReader`'s raw-input dir is `BLOOM_EXPERIMENT_LOCAL_ROOT` when set, otherwise
`BLOOM_TRAITS_DIR` (already required, already mounted at `./bloommcp/data/…` in dev). This
mirrors #389's `_resolve_local_root` (`BLOOM_STORAGE_LOCAL_ROOT` → `BLOOM_OUTPUT_DIR`) so
fully-local needs **no** extra config in dev. Unlike #389's fallback (logged as a "deprecated
dev bridge"), the `BLOOM_TRAITS_DIR` fallback here is a **supported** default, since this
change promotes the local input path rather than retiring it. Cleaned/versioned outputs
resolve from the local output store (the #389 backend, rooted at `BLOOM_STORAGE_LOCAL_ROOT` →
`BLOOM_OUTPUT_DIR`). This env-var name is **decided**, not open — the spec, tasks, docs, and
`docker-compose.dev.yml` all use `BLOOM_EXPERIMENT_LOCAL_ROOT`.

- **Alternative:** a single new var for both input and output roots. Rejected — inputs and
  outputs are distinct dirs with distinct lifecycles (read-only inputs vs. versioned outputs),
  and reusing the two existing, already-validated dirs is lower-friction and less error-prone.

### Decision 3 — A first-class, structurally-coupled `LocalReader`

Because #389 routed the cleaned/legacy reads through the storage backend, `SupabaseReader`
technically produces local bytes under `BLOOM_STORAGE_BACKEND=local`. A distinct `LocalReader`
is still the right call:

- **Identity / self-documentation** — a fully-local server should not be wired with a class
  named `SupabaseReader`; wiring `LocalReader` makes the mode legible in logs and code.
- **No deprecation nudge** — `SupabaseReader`'s raw read emits a `DeprecationWarning` steering
  users off local inputs; that is wrong for a _promoted_ local path. `LocalReader` reads local
  inputs as a supported, first-class path.
- **Guaranteed Supabase-independence** — `LocalReader` imports no `supabase_client` and makes
  no PostgREST/table call, enforced by a static grep/AST guard so it is a **structural**
  property, not a runtime coincidence.

**Structural reader/store coupling.** `LocalReader`'s cleaned tier must not resolve through
the ambient `active_backend()` while the store backend is Supabase — that would yield local
raw reads but Supabase cleaned reads (split lineage into PCA). So `LocalReader` is wired
**only** when the active storage backend is also `local`, validated at boot; a
reader-local / store-supabase mismatch is rejected at boot, not silently tolerated.
`LocalReader` reads raw CSVs with the **same `pd.read_csv` configuration** as the deployed
raw path (no divergent `dtype`/`na_values`/`decimal`), so identical bytes yield identical
declared roles. It declares roles via the shared `detect_columns` oracle and rejects any
`name` resolving outside its configured root (a containment guard mirroring #389's output
guard — bloommcp is LLM-driven, so an agent can be steered to request an arbitrary `name`).

`LocalReader` preserves the port's observable contract: the same resolution order
(versioned-cleaned → legacy cleaned → raw), the same `ExperimentNotFoundError` /
`CleanedVersionRequiredError` signalling, no host-path leakage in errors, and role parity with
`SupabaseReader` / `FakeReader`.

### Decision 4 — Backend-aware boot gate in `server.main()`

`main()` computes `fully_local = is_local_backend()`. If fully-local, it **skips**
`validate_supabase_env()` and instead validates the local input root (exists, is a readable
dir); the local output root and an invalid `BLOOM_STORAGE_BACKEND` value are already validated
by #389's `validate_storage_backend` (invoked from `experiment_utils.validate_env`). The
data-directory / plots validation (`BLOOM_*_DIR`, `BLOOM_PLOTS_URL`) runs in **both** modes.
On the default path, `validate_supabase_env()` runs exactly as today. The gate lives at
`server.main()` (the composition root), keeping module import side-effect-free and matching
where #389 placed its own boot validation. Because the gate keys strictly off `local` (which
prod/staging never set), skipping `validate_supabase_env()` cannot disarm a deployed server.

### Decision 5 — `require_clean` must not honor an un-provenanced legacy cleaned CSV

`load_experiment_data`'s legacy tier reads `OUTPUT_DIR/qc_<stem>/<stem>_cleaned.csv` directly,
bypassing the backend and any manifest/hash lineage. Under `LocalReader` with the local root
defaulting to `BLOOM_OUTPUT_DIR` — the same dir the legacy tier scans — a stale pre-migration
`<stem>_cleaned.csv` could satisfy `require_clean=True` for PCA with data that does **not**
correspond to the current raw input, silently. `LocalReader`'s `require_clean` path therefore
does **not** honor the un-versioned legacy tier as a certified clean (it requires a versioned,
manifest-backed cleaned output), so a certified-clean consumer cannot be fed stale,
un-addressed data without error.

### Decision 6 — Straggler routing preserves provenance

- **Correlation reads** go through `reader.load_experiment(name, version="raw")` (the port
  already supports `version="raw"`, so raw-only semantics are preserved).
  `cross_experiment_correlations.load_and_align_experiments` grows a frame-accepting entry
  point (it currently takes paths), and the hardcoded `EXPERIMENTS` dict / local
  `list_experiments` resolve through the port.
- **`start_run` source provenance** (used by the 5 legacy workflow tools — `qc_clean` /
  `pca_analysis` self-compute their `source_csv` and do not call `start_run`) is preserved by
  resolving the on-disk input **through the active reader** (an optional `raw_source_path`
  adapter capability that `SupabaseReader` and `LocalReader` both implement, each rooting at
  its own input dir), so `input_sha256` stays non-empty and honours the local input root
  rather than a hard-coded `TRAITS_DIR`. This hashes the real input file (exactly what the
  prior code did, just at the correct root) — no temp-snapshot lifetime to manage across
  `create_run`/`commit`. `source_csv` degrades to `None` only for a genuinely path-less
  adapter (e.g. the in-memory `FakeReader` or a future DB adapter), which omits the method.

## Risks / Trade-offs

- **Overloading `BLOOM_STORAGE_BACKEND` changes #389's documented contract.** Mitigation:
  update `storage-backends.md` in the same change; #389's docs already point at this follow-up.
- **Cross-capability ownership.** The switch's semantics now span two capabilities. Mitigation:
  ordering requirement + reconciliation follow-up in the Migration Plan (below), not an
  unbacked promise.
- **Straggler routing changes `correlation_tools` behavior** (it currently reads raw frames
  only) and touches an unlisted file. Mitigation: `version="raw"` preserves semantics; a
  characterization test pins the cross-experiment outputs before/after; `cross_experiment_correlations.py`
  is named as affected.
- **`require_clean` staleness** (Decision 5) — mitigated by not honoring the legacy tier as
  certified-clean under `LocalReader`.
- **Raw-CSV dtype divergence** — `FakeReader` (in-memory) can't catch a trait column that
  `pd.read_csv` infers as `object` (flipping trait→metadata). Mitigation: a fixture parity
  test reads the **same on-disk CSV** with a dtype-ambiguous trait column through
  `SupabaseReader` (raw tier) and `LocalReader`, asserting identical roles; `FakeReader` is the
  oracle only for signalling/role-declaration, not dtype behavior.

## Migration Plan

Additive and opt-in — no data migration. Default (`BLOOM_STORAGE_BACKEND` unset/`supabase`)
is byte-for-byte unchanged; deployed environments are untouched. Rollback is unsetting the var.
**Archive ordering:** this builds on #389 (merged to `staging`) and redefines the shared
`BLOOM_STORAGE_BACKEND=local` switch, but #389's `bloommcp-storage-backend` capability is not
yet in `openspec/specs/`, so this change cannot MODIFY it. #389 MUST archive before (or
together with) this change, and a reconciliation follow-up MUST update #389's "governs only the
five object-storage helpers" wording (now false). Tracked as a task, not assumed.

## Open Questions

- None blocking. (The prior "does the offline path make a live table call?" question is
  resolved: the store commit path touches only the five backend-routed object-storage helpers
  — no PostgREST/table call — and the end-to-end test's network guard confirms it. The env-var
  name is decided in Decision 2.)
