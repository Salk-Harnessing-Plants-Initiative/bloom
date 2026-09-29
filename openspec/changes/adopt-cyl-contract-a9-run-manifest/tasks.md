Sections 2–5 each pair a RED step with its GREEN step, and each pair lands as one commit (see Section 9). Never push a red-only commit.

- **Test command at every commit:** `cd bloomcli && uv run --locked --extra test pytest tests/ -m "not integration" -q`. Use `--locked`, not CI's plain `--extra test` (`pr-checks.yml:188`), because a stale lock would otherwise be re-resolved silently.
- **Lint:** `uvx ruff@0.9.9 check bloomcli/`. Don't reformat untouched code: `ruff format --check` already fails on 34 bloomcli files, and pre-commit's black/ruff-format exclude `bloomcli/`.
- **Regression guard:** a test marked **(guard)** already passes against today's code. It pins behavior that must not change and is not part of the RED set.

## 1. Spec repair of `fix-cyl-pipeline-run-scan-status` (commit A1)

- [x] 1.1 Rebuild that change's stale `cyl-trait-writeback` "Write-back RPC ingests a ResultEnvelope" block as the live text plus its one unarchived scenario and one explanatory sentence (design Decision 6), and check it with a diff against `openspec/specs/cyl-trait-writeback/spec.md`.

## 2. Proposal and takeover (commit A2)

- [x] 2.1 This change's scaffold (`proposal.md`, `design.md`, `tasks.md`, `specs/**`).
- [x] 2.2 In the same commit, delete that change's `specs/cyl-batch-ingest-result/spec.md` and `specs/cyl-pipeline-runs/spec.md`, which this change takes over (design Decision 6). Also:
  - [x] Annotate its `proposal.md` Impact: those two specs now go through `adopt-cyl-contract-a9-run-manifest`.
  - [x] Annotate its `tasks.md` 8.2–8.4: the counts come from roadmap row 6's Bloom-dispatched N=1/N=3 test.
- [x] 2.3 Both `openspec validate adopt-cyl-contract-a9-run-manifest --strict` and `openspec validate fix-cyl-pipeline-run-scan-status --strict` pass.
- [x] 2.4 A grep of every active change's `specs/**` shows each requirement header this change modifies in exactly one active change.

## 3. Dependency: contracts 0.1.0a9 (commit B)

- [x] 3.1 RED: in `bloomcli/tests/test_contracts_pin.py`, import `pipeline_run_id_from_env`, `run_manifest_name_for_writing`, `load_run_manifest`, `RunManifestMissingError` and `RunManifestIdentityError`. Assert `run_manifest_name_for_writing(None) == "run_manifest.json"` and `run_manifest_name_for_writing("wf-1") == "run_manifest.wf-1.json"`. Keep `test_run_manifest_filename_is_the_pinned_literal`.
  - Confirm `ImportError` on a7.
  - These tests prove the *resolved* environment has a9, not that the committed lock does. 3.2's checks cover the lock.
- [x] 3.2 GREEN:
  - [x] Set the pin to `"sleap-roots-contracts>=0.1.0a9"` and update the comment above it.
  - [x] Run `cd bloomcli && uv lock --upgrade-package sleap-roots-contracts`.
  - [x] `git diff -- bloomcli/uv.lock` must touch only the specifier and the `sleap-roots-contracts` package entry, with `version = "0.1.0a9"` and no header/`revision` churn.
  - [x] From the repo root, `python scripts/check-uv-locks.py` passes. It runs `uv lock --check` for every service, including bloomcli.
  - [x] In Git Bash, `cd bloomcli && uv export --frozen --no-hashes | grep sleap-roots-contracts` shows `0.1.0a9`.
  - [x] `git diff` may show a whole-file `uv.lock` change on Windows, because the worktree checks the file out with CRLF and uv writes LF. With `text=auto`, git normalizes it, so check `git diff --ignore-cr-at-eol` if that happens.
