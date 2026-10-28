Commit each red test together with the implementation that turns it green, so CI never goes
red. Record the red output in the PR body. Stage files by pathspec only.

## 1. Pure scope helper (`bloommcp/src/bloom_mcp/audit_scope.py`)

- [x] 1.1 Write failing tests in `bloommcp/tests/test_audit_scope.py` for
      `resolve_experiment_scope(experiments)`: - `None` returns `None` (a full sweep); `[]` raises `ValueError`. - Parity table: for `exp_a.csv`, `exp_a`, `exp.v2.csv`, `exp.v2`, `123` and `EXP.CSV`,
      `stem == AnalysisDir("bloommcp_output", value, "qc").stem`. - Duplicates collapse first-seen: `["exp_a.csv", "exp_a", "exp_b"]` becomes two entries, and
      the first keeps `requested == "exp_a.csv"`. - `ValueError` is raised for `""`, `"   "`, `"."`, `".."`, `"../x.csv"`, `"a/b.csv"`,
      `"a\\b.csv"`, `"/abs.csv"`, `"exp.csv/"` and `"a\x00b"`, and for a non-string element
      (`123` as an int).
- [x] 1.2 Write failing tests for `experiment_scope_record(scope)`: it returns `{"mode": "all"}` for
      `None`, and `{"mode": "experiments", "experiments": [{"requested", "stem"}, ...]}` otherwise.
      Also add an import-hygiene test: importing `bloom_mcp.audit_scope` in a subprocess with a
      bare environment succeeds.
- [x] 1.3 Implement `audit_scope.py` with no storage I/O until 1.1 and 1.2 pass. Its module
      docstring is the one place that documents the identifier rules; the scripts point to it.

## 2. `audit_stale_outlier_trims.py` scoped mode

- [x] 2.1 Write failing tests in `bloommcp/tests/scripts/test_audit_stale_outlier_trims.py`: - **Root-listing spy.** After seeding the fixtures, wrap the cached `active_backend()`
      instance's `list_prefix` to record each prefix. Seed `exp_a`, a hit that also has an
      `outliers_exp_a` manifest, and `exp_b`, another hit. For `scan(experiments=["exp_a.csv"])`,
      assert that no recorded prefix satisfies `p.strip("/") in ("", "bloommcp_output")` and
      that every recorded prefix is under `qc_exp_a/` or `outliers_exp_a/`. Also assert
      `experiments_scanned == 1`, `post_420_status == "remediated_and_current"`, and only
      `exp_a` in hits. - **Complement.** With `audit.list_prefix` patched to raise, a scoped scan still succeeds. - **Parity.** The scoped hit dict equals the full-sweep hit dict for `exp_a`. The same holds
      for a malformed-manifest stem's error dict. - **Dedup.** `["exp_a.csv", "exp_a"]` gives `experiments_scanned == 1`. - **Round trip.** A `qc_exp.v2` hit found by the full sweep is found by a scoped scan of
      `exp.v2.csv`. - **Unevaluated.** A missing manifest gives `{"stem", "reason": "no_manifest"}`, and a
      manifest with `latest` set to `None` gives `no_latest`. Neither appears in `hits` or
      `errors`, and both are counted. - **Full-sweep shape.** A full sweep's result has no `unevaluated` key. - **Scoped `run()`.** `run(["--experiment", "exp_a.csv"])` returns `0`. The persisted
      payload has `experiment_scope` for the scoped mode and `unevaluated`, and stdout contains
      `scoped to 1 experiment(s): `. - **Full-sweep `run()`.** `run()` persists `experiment_scope == {"mode": "all"}`, and stdout
      does not contain `scoped to`. - **Host argv ignored.** `run()` with `sys.argv` monkeypatched to
      `["pytest", "-q", "tests/scripts/"]` returns `0` and does a full sweep. - **Rejected values.** Parametrized over `["../x.csv", "a/b.csv", "a\\b.csv", ""]`,
      `run(["--experiment", v])` returns `2`, puts the error on stderr, makes no `list_prefix`
      call on the backend spy and writes no report. - **Unknown flag.** `run(["--bogus"])` raises `SystemExit(2)` and writes no report. - **All-errored run.** For a scoped run whose every requested manifest read raises (patch
      the backend `list_prefix` to raise for analysis prefixes), `run()` returns `1`, writes an
      error to stderr and persists no report. - **Read-only.** A scoped counterpart of the existing never-writes test passes.
- [x] 2.2 Implement: - `scan_for_stale_outlier_trims(experiments=None)`. - Root `list_prefix` only when the scope is `None`. - `unevaluated` in scoped mode. - `write_report(report, scope=None)`, which adds `experiment_scope`. - `run(argv=())` using argparse `--experiment` (`action="append"`), with the scope resolved
      before the scan's try block. - `main()` passing `sys.argv[1:]`. - Updated module and scan-function docstrings ("every `qc_<stem>`" gets "or only the
      requested experiments"; a usage line shows `--experiment`).
- [x] 2.3 `git diff origin/staging -- bloommcp/tests/scripts/test_audit_stale_outlier_trims.py`
      shows additions only: the existing tests are unmodified.

## 3. `audit_untrustworthy_outlier_fits.py` scoped mode

- [x] 3.1 Write the same failing tests as 2.1, against `outliers_<stem>` manifests built with
      `write_outlier_trim_manifest`. The spy's allowed prefix is `outliers_exp_a/` only. Add one
      extra case: `qc_exp_a` exists but `outliers_exp_a` does not, and the result is
      `unevaluated` with reason `no_manifest`, not an error.
- [x] 3.2 Implement, mirroring 2.2. Inside the combined
      `manifest is None or manifest.latest is None` check, record `no_manifest` and `no_latest`
      separately.
- [x] 3.3 Same additions-only check as 2.3, for the fit-audit test file.

## 4. Changelog and validation

- [x] 4.1 Add `bloommcp/CHANGELOG.md` `[Unreleased]` / `Added`: "`--experiment` filter for the
      outlier audit scripts, with `experiment_scope`/`unevaluated` report fields (#919)."
- [x] 4.2 Run `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not live_smoke"`,
      the same command CI runs.
- [x] 4.3 Run `uv run pre-commit run --files <touched files>` (black, ruff, ruff-format, prettier).
      CI does not lint `bloommcp/`, so this gate is local only.
- [x] 4.4 Run `openspec validate add-bloommcp-scoped-audit-sweeps --strict`. This is also local
      only; paste its output into the PR body.
