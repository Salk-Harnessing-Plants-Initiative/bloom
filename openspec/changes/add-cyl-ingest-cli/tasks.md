# Tasks — bloomctl cyl ingest-result (per-scan ResultEnvelope write-back)

TDD throughout: write the failing test first, confirm RED, then implement to GREEN. This is a
staging-first, protected repo — every pushed commit must keep CI green, so new tests and the
code they exercise land **in the same commit** (no standalone red-tests commit). Unit tests use
`click.testing.CliRunner` + `monkeypatch` fakes, mirroring the **existing** `bloomcli/tests/
test_download_scan.py` (fake query/client) and `test_login.py`; the module structure mirrors
`bloomcli/src/bloomctl/download.py` (pure helpers above the `# --- supabase / storage I/O ---`
marker at `download.py:78`, I/O below). PR #385's `test_list.py` / `@cli.group(...)` /
`_authed_client` are the group precedent but live only on `origin/feat/bloomctl-list` (not this
branch) — read them there if needed. Proposal + implementation land in the **same PR**.

CI note (verified): `.github/workflows/pr-checks.yml` runs `cd bloomcli && uv run --extra test
pytest tests/` with **no** `-m` filter and **no** `--frozen`; bloomcli commits **no `uv.lock`**
and is excluded from `scripts/check-uv-locks.py` and from `pip-audit`; bloomcli is **outside**
the black/ruff pre-commit globs (ruff runs only at release via `release-bloomcli.yml`). These
facts drive tasks 2.x / 6.1 / 9.x below.

## 1. Proposal & specs (this change)

- [x] 1.1 Author `proposal.md`, `design.md`, and the `cyl-ingest-cli` spec delta. Run
      `openspec validate add-cyl-ingest-cli --strict` and resolve all issues.

## 2. Package setup (pre-req; lands before the tests that import it)

- [x] 2.1 Add `sleap-roots-contracts>=0.1.0a3` to `bloomcli/pyproject.toml` dependencies (no
      `[pandas]` extra). **Verify a3 is installable from PyPI** before pinning:
      `uv run --no-project --with 'sleap-roots-contracts>=0.1.0a3' python -c "import
importlib.metadata as m; print(m.version('sleap-roots-contracts'))"` prints `0.1.0a3`. Note in
      the PR that bloommcp's lock resolving `a1` is expected (bloommcp keeps a `>=0.1.0a1` floor and
      never re-locked). **bloomcli has no `uv.lock`** and is excluded from `check-uv-locks.py` — no
      lock refresh applies. Optionally `uv tree`/`pip show` to confirm `pandas`/`numpy` are absent
      (only `pydantic` — already present via `supabase→postgrest` — and `pyyaml` are pulled).
- [x] 2.2 Register an `integration` pytest marker in `bloomcli/pyproject.toml`
      (`[tool.pytest.ini_options] markers = ["integration: requires live staging (env-gated)"]`).
- [x] 2.3 Commit a real emitter-generated fixture at
      `bloomcli/tests/fixtures/<scan>.result.json` (from the sleap-roots trait-extractor;
      byte-stable, `produced_at=null`; carries the **full a3 provenance** the `ResultEnvelope` model
      requires). **Exclude it from prettier** so pre-commit does not rewrite its bytes: add
      `exclude: ^bloomcli/tests/fixtures/.*\.result\.json$` under the prettier hook in
      `.pre-commit-config.yaml`; verify with `pre-commit run prettier --files
bloomcli/tests/fixtures/<scan>.result.json` that it is unmodified. Run `pre-commit run
gitleaks --files <fixture>`; if the fixture's checksums/`idempotency_key` false-positive, add a
      **fixture-scoped** `.gitleaksignore` entry (do not weaken the global rule).
- [x] 2.4 Add `-m "not integration"` to **both** bloomcli test invocations so the default gate
      never depends solely on runtime self-skip: `pr-checks.yml`
      (`cd bloomcli && uv run --extra test pytest tests/ -m "not integration" -v --tb=short`) and
      `release-bloomcli.yml` (`uv run --extra test pytest -m "not integration" -q`). These are the
      only two bloomcli test invocations (grep-confirmed).

## 3. RED — pure helpers (`bloomcli/tests/test_cyl_ingest.py`)

Write first; each must FAIL before `ingest.py` exists.

- [x] 3.1 `load_envelope`: parses a valid envelope from a **path**; reads from **stdin** when the
      source is `-`; raises a readable error on a **missing file**, **invalid JSON**, and **empty
      stdin**.
- [x] 3.2 `validate_envelope`: **accepts** the committed real fixture; **rejects** a _targeted_
      malformed envelope (e.g. `provenance` missing, or `traits` not an array) with a concise
      readable message — the command's own validation error, not a raw `pydantic.ValidationError`
      dump.
- [x] 3.3 send-original: the object handed to the RPC equals the originally parsed JSON (same
      `provenance.idempotency_key`; **no** `model_dump` substitution).