- [x] 3.3 Run the full suite on a9 before any code change. It must pass unchanged, because every contracts symbol bloomctl imports (`RunManifest`, `RUN_MANIFEST_FILENAME`, `ResultEnvelope`, `InputRef`, `PredictionManifest`, `resolve_params`) behaves the same in a7 and a9. a8's `ModelCard` `selectors` rework touches nothing bloomctl uses.

## 4. Writer: per-run name, overwrite, fail fast (commit C)

- [x] 4.1 Add an autouse `delenv("ARGO_WORKFLOW_NAME")` fixture to `test_cyl_download_for_predict.py`, as `test_cyl_ingest.py:62-69` has.
- [x] 4.2 RED: change `_read_manifest(out_dir)` (~l.1279) to `_read_manifest(out_dir, name="run_manifest.json")`. Then add or change these tests:
  - [x] **Per-run name.** `ARGO_WORKFLOW_NAME=wf-abc123` writes `run_manifest.wf-abc123.json` with `pipeline_run_id == "wf-abc123"`, and `run_manifest.json` does not exist. Also change `…pipeline_run_id_from_argo_workflow_name` (~l.1331), which sets the variable and reads the legacy name, to read the per-run name.
  - [x] **Padded id.** `" wf-abc123\n"` names the file `run_manifest.wf-abc123.json` and stamps `"wf-abc123"`.
  - [x] **Blank id.** `"   "` writes `run_manifest.json` stamped `local-[0-9a-f]{8}`. The existing unset-case tests (`…falls_back_to_generated_local_placeholder`, `…two_invocations…distinguishable_ids`) are **(guard)**.
  - [x] **Retry overwrite.** Pre-stage `scan_2` so it really comes back `skipped`, by writing its sidecar as ~l.1312-1318 does or calling `dfp.stage_one_scan(auth.make_authed_client(None), 2, out)` as ~l.1606-1609 does. Confirm the skip through `--json`. Pre-write `run_manifest.wf-abc123.json` with `[scan_1, scan_2]`, then stage `[2, 3]`. The result must be exactly `[scan_2, scan_3]`. This replaces `…second_invocation_merges_disjoint_scan_keys`.
  - [x] **Duplicate scan_id.** `--scan-ids 2,2,3` gives exactly `[scan_2, scan_3]`. This replaces `…overlapping_scan_keys_has_no_duplicates`. Without an id it is a **(guard)**, since today's set already dedups. The RED variant sets `ARGO_WORKFLOW_NAME=wf-abc123` and reads `run_manifest.wf-abc123.json`.
  - [x] **No-id overwrite.** A prior legacy `[scan_1, scan_2]`, then staging `3`, gives `[scan_3]`.
  - [x] **Stale legacy untouched.** Using the l.1434 fixture (`wf-old`/`scan_9`) with the id set, the legacy file stays byte-identical and the per-run file holds only this run's keys.
  - [x] **Corrupt file replaced.** A corrupt same-name file is replaced by a valid manifest, and the command exits `0`. This replaces `…corrupt_existing_manifest_fails_loud…` (~l.1490).
  - [x] **Invalid id.** `"../wf"` exits `1` naming the value, and none of the following happen:
    - a call to `make_authed_client` or `stage_one_scan` (spies)
    - creation of a `.locks/` entry
    - creation of any `run_manifest*` file

    With empty `--scan-ids` and the same value, it exits `0` and writes nothing: the name is resolved after the empty-input return (spec scenario "An invalid run identity fails before any staging"). Spy on `climod._authed_client` as well as `auth.make_authed_client`.
  - [x] **Length bounds.** A 237-character id is written. A 238-character id exits `1` before staging. The 237-character case makes a path longer than 260 characters, which works on Windows only with `LongPathsEnabled`. Skip it with a clear reason where `OSError` shows the path is too long.
  - [x] **All-failed.** Every scan fails: exit `3`, nothing written, and an existing same-name file stays byte-identical. Without an id this is RED, because today's union rewrites the legacy file with a new `local-*` id. With `wf-abc123` it is a **(guard)**.
  - [x] **Lock with an id (guard).** Add one variant of the manifest-lock contention test with the id set, asserting the per-run file isn't created or modified. It passes today, because today's code never creates a per-run file; the RED coverage for per-run names comes from **Per-run name**. The existing no-id contention and `OSError` tests (~l.1428, 1463, 1636) are **(guard)**. Their l.1649 `Path(path).name == RUN_MANIFEST_FILENAME` match stays valid, because no id means the legacy name.

      Confirm the new and changed tests fail against today's code.
