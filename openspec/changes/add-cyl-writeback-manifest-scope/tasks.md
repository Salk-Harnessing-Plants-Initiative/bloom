TDD throughout: write the failing test first, confirm RED, then implement to GREEN, in the same
commit. This is a staging-first, protected repo — every pushed commit must keep CI green.

`bloomcli/uv.lock` already resolves `sleap-roots-contracts` to `0.1.0a7` (no lock/pin change
needed — confirmed by reading `uv.lock` directly). Before running any test in this change, run
`uv sync` inside `bloomcli/` so the local `.venv` actually matches the lockfile — the installed
`.venv` was confirmed stale at `0.1.0a5` during review, which does not export `RunManifest`/
`RUN_MANIFEST_FILENAME` and would make every RED test in section 2 fail for the wrong reason
(`ImportError`, not the intended assertion). This is a one-time local environment step, not a
tracked file change.

**Naming convention:** this proposal's tests use `run_manifest` (not bare `manifest`) in test names
— `test_cyl_ingest.py` already has unrelated tests about a *different* manifest concept
(`test_batch_ingest_cli_predictions_dir_missing_manifest_isolates_one` etc., about predict's
per-scan `.predictions.json` manifest under `--predictions-dir`). Bare "manifest" in a new test name
would read as a near-duplicate of that existing concept on a skim/grep; every test name below is
written to avoid that collision.

**Commit boundaries (corrected during review):** `discover_envelopes` and its only caller,
`batch_ingest_result`, live in the same file (`ingest.py`) and share one contract. Sections 2 and 3
below are **one commit, not two** — landing section 2's return-type change alone would make
`batch_ingest_result`'s existing `if not envelope_paths:` / `for path in envelope_paths` code
raise `TypeError` immediately, breaking roughly a dozen already-passing `batch_ingest_result` CLI
tests. Section 2's tests are written and confirmed RED/GREEN first (as a within-commit TDD
sequence), then section 3's tests, then both land together. Do not run `git commit` between task
2.7 and task 3.5 — keep the working tree uncommitted (or amend a single local WIP commit) until 3.5
is green, then make one commit; the point of the note above is to prevent a reflexive commit right
after 2.7's GREEN task from recreating the exact split this note exists to avoid, even as a local
commit that never gets pushed. Sections 1 and 4 are separate, independent commits (docs/proposal-
only, no shared state with 2+3). Section 5 is verification, not a commit.

## 1. Proposal & specs

- [x] 1.1 `openspec validate add-cyl-writeback-manifest-scope --strict` passes (proposal.md,
      design.md, tasks.md, spec delta already written).

## 2. `discover_envelopes` — manifest-scoped discovery

- [x] 2.1 RED: add `_write_run_manifest(directory, *, scan_keys, pipeline_run_id="wf-test")` test
      helper to `bloomcli/tests/test_cyl_ingest.py` (alongside the existing `_write_envelope`/
      `_envelope_for` helpers) that writes a valid `run_manifest.json` via
      `RunManifest(...).model_dump_json()` into a given directory.
- [x] 2.2 RED: `test_discover_envelopes_scopes_to_run_manifest` — a directory with
      `scan_1.result.json` and `scan_2.result.json` on disk, a manifest listing only `scan_1`;
      assert the result's `.paths` contains only `scan_1.result.json`.
