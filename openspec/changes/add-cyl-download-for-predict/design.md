## Context

`bloomctl cyl download` (download.py) writes the legacy layout for experiment-level and
single-scan downloads. The new warm-predict container (`sleap_roots_predict.batch.discover_scans`,
batch.py:66-102) ignores that layout — it globs for `*.scan_metadata.json` sidecars and uses each
sidecar's parent directory as that scan's frame folder. The sidecar must carry `image_ids` (the
real `cyl_images.id` values from the DB) because the write-back RPC
`insert_cyl_result_envelope` resolves `inputs.image_ids → cyl_images.scan_id`; fake ids produce a
"no matching scan" rejection at write-back time, not at predict time (predict never validates
`image_ids`). The A4 PoC (sleap-roots-pipeline PR #23) hand-authored a sidecar with synthetic ids
to get a green predict run; this change provides the real stage-in.

## Goals / Non-Goals

- **Goals**: a `download-for-predict <scan-id> <out>` command that (1) writes frames into
  `<out>/scan_{id}/<frame_number>{ext}`, (2) authors `<out>/scan_{id}/scan_{id}.scan_metadata.json`
  with valid `scan_key`, `params`, `image_ids`, and `images_checksum`, (3) produces output that
  `discover_scans()` loads without error, (4) produces `image_ids` that the write-back RPC can
  resolve. Pure helpers separated from Supabase I/O for unit-testability.
- **Non-Goals**: modifying existing `cyl download` (untouched); bulk/experiment predict layout;
  non-interactive auth (#398); blob upload (#407); RPC grants (#404).

## Decisions

- **New subcommand, not a flag on `download`.** `bloomctl cyl download-for-predict <scan-id>
<out>` rather than modifying `download --scan-id`. The two commands produce fundamentally
  different output trees; a flag would silently break any existing caller of `download --scan-id`
  that expects `scans.csv`. A dedicated subcommand is self-documenting and non-breaking. The name
  `download-for-predict` is the one named by contracts PR #16's follow-up notes — the canonical
  reference for this command.
- **`scan_key = "scan_{scan_id}"`.** `cyl_scans_extended` has no `scan_key` column — bloomctl
  authors it. The key must be unique within a stage-in directory (predict raises on duplicate
  scan_keys), stable (so write-back can trace), and safe to use as a directory/filename stem. The
  DB primary key `scan_id` satisfies all three. The sidecar's `scan_key` field must equal the
  filename stem (predict's `_load_scan` validates this at batch.py:111).
- **`mode = "cylinder"` passed via `resolve_params(scan, overrides={"mode": "cylinder"})` — not
  row augmentation (corrected after review).** All scans under `bloomctl cyl` are
  cylinder-scanner scans; the multiplant dicot pipeline is retired. **Correction**: the original
  rationale here claimed `resolve_params` reads `mode` from the metadata row, and had bloomctl
  inject `mode: "cylinder"` directly into the row dict before calling it. Verified against the
  actual released `sleap-roots-contracts>=0.1.0a4`: that claim is false.
  `sleap_roots_contracts.params._mode_for_scan(metadata)` ignores its `metadata` argument entirely
  and unconditionally returns the literal `"cylinder"` — row augmentation is not part of the
  documented contract at all (`resolve_params`'s own docstring: "Other columns are ignored").
  Fix: use `resolve_params`'s actual documented mechanism for this instead —
  `resolve_params(scan_row, overrides={"mode": "cylinder"})`. `overrides` is explicitly documented
  as "a param-space dict whose keys are a subset of `{species, mode, age}`... each key wins its
  field over the derived value" — the sanctioned way for "callers who know the assay type" (which
  is exactly bloomctl's situation) to force a value. Verified all three of no-override,
  `overrides=`, and row-augmentation currently produce identical output (since `_mode_for_scan`
  ignores its input either way), but `overrides=` is forward-compatible via contracts' actual
  public API, where row augmentation was relying on undocumented (if currently harmless)
  behavior.
- **`params` via `resolve_params(scan_row, overrides={"mode": "cylinder"}).values` from contracts
  `>=0.1.0a4`.** The canonical `resolve_params` oracle is now in `sleap-roots-contracts` (PR #16,
  v0.1.0a4). It normalises species names and age types; duplicating the logic here would create a
  second normalization path and could diverge `param_hash → idempotency_key` between bloomctl and
  predict (exactly the silent corruption that contracts PR #16 exists to prevent). The sidecar
  stores the flat `.values` dict `{species, mode, age}` — predict's `_load_scan` builds
  `ResolvedParams` from it.
- **`image_ids` in DB `frame_number` order, stored as integers.** `fetch_images` already returns
  rows ordered by `frame_number`; re-using that order is consistent and requires no second sort.
  The DB returns integer ids; JSON serialises them as numbers, which the RPC's `^[0-9]+$` check
  accepts.
- **`images_checksum` over frame bytes in DB `frame_number` order.** The DB is the authoritative
  source of what was ingested; `frame_number` is the stable, per-scan ordering. Computing over
  filesystem natural-sort order (predict's video-creation order) would produce a different checksum
  if a frame were uploaded with a non-integer name, silently breaking provenance tracing.
  `sha256:` prefix matches the pattern used by `cyl_scan_intermediates` (change C).
- **Full contracts re-pin.** contracts PR #16 requires downstream consumers to do a full re-pin
  (`pin.json` + vendored schema `$id` + regenerated TS), not merely a pip-floor bump, because the
  schema `$id` URL changes with the version. The TS generated contract in `contracts/generated/`
  and the vendored schema in `contracts/schema/` both get the `$id`-only diff.
- **Module structure mirrors `download.py` and `ingest.py`.** New
  `bloomcli/src/bloomctl/cyl/download_for_predict.py` with pure helpers above the
  `# --- supabase / storage I/O ---` marker. Reuses `fetch_scan` and `fetch_images` from
  `download.py` directly (imported, not duplicated). Registered in `cyl/__init__.py`.
- **No sidecar is written on partial frame-download failure (added after review).** If any frame
  fails to download, the command exits non-zero and successfully-downloaded frame files remain on
  disk for debugging, but `scan_{scan_id}.scan_metadata.json` is **not** written. Rationale: a
  written sidecar is a claim that `image_ids`/`images_checksum` accurately describe what's on
  disk; a sidecar written over a partial frame set would let `discover_scans` silently load a scan
  that doesn't have all its frames, with a checksum that can't be reproduced by a successful
  re-run. Failing to write the sidecar means `discover_scans` simply doesn't see the scan at all —
  a safe, glob-invisible failure mode — until a clean re-run succeeds.
- **Superseded: end-of-run stray-frame reconciliation → start-of-run full directory clear (revised
  after PR #458 review).** The original decision (kept below, struck through in spirit, for
  history) deleted only files not among the current run's just-written frames, computed
  _immediately before writing the sidecar_ on a fully successful run. PR review reproduced a gap
  this didn't cover: if `scan_dir` already holds a valid sidecar from an earlier _successful_ run,
  and a later re-run partially fails, the end-of-run reconcile+write never runs (the failure path
  exits first) — so the old sidecar survives untouched, now describing frame bytes that a partial
  retry may have already overwritten in place. A diff-based, end-of-run fix can't close this: the
  problem is that stale state can outlive a failed run at all. Fix: **clear `scan_dir` entirely at
  the start of every invocation**, before downloading any frame — not just reconcile stray files
  at the end. This is a strictly simpler model (idempotent "stage fresh," not "diff against a
  moving target") that closes the stale-sidecar gap by construction: a failed run has nothing to
  leave _behind_ to go stale, because whatever was there before this run started is already gone.
  The command echoes what it clears, so this isn't silent (PR review also flagged the original
  reconcile as too quiet). See the next two bullets for how this composes safely with validation.
  <details><summary>Original decision (superseded, kept for history)</summary>
  Verified against the real `sleap_roots_predict.batch._load_scan`: frame discovery globs every
  image-extension file physically present in the sidecar's parent directory, not just
  `image_ids`. Fix (at the time): immediately before writing the sidecar on success, delete any
  file in `scan_dir` not among the frame paths just written. This correctly closed the
  "renumbered-frame" case but not the "prior success, later partial failure" case above.
  </details>
- **Validate everything that can fail _before_ clearing the directory or downloading anything
  (added after PR #458 review — closes a reproduced crash).** PR review reproduced: a scan with a
  null `species_name`/`plant_age_days` makes `resolve_params` raise a bare, uncaught `ValueError`
  from inside `build_sidecar`, which was called _after_ the (then end-of-run) reconcile step — so
  the crash left destructive cleanup already done, with no `ClickException`, just a raw traceback.
  Fix: extract the `resolve_params` call into its own pure helper (`resolve_sidecar_params`),
  call it — and the new frame-number validation below — right after `fetch_images`, wrapped in
  `try/except ValueError` → `ClickException`, **before** the directory clear or any download. The
  already-resolved `params` dict is threaded into `build_sidecar` (new required parameter) rather
  than re-resolved later, so there's no duplicate call and no way to reach the destructive/download
  steps with metadata that can't produce a valid sidecar.
- **Reject duplicate or null `frame_number` values before downloading (added after PR #458
  review).** `cyl_images.frame_number` is nullable and `UNIQUE(scan_id, frame_number)` doesn't
  block `NULL` (Postgres never considers two `NULL`s equal for uniqueness), so two rows can share
  a null/duplicate `frame_number` for one scan. Reproduced: without a guard, both rows map to the
  same on-disk path (`frame_dest_for_predict` derives the filename from `frame_number` alone), the
  second overwrites the first, yet _both_ ids land in `image_ids` and _both_ frames' bytes get
  hashed into `images_checksum` — a checksum that no longer describes what's on disk, silently.
  Fix: new pure helper `validate_frame_numbers(images)` raises `ValueError` (caught the same way as
  the metadata-resolution failure above) if any `frame_number` is `None` or duplicated, before any
  destructive action. `cyl download`'s equivalent data-quality gap (documented below, in Risks) is
  otherwise unaffected — this fix is scoped to `download-for-predict` alone, since only its sidecar
  makes a checksum/`image_ids` claim that duplicate frame numbers would falsify.
- **Reuse `cli.py`'s `_authed_client` instead of duplicating `download.py`'s auth-error-handling
  block (added after PR #458 review).** The command's `load_credentials`/`make_authed_client`
  try/except pair was copy-pasted byte-for-byte from `download.py`, when `ingest.py` (a sibling
  command in this same `cyl` group) already extracted this exact pattern into
  `cli._authed_client(profile)`. Switching to it produces the identical error messages (verified:
  same `ClickException` text, same "run `bloomctl login`" hint), so no test changes are needed —
  it's a pure de-duplication, not a behavior change.
- **`frame_dest_for_predict` fails loudly on a missing `object_path`, matching `download.py`'s
  `image_dest` (added after PR #458 review).** Was `image.get("object_path", "")`, silently
  degrading a malformed row to a bare `.png` extension; `download.py`'s equivalent helper does
  `image["object_path"]` and raises `KeyError` on the identical bad input. Two helpers doing the
  same job should fail the same way. Fix: switch to `image["object_path"]`. The `KeyError` is
  still caught per-frame inside `download_frames_for_predict`'s existing `try/except Exception`
  (unchanged), so a single malformed row still surfaces as a clean per-frame failure, not a crash —
  only the _manner_ of that per-frame failure detection changed (loud vs. silently-wrong).
- **Sidecar and frame writes are atomic (write-to-temp, then `os.replace`) (added after PR #458
  review).** `write_sidecar`/frame writes were direct `write_text`/`write_bytes` calls — a process
  killed mid-write could leave a truncated-but-present file. `os.replace` is atomic on both POSIX
  and Windows (unlike `os.rename`, which fails on Windows if the destination exists), so this is a
  small, portable fix. Low-probability risk, but this module's whole design rests on "a written
  file accurately represents its claimed content" — worth closing given how cheap it is.
- **Concurrent invocations of the same `scan_id`/`out_dir` remain unsupported (accepted, not fixed,
  added after PR #458 review).** PR review reproduced a race: two processes staging the same scan
  into the same directory at once can have one process's directory-clear or write step interleave
  with the other's, since there is no file lock anywhere in this command (nor in sibling `cyl`
  commands). A real fix needs a lock file or DB advisory lock — disproportionate for what is a
  single-operator, run-once-per-scan CLI tool, not a batch-parallel service. The intended usage
  pattern is one invocation per scan, sequential retries, not concurrent runs of the _same_
  scan_id. Accepted as a known limitation; the clear-upfront design (above) doesn't make this
  worse than the previous end-of-run-reconcile design, just differently-shaped.
- **The oracle tests now assert `_IMAGE_EXTENSIONS` equality against predict's real constant
  (added after PR #458 review).** The hardcoded `_IMAGE_EXTENSIONS` frozenset is the safety-
  critical basis for what the directory-clear step (and, previously, stray-frame reconciliation)
  treats as an image file — a drift from predict's real `sleap_roots_predict.batch
._IMAGE_EXTENSIONS` would silently change what gets cleared/protected, in either direction, with
  no signal. Since the two oracle tests already import `sleap_roots_predict` when available, they
  now also assert `dfp._IMAGE_EXTENSIONS == frozenset(sleap_roots_predict.batch._IMAGE_EXTENSIONS)`
  — this is the one place that assumption is actually checked, even though only manually/dev-machine
  (per the existing non-CI-gate decision below).
- **The cross-repo "oracle" tests (3.1, 5.6) are not a CI gate — deferred, not dependency-added
  (added after review).** These tests assert the sidecar is accepted by the real
  `sleap_roots_predict.discover_scans`/`_load_scan`. Adding `sleap-roots-predict` as a bloomcli
  test dependency was considered and rejected: it isn't published to PyPI (git-only), and as of
  this review its `pyproject.toml` exact-pins `sleap-roots-contracts==0.1.0a3`, which conflicts
  with this proposal's `>=0.1.0a4` floor and would break dependency resolution outright, not just
  skip a test. Per Elizabeth (2026-07-15): the sleap-roots-predict repo is actively being re-pinned
  to the newest sleap-roots-contracts as a separate, in-flight piece of work — once that lands,
  add `sleap-roots-predict` as a git dependency in a new bloomcli test extra and wire tests 3.1/5.6
  into CI for real. Until then, they remain `pytest.importorskip`-guarded and are not treated as
  verifying anything in CI. Manually verified once during implementation (task 8.6) using
  `sleap-roots-predict`'s own `uv`-managed `cpu` extra (`uv run --with
"sleap-roots-predict[cpu] @ file:///path/to/sleap-roots-predict" --extra test pytest ...` — this
  repo's convention is `uv`, not a hand-maintained conda env, which is why an earlier attempt
  against a stale pre-existing conda env failed for an unrelated reason and was abandoned): both
  passed (`2 passed, 22 deselected`). The non-skipped tests (4.x/5.x) independently assert every
  shape fact predict's code is documented to require, without importing predict — that remains
  the CI-enforced contract; 3.1/5.6 are a valuable but not CI-gated extra confirmation.

## Risks / Trade-offs

- **`resolve_params`'s `mode` handling is currently a no-op regardless of how it's called
  (corrected after review; see Decisions).** Confirmed by direct inspection of
  `sleap_roots_contracts.params._mode_for_scan` (v0.1.0a4): it ignores its `metadata` argument and
  always returns `"cylinder"`, so `overrides={"mode": "cylinder"}` is not currently load-bearing
  either — but it is the documented API surface, unlike the row-augmentation it replaces.
  Mitigation: task 4.5 asserts the actual `overrides` argument passed to `resolve_params` (via
  monkeypatch/spy) is `{"mode": "cylinder"}`, plus a companion test that documents
  `resolve_params(scan)` (no override at all) already returning `mode="cylinder"` on the pinned
  version — so a future contracts bump that makes `mode` metadata-driven is visible as a
  test-behavior change, not a silent gap.
- **Cross-repo oracle tests (3.1, 5.6) don't run in CI.** See the new Decisions bullet above —
  `sleap-roots-predict` is not added as a dependency (PyPI-absent, and its `pyproject.toml`
  exact-pins a conflicting contracts version). Mitigation: tracked as a follow-up once
  sleap-roots-predict's own re-pin (in progress per Elizabeth) lands; until then the shape-only
  tests (4.x/5.x) are the enforced contract. 3.1/5.6 were manually run before merge and passed
  (task 8.6).
- **Duplicate or non-integer `frame_number` in `cyl_images` (accepted, pre-existing limitation,
  not introduced by this change).** `frame_dest_for_predict` derives each frame's filename from
  `frame_number` alone (`f"{image['frame_number']}{ext}"`), matching `download.py`'s existing
  `image_dest` helper, which has made the identical assumption since the original `cyl download`
  command shipped. Two `cyl_images` rows sharing a `frame_number` would overwrite each other's
  frame file on disk; a non-integer/`None` `frame_number` would produce a malformed filename. Not
  addressed here because it isn't a new risk this change introduces — `cyl_images.frame_number`
  data quality is an upstream-ingestion concern shared by both download commands.
- **`image_ids` as integers vs strings.** The test fixture (`scan0K9E8BI.result.json`) carries
  `image_ids: ['1001', '1002']` as strings (emitted by the trait-extractor). bloomctl will write
  integers. The RPC validates numeric-ness (`^[0-9]+$` / `non-numeric image_id`) and resolves by
  `::bigint` cast; both integer and string-numeric representations pass. Mitigation: the
  integration test confirms resolution.
- **Full re-pin diff size.** The `$id`-only schema diff touches `contracts/schema/` and
  `contracts/generated/`; reviewers may flag this as unrelated. Mitigation: note in the PR
  description that this is a mechanical follow-up required by contracts PR #16.
- **The `$id`-only-diff assumption is not automatically safe (added after review).** Verified this
  time by diffing the fetched `v0.1.0a4` schema against the vendored `a3` schema (version string
  normalized out): byte-identical, confirming the GitHub release notes' claim. But
  `contracts/README.md`'s own history shows the prior two re-pins (`a1→a2`, `a2→a3`) were **both**
  real revisions, not no-ops — a 0-for-2 track record for "expect only `$id`". Mitigation: task
  2.2 now requires actually diffing the fetched schema against the vendored one before
  regenerating TS, not assuming the diff shape in advance.

## Migration Plan

No schema/RPC/DB migration. Rollout: land command + tests in one PR on `staging`. After merge,
hand the `bloomctl cyl download-for-predict` invocation to the A4 pipeline stage-in step
(sleap-roots-pipeline EPIC #10). Rollback is code-only (revert PR); no data effects.

## Open Questions

- None blocking. (Resolved during two `/review-openspec` rounds, 2026-07-15 — see reconciliation
  notes above and in `tasks.md`/`specs/cyl-download-for-predict/spec.md`. Round 1: the
  `resolve_params`/`mode` claim was corrected to use `overrides=`, the cross-repo oracle tests
  were reframed as non-CI dev-machine checks pending sleap-roots-predict's in-progress contracts
  re-pin, the partial-frame-failure sidecar behavior was decided (no sidecar written), and a
  scope-mismatched write-back scenario was removed from this change's spec. Round 2 (adversarial
  re-check): found and fixed a stale row-augmentation reference left behind in spec.md by round
  1's partial fix, and — verified against the real `sleap_roots_predict.batch._load_scan` code —
  added the stray-frame-reconciliation decision above, since "no sidecar on failure" alone doesn't
  fully protect provenance across retries where the scan's `cyl_images` rows changed between
  attempts. Round 3 (`/review-pr` on the merged implementation, PR #458, 2026-07-15): reproduced a
  real crash (uncaught `ValueError` from `resolve_params` on missing scan metadata, with the
  destructive reconcile step already having run) and a real data-integrity gap the round-2 fix
  didn't fully close (a prior successful run's sidecar surviving stale after a later partial-
  failure retry). Superseded end-of-run stray-frame reconciliation with start-of-run full
  directory clear; added frame-number duplicate/null validation, metadata-resolution validation
  before any destructive action, atomic writes, `_authed_client` reuse, loud `frame_dest_for_predict`
  failure, and an `_IMAGE_EXTENSIONS`-equality assertion in the oracle tests. Accepted (not fixed):
  concurrent invocations of the same scan_id/out_dir remain unsupported — see Risks.)