- [x] 4.3 GREEN:
  - [x] Change `resolve_pipeline_run_id(run_id: str | None) -> str` to return `run_id or f"local-{uuid4().hex[:8]}"`, with no env read.
  - [x] In `batch_download_for_predict`, after the empty-input return (~l.622) and before `_authed_client`: call `pipeline_run_id_from_env()` once, compute the name, and turn a `ValueError` into `ClickException`. The both/neither `UsageError` (exit `2`) and the `--lock-staleness-seconds` check still come first.
  - [x] `write_run_manifest(out_dir, result, *, manifest_name, pipeline_run_id, staleness_seconds)` writes `sorted(set(usable))` under the lock with `atomic_write_bytes`, with no read, parse or merge. It returns early when the list is empty.
  - [x] Delete the corrupt-existing branch.
  - [x] Update the section header (l.445), the docstrings (l.448-469), the `--help` text (~l.575-584, including its exit-code paragraph, which gains the invalid-id exit `1`), and the `_locks.py:24` comment. Cite sleap-roots-pipeline#71's design doc §2.3/§2.4, not this change's design.md, which is archived later.
- [x] 4.4 Section 4.2 passes, and the full suite passes. That includes `test_cyl_locks.py` and `test_download_for_predict_concurrency.py`, which imports this file's `_patch_batch`/`_patch_common`/`_FakeClient` helpers.

## 5. Reader: `load_run_manifest`, fail loud, reconcile (commit D)

- [x] 5.1 Signature: `discover_envelopes(envelopes_dir, pipeline_run_id: str | None = None)`. Add the parameter first, accepted and ignored, so every RED test in 5.2 and 6.1 fails on an assertion, not a `TypeError`. The default keeps the 17 existing one-argument call sites (`test_cyl_ingest.py:1197`–`1357`, `:2240`) valid and meaning "no run identity". Discover-level tests pass the id as an argument. Env-var cases (padded, blank) are tested only at the CLI level, because `load_run_manifest` rejects `"   "` with `ValueError` and only `pipeline_run_id_from_env` strips.
- [x] 5.2 RED at discover level: extend `_write_run_manifest` (~l.1188) with a `filename=` argument (default legacy), then:
  - [x] **Per-run scopes.** With the id set, the per-run file scopes discovery.
  - [x] **Per-run wins.** The per-run file wins over a stale legacy file.
  - [x] **Other run ignored.** Another run's per-run file is ignored.
  - [x] **Stale legacy warns.** A legacy fallback naming another run scopes to it and emits one `WARNING` naming both ids. Use `caplog.at_level("WARNING", logger="bloomctl.cyl.ingest")`.
  - [x] **Same-run legacy is quiet.** A legacy fallback naming the same run emits no warning.
  - [x] **Id mismatch.** A per-run file with a different `pipeline_run_id` raises `EnvelopeError`.
  - [x] **Missing with an id.** With the id set and neither file present, it raises `RunManifestNotFoundError(EnvelopeError)` carrying both names.
  - [x] **Invalid id.** An id of `"../wf"` raises `EnvelopeError`, not a bare `ValueError`.
  - [x] **Dangling symlink.** A dangling symlink at the per-run path, with a legacy file listing `scan_2` beside it, raises. It must not fall through to the legacy file. Follow sleap-roots `tests/trait_extractor/test_batch.py:852-863`: `try: os.symlink(...) except OSError: pytest.skip(...)`.
  - [x] **Rewritten tests.** Rewrite `…unreadable` (~l.1318) and `…permission_error` (~l.1339). They patch `Path.read_text`, which `load_run_manifest` no longer calls (it uses `(base / name).open("rb")`), so after GREEN they would fail because nothing raises. Patch `Path.open` instead:
    - raise `OSError`/`PermissionError` only when `self.name == target_name`, so the legacy variant's per-run candidate is absent, not raising;
    - install the patch after `_write_run_manifest`, since `write_text` goes through `self.open`;
    - parameterize over the legacy and per-run names. The no-id legacy variants are **(guard)**, because `Path.read_text` calls `self.open` on Python 3.11-3.13.

    Parameterize the malformed/wrong-schema/as-directory tests too. *(Not done in commit D despite the tick; done in 11.6.)*
  - [x] **Guards.** No id with no legacy file stays unscoped even with `run_manifest.wf-a.json` present, and no id with a legacy file scopes to it. Both are **(guard)**.
  - [x] **Single load call.** `monkeypatch.setattr(ing, "load_run_manifest", spy)` (the name as imported into the ingest module, as in sleap-roots `test_batch.py:884`), then assert `calls == [((Path(tmp_path), "wf-a"), {"allow_legacy": True})]`.
  - [x] **Messages.** The debug log and the missing-scan_key message both name the manifest file that was actually read.
