## Why

`sleap-roots-contracts` 0.1.0a9 names the run manifest per run
(`run_manifest.<pipeline_run_id>.json`), so runs that share an output directory stop sharing one
manifest file. Sharing that file is what let a 1-scan Bloom request process 12 scans
(sleap-roots-pipeline#71). The downstream readers have already adopted a9: predict (roadmap row 4,
deployed 2026-09-25) and traits (row 2, deployed 2026-09-28). `bloomctl` is row 5. It ships the
manifest **writer** (`cyl batch-download-for-predict`) and the write-back **reader**
(`cyl batch-ingest-result`) in one image, so it flips last (bloom#934).

A rebuild alone would not adopt a9. `bloomcli/uv.lock` pins contracts at `0.1.0a7`, and the
Dockerfile runs `uv sync --frozen`.

## What Changes

The details and rationale are in `design.md`, Decisions 1–6.

- **Dependency:** raise the `sleap-roots-contracts` floor to `>=0.1.0a9` and re-lock.
- **Writer:**
  - It names the file `run_manifest_name_for_writing(pipeline_run_id_from_env())`, so Argo runs write per-run files and runs with no id keep writing `run_manifest.json`.
  - `local-<8 hex>` is only ever stamped inside the file, never used to name it.
  - A run id the contract rejects fails with exit `1` before authenticating or staging (empty input still exits `0`).
- **BREAKING, writer:** it overwrites its own file with this invocation's usable keys, and never reads or unions an existing manifest. That holds whether or not there is a run id. The manifest lock is kept.
- **Reader:** it resolves the manifest with `load_run_manifest(dir, pipeline_run_id_from_env(), allow_legacy=True)`, and logs a warning when it falls back to a legacy file written by another run.
- **BREAKING, reader:**
  - When a run id is set and no manifest exists, it fails loudly instead of ingesting every envelope. It still makes the reconciliation call before exiting `1`.
  - A per-run file naming another run, or an invalid run id, also fails loudly.
  - With no run id, behavior is unchanged.
- **Specs:**
  - Update `cyl-batch-download-for-predict`, `cyl-batch-ingest-result`, `cyl-pipeline-runs` and `cyl-pipeline-status-polling`.
  - This change takes over the two requirements it shares with the unarchived `fix-cyl-pipeline-run-scan-status`, and that change's one stale delta (`cyl-trait-writeback`) is rebuilt. Each requirement is then modified by exactly one active change.
- **Docs:** `bloomcli/README.md`, `bloomcli/CHANGELOG.md`, `contracts/README.md`, and the `--help`/docstring/log text in both commands.

## Non-Goals

- Roadmap row 6, in sleap-roots-pipeline: the template pin bumps, `argo template update`, deleting the stale `run_manifest.json` files, and the live end-to-end test.
- `allow_legacy=False` (sleap-roots-pipeline#82).
- Removing the manifest lock (bloom#894).
- GC of accumulated per-run files.
- Adopting a9 in `bloommcp` or `services/workflows`.
- Stripping whitespace from `p_argo_workflow_name` (design Decision 3).
- Archiving. Both changes archive together after row 6 verifies them.

## Impact

- **Affected specs:**
  - `cyl-batch-download-for-predict`: the RunManifest write and the manifest lock.
  - `cyl-batch-ingest-result`: the batch command, isolation, discovery, and missing scan_keys.
  - `cyl-pipeline-runs`: "`cyl_pipeline_runs` table".
  - `cyl-pipeline-status-polling`: "A `'complete'` rollup does not imply every scan produced a result".
  - `fix-cyl-pipeline-run-scan-status`: its deltas are repaired, and its `proposal.md` and `tasks.md` are annotated.
- **Affected code (`bloomcli/` only):**
  - `src/bloomctl/cyl/download_for_predict.py`, `src/bloomctl/cyl/ingest.py`, and the comment in `src/bloomctl/cyl/_locks.py`.
  - `pyproject.toml` and `uv.lock`.
  - `tests/test_cyl_download_for_predict.py`, `test_cyl_ingest.py` and `test_contracts_pin.py`.
- **Affected docs:** `bloomcli/README.md`, `bloomcli/CHANGELOG.md`, `contracts/README.md`.
- **Deployment:**
  - At merge, `:staging` moves to the new image. That affects only manual `docker run` users.
  - The pipeline switches only when row 6 re-pins its three templates. That bump also ships every bloomcli change merged since `sha-28034f6`: #880, #882 (a lock-only anyio bump), #884 and #861.
  - The rollback target is `sha-28034f6`. Restoring it means re-pinning the templates; reverting this PR is not enough.
