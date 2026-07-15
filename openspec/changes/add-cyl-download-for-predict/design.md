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
  >=0.1.0a4.** The canonical
  `resolve_params` oracle is now in `sleap-roots-contracts` (PR #16, v0.1.0a4). It normalises
  species names and age types; duplicating the logic here would create a second normalization path
  and could diverge `param_hash → idempotency_key` between bloomctl and predict (exactly the
  silent corruption that contracts PR #16 exists to prevent). The sidecar stores the flat
  `.values` dict `{species, mode, age}` — predict's `_load_scan` builds `ResolvedParams` from it.
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
- **Stray frame files are reconciled away before the sidecar is written (added after second
  review round).** Verified against the real `sleap_roots_predict.batch._load_scan`
  (`c:\repos\sleap-roots-predict\sleap_roots_predict\batch.py`): frame discovery globs **every**
  image-extension file physically present in the sidecar's parent directory — it does not consult
  `image_ids` or a frame count. So "no sidecar on failure" alone doesn't fully protect the
  provenance guarantee across retries: if attempt 1 downloads frames for `image_ids [1001, 1002,
  1003]` and fails on 1003 (files `0.png`, `1.png` on disk, no sidecar), and between attempt 1 and
  a later successful attempt 2 the scan's `cyl_images` rows change (e.g. row 1002 is deleted or
  renumbered) such that attempt 2 only writes `0.png` and a differently-numbered replacement,
  `1.png` from attempt 1 could survive on disk and be picked up as an extra, unaccounted-for frame
  by `discover_scans` — even though the freshly-written sidecar's `image_ids`/`images_checksum`
  never counted it. Fix: immediately before writing the sidecar on a fully successful run, delete
  any file in `scan_dir` with a recognized image extension that is not one of the frame paths just
  written this run. This is a no-op on the common case (plain retry, same `frame_number`s
  overwrite in place) and only actually removes files in the narrower stale-data case. Failed
  runs are unaffected (no sidecar is written either way, so no reconciliation happens on failure —
  partial frames intentionally remain for debugging, per the bullet above).
- **The cross-repo "oracle" tests (3.1, 5.6) are not a CI gate — deferred, not dependency-added
  (added after review).** These tests assert the sidecar is accepted by the real
  `sleap_roots_predict.discover_scans`/`_load_scan`. Adding `sleap-roots-predict` as a bloomcli
  test dependency was considered and rejected: it isn't published to PyPI (git-only), and as of
  this review its `pyproject.toml` exact-pins `sleap-roots-contracts==0.1.0a3`, which conflicts
  with this proposal's `>=0.1.0a4` floor and would break dependency resolution outright, not just
  skip a test. Per Elizabeth (2026-07-15): the sleap-roots-predict repo is actively being re-pinned
  to the newest sleap-roots-contracts as a separate, in-flight piece of work — once that lands,
  add `sleap-roots-predict` as a git dependency in a new bloomcli test extra and wire tests 3.1/5.6
  into CI for real. Until then, they remain `pytest.importorskip`-guarded, run manually on a dev
  machine with `sleap-roots-predict` installed locally, and are not treated as verifying anything
  in CI. The non-skipped tests (4.x/5.x) independently assert every shape fact predict's code is
  documented to require, without importing predict.

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
  tests (4.x/5.x) are the enforced contract, and 3.1/5.6 are run manually on a dev machine before
  merge.
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
  attempts.)