- [x] 5.3 RED at CLI level (`batch-ingest-result`, CliRunner, `_authed_client` and RPCs mocked):
  - [x] **Existing tests.** Every existing batch test that sets `ARGO_WORKFLOW_NAME` but writes no manifest now gets a per-run manifest via `_write_run_manifest(..., pipeline_run_id=<id>, filename=f"run_manifest.{id}.json")`. That covers ~l.1530, 1745, 1826, 1859, 1892, 1920, 1942, 1969, 2028, 2113, 2146 and 2176. Also convert ~l.1801 (`…missing_scan_key_alone_still_reconciles_when_workflow_name_set`): it sets `wf-missing-only` but writes a legacy manifest stamped `"wf-test"`, which would otherwise take the stale-legacy warning path. Each per-run manifest must list every envelope stem its test asserts on (e.g. l.1530 needs `"scan_corrupt"`), or that entry is excluded as out of scope. Re-express l.1779 (zero envelopes, exit 0) and l.2002 (`len(payload) == 1`) against the new missing-manifest path.
  - [x] **Missing manifest, id set.** No `insert_cyl_result_envelope` call even though `scan_1.result.json` exists. Exactly one reconcile call, with the id. A failed retriable entry with the `scan_key` sentinel `"<run-manifest>"` that names both files, in both the summary and `--json`. Exit `1`.
  - [x] **Missing manifest, reconcile raises.** Both failures are reported, the exit is `1`, and there is no traceback.
  - [x] **No reconcile on other failures.** For a per-run manifest that is malformed, names another run, raises `PermissionError`, or is a directory or dangling symlink, and for an invalid id `"../wf"`. *(The dangling-symlink case was not added in commit D despite the tick; added in 11.6.)* exit `1`, `_authed_client` never called, `reconcile_unresolved_scans` never called.
  - [x] **Padded id.** `ARGO_WORKFLOW_NAME=" wf-a\n"` with `run_manifest.wf-a.json` listing `scan_1`, and `scan_1.result.json` and `scan_2.result.json` present: only `scan_1` is ingested.
  - [x] **Blank id (guard).** `"   "`, no manifest, `scan_1.result.json` and `scan_2.result.json` present: both are ingested (unscoped), exit `0`, exactly one reconcile call under `"   "`. Repeat with an empty directory: exit `0`, one reconcile call. It passes today; the spec scenario "A blank ARGO_WORKFLOW_NAME is unscoped but still reconciles once" pins it.
  - [x] **No id, only missing keys.** A legacy manifest declaring only missing keys still makes no `_authed_client` call. This keeps the existing test ~l.2330 (`…missing_scan_key_alone_makes_no_auth_call`) as a **(guard)**.
  - [x] **Missing-key message.** It names `run_manifest.wf-a.json`. No existing test asserts the old literal text, so assert on the filename.

      Confirm the 5.2 and 5.3 tests fail against today's code, apart from the guards.
