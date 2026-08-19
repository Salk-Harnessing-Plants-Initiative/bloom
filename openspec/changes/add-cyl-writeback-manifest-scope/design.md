## Context

`discover_envelopes(envelopes_dir)` (`bloomcli/src/bloomctl/cyl/ingest.py:74-85`) is currently a
pure, simple helper: validate the directory exists, glob, sort, return `list[Path]`. It has one
caller, `batch_ingest_result` (`ingest.py:652-692`), which turns each path into a `ScanResult` via
`ingest_one_envelope`, aggregates into a `BatchResult`, and exits non-zero via `ctx.exit(1)`
(`ingest.py:691-692`) if any scan failed.

Two upstream stages in the same pipeline chain already solved this exact scoping problem:

- `sleap-roots-predict`'s `discover_scans` (predict #35): checks for `run_manifest.json`, filters
  the glob by filename stem *before* parsing, and — for a manifest key with no matching file —
  appends a synthetic entry carrying an `.error`, into the *same* return list its normal discovery
  results go into (its `ScanInput` type already has an optional `error` field for exactly this).
- `sleap-roots`'s `extract_batch` (sleap-roots #263): same filter-before-parse pattern, plus copies
  the manifest forward into its own `output_dir`. Its missing-scan_key case becomes a per-scan
  failure recorded directly into its own `BatchResult.failed` list at the end of the batch.

`bloomctl`'s shapes are different enough that mechanically copying either isn't a direct fit:

- `discover_envelopes` returns bare `Path`s, not a struct with a place to attach an error (unlike
  predict's `ScanInput`).
- Building a `ScanResult` for a missing scan_key requires nothing beyond the key itself — no file
  I/O, no envelope parsing — so it doesn't need to go through `ingest_one_envelope` at all.

## Decision: `discover_envelopes` returns a small result type, not a bare list

```python
@dataclass
class DiscoveredEnvelopes:
    paths: list[Path]              # in-scope files to ingest, sorted
    missing_scan_keys: list[str]   # manifest scan_keys with no matching file, sorted
```

Rejected alternative: keep the `list[Path]` return and add a second function
(`find_missing_manifest_scan_keys`) that re-reads the manifest and re-globs independently. Rejected
because it would parse `run_manifest.json` twice per invocation and risks the two functions
observing different directory states if a file appears/disappears between calls (unlikely in
practice given no concurrent writers at this stage, but avoidable for free by computing both in one
pass over one glob + one manifest read).

`discover_envelopes` has exactly one caller in the whole codebase (confirmed via search — the
single-envelope `ingest-result` command takes an explicit file/stdin argument and never globs a
directory), so changing its return type is not a breaking change to any public `bloomctl`
interface, only to this module's own internal call site and tests.

`missing_scan_keys: list[str]`, not `list[ScanResult]` — `discover_envelopes` stays a pure
directory/manifest-reading helper with no knowledge of `ScanResult`/`BatchResult` (defined in
`_batch.py`, a different module). Building the `ScanResult(key, "failed", ...)` for each missing
key, with its actual error message text, is the caller's (`batch_ingest_result`'s) job — consistent
with how `ingest_one_envelope` (not `discover_envelopes`) already owns every other `ScanResult`
error-message string in this module.

## Decision: filename-stem filtering, not envelope-body scan_key filtering

Filtering happens on the filename stem (`path.name.removesuffix(".result.json")`), computed before
any file is opened — exactly the point `ingest_one_envelope` itself already uses as its *fallback*
scan_key (`ingest.py:420-421`, overridden once `provenance.scan_key` is readable). This mirrors
predict's and traits' pattern of filtering before parsing, and is a deliberate choice not to open
and JSON-parse every out-of-scope file just to read its internal `provenance.scan_key` — the whole
point of scoping is to avoid touching contamination from a stale run at all.

This does mean a filename/internal-scan_key mismatch (the edge case predict #35's review caught,
`test_manifest_scoped_stem_mismatch_reports_the_real_error`) is possible in principle: a file named
`scan_1.result.json` whose body claims `provenance.scan_key: "scan_2"` and `scan_2` happens to also
be in the manifest. This is not new behavior introduced by this change — `ingest_one_envelope`
already re-derives `scan_key` from the envelope body once it's read (`ingest.py:430`), and
`validate_envelope`'s contract validation is unaffected by which directory-level filter admitted
the file. No additional guard is needed here: the existing per-envelope validation path already
surfaces a body/filename inconsistency as that envelope's own failure, same as it does today with
no manifest involved at all.

## Decision: a missing manifest-declared scan_key never requires an authed client

