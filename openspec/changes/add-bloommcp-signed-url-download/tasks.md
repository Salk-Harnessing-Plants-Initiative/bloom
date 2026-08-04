## 1. Storage backend primitive

- [ ] 1.1 Confirmed already (no action needed at implementation time — recorded here so a
      reviewer doesn't treat it as an open risk): the pinned `storage3==2.31.0` (verified by
      reading `bloommcp/.venv/lib/python3.11/site-packages/storage3/_sync/file_api.py` directly)
      already implements both `create_signed_url(path, expires_in, options=None)` and a batch
      `create_signed_urls(paths, expires_in, options=None)`. No `pyproject.toml`/`uv.lock` change,
      no `pip-audit` re-run needed for this. Its `create_signed_url` returns a `dict`
      (`{"signedURL": ..., "signedUrl": ...}` — key casing has drifted across versions per
      `services/workflows/video.py`'s own comment), not a bare string — task 1.3 extracts it.
- [ ] 1.2 Write `bloommcp/tests/test_storage_backend.py` tests first (red against today's code):
      - `SupabaseStorageBackend.create_signed_url(key, expires_in)` calls through to the storage
        client's signed-url method with the given key/expiry, extracts the URL from a mocked
        dict response (test both `signedURL` and `signedUrl` key casings), and returns it.
      - A mocked response carrying none of the expected URL keys raises (does not return `None`
        or an empty string).
      - The extracted URL is rewritten from the internal `SUPABASE_URL` host to
        `BLOOM_PUBLIC_SUPABASE_URL` when both are set and the URL is on the internal host; is
        returned unchanged when either is unset, or when the URL isn't on the internal host.
      - `LocalStorageBackend.create_signed_url(key, expires_in)` returns
        `f"{BLOOM_STORAGE_URL.rstrip('/')}/{key}"` (env var set, including the boundary case
        where `BLOOM_STORAGE_URL` itself ends in `/` — assert no doubled slash), ignores
        `expires_in`, and raises a redacted (no absolute host path) error when
        `BLOOM_STORAGE_URL` is unset.
      - `StorageBackend` Protocol: `create_signed_url` is part of the `runtime_checkable`
        interface (both concrete backends satisfy `isinstance(..., StorageBackend)`).
- [ ] 1.3 Implement `create_signed_url(key: str, expires_in: int) -> str` on the `StorageBackend`
      Protocol, `SupabaseStorageBackend` (client call → dict-key extraction → internal-host
      rewrite via a small `_to_public_url`-shaped helper reading `SUPABASE_URL` /
      `BLOOM_PUBLIC_SUPABASE_URL`), and `LocalStorageBackend` (`BLOOM_STORAGE_URL`-based served
      URL, `rstrip('/')`) in `storage_backend.py`. Confirm 1.2 is green.
- [ ] 1.4 Add the seventh `bloom_mcp.supabase_client.create_signed_url(key, expires_in)`
      re-export, mirroring the existing six (lazy `active_backend()` import + delegate). Add a
      matching test in this module's existing test file alongside the other six.
- [ ] 1.5 Add `create_signed_url` to `bloommcp/tests/conftest.py`'s `_InMemoryObjectStore` (a
      synthesized string, e.g. `f"fake://signed/{key}?expires_in={expires_in}"`) and to the
      `fake_supabase_storage` fixture's monkeypatch tuple. Without this, every existing test that
      exercises the **real** `SupabaseResultStore` through this fixture (`test_store_parity.py`,
      `test_supabase_result_store.py`, and the `test_*_tool.py` files that persist through it)
      would fall through to the real, unconfigured Supabase client the moment `commit()` starts
      calling `create_signed_url` (task 2.4) and raise, since this fixture's module explicitly
      pops `SUPABASE_URL`/`BLOOM_AGENT_KEY`.

## 2. Result-store plumbing

**Note on ordering:** 2.1 (tests), 2.3 (`hash_outputs` signature change), and 2.4 (both call
sites + `OutputLink` construction) are inseparable — landing 2.3 alone changes `hash_outputs`
from a 2-tuple to a 3-tuple return while `SupabaseResultStore.commit`/`FakeResultStore.commit`
still unpack it as a 2-tuple, which raises `ValueError: too many values to unpack` on **every**
call to either — not a test-assertion failure but a hard exception that breaks nearly the entire
`bloommcp` unit suite (`FakeResultStore` backs most of it). 2.1/2.3/2.4 ship as **one commit**;
there is no intermediate green state to split across (mirrors the `#419` precedent).

- [ ] 2.1 Write `bloommcp/tests/result_store/` tests first (red):
      - `hash_outputs` returns `(output_keys, output_sha256, output_size_bytes)`, where
        `output_size_bytes[name] == len(staged bytes)` — including a zero-byte staged artifact
        (legal; `validate_outputs` only rejects an empty *outputs dict*, not a zero-byte file).
      - `SupabaseResultStore.commit(...)` returns a `StoredRun.output_links` dict keyed
        identically to `outputs`, each `OutputLink` carrying the right `key`/`sha256`/
        `size_bytes` and a URL sourced from the (mocked, via 1.5's fixture) signed-url call.
      - `SupabaseResultStore.commit(...)`: a signed-url generation failure — including a mocked
        response with no extractable URL key — for any one output raises `CommitFailedError` and
        best-effort-cleans-up already-uploaded keys, mirroring the existing upload-failure test.
      - `FakeResultStore.commit(...)` returns `StoredRun.output_links` with the synthesized
        `fake://signed/...` URL shape and real `sha256`/`size_bytes`.
      - `get_run`/`list_runs` on both adapters: `output_links == {}` — including when resolving a
        legacy v2-manifest-sourced entry (via `seed_v2_run`/the real v2 fixture), not only a
        freshly committed one. Add this as an explicit regression test so a future change can't
        silently start eagerly signing here.
      - `test_store_parity.py`: extend the existing fake/real parity assertions to cover
        `output_links`'s *shape* (same output names, same sha256/size_bytes — URLs will
        legitimately differ, `fake://...` vs. a real/served URL, so assert that difference is
        the *only* expected divergence).
      - Extend (or add to) the existing manifest byte-parity test
        (`test_manifest_identical_across_backends_except_storage_backend`-style precedent in
        `test_storage_backend.py`) to assert the written `manifest.json`/`VersionEntry` for a run
        with populated `output_links` has no new `output_links`/URL/size key and is otherwise
        byte-identical to a pre-change golden/fixture manifest for the same inputs.
      - `bloommcp/tests/tools/test_list_existing_analyses_staleness.py` (or wherever
        `list_existing_analyses` is tested): a regression test asserting its `json.dumps()`
        output stays valid and `output_links` stays absent/`{}` for every listed run — guarding
        against a future Decision-1 regression, since `OutputLink` is a Pydantic model (not a
        dataclass) and `dataclasses.asdict()` would not flatten it, so `json.dumps()` would raise
        `TypeError` if `output_links` were ever non-empty on a `list_runs`-sourced `StoredRun`.
- [ ] 2.2 Add `OutputLink` (`key: str`, `url: str`, `sha256: str`, `size_bytes: int`) to
      `bloom_mcp/contract/models.py`; add `output_links: dict[str, OutputLink] =
      Field(default_factory=dict)` to `RunLinks` (additive — `outputs` untouched). Re-export
      `OutputLink` from `bloom_mcp/contract/__init__.py` alongside `RunLinks`.
- [ ] 2.3 Extend `hash_outputs` (`_artifacts.py`) to also return `output_size_bytes`. Add
      `output_links: dict[str, "OutputLink"] = field(default_factory=dict)` to `StoredRun`
      (`ports.py`), importing `OutputLink` under `TYPE_CHECKING` only (matching this file's
      existing `Provenance`/`SourceInfo`/`VersionEntry` import style — `from __future__ import
      annotations` is already active, so no runtime import is needed).
- [ ] 2.4 Add the module constant `_SIGNED_URL_EXPIRES_SECONDS = 3600` and implement the
      per-output `OutputLink` construction in `SupabaseResultStore.commit` (calling
      `_sc.create_signed_url` per output key; a signing/extraction failure fails the whole
      commit, same as an upload failure) and `FakeResultStore.commit` (synthesized URL, no real
      backend call). Attach via `dataclasses.replace(stored, output_links=links)` after
      `StoredRun.from_version_entry`. Confirm 2.1 is green.

## 3. Wire into consumer-tool results

- [ ] 3.1 For each of the 8 tools' existing test files, add (or extend an existing golden/
      characterization test with) an assertion that the result's `output_links` dict has one
      entry per `outputs` entry, with a non-empty `url`, the same `sha256` as the manifest's
      `output_sha256`, and a non-negative `size_bytes`. Prefer extending an existing
      successful-run test over adding a new one per tool. Confirm these fail red (the field
      doesn't exist on the result models yet).
- [ ] 3.2 For each of the 8 tools (`qc_clean.py`, `qc_inspect.py`, `pca_analysis.py`,
      `remove_outliers.py`, `descriptive_stats.py`, `cross_experiment_correlations.py`,
      `umap_analysis.py`, `clustering.py`): add `output_links=stored.output_links` at the
      existing `outputs=dict(stored.output_keys)` call site. No other change to any tool's
      logic. Confirm 3.1 is green.
- [ ] 3.3 Add at least one `live_smoke`-marked assertion (in whichever of
      `bloommcp/tests/smoke/*.py` already exercises a real MCP round trip, e.g.
      `test_pca_analysis_smoke.py`, which already carries a regression pin for a prior
      `RunLinks.outputs` live-transport bug) that `output_links` — a *more* deeply nested
      structure (`dict[str, OutputLink]` vs. that field's plain `dict[str, str]`) — survives a
      real MCP/fastmcp transport round trip with the expected shape. Mark it so it's excluded
      from the default `-m "not integration and not live_smoke"` CI run, consistent with this
      suite's existing markers.
- [ ] 3.4 Run `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and
      not live_smoke"` (the same invocation `pr-checks.yml`'s `python-audit`/test job uses) and
      confirm green. Separately note in the PR description that `live_smoke`-marked tests
      (including 3.3's new one) need a live dev stack to actually run, per this repo's existing
      convention for that marker.

## 4. Env var wiring, threshold decision, and docs

- [ ] 4.1 Add `BLOOM_STORAGE_URL: ${BLOOM_STORAGE_URL:-}` to `docker-compose.dev.yml`'s
      `bloommcp` service environment block (dev/local-backend-only — no equivalent needed in
      `docker-compose.prod.yml`, which never sets `BLOOM_STORAGE_BACKEND=local`).
- [ ] 4.2 Add `BLOOM_PUBLIC_SUPABASE_URL: ${NEXT_PUBLIC_SUPABASE_URL}` to `bloommcp`'s
      environment block in **both** `docker-compose.dev.yml` and `docker-compose.prod.yml`,
      with a one-line comment mirroring the `workflows` service's existing identical entry in
      both files ("Public base for rewriting signed URLs off the internal kong host so
      `output_links` URLs are usable by outside callers"). Without this, every signed URL
      bloommcp returns in prod/staging points at the internal Docker host and is unreachable.
- [ ] 4.3 Update `bloommcp/docs/storage-backends.md`: add a new section on `create_signed_url` /
      `output_links` / `BLOOM_STORAGE_URL` / `BLOOM_PUBLIC_SUPABASE_URL`, naming the code
      constant (`_SIGNED_URL_EXPIRES_SECONDS`) for the 3600s expiry rather than restating the
      number, and explicitly flagging the 100 KB inline-vs-link threshold as documentation-only
      guidance (no tool changes its response shape based on it). Add a caveat to the existing
      "Same output semantics as Supabase" bullet: a Supabase-backed signed URL always resolves;
      the local backend's constructed URL only resolves if an operator has separately configured
      something to serve `BLOOM_STORAGE_URL`'s root. This file does **not** currently document
      `BLOOM_PLOTS_URL` at all (confirmed — zero occurrences), so this section is the first
      URL-serving documentation in this file, not a mirror of an existing one.
- [ ] 4.4 Update this file's existing forward-reference ("this reshapes the same
      `supabase_client.py` storage boundary that #388... will build signed-URL downloads on") to
      reflect that this piece has landed, with #388's remaining upload/explorer thirds pending.
- [ ] 4.5 Update `bloommcp/docs/roadmap.md`'s Deferred-line entry for "#388 Part 2 — download
      outputs / signed URLs" to reflect it shipping. (`bloommcp/docs/data-access-roadmap.md` is
      the unrelated DB-direct-trait-read program's roadmap — confirmed it has no entry for this
      work; no update needed there.)
- [ ] 4.6 Update each of the 8 tools' `Result` model docstrings/field descriptions to mention
      `output_links` alongside the existing `outputs` description.

## 5. Spec

- [ ] 5.1 `openspec validate add-bloommcp-signed-url-download --strict` passes.
