## Why

`bloomctl cyl batch-download-for-predict` stages scans into a shared `out_dir` that
`sleap-roots-predict` will eventually consume, but two correctness gaps block that consumer from
being safely built (bloom #653):

1. Nothing records **which** scans a given staging pass produced. `sleap_roots_contracts.RunManifest`
   (`pipeline_run_id: str`, `scan_keys: list[str]`), released in contracts v0.1.0a7 (contracts PR
   #30), exists to fill exactly this gap, but nothing writes it yet.
2. The batch command's skip-check has no lock/lease — [bloom
   #533](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/533), filed
   2026-07-27 during the sibling `add-cyl-batch-commands` proposal and deliberately deferred
   there ("the real fix belongs with the not-yet-built dispatch worker... not a bolted-on
   bloomctl-side file lock"). Two invocations targeting the same `scan_id` (e.g. a stale Argo
   retry pod and a fresh one) can both pass `scan_is_already_staged` and then race on
   `clear_scan_dir` + the frame/sidecar writes, corrupting each other's output.

Both gaps are load-bearing for the pipeline's actual architecture, not hypothetical: per
`sleap-roots-pipeline`'s own docs (`docs/bloom-integration/roadmap.md`,
`docs/superpowers/specs/2026-07-06-a4-request-driven-pipeline-design.md` §6/§9), a large request
is chunked into multiple batches, **each becoming its own Argo workflow / `bloomctl` invocation**,
all targeting the **same fixed `out_dir`** (a shared hostPath, deliberately not per-run —
cluster-side dedup in `sleap-roots-predict` depends on that path being shared). The design
explicitly allows up to `K` such batches to run concurrently (a semaphore, not a mutex — see
design.md). A downstream consumer reading a manifest to know what's in `out_dir` needs that
manifest to reflect everything staged there across every invocation, not just the last one; and
concurrent invocations against that same shared directory need to not corrupt each other.

This proposal closes both gaps in `batch-download-for-predict`, the one command that needs them
today. It does **not** implement the Argo-side `ARGO_WORKFLOW_NAME` wiring that will eventually
give this a real `pipeline_run_id` (tracked separately as `sleap-roots-pipeline` #38), and does
not touch anything downstream that will one day consume the manifest (separate repos/PRs).

## What Changes

- **Bump the `sleap-roots-contracts` pin.** `bloomcli/pyproject.toml`'s floor moves from
  `>=0.1.0a5` to `>=0.1.0a7` (`RunManifest` ships in a7); `bloomcli/uv.lock` is regenerated to
  match.

- **New shared lock/lease primitive** (`bloomcli/src/bloomctl/cyl/_locks.py`): a file-based
  advisory lock — atomic acquire (`os.open(..., O_CREAT | O_EXCL)`, never `os.replace`, which
  would silently clobber a live lock), a recorded acquisition timestamp for staleness detection,
  and a one-shot reclaim of a lock whose age exceeds a configurable staleness threshold. Exposed
  as a context manager that raises a `LockContendedError` (naming the current holder's pid and
  lock age) when the lock is held and not stale, and always removes the lock file on exit. This
  is deliberately a small, reusable primitive — not specific to this command — since it is the
  first concrete implementation of [bloom
  #481](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/481)'s deferred
  cross-command lock design.

- **Deliberate departure from #653's own literal wording, not incidental scope-matching.** Issue
  #653's own "Scope" text (and a matching clause in `sleap-roots-pipeline`'s roadmap) describes the
  intended mechanism as one lock, held from before the skip-check through the manifest write, for
  the whole invocation. This proposal does **not** build that — it uses a narrower per-scan lock
  plus a separate manifest lock instead, because a single invocation-spanning lock would silently
  serialize the pipeline's own designed-for concurrent chunked batches (see design.md's "Lock
  granularity" decision for the full reasoning and citations). This is called out explicitly here,
  not just in design.md, since an approver reading only this file should know the lock shape is a
  considered deviation from the issue's literal ask, not an oversight.

- **Per-scan lock, wired into `stage_one_scan`.** Before a scan's `scan_is_already_staged` check
  through its `write_sidecar` call, `stage_one_scan` holds a lock at `out_dir/.locks/{scan_key}.lock`
  — a location deliberately **outside** `out_dir/{scan_key}/` itself, so `clear_scan_dir`'s
  `shutil.rmtree` can never delete the lock file out from under its own holder (see design.md).
  Two invocations targeting the *same* `scan_id` at the same time — the literal bloom #533
  scenario — now can't both pass the skip-check. Two invocations targeting *different,
  non-overlapping* `scan_id`s (the normal case for concurrent chunked batches per the architecture
  above) never contend at all, since they lock different files. Lock contention is reported as an
  ordinary per-scan failure (`ScanResult(scan_key, "failed", ...)`) — it does not abort the rest
  of the batch, consistent with every other per-scan failure mode this command already isolates.

- **`RunManifest` write + merge, once per invocation.** After every scan in the batch is
  processed, `batch_download_for_predict` computes this invocation's `scan_keys` as every scan
  whose result was `ok` or `skipped` (a scan that failed this run is excluded — it isn't usable
  output). `pipeline_run_id` is `ARGO_WORKFLOW_NAME` from the environment if set, else a freshly
  generated `local-<8 hex chars>` placeholder — so the command never hard-fails outside Argo
  (`ARGO_WORKFLOW_NAME` wiring is `sleap-roots-pipeline` #38, separate, not implemented here).
  Under a second, separate lock at `out_dir/.locks/manifest.lock`, the command reads any existing
  `RunManifest` at `out_dir / RUN_MANIFEST_FILENAME` (the filename constant imported from
  `sleap_roots_contracts`, currently `"run_manifest.json"` — never a bloomctl-local literal, so a
  downstream consumer reading via the same constant actually finds it), **merges** (`scan_keys` = union of the
  existing set and this invocation's; `pipeline_run_id` = this invocation's — last-writer-wins),
  and writes the result back atomically. Merge (not overwrite) is required by the chunking
  architecture described above: an overwrite would silently drop the scan_keys of every earlier
  invocation into the same shared `out_dir` whose scan_id subset didn't overlap with this one.

- **`--lock-staleness-seconds` CLI option** on `batch-download-for-predict` (default `900`,
  `show_default=True`) — the one threshold both locks use.

- **Explicitly out of scope** (do not touch in this change):
  - `download-for-predict` (the single-scan command) — no lock, no manifest write. The issue
    frames this as `batch-download-for-predict`'s gap specifically.
  - Making `batch-download-for-predict`'s own frame-fetch loop concurrent/multi-worker — that's
    [bloom #652](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/652), a
    separate, deliberately-decoupled performance change (precedent: PR #623 added an 8-worker
    pool to the sibling `cyl download` command, explicitly not touching this one).
  - `sleap-roots-predict`/`sleap-roots` actually consuming the manifest — separate repos, separate
    PRs, unblocked once this lands.
  - The Argo `WorkflowTemplate` wiring that sets `ARGO_WORKFLOW_NAME` (`sleap-roots-pipeline` #38)
    — this proposal only reads the env var if it happens to already be present.

## Impact

- **Affected code**:
  - `bloomcli/src/bloomctl/cyl/download_for_predict.py` — `stage_one_scan` gains the per-scan
    lock; `batch_download_for_predict` gains the manifest write/merge and the new CLI option.
  - `bloomcli/src/bloomctl/cyl/_locks.py` (new) — the shared lock/lease primitive.
  - `bloomcli/pyproject.toml`, `bloomcli/uv.lock` — dependency pin bump.
  - `bloomcli/CHANGELOG.md` — `[Unreleased]` entry.
  - `bloomcli/README.md` — document the manifest file, the `.locks/` files, and
    `--lock-staleness-seconds`.
- **Capability extended**: `cyl-batch-download-for-predict` gains manifest write + merge, per-scan
  lock/lease, manifest lock/lease, and configurable stale-lock reclaim (`ADDED` requirements). The
  existing "One scan's failure is isolated, not fatal to the batch" requirement is `MODIFIED` to
  add lock contention to its enumerated per-scan failure causes — its two existing scenarios are
  otherwise unchanged.
- **No server/RPC/schema changes.**
- **Cross-repo follow-up (not part of this change's own tasks):** once merged, tick the
  `bloomctl` row in `sleap-roots-pipeline`'s `docs/bloom-integration/roadmap.md`
  ("Cross-repo correctness: manifest-scoped processing" section) per that repo's own
  close-the-loop convention.
- **Tracking**: [bloom #653](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/653)
  (this proposal implements it in full except the `ARGO_WORKFLOW_NAME` env var wiring, which is
  `sleap-roots-pipeline` #38 — a separate repo's work); resolves [bloom
  #533](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/533); first concrete
  implementation of [bloom #481](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/481)'s
  deferred cross-command lock design. Context, not implemented here: [bloom
  #652](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/652), `sleap-roots-pipeline`
  #37 (cross-repo idempotency tracker).