- [x] 2.3 RED: `test_discover_envelopes_no_run_manifest_is_fully_unscoped` — no `run_manifest.json`
      present; assert every `*.result.json` file is returned via `.paths`, exactly matching current
      (pre-change) behavior. This is the regression guard for the fallback path design.md calls
      load-bearing for current production state (traits' manifest-writing image not yet
      redeployed, bloom#685).
- [x] 2.4 RED: `test_discover_envelopes_missing_run_manifest_scan_key_is_reported` — manifest lists
      `scan_1` and `scan_2`, only `scan_1.result.json` exists on disk; assert `.missing_scan_keys`
      reports `["scan_2"]` (not silently dropped, not raised).
- [x] 2.5 RED: two `caplog`-based tests (at `DEBUG` level) for the exclusion log line:
      - `test_discover_envelopes_excluded_file_logs_debug` — one file present but out of the
        manifest's scope; assert exactly one debug log record naming it; a clean scoped run
        (nothing excluded) logs nothing at that level.
      - `test_discover_envelopes_multiple_excluded_files_log_one_aggregated_line` — two or more
        files out of scope; assert exactly **one** debug log record naming all of them, not one
        record per excluded file. Proposal.md and task 2.7 both describe this as "once per
        invocation," but a single-exclusion test can't distinguish "one line total" from "one line
        per file" — this second test is what actually pins that design choice.
      Mirrors predict #35's `test_excluded_out_of_scope_sidecar_logs_debug` /
      `test_no_exclusion_logs_no_debug_line` pair. Note: `caplog`/`logging` is a new pattern for
      this module — `ingest.py` has no existing `import logging`; add `import logging` + a
      module-level `logger = logging.getLogger(__name__)` as part of 2.7, not as an incidental
      one-liner. Be aware (call out in the PR description, not something to fix here): `bloomcli`
      has no logging handler configured anywhere (no `--verbose`/`--debug` flag, no
      `logging.basicConfig` call), so this debug line is only ever visible under `pytest`'s
      `caplog` today, not to an operator running the shipped CLI — real but out of scope for this
      pure-scoping change to fix.
- [x] 2.6a RED: `test_discover_envelopes_malformed_run_manifest_json_raises` — `run_manifest.json`
      content is not valid JSON at all; assert `EnvelopeError` (or a documented subclass) before
      any file is globbed or ingested.
- [x] 2.6b RED: `test_discover_envelopes_run_manifest_wrong_schema_raises` — content is valid JSON
      but doesn't conform to `RunManifest` (e.g. missing the required `scan_keys` field); assert the
      same `EnvelopeError`.
- [x] 2.6c RED: `test_discover_envelopes_unreadable_run_manifest_raises` — `run_manifest.json`
      exists but reading it raises `OSError` (e.g. `monkeypatch.setattr(Path, "read_text", ...)` to
      raise — a real permission-denied file is awkward to construct portably, especially on the
      Windows dev machine this was authored on); assert the same `EnvelopeError`, not a raw
      unwrapped `OSError` escaping the function. This exact case is why
      `download_for_predict.py:407-414`'s precedent catches `(OSError, ValidationError)` together,
      not `ValidationError` alone (a manifest file can exist but fail to *read*, independent of its
      content being malformed) — 2.6a/2.6b alone would leave half of that precedent's own
      reasoning unimplemented.
- [x] 2.7 GREEN: implement in `bloomcli/src/bloomctl/cyl/ingest.py`:
      - Add `DiscoveredEnvelopes` (`@dataclass`: `paths: list[Path]`, `missing_scan_keys: list[str]`)
        per design.md.
      - Add `import logging` + `logger = logging.getLogger(__name__)` at module level (first use of
        `logging` anywhere in `bloomcli` — not incidental, call it out in the diff/PR description).
      - `discover_envelopes` reads `envelopes_dir / RUN_MANIFEST_FILENAME` if present
        (`from sleap_roots_contracts import RUN_MANIFEST_FILENAME, RunManifest`, matching
        `download_for_predict.py`'s existing import convention); catches `(OSError,
        pydantic.ValidationError)` from reading/parsing `run_manifest.json`
        (`RunManifest.model_validate_json(...)`) and re-raises as `EnvelopeError` — matching
        `download_for_predict.py:407-414`'s precedent exactly, not a truncated version of it;
        builds `scoped_keys = set(manifest.scan_keys)`; filters the sorted glob by filename stem
        (`path.name.removesuffix(".result.json")`) before returning; computes
        `missing_scan_keys = sorted(scoped_keys - {stem for every in-scope path})`; logs excluded
        (present-but-out-of-scope) stems in a single aggregated `DEBUG` record per invocation
        (see 2.5's second test); returns `DiscoveredEnvelopes(paths=..., missing_scan_keys=...)`.
        Absent manifest → unscoped behavior, `missing_scan_keys=[]`.
      - Update `discover_envelopes`'s docstring for the new return type and manifest-aware
        behavior (it currently documents the old `list[Path]` unconditional-glob contract).
      - Update the existing nonexistent/non-directory `discover_envelopes` tests
        (`test_cyl_ingest.py:938-967`) only insofar as the return-type change requires touching
        their assertions — their asserted *behavior* (raise `EnvelopeError`) is unchanged.
      - Update `test_batch_ingest_oracle_matches_extract_batch_output_shape`
        (`test_cyl_ingest.py:1305-1316`) — it also calls `discover_envelopes` directly and asserts
        on the result as a bare list (`len(paths)`, `paths[0].parent`); this self-skips in CI via
        `pytest.importorskip("trait_extractor")` but will `TypeError` for a local/oracle run with
        `trait_extractor` installed unless updated to use `.paths`.
- [x] 2.8 Confirm 2.1-2.6c pass. This is a within-commit checkpoint, not a clean-suite gate: at this
      point `batch_ingest_result`'s existing CLI tests (the ones exercising `envelope_paths` as a
      bare list — roughly a dozen, e.g. `test_batch_ingest_cli_happy_path`,
      `test_batch_ingest_cli_empty_dir_is_noop`) are expected to be **red** until section 3's task
      3.5 lands in the same commit. Do not push (and per the note at the top of this file, do not
      `git commit`) between 2.7 and 3.5.

## 3. `batch_ingest_result` — consume the new return shape

- [x] 3.1a RED: `test_batch_ingest_result_missing_run_manifest_scan_key_is_reported_failed_json` —
      CLI invocation with `--json` against a directory with a manifest declaring a scan_key that has
      no file; assert the JSON output reports that scan_key as `failed` with a message naming it,
      and the command exits non-zero.
- [x] 3.1b RED: `test_batch_ingest_result_missing_run_manifest_scan_key_is_reported_failed_default_output`
      — same setup, default (non-JSON) output mode; assert the human-readable summary names the
      missing scan_key. (Split from a single combined test to match this file's existing
      one-test-per-output-mode convention, e.g. `test_batch_ingest_cli_mixed_statuses_json_output` /
      `..._default_output`.)
- [x] 3.1c RED: `test_batch_ingest_cli_malformed_run_manifest_makes_no_auth_call` — an invalid
      `run_manifest.json` in `envelopes_dir`; assert `_authed_client` is never called using this
      file's existing flag-dict convention (`called = {"auth": False}`,
      `monkeypatch.setattr(climod, "_authed_client", lambda p: called.__setitem__("auth", True) or
      object())`, then `assert not called["auth"]`) — matching
      `test_batch_ingest_cli_empty_dir_is_noop`/`test_batch_ingest_cli_nonexistent_dir_makes_no_call`,
      not a "raise if invoked" lambda (which this file only ever uses for downstream RPC/storage
      calls, never for `_authed_client`). Assert non-zero exit. Pins the "malformed manifest fails
      loud... makes no RPC calls" scenario at the CLI level, not just via `discover_envelopes`
      raising directly in 2.6a/2.6b/2.6c.
- [x] 3.2 RED: `test_batch_ingest_result_missing_scan_key_alone_makes_no_auth_call` — a directory
      containing *only* a manifest whose every declared scan_key is missing (no `.result.json`
      files at all); assert `_authed_client` is never called, using the same flag-dict convention as
      3.1c (not a raise-if-invoked lambda), and that the command still reports the failure(s) and
      still exits non-zero. This is the "no-op vs. reported-failure-without-auth" distinction from
      design.md — must not collapse into the existing empty-directory no-op message.
- [x] 3.3 RED: `test_batch_ingest_result_mixed_present_and_missing_scan_keys` — one scan_key present
      and ingestable, one manifest-declared scan_key missing; assert the present one is actually
      ingested via the (stubbed) RPC path, auth *is* called, and the missing one is reported
      `failed` alongside it in the same `BatchResult`.
- [x] 3.3b RED: `test_batch_ingest_cli_run_manifest_present_all_scan_keys_ingest_successfully` —
      manifest lists `scan_1` and `scan_2`, both files present and valid, no missing keys; assert
      exit 0, both reported `ok`, and no spurious `missing_scan_keys` noise in the output. Mirrors
      `test_batch_ingest_cli_happy_path` with a manifest added — the eventual common-case
      production path once traits' manifest-writing image redeploys (bloom#685), and the case most
      likely to regress silently from an off-by-one in the stem-filtering set logic.
- [x] 3.4 RED: confirm the existing empty-directory no-op test (no manifest, no files — auth never
      called, "nothing to ingest" message, exit 0) still passes conceptually against the new control
      flow before implementing 3.5 (it's an existing test, not a new one — this task is a checkpoint,
      not a new test file addition).
- [x] 3.5 GREEN: update `batch_ingest_result` (`ingest.py`) per design.md's restructured control
      flow — build `missing_results` from `discovered.missing_scan_keys` unconditionally; only the
      genuinely-nothing-at-all case (`not discovered.paths and not missing_results`) takes the
      existing no-op early return; `_authed_client` is called only when `discovered.paths` is
      non-empty; the final `BatchResult` always includes `missing_results` alongside whatever
      `ingest_one_envelope` produced. Also update `batch_ingest_result`'s own docstring — it is the
      command's `--help` text and currently asserts "every" file unconditionally
      (`ingest.py:659-661`); it needs a sentence on manifest-scoped discovery and the
      missing-scan_key failure mode.
- [x] 3.6 Confirm every checkbox added or touched in sections 2 and 3 above (2.1–2.8, 3.1a–3.3b,
      3.4) passes together — this is the actual commit-boundary checkpoint (see note at the top of
      this file: 2 and 3 land as one commit). Spelled out explicitly, not as a numeric range alone,
      so the lettered subtasks (3.1a/3.1b/3.1c/3.3b) aren't accidentally skipped by a skim-read of
      a range like "2.1-3.4".

## 4. Docs & changelog

- [x] 4.1 `bloomcli/CHANGELOG.md` — add an entry under `[Unreleased]` → `### Fixed` (this is a bug
      fix to already-shipped behavior — the unscoped-glob bug being closed — not a new capability;
      don't fold it into the existing `### Added` entry for bloom #653's manifest-*writing* work).
      Cite as bare `(#678)`, matching this file's existing citation style (e.g. `(#653)`, `(#533)`),
      not `bloom #678`.
