## 1. Test infrastructure prerequisite

- [x] 1.1 Add `pyyaml>=6` to the `test` extra in the root `pyproject.toml` (alphabetically ordered between `psycopg` and `langchain-core` is fine; or appended — the existing list has no strict order). Verify the addition resolves: `uv lock --check` (or, if not in a uv-managed root project, just `uv run --extra test python -c "import yaml; print(yaml.__version__)"`).

## 2. Test-first: regression-guard test (TDD red)

- [x] 2.1 Create `tests/unit/test_ci_workflow_uv_conventions.py`. The test SHALL:
  - Discover workflow files via `pathlib.Path(REPO_ROOT, ".github/workflows").glob("*.yml")` AND `glob("*.yaml")` (both extensions).
  - Parse each with `yaml.safe_load()`.
  - For each `(workflow_file, job_name, step)` triple, walk `step.get("run", "")` and `step.get("uses", "")` to evaluate the invariants.
  - Emit failure messages of the form `f"{workflow_file}: job '{job_name}': step {idx} ({step.get('name','<unnamed>')}): <invariant> violated: <evidence>"` so a CI reader can locate the offence without re-grepping.

  Three test cases — **all three MUST fail against the current `.github/workflows/pr-checks.yml`** before any workflow edit:

  - `test_no_pip_install_uv_in_any_workflow_step`
    - For each `step.get("run", "")`, run a shlex-aware tokenizer (or split on whitespace) over each non-comment line.
    - Flag the step if the line contains the tokens `pip` and `install` AND the literal token `uv` (NOT `uv-something`, NOT `pyuv`). Acceptable patterns to also catch: `python -m pip install uv`, `pip install --upgrade uv`, `pip3 install uv==<ver>`.
    - Concrete reference regex (used as a guideline; tokenization is preferred): `(?<![\w-])uv(?![\w-])` applied to lines that contain both `pip` and `install` after stripping trailing comments.
    - Expected to FAIL on `validate-env-defaults` step at line 103 (`pip install uv`).

  - `test_no_setup_python_adjacent_to_uv`
    - Build per-job step lists from the parsed YAML.
    - A job "uses uv" if ANY step matches `step.get("uses", "").startswith("astral-sh/setup-uv@")` OR ANY step's `step.get("run", "")` has a non-comment line whose first token is exactly `uv` (i.e. `uv` then whitespace; rejects `uvx`, `uv-helper`, `# uv ...`, and absolute paths like `/usr/local/bin/uv`).
    - Flag the job if it BOTH "uses uv" AND has a step matching `step.get("uses", "").startswith("actions/setup-python@")`.
    - Expected to FAIL on `validate-env-defaults` (setup-python at line 97 + `uv run` at line 104).

  - `test_pytest_uses_extra_test_not_with`
    - For each `step.get("run", "")`, scan each non-comment line.
    - If the line contains BOTH `uv run` AND `pytest`, then it MUST contain `--extra test` AND MUST NOT contain `--with` (`--with` here is gated on co-occurrence with `pytest` — `--with` paired with non-pytest commands such as `uvx pip-audit` or `uv run --with somepkg python ...` is OUT OF SCOPE for this rule).
    - Expected to FAIL on `validate-env-defaults` at line 104 (`uv run --with pytest pytest tests/unit/...`).

  - **NOT a separate test in this file**: the SHA-pinning of `astral-sh/setup-uv@` is already covered by the existing "CI actions SHALL be pinned to immutable commit SHAs" requirement in pin-python-deps. Adding a duplicate spec invariant here would be redundant. If we want belt-and-suspenders coverage, add it in a follow-up that targets that existing requirement directly (e.g., MODIFIED with a "machine-checkable enforcement" clause). Out of scope here.

- [x] 2.2 Run the suite locally: `uv run --extra test pytest tests/unit/test_ci_workflow_uv_conventions.py -v --tb=short`. Confirm all three test cases FAIL. **Capture the failure output and paste it into the PR description** (or a fixture file) so reviewers can confirm the test was meaningfully red, not silently empty.

