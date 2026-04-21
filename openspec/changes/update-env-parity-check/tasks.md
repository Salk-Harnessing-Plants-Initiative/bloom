## 1. Tests first (TDD)

- [x] 1.1 Create `tests/unit/__init__.py` (empty) and `tests/unit/test_verify_env_parity.py`. All 16 cases below use `tmp_path` to write minimal synthetic `deploy.yml` fixtures — no Docker stack, no network, no shared state.
- [x] 1.2 Happy-path tests:
  - `test_happy_path_single_ref_per_line` — both blocks with correctly-prefixed single-ref lines; asserts exit 0 and stdout matches regex `OK — \d+ vars, \d+ secret refs, prod and staging aligned` (note: em-dash `—`, U+2014, not hyphen-minus).
  - `test_happy_path_composite_value` — `LANGCHAIN_POSTGRES_URL` with 5 refs per line in each block, all correctly prefixed; asserts exit 0 and that the stdout `M` count reflects all refs (not just first per line).
  - `test_literal_port_line_passes` — lines like `POSTGRES_HOST_PORT=5432` with zero secret refs; asserts LHS parity passes and no prefix/suffix error.
  - `test_empty_rhs_line_passes` — both blocks contain `CADDY_HTTP_PORT=` (empty RHS after `=`); asserts LHS parity passes and no ref-based error is raised.
  - `test_lhs_suffix_differs_from_rhs_suffix_passes` — e.g. `NEXT_PUBLIC_SUPABASE_ANON_KEY=${{ secrets.PROD_ANON_KEY }}` (LHS `NEXT_PUBLIC_SUPABASE_ANON_KEY` ≠ RHS suffix `ANON_KEY`); asserts accepted (LHS and suffix checks are independent).
  - `test_alternate_terminator_accepted` — both heredocs use a non-`ENVEOF` terminator like `PROD_ENV_END` / `STAGING_ENV_END`; asserts the dynamic-terminator discovery works and exit 0.
  - `test_comments_and_blank_lines_ignored` — both blocks contain `# comment`, blank lines, and normal var lines interspersed; asserts comments/blanks do not contribute to LHS set and no error is raised for them.
- [x] 1.3 Failure-mode tests. Each asserts **all three** of:
  - exit code `== 1`
  - stderr contains a line matching regex `\.github/workflows/deploy\.yml:\d+: ` followed by the exact offending line content
  - stdout contains a line matching regex `::error file=\.github/workflows/deploy\.yml,line=\d+::.+`
  - the error description contains the failure-class substring named in each test's assertion (not just any error text)
  - Tests:
    - `test_cross_prefix_leak_in_staging_block_fails` — staging contains `POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}`; description includes `wrong-prefix` and names `PROD_POSTGRES_DB`.
    - `test_cross_prefix_leak_in_prod_block_fails` — symmetric direction; description includes `wrong-prefix` and names `STAGING_POSTGRES_DB`.
    - `test_composite_value_with_one_leaked_ref_fails` — staging `LANGCHAIN_POSTGRES_URL` where 4 refs are `STAGING_` but 1 is `PROD_`; description includes `wrong-prefix` and names the leaked `PROD_...` ref.
    - `test_suffix_present_in_prod_missing_in_staging_fails` — prod has `${{ secrets.PROD_NEW_VAR }}`, staging has no matching suffix; description includes `missing from staging` and names `NEW_VAR`.
    - `test_suffix_present_in_staging_missing_in_prod_fails` — symmetric direction; description includes `missing from prod` and names the suffix.
    - `test_lhs_missing_in_staging_fails` — prod has `NEW_VAR=...`, staging block has no `NEW_VAR=` line; description includes `LHS missing` and names `NEW_VAR`.
    - `test_literal_in_one_block_secret_in_other_fails` — prod uses `secrets.PROD_POSTGRES_DB`, staging uses literal `bloom_prod`; description includes `inconsistent` and names `POSTGRES_DB`.
    - `test_malformed_heredoc_fails_fast` — prod heredoc start without matching terminator; description includes `unclosed heredoc` and the start-line number.
    - `test_third_env_block_fails` — deploy.yml contains `.env.dev` heredoc; description includes `unexpected env block` and names `.env.dev`.
    - `test_zero_env_blocks_fails` — deploy.yml contains no matching heredocs; description is `0 env blocks found, expected 2`.
    - `test_malformed_secret_ref_lowercase_fails` — line contains `${{ secrets.prod_POSTGRES_DB }}`; description includes `malformed` and names the reference.
- [x] 1.4 Drift-guard test:
  - `test_real_deploy_yml_passes` — loads the actual repo `.github/workflows/deploy.yml` and asserts exit 0. Place FIRST in the test module so `deploy.yml` drift surfaces immediately. Note: will need fixture update when PR #146 merges (adds `POSTGRES_HOST_PORT=` literal lines — already covered by requirement 7).
- [x] 1.5 Verify all 19 tests fail before implementation (script does not exist yet). Tests + implementation land in a single commit when ready; no intermediate red commits on `main`.

## 2. Implementation

