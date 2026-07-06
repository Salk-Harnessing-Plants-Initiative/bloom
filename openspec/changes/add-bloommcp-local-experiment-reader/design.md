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
constrained by three things already true on `staging`:

1. Consumers already depend only on the `ExperimentReader` port — `qc_tools`,
   `storage_tools`, and the workflows read through `_ports.reader()` / `_ports.load_frame`.
   So a new adapter is transparent to them.
2. Post-#389, `SupabaseReader`'s cleaned/legacy reads already resolve through the active
   storage backend, so under `BLOOM_STORAGE_BACKEND=local` they already read local files;
   its raw read already comes from local `BLOOM_TRAITS_DIR`. The residual Supabase
   couplings on the input path are the **boot gate** and the **class's identity / deprecation
   nudge** — not the byte path.
3. Two call sites still bypass the port: `correlation_tools.py` (direct
   `pd.read_csv(TRAITS_DIR / …)`) and `start_run` (`src = _eu.TRAITS_DIR / filename`).

## Goals / Non-Goals

- **Goals:** a first-class Supabase-free `LocalReader` behind the existing port; a single
  ergonomic fully-local switch; a backend-aware boot gate so a fully-local run needs no
  Supabase; consistent port routing for the stragglers; docs + a real offline end-to-end test.
- **Non-Goals:** packaging/distribution for non-technical users; per-user identity/RLS;
  PostgREST/table-read locality; any change to the deployed Supabase default.

## Decisions

### Decision 1 — Selection: reuse `BLOOM_STORAGE_BACKEND=local` as the single fully-local switch

When `BLOOM_STORAGE_BACKEND=local`, the composition root wires **both** the `LocalReader`
(input) and #389's `LocalStorageBackend` (output), and the boot gate skips Supabase. One
knob means "fully local." Unset / `supabase` is unchanged.

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
fully-local needs **no** extra config in dev. Cleaned/versioned outputs resolve from the
local output store (the #389 backend, rooted at `BLOOM_STORAGE_LOCAL_ROOT` → `BLOOM_OUTPUT_DIR`).

- **Alternative:** a single new var for both input and output roots. Rejected — inputs and
  outputs are distinct dirs with distinct lifecycles (read-only inputs vs. versioned outputs),
  and reusing the two existing, already-validated dirs is lower-friction and less error-prone.

### Decision 3 — A first-class `LocalReader`, even though `SupabaseReader` is "accidentally local" under #389

Because #389 routed the cleaned/legacy reads through the storage backend, `SupabaseReader`
technically produces local bytes under `BLOOM_STORAGE_BACKEND=local`. A distinct `LocalReader`
is still the right call:

- **Identity / self-documentation** — a fully-local server should not be wired with a class
  named `SupabaseReader`; wiring `LocalReader` makes the mode legible in logs and code.
- **No deprecation nudge** — `SupabaseReader`'s raw read emits a `DeprecationWarning` steering
  users off local inputs; that is wrong for a _promoted_ local path. `LocalReader` reads local
  inputs as a supported, first-class path.
- **Guaranteed Supabase-independence** — `LocalReader` imports no `supabase_client` and makes
  no PostgREST/table call, so "no live Supabase" is a structural property, not a runtime
  coincidence of which code paths happened to be exercised.
- **Configurable root + declared roles** — it roots inputs at its own local dir and declares
  column roles via the shared `detect_columns` oracle, matching the port's "adapter declares
  roles" contract.

`LocalReader` preserves the port's observable contract: the same resolution order
(versioned-cleaned → legacy cleaned → raw), the same `ExperimentNotFoundError` /
`CleanedVersionRequiredError` signalling, no host-path leakage in errors, and role parity with
`SupabaseReader` / `FakeReader` (verified by running the shared scenario set against all three).

### Decision 4 — Backend-aware boot gate in `server.main()`

`main()` computes `fully_local = (selected storage backend == "local")`. If fully-local, it
**skips** `validate_supabase_env()` and instead validates the local input root (exists, is a
readable dir); the local output root is already validated by #389's `validate_storage_backend`
(invoked from `experiment_utils.validate_env`). On the default path, `validate_supabase_env()`
runs exactly as today. The gate lives at `server.main()` (the composition root), keeping module
import side-effect-free and matching where #389 placed its own boot validation.

## Risks / Trade-offs

- **Overloading `BLOOM_STORAGE_BACKEND` changes #389's documented contract.** Mitigation:
  update `storage-backends.md` in the same change; #389's docs already point at this follow-up.
- **Skipping `validate_supabase_env()` could mask a misconfigured _deployed_ server.** The
  gate keys strictly off `local`, which is opt-in and off by default; prod/staging never set it,
  so their fail-fast is unchanged. An end-to-end test asserts the default path still requires Supabase.
- **Routing the stragglers can change `correlation_tools` behavior** (it currently reads raw
  frames only). Mitigation: route through `load_experiment`'s raw/default resolution to preserve
  today's semantics, with tests pinning the cross-experiment outputs before/after.
- **`start_run` source-CSV provenance** currently records a `TRAITS_DIR` path. Under `LocalReader`
  the source is a local file too; the provenance stays a real, hashable file. Where the reader
  cannot expose a concrete path, `source_csv` degrades to `None` (already supported by
  `create_run`) rather than a fabricated path.

## Migration Plan

Additive and opt-in — no data migration. Default (`BLOOM_STORAGE_BACKEND` unset/`supabase`)
is byte-for-byte unchanged; deployed environments are untouched. Rollback is unsetting the var.
Sequencing note: builds on #389 (already merged to `staging`); the shared switch semantics are
documented jointly when `bloommcp-storage-backend` archives.

## Open Questions

- Does the fully-local `qc_clean → pca_analysis` path make **any** live PostgREST/table call
  (e.g. run-tracking inside `SupabaseResultStore.create_run`)? The end-to-end test runs with
  `SUPABASE_URL` / `BLOOM_AGENT_KEY` unset and will fail if one exists; if so, that call is
  stubbed/routed as a follow-up (table locality stays out of scope here).
- Final env-var name: `BLOOM_EXPERIMENT_LOCAL_ROOT` (proposed, symmetric with
  `BLOOM_STORAGE_LOCAL_ROOT`) vs. reusing `BLOOM_TRAITS_DIR` alone with no override.