`batch_ingest_result`'s existing empty-directory no-op case
(`if not envelope_paths: click.echo(...); return`) explicitly avoids calling `_authed_client` — an
existing test asserts auth is never attempted for an empty directory. This property must be
preserved for the new missing-scan_key case too: reporting "manifest says `scan_9` should exist but
doesn't" requires no network access at all.

Restructured control flow:

```python
discovered = discover_envelopes(envelopes_dir)   # raises EnvelopeError -> ClickException, as today

missing_results = [
    ScanResult(key, "failed", f"run_manifest.json lists scan_key {key!r} but no "
               f"{key}.result.json was found in {envelopes_dir}")
    for key in discovered.missing_scan_keys
]

if not discovered.paths and not missing_results:
    click.echo("No envelope files found; nothing to ingest.")
    return

if discovered.paths:
    client = _authed_client(profile)   # only reached when there is real ingest work to do
    scan_results = [ingest_one_envelope(client, p, ...) for p in discovered.paths] + missing_results
else:
    scan_results = missing_results     # every manifest key was missing; nothing to authenticate for

batch_result = BatchResult(scan_results)
...
if not batch_result.ok:
    ctx.exit(1)
```

This keeps three cases distinct and each independently testable: (1) truly nothing found anywhere
→ no-op, exit 0, no auth; (2) some files present, some manifest keys missing → auth happens, mixed
`BatchResult`, exit 1; (3) manifest present but *every* declared key is missing (e.g. the directory
is empty but a manifest still landed there) → no auth, but still a non-empty failing `BatchResult`
and exit 1, not the no-op message.

## Decision: reconcile missing_scan_keys against actual ingest results before assembling BatchResult

**Bug found in `/review-pr` and independently reproduced against the real code before this decision
was written** (not a hypothetical): `discover_envelopes` computes `missing_scan_keys` purely from
**filename stems**. `ingest_one_envelope` separately re-labels its own `ScanResult` using the
envelope's **body** content once read (`data.get("provenance", {}).get("scan_key")` overrides the
filename-derived fallback — this re-labeling is pre-existing behavior, unrelated to this change).
These are two different sources of truth for "what scan_key is this," and the original
implementation of `batch_ingest_result` concatenated both lists with no reconciliation.

Concrete reproduction: a manifest declares `scan_keys=["scan_A", "scan_B"]`; only
`scan_A.result.json` exists on disk, but its body's `provenance.scan_key` says `"scan_B"`.
`discover_envelopes` correctly (by filename) computes `paths=["scan_A.result.json"]`,
`missing_scan_keys=["scan_B"]` (no file is named `scan_B.result.json`). But `ingest_one_envelope`
relabels its result to `scan_key="scan_B"` once it reads the body, and a stubbed successful RPC
produces `ScanResult("scan_B", "ok")`. The unreconciled batch was
`[ScanResult("scan_B","ok"), ScanResult("scan_B","failed")]` — the same key twice with contradictory
statuses. Any `--json` consumer keying by `scan_key` (this module's own tests use exactly
`{entry["scan_key"]: entry for entry in ...}`) would see the `"failed"` entry — which lands second —
silently overwrite the real `"ok"` entry, hiding a successful database write behind a false failure.

**Fix**: after computing `ingest_results` (only reachable when `discovered.paths` is non-empty), drop
any `missing_results` entry whose key coincides with a key `ingest_results` actually reported:

```python
ingested_scan_keys = {r.scan_key for r in ingest_results}
missing_results = [r for r in missing_results if r.scan_key not in ingested_scan_keys]
scan_results = ingest_results + missing_results
```

**Scope of this fix, stated explicitly**: this resolves the mechanical contradiction (the same key
never appears twice with conflicting status in one `BatchResult`) by letting the real, actually-
observed outcome win. It does **not** newly detect or validate the underlying filename/body
mismatch itself — `discover_envelopes` still only checks file *existence* by name, never envelope
*content*, exactly as before this fix. Content-level validation is `ingest_one_envelope`'s job
(contract validation, RPC-side checks), unaffected by this change. This is a deliberate scope
boundary matching the existing "filename-stem filtering, not envelope-body scan_key filtering"
decision above — that decision already accepted a filename/body mismatch as possible and pre-
existing; this fix only ensures such a mismatch can never surface as a self-contradictory batch
result, not that it gets flagged as its own new failure mode.

## Decision: `run_manifest.json` existing as a non-file entry fails loud, not silently unscoped