- [x] 2.1 Create `scripts/verify_env_parity.py` — pure stdlib (no extra deps, no shell-outs, no network).
- [x] 2.2 Block discovery: scan deploy.yml for lines matching `cat\s*>\s*[^<]*\.env\.([a-z][a-z0-9_]*)\s*<<\s*'([A-Z_]+)'` — captures any env name and the heredoc terminator. Then read until a line whose stripped content equals the captured terminator. After the scan, classify captured env names: `prod`/`staging` populate block dict; any other (e.g. `dev`) is an unexpected-block error with its start line. Exit 1 if not exactly 2 blocks (one prod + one staging) found.
- [x] 2.3 Per-block line parser: strip leading whitespace, match `^([A-Z][A-Z0-9_]*)=(.*)$`. Skip blank lines and lines starting with `#`. Record `(line_number, lhs, rhs_string)`.
- [x] 2.4 Per-line secret-ref scanner: apply canonical regex `\$\{\{\s*secrets\.([^}\s]+)\s*\}\}` globally (`re.findall`) — extracts every `secrets.X` occurrence. Then validate each captured name against `^(PROD|STAGING)_[A-Z][A-Z0-9_]*$`. A name that matches the outer bracket-aware regex but fails the strict inner pattern is MALFORMED and raises an error (no silent skip). A line with zero findings is a literal-only line (requirement 7).
- [x] 2.5 Build a per-block dict keyed by LHS: `{lhs: (line_number, rhs_string, [secret_refs])}`. Iterate the union of prod and staging LHS keys for pairing — do NOT zip by position (line order between blocks may differ). Then implement the five validations:
  - (a) Exactly 2 env blocks discovered, one prod one staging.
  - (b) LHS var name set identical between blocks.
  - (c) Every secret ref inside prod block matches `^PROD_[A-Z][A-Z0-9_]*$`; inside staging `^STAGING_[A-Z][A-Z0-9_]*$`.
  - (d) Suffix sets equal between blocks.
  - (e) Per-LHS consistency: for every LHS key present in both blocks, if `len(prod[lhs].refs) > 0` then `len(staging[lhs].refs) > 0` must also hold (and vice versa).
- [x] 2.6 Fail-fast with actionable messages. Every error emits BOTH channels with a failure-class prefix in the description (one of: `wrong-prefix`, `missing from staging`, `missing from prod`, `LHS missing`, `inconsistent`, `unclosed heredoc`, `unexpected env block`, `0 env blocks found, expected 2`, `malformed`):
  - stderr: human-readable `<path>:<line>: <class>: <description>` (followed by the offending line content on the next line)
  - stdout: GitHub Actions annotation `::error file=<path>,line=<line>::<class>: <description>`
- [x] 2.7 Happy-path: print to stdout the exact format `OK — <N> vars, <M> secret refs, prod and staging aligned` using em-dash U+2014. Exit 0. (No `M >= N` invariant.)
- [x] 2.8 Verify all 19 tests from Section 1 now pass.

## 3. Wire into CI

- [x] 3.1 Replace the inline bash block in the `verify-env-parity` job of `.github/workflows/pr-checks.yml` with a single step: `python3 scripts/verify_env_parity.py .github/workflows/deploy.yml`. KEEP the job name `verify-env-parity` identical (branch protection references it).
- [x] 3.2 Keep `runs-on: ubuntu-latest` and the checkout step unchanged. No install step needed for the verification script — Python 3.10+ is preinstalled on `ubuntu-latest` and the script is stdlib-only.
- [x] 3.3 Confirm job runs in <5 seconds for the verification step.
- [x] 3.4 Add a second step in the same job that runs the test suite: `uv run --with pytest pytest tests/unit/test_verify_env_parity.py -v`. This matches the pattern used in task 4.2 and the existing `python-audit` job (which installs `uv`). Rationale: guards against the script being weakened in a PR that doesn't also touch the test file. `uv` is preinstalled on `ubuntu-latest`; `--with pytest` pulls pytest on demand without persistent installs.

## 4. Validate

- [x] 4.1 `python3 scripts/verify_env_parity.py .github/workflows/deploy.yml` exits 0 on the current file.
- [x] 4.2 `uv run --with pytest pytest tests/unit/test_verify_env_parity.py -v` — all 19 cases pass.
- [x] 4.3 `openspec validate update-env-parity-check --strict` passes.
- [ ] 4.4 Fault injection via draft PR: open a draft PR with a deliberate leak — add `POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}` inside the staging block. Confirm `verify-env-parity` fails on the PR with the cross-prefix annotation pointing at the line (visible inline on the PR diff). Close the draft PR without merging.
- [x] 4.5 Confirm `verify-env-parity` job name appears identically in both old and new CI (no branch-protection breakage).
- [x] 4.6 Confirm with repo admin that `verify-env-parity` is in the `main` branch-protection required-checks list. If missing, this check only advises PR authors — it does not block merge. File a follow-up to add it if currently absent.

## 5. Reproduction guide (for PR authors who hit a failure)

Include in the script's `--help` output and in the failure annotation suffix:

```
To reproduce locally:
  python3 scripts/verify_env_parity.py .github/workflows/deploy.yml

Expected format on cross-prefix leak:
  .github/workflows/deploy.yml:215: wrong-prefix: PROD_POSTGRES_DB appears in staging block
  <offending line content>
```