- [x] 5.4 GREEN:
  - [x] Imports.
  - [x] `discover_envelopes` calls `load_run_manifest(path, pipeline_run_id, allow_legacy=True)`, with a comment citing sleap-roots-pipeline#82.
  - [x] Catch in this order:
    1. `RunManifestMissingError` → `RunManifestNotFoundError`.
    2. `RunManifestIdentityError`, `ValidationError`, `ValueError` and `OSError` → `EnvelopeError`, in one clause.
  - [x] Warn on a legacy fallback whose `pipeline_run_id` differs from the id.
  - [x] Add `DiscoveredEnvelopes.manifest_filename: str | None`.
  - [x] In `batch_ingest_result`, call `discover_envelopes(envelopes_dir, pipeline_run_id_from_env())`. On `RunManifestNotFoundError`: authenticate, call `_reconcile_unresolved_scans_result`, emit the `BatchResult`, and exit `1`. Any other `EnvelopeError` keeps today's `ClickException` path.
  - [x] Keep the `not paths and not missing` branch's reconciliation call. With a run identity the branch can't be reached, but the blank-value edge still reaches it with a truthy `ARGO_WORKFLOW_NAME` (design Decision 3).
  - [x] Leave `resolve_argo_workflow_name()` unchanged.
  - [x] Update the docstrings (l.82, 94-104), the `--help` text (l.1044-1051) and the debug log text (l.150).
- [x] 5.5 Sections 5.2 and 5.3 pass, and so does the full suite.

## 6. Writer → reader handoff (commit D)

- [x] 6.1 RED, written before 5.4 lands, in its own `tests/test_run_manifest_handoff.py` (importing from `test_cyl_download_for_predict`, as `test_download_for_predict_concurrency.py:19` does):
  1. Drive `batch-download-for-predict` into `tmp/stage` with `ARGO_WORKFLOW_NAME=" wf-e2e"`, reusing `_patch_batch`.
  2. Copy the written `run_manifest.*.json` by its own name into `tmp/traits`, standing in for predict/traits forwarding under `read.filename`.
  3. Add `scan_1.result.json`, a stray `scan_9.result.json`, and a stale legacy `run_manifest.json` listing `scan_9`.
  4. Call `discover_envelopes(tmp/traits, pipeline_run_id_from_env())`.

  Assert that exactly one file in `tmp/stage` matches `run_manifest.*.json` (a glob that cannot match `run_manifest.json`), and that only `scan_1` is in scope. This exercises a strip or naming mismatch between writer and reader.

## 7. Docs (commit E)

- [x] 7.1 `bloomcli/README.md`:
  - [x] l.459-482: the per-run name, overwrite, the no-id legacy name, and an invalid id exiting `1`.
  - [x] l.660-666: ingest resolution. l.665-666 ("With no manifest present, discovery is fully unscoped") holds only without a run id.
  - [x] l.686-695: the exit-zero and zero-envelope statements no longer hold when an id is set with no manifest.
  - [x] Near l.697, which documents `"<reconciliation>"`: document the `"<run-manifest>"` sentinel entry.
  - [x] A note on the `allow_legacy=True` rollout.
- [x] 7.2 `bloomcli/CHANGELOG.md` `[Unreleased]`:
  - [x] Add a `### Changed` section between Added and Fixed, following Keep a Changelog order.
  - [x] Two entries, each starting `**Breaking:**`: the writer overwrites and names per run; the reader fails when it knows its run id and has no manifest.
  - [x] One entry worded like the l.131 precedent: "Bumped the `sleap-roots-contracts` floor to `>=0.1.0a9` for …".
  - [x] Don't touch the historical 0.1.0a5 entry.