Also found in review: `manifest_path.is_file()` returning `False` was used as the sole signal for
"no manifest, fall back to unscoped" — which is correct when the path simply doesn't exist, but
also silently (and wrongly) applies if a directory (or other non-file entry) happens to occupy that
path, e.g. from a botched deploy step. Since the entire point of this change is to stop silent
stale-file contamination, a manifest-shaped path that can't actually be read as a manifest should
fail loud — matching the existing "malformed manifest fails loud" precedent for bad JSON/schema —
rather than silently degrading to the least-safe (fully unscoped) behavior with no signal at all.
Distinguished from "absent" by checking `.exists()` before falling back:

```python
if manifest_path.exists() and not manifest_path.is_file():
    raise EnvelopeError(f"{manifest_path} exists but is not a file")
if not manifest_path.is_file():
    return DiscoveredEnvelopes(paths=all_paths, missing_scan_keys=[])
```

## Decision: a malformed OR unreadable manifest both fail loud, matching the cited precedent exactly

`download_for_predict.py:407-414`'s existing "corrupt existing manifest fails loud" precedent
catches `(OSError, ValidationError)` together, not `ValidationError` alone — a manifest file can
exist but fail to *read* (permission error, transient filesystem issue) independently of its
*content* being malformed. `discover_envelopes` catches the same pair and re-raises both as
`EnvelopeError`, so `batch_ingest_result`'s existing `except EnvelopeError` handling (which already
turns this into a clean `click.ClickException` before any RPC call) covers both cases uniformly.
Catching only `ValidationError` would let a bare `OSError` escape as an unhandled traceback instead
of the readable error the "malformed manifest fails loud" spec scenario promises.

## Note: the new debug-log line has no real operator-facing visibility yet

`bloomcli` has no logging configuration anywhere today — no `--verbose`/`--debug` CLI flag, no
`logging.basicConfig` call, no handler attached at any entry point (confirmed by search). Python's
default behavior with no handler configured caps visibility at `WARNING`, so the `logger.debug(...)`
call this change adds for excluded out-of-scope files will be captured by `pytest`'s `caplog` (which
installs its own handler) but will not be visible to an operator running the shipped CLI. This is a
real, pre-existing gap in `bloomcli`, not something this change regresses — the proposal's own "an
operator debugging... has a trail" framing is aspirational until `bloomcli` gains a verbosity flag
or equivalent handler wiring, which is out of scope here (this change is pure discovery scoping, not
a CLI-observability feature). Worth a one-line callout in the PR description so it isn't mistaken
for a working feature; not a reason to hold this change, since the log call itself is harmless and
becomes real the moment `bloomcli` gets logging configuration for any other reason.

## Decision: fallback to fully unscoped discovery when no manifest is present

Both reference implementations treat an absent manifest as "behave exactly as before," and that
matters here for two independent reasons, not just consistency:

1. **Manual/dev CLI use.** A developer running `batch-ingest-result` by hand against a directory
   they populated themselves has no manifest and no expectation of one.
2. **Current production state.** Per the roadmap, the traits image that writes the manifest forward
   cannot redeploy yet (blocked on bloom#685, a `contract_version` pin mismatch — unrelated to this
   change). Until that unblocks, `envelopes_dir` will have no `run_manifest.json` in real pipeline
   runs either, so the fallback path is not a rarely-exercised edge case — it is what every real
   invocation does today and will keep doing until the unrelated blocker clears.

## Out of scope, called out explicitly

- **Locking.** No new lock around the manifest read — `write-back` only *reads*
  `run_manifest.json`; it never writes it, so there is no write/write or read/write race to guard
  against here (unlike `batch-download-for-predict`, which writes the manifest and needed
  bloom#653/#655's lock for that reason).
- **Copy-forward.** Write-back is the terminal pipeline stage; nothing downstream reads a manifest
  from `envelopes_dir` after this.
- **Skip-if-done / idempotency-key comparisons.** Write-back's idempotency is already handled
  server-side (`insert_cyl_result_envelope`'s first-writer-wins `idempotency_key` check). Unlike
  sleap-roots #263, this change does not add a local skip-if-done — that would be solving a
  different, already-solved problem.
- **Case-fold / duplicate scan_key handling.** Predict's and traits' discovery functions build one
  in-memory entry *per scan_key* and need duplicate detection because a collision would silently
  overwrite one scan's data with another's before either is used. `discover_envelopes` does not:
  every discovered path is still ingested independently through the existing per-envelope pipeline,
  so two files that happen to share a case-folded stem are simply two independent envelopes to
  `ingest_one_envelope`, exactly as they are today with no manifest involved. No new duplicate
  detection is needed for this change.
