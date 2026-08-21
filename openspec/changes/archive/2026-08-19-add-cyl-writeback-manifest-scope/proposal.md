## Why

`bloomctl cyl batch-ingest-result <envelopes_dir>`'s `discover_envelopes()`
(`bloomcli/src/bloomctl/cyl/ingest.py:74-85`) does a flat, unscoped glob —
`sorted(path.glob("*.result.json"))` — over `envelopes_dir`. Every `.result.json` file present
gets ingested, regardless of which pipeline run produced it. A leftover file from a stale or
concurrently-staging run sitting in the same shared directory would be silently (re)ingested into
Bloom's database.

This is the identical bug class already found and fixed at the two upstream stages of the same
pipeline chain:

- `sleap-roots-predict`'s `discover_scans` — fixed in
  [predict #35](https://github.com/talmolab/sleap-roots-predict/pull/35), merged 2026-08-18.
- `sleap-roots` (traits)'s `extract_batch` — fixed in
  [sleap-roots #263](https://github.com/talmolab/sleap-roots/pull/263), merged 2026-08-17. This
  fix also copies `run_manifest.json` forward into its own `output_dir` — which **is**
  `bloomctl`'s `envelopes_dir` (per this repo's own spec, `envelopes_dir` matches
  `trait_extractor.extractor.extract_batch`'s `output_dir` exactly, one `{scan_key}.result.json`
  per scan, no nesting). So once traits' pipeline image redeploys, the manifest this change reads
  will already be sitting where it needs to be, with no new mount or copy-forward step required
  from `write-back` itself.

Both fixes were tracked as gaps found during `sleap-roots-contracts`' adversarial review of the
`RunManifest` shape (`sleap-roots-pipeline` issue
[#37](https://github.com/talmolab/sleap-roots-pipeline/issues/37)). That review explicitly
identified `write-back` as needing the identical fix as a fifth step in the chain (contracts →
`bloomctl` images-downloader → predict → traits → **write-back**), tracked downstream as
[bloom #678](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/678). As of
2026-08-18 it is the only leg of that chain still open — `sleap-roots-contracts` (v0.1.0a7),
`bloomctl`'s manifest write/lock (bloom #653/#655), `sleap-roots-predict`, and `sleap-roots` are
all shipped.

## What Changes

- **`discover_envelopes()` becomes manifest-aware.** It checks for
  `envelopes_dir / RUN_MANIFEST_FILENAME` (the constant from `sleap_roots_contracts`, already a
  satisfied dependency — `bloomcli/pyproject.toml` already pins `sleap-roots-contracts>=0.1.0a7`
  since bloom #655; **no pin bump needed**). If present, it parses the file as a `RunManifest` and
  filters the glob results to only files whose filename stem (`{scan_key}` before the
  `.result.json` suffix) is in `manifest.scan_keys` — mirroring predict #35's and sleap-roots
  #263's filter-before-parse pattern exactly. If absent, discovery is fully unscoped, byte-for-byte
  identical to today's behavior (this also covers manual/dev CLI use with no manifest at all, and
  the current production state where traits' manifest-writing image hasn't redeployed yet — see
  bloom#685, unrelated and not addressed here).
- **A manifest-declared scan_key with no matching file becomes a reported batch failure**, not a
  silent gap — mirroring the same convention predict #35 and sleap-roots #263 both adopted. This
  flows through the existing `BatchResult`/exit-code machinery in `batch_ingest_result`
  (`ingest.py:652-692`) so the command already exits non-zero and names the missing scan, with no
  new reporting mechanism needed. (User-confirmed design decision — the alternative, silently
  omitting it, was considered and rejected: it would remove the only visibility into an incomplete
  write-back that Argo/monitoring gets.)
- **`discover_envelopes()`'s return type changes** from `list[Path]` to a small result carrying
  both the in-scope paths to ingest and any manifest-declared-but-missing scan_keys, since the
  command needs both to build the full `BatchResult` (see design.md). This function is internal
  (no callers outside this module and its tests), so this is not a breaking change to any public
  `bloomctl` interface.
- **Excluded (out-of-scope) files are logged at debug level**, once per invocation as a single
  aggregated record naming every excluded scan_key (not one record per file), matching predict
  #35's convention. Caveat, not a defect: `bloomcli` has no logging handler configured anywhere
  today (no `--verbose` flag, no `logging.basicConfig` call), so this line is currently visible
  under test (`caplog`) but not to an operator running the shipped CLI — see design.md's note.
  Adding a verbosity flag is out of scope for this pure-scoping change.
- **No sleap-roots-contracts pin bump.** Already satisfied.
- **No new locking.** The concurrency hazard on this shared directory (bloom #533) is already
  resolved upstream, at the images-downloader stage, by bloom #653/#655's lock primitive. This
  change does not touch `bloomcli/src/bloomctl/cyl/_locks.py` — it is pure discovery scoping, not
  concurrency.
- **No manifest copy-forward from write-back.** Write-back is the terminal stage of this chain —
  nothing downstream needs to read a manifest from it.
- **No skip-if-done / idempotency-key changes.** Write-back's idempotency is already handled
  server-side by the `insert_cyl_result_envelope` RPC's first-writer-wins `idempotency_key` check
  (see `cyl-trait-writeback` spec). This is a different bug class than directory scoping and is not
  part of this change, unlike sleap-roots #263 which *did* add local skip-if-done alongside its
  scoping fix.

## Impact

- **Affected code**:
  - `bloomcli/src/bloomctl/cyl/ingest.py` — `discover_envelopes()`'s implementation, return type,
    and docstring; `batch_ingest_result()`'s docstring (its `--help` text) and its assembly of the
    final `BatchResult` to include manifest-declared-missing failures without requiring an authed
    client when there is nothing else to ingest. Both land in one commit — they share the same
    file and contract, and splitting them would regress the ~12 existing `batch_ingest_result` CLI
    tests (see tasks.md).
  - `bloomcli/tests/test_cyl_ingest.py` — existing `discover_envelopes` tests
    (`test_cyl_ingest.py:938-967`) and the oracle test (`test_cyl_ingest.py:1305-1316`, which also
    calls `discover_envelopes` directly) updated for the new return shape; new tests for manifest
    presence/absence, scoping, and the missing-scan_key failure path; `batch_ingest_result` CLI
    wiring tests extended for the same.
  - `bloomcli/CHANGELOG.md` — `[Unreleased]` → `### Fixed` entry (this is a bug fix to already-
    shipped behavior, not a new capability), citing `(#678)` per this file's existing citation
    style.
  - `bloomcli/README.md` — the `batch-ingest-result` section's discovery-behavior bullet and its
    exit-code bullet both currently describe the unconditional pre-change behavior and need
    updating.
- **Capability extended**: `cyl-batch-ingest-result` gains two `ADDED` requirements
  (manifest-scoped discovery; missing-scan_key-is-a-failure) and `MODIFIED` updates to its two
  existing requirements ("...ingests every envelope in a directory" and "One envelope's failure is
  isolated, not fatal to the batch") — both pasted in full with a narrowing caveat added, per
  OpenSpec's convention for changing an existing requirement's scope rather than leaving that
  narrowing implicit in this proposal's prose alone.
- **No server/RPC/schema changes.**
- **Cross-repo follow-up (not part of this change's own tasks, done after merge is verified
  live):** update `sleap-roots-pipeline`'s `docs/bloom-integration/roadmap.md`
  ("Cross-repo correctness: manifest-scoped processing" table and status log) — this closes gap
  (2) of issue #37, the last open leg of that chain.
- **Tracking**: [bloom #678](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/678);
  closes gap (2) of [sleap-roots-pipeline #37](https://github.com/talmolab/sleap-roots-pipeline/issues/37).
  Explicitly not addressed here: `download-for-predict` concurrency
  ([bloom #652](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/652)); Phase 3
  status polling (separate issue); bloom#685's `contract_version` pin mismatch blocking traits'
  redeploy.