- [x] 4.2 Update `bloomcli/README.md`'s `batch-ingest-result` section — confirmed two spots need
      updating, not conditional: the discovery-behavior bullet (currently states "every
      `{scan_key}.result.json` file" unconditionally) needs the manifest-scoping caveat, and the
      exit-code bullet (currently says "zero if... the directory was empty") needs the
      manifest-present-all-missing case called out as non-zero, not the empty case.

## 5. Verify

- [x] 5.1 Full `bloomcli` test suite passes, matching CI exactly: `uv run --extra test pytest
      tests/ -m "not integration" -v --tb=short` from `bloomcli/`.
- [x] 5.2 `ruff check .` clean for changed files, from `bloomcli/` — this is the only
      formatting/lint hook actually scoped to include `bloomcli/` in `.pre-commit-config.yaml`
      (`black` and `ruff-format` are scoped to `langchain/`, `bloommcp/`, `services/workflows/`
      only). Note this isn't a per-PR CI gate today (`pr-checks.yml` has no Python lint job; `ruff`
      runs only via local pre-commit and in `release-bloomcli.yml` at actual release time) — run it
      anyway since it's the project's own local convention for this package.
- [x] 5.3 `openspec validate add-cyl-writeback-manifest-scope --strict` still passes.
- [x] 5.4 Manual sanity check: run `bloomctl cyl batch-ingest-result` against a hand-built directory
      with a `run_manifest.json` and a mix of present/missing scan_keys, confirm the reported output
      matches what the tests assert.