- [x] 3.4 `summarize_result`: `was_noop=false` → an "ingested" summary naming `source_id` and the
      trait/blob counts (incl. a **zero-count** source-only case); `was_noop=true` → an "already
      ingested" (benign) summary naming `source_id` that does **not** assume a non-null `scan_id`.
- [x] 3.5 `map_rpc_error` matches on the **exact** RPC substrings from
      `supabase/migrations/20260706170000_cyl_writeback_contract_a3.sql` (source of truth), keying on
      stable prefixes and fed **interpolated** (not `%`-template) messages. The scan-resolution cases
      (`no image_ids: cannot resolve a scan`, `non-numeric image_id in inputs.image_ids`,
      `unresolvable image_ids: matched 1 of 2 to a scan`,
      `image_ids resolve to 2 scans, expected exactly 1`) → actionable text naming the ids +
      profile/server; `contract_version mismatch: got …, pinned …` → expected-vs-got.
- [x] 3.6 `map_rpc_error` is **exhaustive** over the 17 RPC `RAISE EXCEPTION` strings — the
      five `invalid envelope: …` structural strings, `empty or absent idempotency_key`,
      `invalid envelope: missing provenance.scan_key`, `trait scan_key disagrees with
provenance.scan_key`, `blob scan_key disagrees with provenance.scan_key`,
      `non-scan-grain trait rejected (grain=…)`, `invalid trait: missing name`,
      `invalid blob: file_size must be an integer, got …`, plus the five in 3.5 — each maps to
      actionable **or** verbatim text; a **fabricated unknown** message and a `None`/empty message
      pass through **verbatim** (never swallowed).
- [x] 3.7 validation-stricter-than-RPC: an envelope the RPC would accept but the model rejects
      (missing a model-required, RPC-ignored field such as `provenance.params` /
      `inputs.images_checksum`) is caught by `validate_envelope` **before** any client is built.

## 4. RED — command wiring (`bloomcli/tests/test_cyl_ingest.py`)

- [x] 4.1 `call_insert_envelope` builds exactly `client.rpc("insert_cyl_result_envelope",
{"envelope": <dict>}).execute()` and returns `.data` (fake client captures the call args).
- [x] 4.2 CLI happy path: `CliRunner().invoke(cli, ["cyl", "ingest-result", <fixture>])` with
      `climod._authed_client` and `ingest.call_insert_envelope` monkeypatched → exit 0, ingested
      summary.
- [x] 4.3 CLI no-op: RPC result `was_noop=true` (null `scan_id`) → exit 0, "already ingested"
      (not an error).
- [x] 4.4 CLI no-scan: `call_insert_envelope` raises a **genuine** `postgrest.APIError({"message":
"unresolvable image_ids: matched 1 of 2 to a scan", "code": "P0001"})` → non-zero exit with the
      actionable message.
- [x] 4.5 CLI validation-fail-before-call: a malformed envelope → non-zero exit and
      `call_insert_envelope` / `_authed_client` are **never** invoked.
- [x] 4.6 CLI `--json`: prints the RPC result object (parseable, includes `source_id` +
      `was_noop`) to stdout; without `--json`, stdout is a human summary line.
- [x] 4.7 CLI auth error: missing credentials → guidance to run `bloomctl login`, non-zero exit.
- [x] 4.8 CLI permission-denied: `call_insert_envelope` raises `postgrest.APIError({"message":
"permission denied for function insert_cyl_result_envelope", "code": "42501"})` → non-zero exit
      with a message naming the RPC and the `bloom_writer`/`bloom_admin` access requirement (covers
      the "lacks write access" scenario).
- [x] 4.9 CLI blobs pass-through: an envelope with a **non-empty** `blobs` array → the dict
      captured by the fake `call_insert_envelope` contains the identical `blobs`, and no
      object-storage API is touched (fake client whose `.storage` raises if accessed); a **zero-length
      blobs** case is forwarded as `[]`, exit 0.
- [x] 4.10 CLI stdin end-to-end: `CliRunner().invoke(cli, ["cyl","ingest-result","-"],
input=<fixture bytes>)` with the RPC faked → exit 0 and ingested summary.
- [x] 4.11 CLI `--json` + no-op: `was_noop=true` with `--json` → stdout is parseable JSON with
      `"was_noop": true` and `source_id` (null `scan_id` tolerated) — the shape A4 consumes on re-run.
