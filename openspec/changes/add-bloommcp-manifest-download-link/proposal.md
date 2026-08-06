## Why

`get_download_links` (#599) re-signs fresh download links for an already-committed run's
declared outputs — but never for the run's own `manifest.json`. `RunLinks.manifest_path` /
`StoredRun.manifest_path` is (and remains, after #599) a raw, unsigned storage key string on
every tool result and on `get_download_links`'s own response. A user or agent wanting to
verify a run's provenance (exact params, environment/library pins, seed, per-output sha256 —
everything recorded in the manifest) has no way to actually fetch and read it themselves;
direct Supabase Storage/admin access is required today. This is `#600`, filed as one of three
non-blocking follow-ups from the #581/#595 post-PR review, alongside #598 (closed via #609)
and #599 (this change's own prerequisite).

## What Changes

- Add a `manifest_url: Optional[str]` field to `StoredRun` (`result_store/ports.py`),
  populated only by `ResultStore.get_download_links` — mirroring exactly how `output_links`
  is populated only by that method (never by `get_run`/`list_runs`, which leave it at its
  default). `create_run`/`commit`/`get_run`/`list_runs` are entirely untouched; see
  design.md's Decision 1 for why commit-time signing (the alternative that would put
  `manifest_url` on every tool's own immediate response, mirroring `output_links`'s
  `commit()`-time population) is deliberately not pursued here.
- Extend `ResultStore.get_download_links` (both adapters, `SupabaseResultStore` and
  `FakeResultStore`) to also sign the resolved run's `manifest_path` — one more
  `create_signed_url` call, reusing the existing `SIGNED_URL_EXPIRES_SECONDS` constant, no new
  `StorageBackend` primitive — and attach the result as `manifest_url`. Unlike each output's
  `create_signed_url` call, this one needs no `CorruptRunLinksError`-style key-scope guard:
  `manifest_path` is always deterministically recomputed by the adapter itself
  (`f"{adir.path}manifest.json"` / the fake's equivalent), never read back from the manifest's
  own JSON content the way `output_keys` is — there is no "corrupt manifest points at a
  foreign key" vector for this particular key to guard against (see design.md Decision 2).
- Extend the `get_download_links` MCP tool's JSON response
  (`sections/core/get_download_links.py`) with a `manifest_url` field. Note: the tool's
  current response does not include a raw `manifest_path` key at all (only `experiment`,
  `tool_class`, `run_ref`, `version_dir`, `outputs`, `output_links`) — `manifest_url` is a
  wholly new key on this response, not a signed sibling of an existing unsigned one (unlike
  `RunLinks.manifest_path`, which every *consumer tool's own* result carries unsigned today,
  per the Why section above; this tool's response is a different, narrower shape).
- Update `bloommcp/docs/storage-backends.md`'s `get_download_links` section to mention
  `manifest_url` (see design.md / tasks.md task 3.1 for the three specific edits this
  requires, not a single generic mention).

## Non-Goals

- **Signing `manifest_url` at `commit()` time**, i.e. adding it to every consumer tool's own
  immediate `RunLinks`-shaped response the same way `output_links` is populated at commit.
  `write_manifest` must run before a manifest key can be meaningfully signed (the object must
  exist), which — unlike output-signing, which happens strictly *before* the manifest write
  and can cleanly abort an as-yet-uncommitted run on failure — would put `manifest_url`'s
  own signing *after* the point `commit()` is already effectively durable (`latest` advanced).
  A signing failure at that point is not a commit failure and needs its own partial-success
  shape (a committed run with `manifest_url: None` plus a surfaced warning) that this change
  does not attempt to design. See design.md Decision 1. A caller who wants the manifest link
  immediately after a tool call already has `run_ref` from that same response and can call
  `get_download_links` in the same session — one extra call, not a missing capability.
- **A dedicated new tool** (the issue's own alternative-B, "a small `get_manifest_url(run_ref)`
  -style tool"). `get_download_links` already is exactly that shape of tool
  ("caller-opted-in, already-committed-run link fetch"); a second, near-duplicate tool would
  fragment the read-path surface for no behavioral benefit.
- **`sha256`/`size_bytes` for the manifest**, or any `OutputLink`-shaped object for it. The
  issue's own acceptance criteria ask only for "a working download link" — not a hash or byte
  count — and the manifest has no persisted `manifest_sha256` field for any such hash to come
  from (unlike each output's `output_sha256`).
- **Any manifest, `Provenance`, or `VersionEntry` schema change of any kind** — identical
  framing to #599/#581. `manifest_url` is resolved fresh on every `get_download_links` call,
  never persisted.
- **A new `StorageBackend` primitive.** Reuses `create_signed_url` verbatim — the manifest key
  is a plain object key like any other; no `get_object_size`-style addition is needed since
  size isn't in scope (see above).
- **A key-scope guard for `manifest_path`** analogous to `CorruptRunLinksError`'s guard on
  `output_keys`. See design.md Decision 2 for why this key has no equivalent corruption
  vector.

## Impact

- **Affected specs:** `bloommcp-result-store`, `bloommcp-get-download-links-tool`
- **Affected code:** `bloommcp/src/bloom_mcp/result_store/ports.py`,
  `bloommcp/src/bloom_mcp/result_store/supabase_store.py`,
  `bloommcp/src/bloom_mcp/result_store/fake_store.py`,
  `bloommcp/src/bloom_mcp/sections/core/get_download_links.py`,
  `bloommcp/docs/storage-backends.md`
- **Unaffected:** `manifest/schema.py` / `CURRENT_SCHEMA_VERSION`; `contract/provenance.py`;
  `contract/models.py`'s `RunLinks`/`OutputLink` (no change — this stays a `get_download_links`
  -only field on `StoredRun`, not a `RunLinks` field every consumer tool inherits);
  `storage_backend.py` (no new primitive); the 8 consumer tools (`qc_clean`, `qc_inspect`,
  `pca_analysis`, `remove_outliers`, `descriptive_stats`, `cross_experiment_correlations`,
  `umap_analysis`, `clustering`) and their own `commit()`-time responses — none of them call
  `get_download_links`, and their own `manifest_path` stays unsigned exactly as today.
- **Dependencies:** none new.
- **Sequencing:** branch cut from `egao28/bloommcp-get-download-links-599` (PR #611, not yet
  merged into `staging`), not `origin/staging` directly — `get_download_links`
  (`ResultStore` method, MCP tool, `StoredRun.output_links`) does not exist on `staging` yet,
  and this change is a direct extension of it. **This change must not be archived
  independently of `add-bloommcp-get-download-links`** (#599's own change), and — per that
  change's own note — nor of `add-bloommcp-signed-url-download` (#581/#595) or
  `add-bloommcp-signed-url-key-scoping` (#598), all still unarchived as of this writing.
- **Closes #600.**