- [x] 7.3 `contracts/README.md:48`: "With `ARGO_WORKFLOW_NAME` set, bloomctl writes and reads `run_manifest.<id>.json` (reads fall back to the legacy name via `allow_legacy=True`); without it, it uses `run_manifest.json`."

## 8. Verification

- [x] 8.1 Full suite with `--locked` at every commit B–E. Record the pass counts in each commit message.
- [x] 8.2 `uvx ruff@0.9.9 check bloomcli/` and, from the repo root, `python scripts/check-uv-locks.py`. Then the bloomcli pip-audit step:
  - CI's command (`pr-checks.yml:242`): `cd bloomcli && uv export --frozen --no-hashes --extra scrna | uvx pip-audit@2.10.0 -r /dev/stdin`.
  - On Windows (`/dev/stdin` fails in Git Bash): `uv export --frozen --no-hashes --extra scrna -o <scratchpad>/req.txt && uvx pip-audit@2.10.0 -r <scratchpad>/req.txt`. Keep `--extra scrna`.
- [x] 8.3 Build the image locally and check `docker run --rm --entrypoint python <img> -c "import importlib.metadata as m; print(m.version('sleap-roots-contracts'))"` prints `0.1.0a9`. The PR-time `bloomcli:ci` image never leaves the runner, so it can't be used.
- [x] 8.4 Grep `openspec/specs/**`, `openspec/changes/*/specs/**`, `bloomcli/**`, `contracts/**` and `services/workflows/**` for leftover claims that contradict the new behavior (`run_manifest.json` literals, "union", "merge"). Fix the ones in files this change owns, and list the rest. The `repin-cyl-contract-a9` design.md:31 and tasks.md:63 claim ("bloomctl still writes and reads the legacy") goes stale, but it is change history, so leave it.
- [x] 8.5 Two spec scenarios have no automated test: cyl-pipeline-runs "A batch in which every scan failed at images-downloader reads failed" and cyl-pipeline-status-polling "A batch whose downloader staged nothing rolls up to failed". Only 10.3 step 5 (live) verifies them.
- [x] 8.6 `/pre-merge`.

## 9. Commit plan (one PR against `staging`)

- **Merge method:** the user merges, and must **squash-merge**. The repo also allows merge and rebase commits (#823 was a merge commit). Reverting or cherry-picking C or D alone would leave a writer and reader that don't match.
- **PR title:** a plain sentence, as recent staging PRs use, e.g. "Name bloomctl's run manifest per run and fail when it is missing (contracts a9)". Put the breaking-change note in the body.
- **Closing keywords:** the PR says `Part of #934`, not a closing keyword. `auto-close-issues-on-staging.yml` reads only the PR title and body, and would close #934 on the staging merge, before row 6's verification.
- **Commit messages:** they must also avoid close/fix/resolve + `#N`, including cross-repo references such as `talmolab/sleap-roots-pipeline#71`. The squash body is the joined commit messages (`COMMIT_MESSAGES`), and it reaches `main` at promotion, where GitHub's own keyword closing applies to commits. Don't paste `fix-cyl-pipeline-run-scan-status/design.md:138` ("fix #1 hardens") into either.
- **Commit bodies:** keep them short. At merge, trim the squash body to the PR summary.

- [x] 9.1 A1 `docs(openspec): rebuild fix-cyl-pipeline-run-scan-status's stale cyl-trait-writeback delta` (Section 1)
- [x] 9.2 A2 `docs(openspec): propose adopt-cyl-contract-a9-run-manifest` (Section 2)
- [x] 9.3 B `build(bloomctl): require sleap-roots-contracts 0.1.0a9` (Section 3)
- [x] 9.4 C `feat(bloomctl)!: name the run manifest per run and overwrite it` (Section 4). Never revert or cherry-pick this commit without D, since its writer doesn't match the old reader.
- [x] 9.5 D `feat(bloomctl)!: resolve the run manifest by run id and fail when it is missing` (Sections 5 and 6)
- [x] 9.6 E `docs(bloomctl): document per-run run manifests` (Section 7)
- [x] 9.7 F `docs(openspec): tick adopt-cyl-contract-a9-run-manifest tasks`

## 10. Post-merge (each step needs explicit user approval)

- [ ] 10.1 Wait for `docker-build-bloomcli` on the staging push. Its `cancel-in-progress` can drop the build if another bloomcli push lands at the same time. `workflow_dispatch` can't target a SHA; it builds the branch tip and shares the concurrency group. So:
  - if the merge's own build succeeded, use `sha-<merge short>`;
  - if it was cancelled because a later bloomcli push landed, pin that later push's image, which contains the merge, and re-derive the carried-along PR list from `git log 28034f6d..<that sha> -- bloomcli`;
  - dispatch only while staging's tip is still the merge commit.
  - [ ] Confirm the image's `org.opencontainers.image.revision` label with `docker buildx imagetools inspect <ref> --format '{{json .Image.Config.Labels}}'`, and its contracts version is `0.1.0a9` (8.3's command).
  - [ ] Record the digest.
