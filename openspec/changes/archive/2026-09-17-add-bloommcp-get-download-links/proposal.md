## Why

`output_links` (added in #581/#595) is populated only on a tool call's own immediate
`commit()` response — `get_run`/`list_runs`/`list_existing_analyses` always return it
empty by design (see `StoredRun.output_links`'s docstring in `result_store/ports.py`).
If a chat session ends, the browser closes, or the 1-hour signed URL simply expires,
there is no tool-level way to get a working download link for that same historical run
again — someone with direct Supabase Storage/admin access would have to fetch it
manually. `bloommcp/docs/storage-backends.md` already discloses this as a known
limitation ("browsing and downloading historical runs is not yet supported (tracked
separately)"); this issue is that tracking.

`#599`'s own second acceptance criterion is explicit: **"Manifest/provenance fields
unchanged — this only adds an access path to already-persisted artifacts (same framing
as #581)."** The sibling issue `#600` (manifest.json's own signed-URL access, out of
scope here) states the identical constraint for its own scope. This proposal is written
to hold that line exactly: nothing about the manifest schema, `Provenance`, or
`VersionEntry` changes. An earlier draft of this proposal added a manifest schema bump
to persist each output's byte size going forward — caught in review as a direct
violation of that acceptance criterion (and, independently, as an unsound `StoredRun`
dataclass change — see design.md's superseded-decision note). This version resolves
size the same way for every run, old or new: a live storage lookup, never a persisted
field.

## What Changes

- Add `get_object_size(key: str) -> int` to the `StorageBackend` Protocol
  (`storage_backend.py`), both adapters, and a matching
  `bloom_mcp.supabase_client.get_object_size` re-export — mirroring exactly how #581
  added `create_signed_url` as the sixth-becomes-seventh helper; this becomes the
  eighth. `SupabaseStorageBackend.get_object_size` calls storage3's per-object
  `client.info(key)` endpoint (confirmed present in the installed `storage3` client at
  `.venv/lib/python3.11/site-packages/storage3/_sync/file_api.py:376`); the exact
  response shape (a flat `size` key vs. a nested `metadata.size`, matching the only
  other typed object in this client, `SearchV2Object`) is **not yet confirmed** and
  MUST be verified empirically against a live/mocked response before the extraction is
  written (see design.md Decision 2 — this replaces an earlier, overconfident draft
  that asserted a flat key with no evidence). A response missing the resolved size
  field SHALL raise rather than return a fabricated `0`.
  `LocalStorageBackend.get_object_size` is `Path.stat().st_size` after the existing
  `_resolve(key)` containment guard.
- Add a `get_download_links(experiment, tool_class, run_ref="latest") -> StoredRun`
  method to the `ResultStore` Protocol (`result_store/ports.py`) and both adapters
  (`SupabaseResultStore`, `FakeResultStore`). Implementation: resolve the run through
  the same manifest lookup `get_run` already uses; when `output_keys` is non-empty,
  recompute the expected object-key prefix fresh from `(experiment, tool_class,
  resolved version_dir)` — the read-side counterpart to
  `add-bloommcp-signed-url-key-scoping`'s (#598, PR #609 — **not yet merged as of this
  writing**) write-side guard on `commit()`'s call to `build_output_links` — and reject
  (a new `CorruptRunLinksError(ResultStoreError)`, never a caller-input condition) any
  persisted `output_key` that falls outside it, for **both** the `create_signed_url`
  and `get_object_size` calls that follow, before either is made. Then, for each
  output: `sha256` from the persisted `output_sha256` (manifest/provenance untouched —
  this field already exists), `size_bytes` from a live `get_object_size(key)` call
  (always — no persisted-size fast path, no schema change, uniform for a run committed
  a minute ago or a year ago), and `url` from `create_signed_url`. Any single output's
  lookup failure aborts the whole call rather than returning a partially-populated
  `output_links` (see design.md's partial-failure decision). A run whose `output_keys`
  is empty (a legacy v2-shaped entry — no per-artifact key was ever recorded) returns
  `output_links == {}` rather than raising, since there is nothing to look up. This is
  a **separate, independent** guard from #598's — it carries no ordering dependency on
  PR #609 landing first (see design.md).
- Add the MCP tool `get_download_links(experiment, tool_class, run_ref="latest") -> str`
  (JSON) in `sections/core/get_download_links.py` — a thin shim over
  `_ports.store().get_download_links(...)`, mirroring `list_existing_analyses.py`'s
  existing "is this experiment known" check and `{"error": ...}` JSON-error style,
  extended to catch the `StorageKeyNotFound`/`StorageBackendError` errors a live
  size/signing lookup can raise (e.g. an object deleted from storage since commit)
  alongside `RunNotFoundError`/`ManifestReadError`/`ManifestIncompatibleError`/
  `CorruptRunLinksError` — never a raw traceback. Registered in
  `sections/core/__init__.py` alongside the other three core tools. **Not** added to
  `ALWAYS_INCLUDE_MCP_TOOLS` (`langchain/helpers/foundational_tools.py`) — this is a
  targeted, on-demand retrieval tool (the caller already knows or has just listed the
  run it wants a link for), not a session-bootstrap discovery tool like the three
  existing foundational core tools; it is discovered dynamically like every other
  analysis tool (see `langchain/tools/context_tools.py`'s `CONTEXT_MCP`).
- Add a dedicated `bloommcp-get-download-links-tool` capability spec covering the
  tool's registration/discoverability, mirroring the precedent every prior new-tool
  proposal has established (e.g. `add-bloommcp-qc-inspect-tool`'s "QC Inspect Tool
  Registration and Discovery" requirement) — a gap in this proposal's first draft,
  caught in review.
- Update `bloommcp/docs/storage-backends.md`'s "Downloading outputs: signed URLs"
  section to replace the "browsing and downloading historical runs is not yet
  supported (tracked separately)" line with a description of `get_download_links`,
  and disclose the same-key-immutability assumption a live size lookup relies on (see
  design.md).
- Extend `bloommcp/tests/conftest.py`'s `_InMemoryObjectStore` (`fake_supabase_storage`
  fixture) and `test_storage_backend.py`'s separate `_FakeSbStorageClient` double with
  `get_object_size` so the existing test files backing `SupabaseResultStore` keep
  working once `get_download_links` calls the real eight-helper set (#581's own
  rollout found a third double needed the same update, unanticipated at its proposal
  time — checked from the start here instead).

## Non-Goals

- A file explorer or cross-run/cross-experiment browsing UI — out of scope per the
  issue; this re-fetches a link for one already-known `(experiment, tool_class, run_ref)`,
  not open-ended browsing.
- Signed-URL access for `manifest_path` itself — that is `#600`'s explicitly separate
  scope ("Re-fetching links for historical runs' *outputs* — tracked separately," i.e.
  this issue), not this one's.
- **Any manifest, `Provenance`, or `VersionEntry` schema change of any kind.** Per
  `#599`'s own second acceptance criterion and `#600`'s identical framing, `size_bytes`
  is resolved live on every call, for every run regardless of when it was committed —
  never persisted, never a schema version bump. (An earlier draft proposed exactly
  such a bump; it is removed — see design.md.)
- Consolidating this change's read-side key-scoping check with #598's write-side guard
  on `build_output_links` into one shared helper — a real DRY observation once both
  land, but orthogonal to shipping either independently; whichever change merges second
  can consider it.
- A configurable expiry for the re-signed URL — reuses the existing
  `SIGNED_URL_EXPIRES_SECONDS` constant (3600s) `commit()` already signs with, per
  #581's own "fixed constant, not a per-call parameter" decision.
- Any change to `create_signed_url`'s own signature, or to what `list_runs`/
  `list_existing_analyses` return — those continue to leave `output_links` empty for
  every run except the one a tool call just committed, exactly as #581 established;
  `get_download_links` is the new, deliberate exception a caller must opt into by name.

## Impact

- **Affected specs:** `bloommcp-result-store`, `bloommcp-storage-backend`,
  `bloommcp-get-download-links-tool` (new)
- **Affected code:** `bloommcp/src/bloom_mcp/storage_backend.py`,
  `bloommcp/src/bloom_mcp/supabase_client.py`,
  `bloommcp/src/bloom_mcp/result_store/{ports,supabase_store,fake_store}.py`,
  `bloommcp/src/bloom_mcp/result_store/__init__.py` (re-export `CorruptRunLinksError`),
  new `bloommcp/src/bloom_mcp/sections/core/get_download_links.py`,
  `bloommcp/src/bloom_mcp/sections/core/__init__.py`,
  `bloommcp/tests/conftest.py`, `bloommcp/tests/test_storage_backend.py`,
  `bloommcp/docs/storage-backends.md`
- **Unaffected:** `manifest/schema.py` / `CURRENT_SCHEMA_VERSION` (stays at 5 — no
  schema change of any kind, per the Non-Goals above); `contract/provenance.py`;
  `StoredRun`'s dataclass fields; `create_signed_url`'s signature and both its
  adapters' bodies (only a new sibling helper, `get_object_size`, is added);
  `get_run`/`list_runs`/`list_existing_analyses` (still never populate `output_links` —
  unchanged non-goal); the 8 consumer tools (`qc_clean`, `qc_inspect`, `pca_analysis`,
  `remove_outliers`, `descriptive_stats`, `cross_experiment_correlations`,
  `umap_analysis`, `clustering`) — none of them call the new method, and their own
  `output_links` continue to come from `commit()` exactly as before;
  `ALWAYS_INCLUDE_MCP_TOOLS` (deliberately not touched — see the tool bullet above).
- **Dependencies:** none new — `storage3==2.31.0` (already pinned) implements
  `client.info()`, confirmed by reading the installed package; its exact response
  shape still needs empirical confirmation (task 1.1), not assumed.
- **Sequencing:** based directly on `origin/staging` (`47a94ba`, the #581/#595 merge
  commit) — `add-bloommcp-signed-url-key-scoping` (#598, PR #609) is **not yet merged**
  as of this writing (confirmed via `gh pr view 609`: state OPEN). This change does not
  depend on PR #609 landing in either order; see design.md for why its own key-scoping
  check is independent rather than reusing `build_output_links`'s (currently
  nonexistent on `staging`) `expected_prefix` param.
- **Closes #599.** Related: `#581`/`#595` (introduced `create_signed_url`/
  `output_links`/`OutputLink`, unarchived), `#598`/PR #609 (the write-side key-scoping
  guard this change's read-side check independently mirrors, also unarchived), `#600`
  (manifest.json's own signed-URL access — explicitly separate scope, same
  no-schema-change framing this change also holds to).
