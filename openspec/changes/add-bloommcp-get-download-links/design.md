## Context

`output_links` (#581/#595) is populated only inside `ResultStore.commit()` — deliberately,
per that change's own Decision 1, so `get_run`/`list_runs`/`list_existing_analyses` never
eagerly re-sign a URL for potentially many historical versions on every read. That decision
is still correct for those three read paths; #599 asks for a **fourth**, narrower read path
that a caller opts into by name for one specific `(experiment, tool_class, run_ref)` it
already knows about.

Two things this change needs are not fully available from the manifest today:

1. **The object key.** Only present for v3+ runs (`output_keys`, added alongside
   `output_sha256` in the #324/#464 era). A v2 manifest entry has neither — there was never
   a per-artifact logical key recorded for it. This is a pre-existing gap, not something
   #599 introduces or attempts to backfill (see Non-Goals).
2. **The byte size.** `hash_outputs` (`result_store/_artifacts.py`) already computes this at
   every commit — for free, since the bytes are already read into memory to hash — but
   today it is used only to build the ephemeral `OutputLink` `commit()` returns, then
   discarded. It has never been persisted into `VersionEntry`/the manifest, and **this
   proposal does not change that** (see Decision 1 below — an earlier draft did, and was
   corrected).

This branch is cut directly from `origin/staging` at `47a94ba` (the #581/#595 merge).
`add-bloommcp-signed-url-key-scoping` (#598, PR #609) is proposed and implemented but
**not yet merged** as of this writing (confirmed via `gh pr view 609`: state OPEN) —
`build_output_links` on `staging` today takes no `expected_prefix` parameter and there is
no `KeyScopeGuardError`. This design does not assume PR #609 merges before or after this
change.

## Goals / Non-Goals

- Goal: a caller who already knows `(experiment, tool_class, run_ref)` — e.g. because
  `list_existing_analyses` just showed it, or because a prior tool call in a now-expired
  session returned it — can get a fresh, real (not fabricated) download link, hash, and
  size for each of that run's outputs.
- Goal: apply the same "never sign a key outside this run's own scope" discipline #598
  established for the write path to this new read path too, without taking an ordering
  dependency on #598 itself.
- Goal: hold #599's own second acceptance criterion exactly — no manifest, `Provenance`,
  or `VersionEntry` change of any kind.
- Non-Goal: browsing across runs/experiments (a file explorer) — the issue scopes this out
  explicitly.
- Non-Goal: making v2-era (pre-`output_keys`) runs downloadable — there is no key to sign;
  `get_download_links` on such a run returns an empty `output_links`, the same shape
  `list_existing_analyses` already tolerates for v2 entries elsewhere.
- Non-Goal: signed-URL access for `manifest_path` — `#600`'s explicitly separate scope.

## Decisions

### Decision 1 — `size_bytes` is always resolved live; nothing is ever persisted

**Superseded decision, kept here for the record:** the first draft of this proposal added
`output_size_bytes: dict[str, int]` as a manifest schema v6 field (mirroring how
`output_sha256`/`output_keys` are already persisted), reasoning that a live per-output
network call on every `get_download_links` call was wasteful when the value is already
computed for free at commit time. Openspec-review caught two independent, decisive problems
with that:

- It **directly contradicts `#599`'s own acceptance criterion** ("Manifest/provenance
  fields unchanged — this only adds an access path to already-persisted artifacts, same
  framing as #581") and the identical framing the sibling issue `#600` states for its own,
  separate scope. This alone is disqualifying regardless of the engineering trade-off.
- It is **not soundly implementable as described**: `StoredRun` is a plain, non-Pydantic
  frozen `@dataclass` (`ports.py`) where `output_sha256` has no default value (it precedes
  the first defaulted field). Adding a new field to mirror it would either need a default
  (contradicting "mirrors `output_sha256` exactly") or would break every direct
  `StoredRun(...)` construction site that doesn't pass it by keyword —
  `fake_store.py`'s `_stub_stored_run`, used by `seed_collision`/`seed_v2_run`, both
  actively exercised by `test_fake_result_store.py` and `test_store_parity.py`.

**Chosen instead:** `get_download_links` calls the new `StorageBackend.get_object_size(key)`
live, for every output, on every call — for a run committed a minute ago exactly as much as
one committed a year ago. No manifest field, no schema version bump, no `StoredRun` change.
The cost is one extra network round-trip per output per call; `get_download_links` is a
caller-opted-in, on-demand tool, not a hot path like `commit()`, so this cost is acceptable
and — per the acceptance criterion above — not actually optional.

### Decision 2 — `get_object_size`'s response shape must be verified, not assumed

storage3's `client.info(key)` (`_sync/file_api.py:376`) returns an untyped `dict[str, Any]` —
nothing in the installed client documents its keys. The only comparable *typed* object in the
same client, `SearchV2Object` (`types.py`), nests object metadata under a `metadata` key
rather than a flat top level — real Supabase Storage API responses commonly follow that same
nested convention (size/mimetype/etc. under `metadata`). An earlier draft of this proposal
asserted a flat `size` key with no supporting evidence; that confidence was unearned and is
withdrawn here. Implementation MUST confirm the actual shape (against a live dev-stack call
or a recorded fixture response) before writing the extraction, and SHOULD use the same
best-effort, multiple-key-shape extraction style `_extract_signed_url` already uses for
`create_signed_url`'s own casing drift, trying both a flat and a `metadata`-nested location
rather than committing to one guess.

### Decision 3 — The read-side key-scoping guard is independent of #598, not built on it

#598/PR #609 adds an `expected_prefix` parameter to `build_output_links` for the *write*
path (`commit()`). This change needs an analogous check for the *read* path (resolving an
already-persisted `StoredRun` from the manifest and re-signing/re-sizing it) — the same
underlying worry (a future bug in manifest resolution, or in this new method itself, handing
`create_signed_url`/`get_object_size` a key belonging to a different experiment's run)
applies here too, and this is precisely the kind of "a new call site added without noticing
the invariant" #598's own Why section warns about. The guard covers **both** per-key calls
this method makes — `create_signed_url` and `get_object_size` — not signing alone; either
one handed a key outside the run's own scope would leak the same information (the object's
existence/size, or a working download URL).

`build_output_links`'s signature on `staging` today has no `expected_prefix` param at all —
reusing it would require this change to either depend on PR #609 merging first (an
ordering constraint neither change should need) or duplicate #609's own diff speculatively
against code that doesn't exist yet on `staging`. Instead, `get_download_links` computes its
own expected prefix fresh from `(experiment, tool_class, resolved version_dir)` — the same
inputs #598's guard derives its prefix from, just recomputed independently at read time
instead of read from the write-time closure — and raises a new `ports.py` error,
`CorruptRunLinksError(ResultStoreError)`, structurally distinct from #598's `_artifacts.py`
`KeyScopeGuardError` (a `RuntimeError` subclass) since the two guard different lifecycle
points (before upload vs. after a manifest read) and living in different modules avoids any
merge collision regardless of which of #598/#599 lands second. Consolidating the two into
one shared helper is a real DRY opportunity once both exist — logged as a Non-Goal for
whichever change merges second to pick up, not attempted here.

### Decision 4 — A partial per-output failure aborts the whole call

Neither `create_signed_url` nor the new `get_object_size` performs an ownership check of its
own (both are generic object-storage primitives); either can also fail organically — most
plausibly, an object was deleted from storage sometime after commit while the manifest still
lists it (`get_object_size`'s own contract requires it to raise, not fabricate `0`, exactly
for this case). `get_download_links` SHALL treat any single output's lookup failure as a
failure of the whole call — propagating a clear error rather than returning a
partially-populated `output_links` with no indication some outputs were silently skipped.
The MCP tool shim's catch list is extended to include `StorageKeyNotFound`/
`StorageBackendError` (the `storage_backend.py` error types a live lookup can raise)
alongside the `ResultStore`-level errors, so this still surfaces as a clean `{"error": ...}`
JSON response, never a raw traceback.

### Decision 5 — `get_download_links` is not a foundational tool

`ALWAYS_INCLUDE_MCP_TOOLS` (`langchain/helpers/foundational_tools.py`) exists so an agent
can always bootstrap a session (`list_available_experiments`, `load_experiment_data`,
`list_existing_analyses`) regardless of which tool_set/mcp_tool_names routing narrowed the
rest of its toolset. `get_download_links` doesn't fit that shape: it is only useful once a
caller already has a specific `(experiment, tool_class, run_ref)` in hand — from
`list_existing_analyses` (which already is foundational) or from a prior tool's own
response — the same category as `pca_analysis`/`remove_outliers`/etc., which are
dynamically discovered rather than hand-listed (`context_tools.py`'s `CONTEXT_MCP`).

### Decision 6 — Fake/real parity: `FakeResultStore` keeps its own internal size bookkeeping

`SupabaseResultStore.get_download_links` calls the live `get_object_size` fallback described
in Decision 1 for every output. `FakeResultStore` never calls `StorageBackend` at all (it
synthesizes URLs directly — see its existing `url_for=lambda key:
f"fake://signed/{key}?..."` in `commit()`) and never uploads real bytes, so there is nothing
external for it to genuinely stat. Rather than inventing a second, meaningless "fake size"
concept (or, worse, fabricating one), `FakeResultStore` records each output's real computed
byte size (from the same `hash_outputs` call every commit already makes) in its own private,
in-memory bookkeeping at commit time — not on the shared `StoredRun`/manifest shape, purely
internal to this test double — and `get_download_links` reads from that. This gives the fake
a genuinely real size for anything it itself recorded, with no live `StorageBackend` call
ever needed, keeping parity with the real adapter's *observable result* (a real byte count)
without duplicating its *mechanism* (a live storage query) — consistent with why the fake's
URLs are visibly `fake://`-prefixed rather than plausible-looking fabrications, not a
divergence that needs disclosing against the archived spec's "fake and real adapters agree
on observable behaviour" requirement.

## Risks / Trade-offs

- **A live network call on every `get_download_links` call, for every output.** Accepted per
  Decision 1 — this is a deliberate, caller-opted-in, on-demand tool, not a hot path, and the
  alternative (persisting the value) is foreclosed by #599's own acceptance criterion.
- **`client.info()` shape drift / uncertainty.** Mitigated by Decision 2's multi-shape
  extraction and by verifying the real shape empirically before implementation (tasks.md
  1.1), rather than assuming one.
- **Same-key-immutability assumption.** `sha256` always comes from the immutable manifest
  snapshot recorded at commit time; the live `size_bytes` query reads the object's *current*
  state. These are only guaranteed to describe the same bytes if the object was never
  mutated out-of-band after commit — true under bloommcp's normal write path (each version
  directory is written once, never overwritten), but not something this method can verify
  or enforce; a storage-side incident or manual admin intervention could make them disagree
  silently. This is the same threat class `CorruptRunLinksError` already treats as
  "never caller input, always a bug/corruption elsewhere," and is disclosed in
  `storage-backends.md` rather than left implicit.
- **Two unarchived sibling changes touching the same specs.** Like #598 before it, this
  change's `bloommcp-result-store`/`bloommcp-storage-backend` deltas are written against the
  *currently archived* text (which has none of #581's `output_links` language yet either) —
  whoever archives any of `add-bloommcp-signed-url-download`,
  `add-bloommcp-signed-url-key-scoping`, and this change will need to fold all three
  deltas together. **This change MUST NOT be archived independently of
  `add-bloommcp-signed-url-download`** (same constraint #598 already documented).

## Migration Plan

None. This change makes no manifest, `Provenance`, or schema change of any kind — every
value it returns is either already-persisted (`output_sha256`, `output_keys`) or resolved
live at call time (`size_bytes`, `url`). Nothing to migrate or backfill.

## Open Questions

- Exact naming of the new `StorageBackend` method (`get_object_size` here) and the new
  `ResultStore` method (`get_download_links`, matching the MCP tool name) — open to
  bikeshedding at review; chosen for consistency with existing verb-first naming
  (`create_signed_url`, `get_run`) rather than a strong independent preference.