## 3. Implementation: workflow fix (TDD green)

- [x] 3.1 Edit `.github/workflows/pr-checks.yml`. Replace lines 89-104 (the four steps under `validate-env-defaults`: Checkout, Setup Python, Run env-defaults unit tests with pip install uv) with:
  ```yaml
    validate-env-defaults:
      name: Validate committed env defaults
      runs-on: ubuntu-latest
      steps:
        - name: Checkout code
          uses: actions/checkout@v4

        - name: Setup uv
          uses: astral-sh/setup-uv@37802adc94f370d6bfd71619e3f0bf239e1f3b78  # v7.6.0

        - name: Run env-defaults unit tests
          run: uv run --extra test pytest tests/unit/test_env_defaults.py -v --tb=short
  ```
  Match the indentation pattern of the surrounding jobs. Use the SAME SHA (`37802adc94f370d6bfd71619e3f0bf239e1f3b78  # v7.6.0`) referenced by `python-audit` (line 115) and `compose-health-check` (line 293) so all three jobs stay aligned.
- [x] 3.2 Re-run `uv run --extra test pytest tests/unit/test_ci_workflow_uv_conventions.py -v --tb=short`. All three cases now PASS.
- [x] 3.3 Run the existing `tests/unit/test_env_defaults.py` locally to verify the fix doesn't regress what `validate-env-defaults` actually exists to test: `uv run --extra test pytest tests/unit/test_env_defaults.py -v --tb=short`.

## 4. Failure-message acceptance check

- [x] 4.1 Deliberately introduce a violation in a scratch copy of a workflow file (e.g., add `pip install uv` to a temp YAML, or briefly revert step 3.1). Run the test. Confirm the failure message includes:
  - The workflow file path
  - The job name
  - The step index and step `name:`
  - The literal offending token/substring
  Revert the scratch change. This step is verification-only — no diff committed.

## 5. CI wiring

- [x] 5.1 No workflow change required. The new test lives in `tests/unit/`, so it is automatically picked up by `python-audit`'s existing step at `pr-checks.yml:122-123`: `uv run --extra test pytest tests/unit/ -v --tb=short`. Verify the path glob covers the new file by running `uv run --extra test pytest tests/unit/ --collect-only` from the worktree root and confirming `test_ci_workflow_uv_conventions.py` appears.

## 6. Spec validation

- [x] 6.1 From the worktree root, run `openspec validate harden-ci-uv-conventions --strict`. Resolve any issues until validation is clean.
- [x] 6.2 Run `openspec show harden-ci-uv-conventions --json --deltas-only` and inspect the parsed deltas to confirm both ADDED requirements are recognised with all scenarios attached.

## 7. Pre-merge gates and commit hygiene

- [x] 7.1 **Commit safety**: do NOT commit step 2 (failing test) and step 3 (workflow fix) as separate commits to `main` history — that would leave a RED commit that breaks `git bisect`. Two acceptable patterns:
  - Single combined commit (test + fix together).
  - Two commits during local development, then squash before push (`git rebase -i origin/staging`), or merge the PR as a squash merge.
  Use whichever the user prefers; the user's branch protection allows squash merge by default.
- [x] 7.2 Run the project-standard pre-merge skill (`/pre-merge`) locally before opening the PR. Block on any failure rather than skipping with `--no-verify`.
- [x] 7.3 Confirm the worktree's branch is `fix/uv-conventions-validate-env`, no upstream pointing at `staging`, and the only diffs are: the workflow fix, the new test, the `pyproject.toml` test-extra addition, and the four files under `openspec/changes/harden-ci-uv-conventions/`.

## 8. Mark tasks complete

- [x] 8.1 After all preceding items are verified, flip every `- [ ]` to `- [x]` so this checklist reflects reality at archive time.