- [ ] 10.2 In sleap-roots-pipeline `docs/bloom-integration/roadmap.md`:
  - [ ] Correct l.410-411, l.480-484 and l.1010-1011. The unbounded pin did not adopt a9: `uv.lock` plus `--frozen` held a7.
  - [ ] Update l.418 and l.705 ("still on the pre-a9 image").
  - [ ] Tick row 5 as "merged, image `sha-<X>`", noting that the bump also carries #880, #882, #884 and #861.
- [ ] 10.3 Row 6, in sleap-roots-pipeline:
  1. Bump all three template pins in one step to `sha-<X>@sha256:<digest>`, as predictor/traits already pin. Update the pin-history comments (images-downloader l.20-26, write-back l.16).
  2. Run `check_manifests.py`, then drain, then run `argo template update`.
  3. Snapshot the three stale `run_manifest.json` files to a dated folder, then delete them.
  4. Run the Bloom-dispatched live test against `A4-PIPELINE-E2E-TEST` at N=1 and N=3. Assert:
     - the manifest is `run_manifest.<wf>.json` with exactly N `scan_keys`
     - write-back reports `Ingested N/N`
     - `cyl_trait_sources` gains exactly N rows
     - no `run_manifest.json` reappears
     - #880's fallback path is not triggered unexpectedly
  5. Add one all-fail batch, using a scan with zero `cyl_images` so it fails at `images-downloader`. Assert `status = 'failed'` and `failed_count = scan_count`.
  6. Record `done_count`/`failed_count` for each run, which also supplies `fix-cyl-pipeline-run-scan-status` 8.2–8.4.
  7. Rollback plan, written down before starting: revert the pins, run `argo template update`, and **restore** the snapshotted legacy files to all three directories if step 3 has already deleted them (design, Rollout and rollback).
- [x] 10.4 File the sleap-roots follow-up drafted with this change (traits' manifest forward is best-effort; make it fail loudly as predict's does; design Decision 4), with the user's approval. Filed 2026-09-29 as talmolab/sleap-roots#271.
- [ ] 10.5 After 10.3 passes:
  - [ ] Close bloom#934 by hand.
  - [ ] Open one archive PR that archives `fix-cyl-pipeline-run-scan-status` first, then this change, and fills that change's placeholder Purpose sections (its 15.3).
  - [ ] Tick roadmap row 6.
  - [ ] The staging→main promotion PR must not carry a closing keyword for #934.

## 11. Post-review revisions (PR #940 review, 2026-09-29)

The user decided two points in the review: a legacy file naming another run is no manifest for this run, and there is one run identity everywhere. The specs and design above already reflect both. Each item below is RED then GREEN in one commit, per Section 9's rules.

