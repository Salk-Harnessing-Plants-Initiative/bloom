## Why

The A4 pipeline's warm-predict container expects a `*.scan_metadata.json` sidecar layout that
`bloomctl cyl download` doesn't produce, causing a silent 0-scan no-op; the sidecar also needs
real `cyl_images` ids or the write-back RPC rejects it with "no matching scan." This adds
`bloomctl cyl download-for-predict <scan-id> <out>` to stage one scan correctly (closes bloom
#411). (Mechanism detail: see `design.md` Context.)

## What Changes

- Add **`bloomctl cyl download-for-predict <scan-id> <out>`** (new Click subcommand under the
  existing `cyl` group, alongside `download` and `ingest-result`):
  - Write frame files into `<out>/scan_{scan_id}/<frame_number>{ext}` (frames co-located with
    the sidecar, not the old `images/Wave{n}/…` tree).
  - Author `<out>/scan_{scan_id}/scan_{scan_id}.scan_metadata.json` with:
    - `scan_key` — `"scan_{scan_id}"` (must equal filename stem; predict validates this).
    - `params` — `{species, mode, age}` flat dict from
      `resolve_params(scan_row, overrides={"mode": "cylinder"}).values`
      (`sleap-roots-contracts>=0.1.0a4`); `mode` is always `"cylinder"` for cyl-scanner scans.
    - `image_ids` — list of `cyl_images.id` integers in DB `frame_number` order; the write-back
      RPC resolves these to the scan (fake ids → "no matching scan").
    - `images_checksum` — `sha256:<hex>` over frame bytes concatenated in DB `frame_number`
      order, tying provenance to what was actually downloaded.
- Bump `sleap-roots-contracts>=0.1.0a3` → `>=0.1.0a4` in `bloomcli/pyproject.toml`; do the
  **full re-pin** per contracts PR #16 instructions: update `contracts/pin.json` (version,
  `$id`/source URL), update the vendored `contracts/schema/result_envelope.schema.json` (verified
  `$id`-only diff against the fetched `v0.1.0a4` schema), regenerate
  `contracts/generated/result-envelope.ts`, and update `contracts/README.md`'s "Currently pinned"
  line plus a new dated re-pin note.
- **Existing `bloomctl cyl download`** is untouched — `--experiment-id`, `--scan-id`, and
  `scans.csv` output are unchanged.
- Implementation lands in the **same PR** as this proposal (bundled proposal + implementation).

## Impact

- **New capability**: `cyl-download-for-predict` (the stage-in command for the A4 per-scan
  pipeline). Consumes `cyl_scans_extended` (read), `cyl_images` (read), and Storage `images`
  bucket (download via existing Supabase Storage client). No server/RPC/schema changes.
- **Contracts re-pin**: `bloomcli/pyproject.toml` floor `>=0.1.0a4`; `contracts/pin.json` +
  vendored schema `$id` + regenerated TS (verified `$id`-only diff, no logic change) +
  `contracts/README.md` pin line and re-pin note.
- **Affected code**: new `bloomcli/src/bloomctl/cyl/download_for_predict.py`;
  `bloomcli/src/bloomctl/cyl/__init__.py` (register new command); `bloomcli/pyproject.toml`
  (dep floor); `contracts/pin.json`, `contracts/schema/result_envelope.schema.json`,
  `contracts/generated/result-envelope.ts`, `contracts/README.md` (re-pin); new
  `bloomcli/tests/test_cyl_download_for_predict.py`; `bloomcli/README.md`, `bloomcli/CHANGELOG.md`.
- **Not added**: `sleap-roots-predict` as a test dependency (considered, rejected — see
  `design.md` Decisions/Risks: not on PyPI, and its `pyproject.toml` currently exact-pins a
  conflicting `sleap-roots-contracts` version; its own re-pin is in progress separately).
- **Out of scope** (separate follow-ups): non-interactive auth (#398); blob upload (#407); RPC
  grants (#404); bulk/experiment-level predict layout (future). Consumer: the A4 stage-in step
  (EPIC `talmolab/sleap-roots-pipeline#10`).
- **Coordination**: same `cyl` group and Storage download pattern as `download.py`; branch
  protection requires a non-author reviewer.
