## Context

Read in full before this design: `bloomcli/src/bloomctl/cyl/download_for_predict.py`,
`bloomcli/src/bloomctl/cyl/download.py` (for its existing `.bloomctl-download.json`
manifest/`atomic_write_bytes` precedent — a different, unrelated manifest for a different
command, not touched by this change), `bloomcli/src/bloomctl/cyl/_batch.py`,
`sleap-roots-contracts`'s `src/sleap_roots_contracts/run_manifest.py` (the `RunManifest` shape,
its `_check_scan_keys` validator, and the `RUN_MANIFEST_FILENAME` constant this design imports
rather than re-literals — see "Manifest filename" below), and `sleap-roots-pipeline`'s
`docs/superpowers/specs/2026-08-03-manifest-scoped-processing-redesign.md` (why the manifest is
scoped to the shared directory, not per-run path isolation) and
`docs/superpowers/specs/2026-07-06-a4-request-driven-pipeline-design.md` §6/§9 (execution
topology and concurrency control — the source of the lock-granularity decision below).

## Goals / Non-Goals

**Goals:**
- Give a downstream consumer a manifest it can read to know which scans are usable in `out_dir`,
  correct even when multiple invocations have staged into that directory over time.
- Close bloom #533's actual race (two invocations racing on the *same* scan_id's skip-check) without
  serializing invocations that are legitimately working on *disjoint* scan_ids, since the
  pipeline's own concurrency design (see below) depends on that.
- A crashed process must not permanently wedge `out_dir` for future invocations.
- Never hard-fail outside Argo (manual/dev runs must still work).