- [x] 11.1 Specs, design, proposal and tasks updated, and `cyl-ingest-cli` "Cyl ingest command reads an envelope from a path or stdin" taken over from `fix-cyl-pipeline-run-scan-status` (its delta file removed there). `openspec validate --strict` passes for both changes, and a header grep shows one owner per modified requirement. (commit G)
- [x] 11.2 **One run identity.**
  - RED:
    - `ARGO_WORKFLOW_NAME=" wf-x\n"` on `ingest-result` gives `p_argo_workflow_name == "wf-x"`.
    - `"   "` omits the key.
    - `resolve_argo_workflow_name()` returns `"wf-x"` / `None`.
    - In the batch padded test, `_record_inserts` captures `argo_workflow_name`, and both it and the reconcile argument equal `"wf-a"`.
    - Blank `"   "` gives no reconcile call and inserts without `argo_workflow_name`. This replaces the old blank-reconciles guard.
  - GREEN: `resolve_argo_workflow_name()` returns `pipeline_run_id_from_env()`. `batch_ingest_result` reads the identity once and passes it to `discover_envelopes`.
- [ ] 11.3 **A legacy file naming another run is no manifest for this run.**
  - RED:
    - Discover level: raises `RunManifestNotFoundError`, and the message names both ids.
    - CLI level: no insert, one reconcile, a `"<run-manifest>"` entry naming both ids, exit `1`.
    - Same-run legacy still scopes, with no warning.
    - No-id legacy stamped with any id: scopes, zero WARNING records.
  - GREEN: in `discover_envelopes`, a non-per-run read whose `pipeline_run_id` differs from the identity raises `RunManifestNotFoundError`. Build the per-run name with `run_manifest_filename`. Remove the warning path.
- [ ] 11.4 **The missing-manifest path goes through the normal tail, with a cause-specific reconcile message.**
  - RED: the reconcile call receives an `error_message` saying no run manifest reached write-back. The normal path still sends "no result produced for this scan by write-back". The exit follows `needs_retry`.
  - GREEN:
    - `reconcile_unresolved_scans(client, name, *, error_message=...)` and `_reconcile_unresolved_scans_result(..., error_message=...)`.
    - The `except RunManifestNotFoundError` handler seeds a `"<run-manifest>"` entry and an empty discovery, and falls through to the existing auth/reconcile/emit/exit code.
    - Delete the duplicated emit/exit block.
- [ ] 11.5 **Code hygiene.**
  - Catch `(RunManifestError, ValueError, OSError)` after `RunManifestMissingError`.
  - Make `discover_envelopes(envelopes_dir, *, pipeline_run_id)` keyword-only and required, and update every call site.
  - Hoist the `"<run-manifest>"` and `"<reconciliation>"` sentinels into module constants.
  - Rename `resolve_pipeline_run_id` to `stamped_pipeline_run_id`.
  - Make the writer's invalid-id message name `PIPELINE_RUN_ID_ENV_VAR` and say "whitespace-stripped".
  - Guard: the existing suite stays green.
- [ ] 11.6 **Tests the review found missing or weak.**
  - Per-run variants of the malformed, wrong-schema and directory discover tests.
  - A dangling-symlink case in the CLI "never authenticates or reconciles" test.
  - `assert not isinstance(exc, RunManifestNotFoundError)` on the per-run unreadable, mismatch and invalid-id discover tests.
  - Skip the 237-character test only on `sys.platform == "win32"`.
  - The 238-character test also asserts that `_authed_client` is never called.
  - Rename the zero-envelope reconcile test to say what it now tests, and tighten it to `== 1`.
- [ ] 11.7 **Docs:** README (fallback, blank, run identity, manual-recovery warning), CHANGELOG (the run-identity breaking entry, the legacy-other-run rule) and the `batch-ingest-result` `--help`.
- [ ] 11.8 Full suite with `--locked` compared with the Windows baseline, `uvx ruff@0.9.9 check bloomcli/`, and both `openspec validate --strict`. Push, then confirm the PR's `Python Security Audit for CVEs` job (bloomctl's Linux test run) passes, including the dangling-symlink tests' first real run.