- [x] 4.12 CLI registration smoke test: `cyl` and `cyl ingest-result` appear in `--help`
      (analogous to `test_cli.py`'s root-help test).

## 5. GREEN — implementation

- [x] 5.1 Add `bloomcli/src/bloomctl/ingest.py`: pure helpers (`load_envelope`,
      `validate_envelope`, `summarize_result`, `map_rpc_error`) above a
      `# --- supabase / storage I/O ---` marker; `call_insert_envelope(client, envelope)` below it.
- [x] 5.2 In `bloomcli/src/bloomctl/cli.py`: add the `_authed_client(profile)` helper
      (byte-identical to #385's; see 8.2), a `@cli.group(name="cyl")` group, and a `cyl ingest-result`
      subcommand (positional envelope arg accepting `-` for stdin, `-p/--profile`, `--json`). Wire:
      load envelope → validate → authed client → `call_insert_envelope` → summarize/print. Catch
      `EnvelopeValidationError`/`AuthError`/`postgrest.APIError` (import `from postgrest import
APIError`) as `click.ClickException`; extract `exc.message` for `map_rpc_error`, guarding a
      `None`/empty message by surfacing `str(exc)` verbatim. Iterate until tasks 3–4 are GREEN.

## 6. RED/GREEN — env-gated integration test (`bloomcli/tests/test_cyl_ingest_integration.py`)

- [x] 6.1 `@pytest.mark.integration` module that self-skips at **collection/import** time when
      staging creds env is absent — a module-level guard (`pytest.skip(..., allow_module_level=True)`
      / `pytest.importorskip`), mirroring `tests/integration/test_cyl_writeback_rpc.py`. No live
      client/staging import at module top before the guard (CI collects this file unfiltered unless
      2.4 lands — and even with 2.4, keep the guard robust). Use a real authed client.
- [x] 6.2 Happy path end-to-end: after `cyl ingest-result`, exactly one `cyl_trait_sources` row and the
      expected `cyl_scan_traits` rows exist for the resolved scan (`was_noop=false`).
- [x] 6.3 Idempotency: a **second** `cyl ingest-result` returns `was_noop=true` and creates **no**
      duplicate `cyl_trait_sources` row.
- [x] 6.4 Build the envelope **from the seeded numeric `cyl_images.id`s** so it satisfies both the
      model's full a3 provenance **and** the RPC's `^[0-9]+$` + single-scan resolution; do **not**
      mutate the committed byte-stable fixture's `image_ids` (that would break `images_checksum` /
      `idempotency_key` self-consistency). Assert the resolved `scan_id` equals the seeded scan.

## 7. Blob byte-upload follow-up (tracking only — no standalone commit)

- [ ] 7.1 At PR time, file a GitHub issue in `Salk-Harnessing-Plants-Initiative/bloom` tracking
      the deferred **cyl blob (MinIO/Box) byte-upload as a future extension of
      `bloomctl cyl ingest-result`**: upload the referenced `.slp`/intermediate bytes and populate
      `blobs[].s3_location`/`box_link` before the RPC call (the "later slice"). Reference #397 and this
      change, and note the current pass-through behavior. The scaffold commit ships a placeholder
      ("issue TBD"); the final docs commit **backfills the real `#NNN`** into `proposal.md`/`design.md`
      (mirrors the repo's `link companion … issue` backfill precedent).

## 8. Docs & coordination

- [x] 8.1 Update `bloomcli/README.md` to document `bloomctl cyl ingest-result` (path/`-` stdin,
      `--profile`, `--json` output contract, interactive-auth requirement; link #398 for the
      non-interactive path) and add a short **command-layout** note (flat `login`/`download` vs
      grouped `cyl`/`list`) so the emerging convention reads coherently. Note that full `login`/
      `download` usage docs remain deferred (the README stub's existing promise) — do not regress
      them, but completing them is out of scope here. Add an `### Added` entry under `[Unreleased]`
      in `bloomcli/CHANGELOG.md` for `bloomctl cyl ingest-result` (the `prepare-release-bloomctl` flow
      depends on it). Update the `--help` text (already added in 5.2).
- [ ] 8.2 Note the `_authed_client` overlap with PR #385 in the PR description and request
      @blm3886's review. Whichever of #385/#397 merges second reconciles to a **single**
      `_authed_client` definition on rebase (drop the duplicate if identical; unify the signature
      otherwise), then re-runs `openspec validate --strict` + the bloomcli unit suite before the
      second review round.

## 9. Verify

- [x] 9.1 `openspec validate add-cyl-ingest-cli --strict` passes.
- [x] 9.2 bloomcli unit suite green **using the CI invocation** (`cd bloomcli && uv run --extra
test pytest tests/ -m "not integration" -v` — `cd bloomcli` matters: `test_download_scan.py`
      imports `SCAN` rootlessly). Because bloomcli is outside the black/ruff pre-commit globs, run the
      release-time gate manually: `uvx ruff@0.9.9 check bloomcli && uvx ruff@0.9.9 format --check
bloomcli`. Run `pre-commit run --files` over the new `.py`/`.md`/fixture and confirm prettier
      leaves the fixture byte-stable and gitleaks is clean.
- [ ] 9.3 Run the integration test against staging locally (creds present) → happy-path + no-op
      assertions pass; confirm it **skips** cleanly (not errors) when creds are absent.
- [ ] 9.4 Confirm the blob-upload follow-up issue number is filled into `proposal.md`/`design.md`
      (no `TBD`/placeholder remains).