**Non-Goals:**
- `download-for-predict` (single-scan command), `sleap-roots-predict`/`sleap-roots` consuming the
  manifest, the `ARGO_WORKFLOW_NAME` Argo-side wiring (`sleap-roots-pipeline` #38), and
  `batch-download-for-predict`'s own frame-fetch concurrency (bloom #652) — see proposal.md's
  "Explicitly out of scope."
- A distributed-lock-service-grade guarantee. This is a single-host (single Argo pod / single dev
  machine) advisory file lock. See "Accepted limitation" below.

## Decisions

### Lock granularity: per-scan + a separate manifest lock, not one invocation-spanning lock

Bloom issue #653's own "Scope" text describes the intended mechanism as "an atomic lock-file
created before the skip-check runs, released after the manifest write" — one lock spanning the
whole invocation. `sleap-roots-pipeline`'s roadmap contains a matching clause ("lock scopes the
invocation, workers inside stay independent") — worth noting where it actually appears: in a
2026-08-12 status-log entry contrasting this proposal's lock work against the separate, still
hypothetical frame-fetch worker-pool concurrency (bloom #652), not in a passage analyzing the
K-concurrent-batch semaphore (§9) directly. It is nonetheless a real, on-topic characterization of
this lock's intended scope, not a misquote — and reading it plainly, both the issue and the roadmap
describe a single lock held for an entire `batch-download-for-predict` call. That reading was
rejected here, and it's worth recording why, because it directly contradicts something else the
same pipeline's design explicitly commits to:

- `docs/superpowers/specs/2026-07-06-a4-request-driven-pipeline-design.md` §6 ("Execution
  topology"): one Argo workflow per **batch** (not per scan) — a large request is chunked into
  `⌈N/BATCH_SIZE⌉` batches, each its own workflow, all targeting the same shared `out_dir`
  (`images-input-dir`, a fixed hostPath — deliberately not per-run, per the manifest-scoped-processing
  redesign doc: isolating it "would make every run start from an empty directory — defeating
  reuse entirely").
- §9 ("Concurrency & resource control"): an Argo semaphore permits up to `K` batches active at
  once — "**a mutex would serialize to 1**," explicitly rejected as the concurrency model.

If `batch-download-for-predict` took one lock at the top of the command and held it through every
scan's staging work to the final manifest write, then `K` concurrent chunk-batches against the
same `out_dir` could not actually run concurrently — chunk 2 would block on its very first
skip-check until chunk 1's entire multi-scan run finished, reintroducing exactly the mutex-style
serialization §9 rejects. (This concurrency is not yet exercised in production — per the roadmap's
own 2026-07-30 status log, "no caller submits concurrently yet" — but the design commits to it,
and a whole-invocation lock would silently defeat it the first time something does submit
concurrently.)

The actual race bloom #533 names is narrower than "the whole invocation": *"`scan_is_already_staged`
→ `clear_scan_dir` has no lock/lease; a stale Argo retry pod and a fresh one could both pass the
skip check and clobber each other's in-progress writes"* — a race between two invocations working
on the **same scan_id**, not a race that spans a whole batch of disjoint scan_ids. So this proposal
uses:

1. A **per-scan lock** (`out_dir/.locks/{scan_key}.lock`), held only around that one scan's
   `scan_is_already_staged` → `clear_scan_dir` → download → `write_sidecar` sequence inside
   `stage_one_scan`. Two invocations racing on the *same* scan_id now can't both pass the
   skip-check (closes #533 exactly as described). Two invocations working disjoint scan_ids never
   contend — they lock different files — so `K` concurrent chunk-batches stay concurrent.
2. A separate, much shorter-lived **manifest lock** (`out_dir/.locks/manifest.lock`), held only
   around the final `RunManifest` read-merge-write — a small JSON file, milliseconds of work.
   Concurrent chunk-batches finishing around the same time briefly contend here, but the
   contention window is negligible next to the minutes-to-hours a batch's actual staging work
   takes.

This does mean lock contention on a scan is reported as a per-scan `failed` result rather than the
whole command failing fast — a better fit for this command's existing per-scan isolation model
than a batch-wide abort would have been, and a natural consequence of narrowing the lock's scope.

### Lock file location: outside `{scan_key}/`, not inside it

An initial sketch put the per-scan lock file inside `out_dir/{scan_key}/` itself (alongside the
frames and sidecar, the natural-looking place). Rejected: `clear_scan_dir` does
`shutil.rmtree(scan_dir)` — if the lock file lived inside `scan_dir`, the holder's own
clear-and-redownload step would delete the on-disk evidence of its own still-held lock. A second
invocation arriving mid-critical-section would then see no lock file at all, conclude the scan is
free, and start its own skip-check + clear + download concurrently — reintroducing precisely the
#533 race the lock exists to prevent, and doing so *silently* (no error, just silent corruption).
The lock directory (`out_dir/.locks/`) is therefore a sibling of every `{scan_key}/` directory, at
`out_dir`'s own root, never touched by `clear_scan_dir`.

### Manifest filename: imported from the contract, not invented

The manifest is written to `out_dir / RUN_MANIFEST_FILENAME`, where `RUN_MANIFEST_FILENAME` is
imported from `sleap_roots_contracts` (currently `"run_manifest.json"`), never a bloomctl-local
literal. An earlier draft of this design hardcoded `out_dir/.bloomctl-run-manifest.json` — caught
during cross-repo review against `sleap-roots-contracts`'s own `run_manifest.py`, whose module
docstring states the constant exists specifically "so bloomctl/predict/traits agree on it via
import rather than each hardcoding the string," and whose design doc names the exact file
(`run_manifest.json`) `batch-download-for-predict` is expected to write during `images-downloader`.
Hardcoding a different name would have made this proposal's entire purpose — giving a downstream
consumer a manifest to read — silently fail: the manifest would exist, just never at the path any
future consumer looks for it. Importing the constant (rather than copying its current value as a
string literal) also means a future rename of the constant on the contracts side is a visible
`ImportError` here, not a silent divergence.

### An empty scan_keys result does not attempt to construct a RunManifest

`RunManifest`'s own validator (in `sleap_roots_contracts`) rejects an empty `scan_keys` list. If
every scan in this invocation failed and no prior manifest exists in `out_dir` to merge with, the
merged `scan_keys` would be empty — constructing `RunManifest(scan_keys=[])` in that situation
would raise an uncaught pydantic `ValidationError`, not the actionable `click.ClickException` every
other manifest-write failure mode in this design produces. In that specific case (empty merged
`scan_keys`), the manifest write step SHALL be skipped entirely — there is nothing usable to
record, and the batch has already exited non-zero via its existing every-scan-failed behavior, so
skipping the write adds no additional silent failure. This only arises when no pre-existing
manifest is present; if one exists, its own `scan_keys` are non-empty (the validator guarantees
this at every prior write) and the merged result is never empty.

### `--lock-staleness-seconds` threads through both `stage_one_scan` and the manifest write

`stage_one_scan` gains a `staleness_seconds` parameter (passed by `batch_download_for_predict` from
the new CLI option) used for its per-scan `acquire_lock` call; `batch_download_for_predict`'s own
manifest-lock `acquire_lock` call uses the same value directly. This is a signature change to an
existing function (`stage_one_scan(client, scan_id, out_dir)` → `stage_one_scan(client, scan_id,
out_dir, staleness_seconds)`), not just an internal detail of the lock module — noted explicitly
here since the option is meaningless unless both call sites actually receive it. Section 4 (per-scan
lock wiring) lands before section 5 (the `--lock-staleness-seconds` CLI option itself), so
`stage_one_scan`'s new parameter needs a default in the interim — a module-level
`DEFAULT_LOCK_STALENESS_SECONDS = 900` constant in `_locks.py`, the same 900s default the CLI
option itself uses once section 5 adds it, so section 4's own tests exercise the real default
rather than an arbitrary placeholder value that would need changing later.

### `RunManifest` merge, not overwrite

See proposal.md's "Why" — confirmed against the pipeline's actual chunking architecture (not
assumed): batches are deliberately split across multiple invocations sharing one `out_dir` with
non-overlapping scan_ids, so an overwriting write would silently drop the manifest's record of
every earlier invocation's scans the moment a later invocation with a different subset writes.
Merge degrades to the same result as overwrite when scan_id sets are identical or a subset (e.g.
an Argo `retryStrategy` retry of the same step) — there is no case where merge does *worse* than
overwrite, only cases where it does better. `pipeline_run_id` is last-writer-wins (the most recent
invocation to touch this directory) since it's a single scalar, not a set — there is no
"identity" to preserve about which invocation wrote it first. `RunManifest` is a **frozen** pydantic
model (`model_config = ConfigDict(frozen=True)` in `sleap_roots_contracts`), so a merge always
constructs a brand-new `RunManifest` instance from the combined `scan_keys`/`pipeline_run_id` — it
never mutates an existing instance's fields in place (that would raise on a frozen model).

### `pipeline_run_id` fallback: generated placeholder, not a fixed sentinel

`os.environ.get("ARGO_WORKFLOW_NAME")` when set (once `sleap-roots-pipeline` #38 lands, this will
always be set inside Argo). When absent — any manual/dev invocation, since #38 isn't built yet and
even after it lands someone will still run this by hand — `f"local-{uuid4().hex[:8]}"`, generated
fresh per invocation. A fixed sentinel (e.g. always `"local-dev"`) was considered and rejected:
every manual invocation would be indistinguishable from every other in the manifest, which matters
here specifically because `pipeline_run_id` is a traceability anchor (which run touched this
directory) and manual/dev usage is exactly the case where distinguishing separate runs from each
other is most useful (debugging, side-by-side testing).

### Lock mechanism: atomic exclusive create, content-based staleness, one-shot reclaim

Acquire: `fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)` — atomic across processes on
both POSIX and Windows, unlike `os.replace` (which would silently clobber a live lock rather than
fail). On success, write a small JSON body (`pid`, `acquired_at` — a `time.time()` float) through
`fd`, then **`os.close(fd)` immediately, before running any of the guarded code**. The lock file
must not be held open for the duration of the `with` block: on Windows, an open handle blocks
deletion by default (no `FILE_SHARE_DELETE`), so if the fd stayed open across the guarded code, the
lock's own release-on-exit would fail with `PermissionError` — POSIX permits unlinking an open file
unconditionally, so this bug has **no signal from this repo's CI**, which runs Linux-only
(`pr-checks.yml`'s `python-audit` job is `ubuntu-latest`); it would only surface on a Windows dev
machine (this repo's actual local dev environment). Closing the fd immediately, before yielding to
the guarded code, is therefore a load-bearing implementation detail, not a style preference — see
tasks.md 3.8, which verifies the close happens at the right point via mock/call-order assertion
rather than an end-to-end deletion test (which would pass on Linux CI regardless of correctness).

Staleness is decided from the lock file's own JSON body (`acquired_at`), not filesystem mtime — the
two were briefly conflated in an earlier draft of this design, which is worth recording so a future
reader doesn't reintroduce the ambiguity: mtime varies with how the write was performed and this
repo's filesystems don't guarantee sub-second mtime resolution consistently across platforms,
whereas `acquired_at` is a value this module itself writes and fully controls. `acquired_at` is read
via `time.time()`, called through the module's own `time` import (`_locks.py` does `import time`
and calls `time.time()`, never `from time import time`) specifically so a test can
`monkeypatch.setattr(_locks.time, "time", ...)` to fix the clock — see task 3.6, which needs this to
assert the exact-equality boundary deterministically rather than racing the real wall clock (real
elapsed time between constructing a test fixture and the code under test running makes hitting an
*exact* age impossible to guarantee without freezing time).

On `FileExistsError`: `path.read_text()` (auto-closing) the existing lock file, parse `acquired_at`
as `first_seen_acquired_at`; if `time.time() - first_seen_acquired_at` is **strictly greater than**
`--lock-staleness-seconds` (default 900s — generous enough to cover one scan's frame downloads,
which extended network/storage latency could stretch to several minutes, without falsely reclaiming
a lock a legitimately-running invocation still holds) — age exactly equal to the threshold is
**not** yet stale — attempt the reclaim: **re-read** the lock file's `acquired_at` immediately
before unlinking and compare it to `first_seen_acquired_at`. If it has changed, or the file is
already gone (`FileNotFoundError` on either the re-read or the `unlink()` call itself), another
process has already reclaimed or freshly re-acquired this lock — raise `LockContendedError` rather
than proceeding to unlink what might now be a live lock. Only if the re-read confirms the content is
unchanged does `acquire_lock` unlink and retry the exclusive create once; if that retry also fails
(another process's create won the race in the remaining gap) or the original lock wasn't stale,
raise `LockContendedError` naming the holder's pid and lock age — fail fast, no blocking/wait/retry
loop, matching this CLI's existing style everywhere else.

This re-read-before-unlink step exists specifically to close a concrete failure mode an earlier
draft of this design missed: without it, two processes (P1, P2) that both observe the *same* stale
lock could race so that P1 unlinks the stale file and recreates its own fresh, live lock, and then
P2 — which already decided (before P1 acted) that the file was stale, and never re-checks — unlinks
P1's brand-new live lock and successfully creates its own. Both P1 and P2 now believe they hold the
lock and proceed into the guarded section simultaneously: the exact #533-style corruption this
whole feature exists to prevent, reintroduced through the reclaim path itself. The re-read
narrows (but, per "Accepted limitation" below, does not perfectly eliminate) that window: P2 would
now see P1's changed `acquired_at` on its re-read and back off with `LockContendedError` instead of
deleting P1's live lock.

Before the first exclusive-create attempt, `acquire_lock` creates the lock file's parent directory
via `path.parent.mkdir(parents=True, exist_ok=True)` if it doesn't already exist. Without this, the
very first scan staged into a brand-new `out_dir` (before `.locks/` exists at all) would raise
`FileNotFoundError`, not `FileExistsError` — a genuinely unhandled case in an earlier draft of this
design, since nothing else creates `.locks/` ahead of time (unlike `write_sidecar`/
`atomic_write_bytes`, which both already `mkdir(parents=True, exist_ok=True)` their own targets).

**Accepted limitation:** even with the re-read-before-unlink check above, the reclaim sequence is
not a single atomic operation — there remains a narrow window between the re-read and the `unlink`
call itself where a peer could still re-acquire, and (rarely) a reclaimer could still delete a lock
that became live in that exact instant. This mirrors the same limitation every content/unlink-based
file lock has (there is no filesystem-portable compare-and-swap primitive available here); it is
accepted because this is single-host advisory locking for an internal pipeline tool with low
contention (reclaim only happens after a crash, which is rare, followed by a second invocation
arriving within the now-much-narrower reclaim window, which is rarer still), not a security boundary
or a high-frequency-contention system. See tasks.md 3.10/3.11/3.12 for the tests this re-read step adds.

### A corrupt existing manifest fails loud, rather than being silently treated as empty

If `out_dir / RUN_MANIFEST_FILENAME` exists but does not parse as valid JSON, or doesn't match
`RunManifest`'s shape, the write step SHALL raise a `click.ClickException` rather than silently
proceeding as if no manifest existed. The entire justification for merge-not-overwrite semantics
(see above) is not losing an earlier invocation's `scan_keys` — silently treating a corrupt file as
empty would discard exactly those scan_keys, at exactly the moment (existing-manifest present) merge
was designed to protect. This differs deliberately from `download.py`'s own `read_manifest` (which
treats `OSError`/`JSONDecodeError` as "no manifest" and proceeds) — that manifest is an identity
check where treating corruption as absent is harmless (worst case: an unnecessary redownload);
this manifest's whole purpose is an append-only record that must not silently lose entries.

### `_locks.py` is a new shared module, not private to `download_for_predict.py`

bloom #481 explicitly frames this as needing "a first concrete implementation" of a cross-command
lock design — naming the module generically (not e.g. `_download_for_predict_locks.py`) means a
future command that needs the same primitive doesn't need a rename/extraction first.

## Risks / Trade-offs

- `download-for-predict` (single-scan) remains unlocked. If someone runs it concurrently with
  `batch-download-for-predict` against the same scan_id, the original #533 race is still possible
  between those two specific commands. Accepted: out of scope per the issue's own framing, and
  the two commands are used in different contexts today (single-scan is a manual/debug tool per
  the sibling `add-cyl-batch-commands` design.md).
- The manifest lock's contention path fails the *manifest write step* with a non-zero exit even if
  every individual scan in the batch staged successfully — a caller re-running after a manifest-lock
  failure will find all scans already staged (skip-check hits) and only need to retry the (fast)
  manifest write. Accepted: manifest-lock contention should be rare (millisecond-scale critical
  section) and failing loud is preferable to silently dropping scan_keys from the manifest.
- The manifest write happens once, after every scan in the invocation has been processed — there is
  no interim/incremental write partway through a batch. If the process is killed mid-batch (e.g.
  after 5 of 10 scans have valid sidecars on disk), those 5 scans are fully staged but this
  invocation's manifest write never runs, so the manifest doesn't yet reflect them. This is only
  made whole by a **later invocation resubmitting the identical scan_ids** — the 5 already-staged
  scans then hit the skip-check (`skipped`) and get folded into that later manifest write normally.
  Accepted: this matches how Argo's own `retryStrategy` already re-invokes a failed step with the
  same scan_ids, so the gap self-heals on the orchestrator's own retry without bloomctl needing a
  slower, per-scan interim manifest write (which would trade one fast lock acquisition per batch for
  one per scan). See tasks.md 5.18 for the test confirming this retry story actually closes the gap.
- Lock directory (`out_dir/.locks/`) accumulates one file's worth of transient state per scan ever
  staged, but each file is removed on the lock's own release (success or failure) — the directory
  does not grow unboundedly across normal operation, only a crash leaves a file behind, and that
  file is reclaimed (and removed) by the next invocation that touches the same scan_key.
