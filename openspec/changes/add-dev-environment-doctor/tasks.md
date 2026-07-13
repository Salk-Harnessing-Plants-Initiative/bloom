# Tasks — add-dev-environment-doctor

TDD throughout: each check gets a failing test (RED) before the shell
implementation (GREEN). Tests shell out to `scripts/doctor.sh` with the
overridable env vars from `design.md` so no real `/mnt/c`, Postgres, or missing
toolchain is required. Every positive-path test **constructs its own `PATH` of
stub tools** — the `python-audit` CI job (where `tests/unit/` runs) has only
`uv`+`git`, not `node`/`npm`/`supabase`/`docker`/`make`. `test_doctor.py` skips
when `sh` is unavailable (native-Windows dev), mirroring the `skipif` guard in
`test_makefile_migrate_local.py`.

## 1. Doctor scaffold + Windows-filesystem check
- [ ] 1.1 (RED) Add `tests/unit/test_doctor.py` (with `@pytest.mark.skipif(
  shutil.which("sh") is None ...)`): running `scripts/doctor.sh` with
  `DOCTOR_WSL=1 DOCTOR_REPO_PATH=/mnt/c/repos/bloom` and a full stub `PATH` exits
  non-zero and prints a message naming `/mnt/` and the "clone into the WSL2 Linux
  filesystem" remedy. Fails (script absent).
- [ ] 1.2 (GREEN) Add `scripts/doctor.sh` (POSIX `sh`) with a findings/severity
  framework (collect ERROR/WARN, print a summary, exit non-zero iff any ERROR)
  and the `/mnt/` filesystem check. Honor `DOCTOR_SKIP=1` as an early clean exit.
  Test passes.
- [ ] 1.3 (RED→GREEN) Test: with `DOCTOR_WSL=0` and a normal repo path (all six
  tools stubbed present), the filesystem check is skipped and the doctor exits 0.

## 2. Required-tool presence + Windows-leak checks
- [ ] 2.1 (RED) Parametrized test over `{uv,node,npm,supabase,make,docker}`: a
  `PATH` that stubs the other five but omits each tool in turn exits non-zero,
  names that tool, and prints its install hint; a `PATH` stubbing all six passes
  the tool check.
- [ ] 2.2 (RED) Test: with a fake `node` resolving under a `/mnt/`-rooted stub
  dir and `DOCTOR_WSL=1`, doctor WARNs (exit 0) about a Windows-mount toolchain
  leak.
- [ ] 2.3 (GREEN) Implement required-tool presence (ERROR) and the `/mnt/` leak
  (WARN). Tests pass.

## 3. supabase-version (+ pin source of truth), host-port, CRLF
- [ ] 3.1 (RED) Add `tests/unit/test_supabase_version_pin.py`: the committed
  `.supabase-version` equals the `SUPABASE_VERSION` in
  `.github/workflows/pr-checks.yml` and `deploy.yml` (drift guard). Fails
  (file absent).
- [ ] 3.2 (GREEN) Add `.supabase-version` (one line, the current pin) and make
  the drift-guard test pass.
- [ ] 3.3 (RED) Test: a stub `supabase` printing a version ≠ `.supabase-version`
  ⇒ WARN naming the pin; a matching version ⇒ no warning; missing
  `.supabase-version` ⇒ check skipped (no error).
- [ ] 3.4 (RED) Test: bind a socket on an ephemeral port, keep it **open across
  the subprocess call**, pass it as `DOCTOR_PORT` ⇒ WARN advising a
  `POSTGRES_HOST_PORT` override; a closed/free port ⇒ no warning.
- [ ] 3.5 (RED) Test: a CRLF fixture under a `minio/init/`-shaped path **and** one
  under a `volumes/db/`-shaped path (both globs) under `DOCTOR_SCAN_ROOT` ⇒ WARN
  naming each file; an LF-only tree ⇒ no CRLF warning.
- [ ] 3.6 (GREEN) Implement the version (reads `.supabase-version`), host-port,
  and CRLF checks. Tests pass.

## 4. Precedence + clean-repo self-guard
- [ ] 4.1 (RED) Test: a run triggering BOTH an ERROR (`DOCTOR_WSL=1
  DOCTOR_REPO_PATH=/mnt/c/...`) and a WARN (CRLF fixture and/or bound
  `DOCTOR_PORT`) exits non-zero AND prints both findings — advisories never mask
  the error. (Regression for the real 2026-07-09 `/mnt/c` case.)
- [ ] 4.2 (RED) Self-guard test: with all six tools stubbed on `PATH`,
  `DOCTOR_WSL=0`, and `DOCTOR_SCAN_ROOT` pointed at the real repo tree,
  `scripts/doctor.sh` exits 0 — proving the clean-Linux path and catching any CRLF
  regression committed into the real `minio/init/*.sh` or `volumes/db/**`.
- [ ] 4.3 (GREEN) Ensure the severity framework satisfies both (should already
  hold from 1.2; add code only if a test exposes a gap). Tests pass.

## 5. `dev-up` preflight wiring + CI guard (single commit)
- [ ] 5.1 (RED) Add `tests/unit/test_makefile_doctor.py` (modeled on
  `test_makefile_migrate_local.py`): a `doctor` target exists; the `dev-up`
  recipe invokes `scripts/doctor.sh` **before** `docker compose ... up`
  (assert `recipe.index("doctor") < recipe.index("docker compose")`); and the
  wiring honors `DOCTOR_SKIP`. Fails.
- [ ] 5.2 (RED) Extend `tests/unit/test_ci_dev_stack_smoke.py`: the
  `dev-stack-smoke` job's `make dev-up` step sets `DOCTOR_SKIP=1` (so the
  preflight never runs on the CI runner). Fails.
- [ ] 5.3 (GREEN) In one commit: add the `make doctor` target; wire `dev-up` to
  run the doctor first (skippable via `DOCTOR_SKIP=1`); and set `DOCTOR_SKIP=1` on
  the `dev-stack-smoke` `make dev-up` step in `.github/workflows/pr-checks.yml`.
  Tests (5.1, 5.2) pass.
- [ ] 5.4 (RED→GREEN, behavioral) Test with a stub `docker` on `PATH` (writes a
  sentinel when called): `make dev-up` with a forced doctor ERROR
  (`DOCTOR_WSL=1 DOCTOR_REPO_PATH=/mnt/c/...`) exits non-zero and the docker stub
  is never called (no sentinel); `make dev-up DOCTOR_SKIP=1` does not run the
  doctor and reaches the docker step.

## 6. DEV_SETUP.md + roadmap rewrite + regression guard (supersedes PR #424)
- [ ] 6.1 (RED) Strengthen `tests/unit/test_dev_setup_doc.py`: `DEV_SETUP.md`
  contains **no `~/minio` token anywhere** (not just `mkdir -p ~/minio`) and no
  `MINIO_DATA_PATH=/Users`; DOES reference `make doctor`, `DOCTOR_SKIP`, and the
  `MINIO_DATA_PATH` default `./volumes/minio-dev`; and (extending the existing
  phantom-target guard) every `make <target>` it names resolves to a real
  `Makefile` rule. Fails against current docs (lines ~332, ~403, ~486 still use
  `~/minio`).
- [ ] 6.2 (GREEN) Rewrite `DEV_SETUP.md`: correct the MinIO Storage Setup section
  (buckets auto-create via `minio-init`; default `./volumes/minio-dev`; no
  `mkdir ~/minio`) and fix the three stale `~/minio` references lower in the file;
  add a one-line MinIO **writability** note cross-referencing `project.md`'s
  `chmod 770` constraint; add the toolchain-inside-WSL guidance + `/mnt/c` npm-leak
  warning; add the "why Linux-ext4 clone" rationale (MinIO `/data` I/O error
  primary, CRLF secondary); surface `make doctor` as the first step and document
  `DOCTOR_SKIP=1`; keep a **single** canonical `POSTGRES_HOST_PORT` note
  (cross-reference the existing Step 2, don't duplicate the value); bump the
  "Last Updated" footer. Tests pass.
- [ ] 6.3 (GREEN) Update `bloommcp/docs/roadmap.md` `/mnt/c` line so its rationale
  matches (MinIO `/data` I/O error primary, CRLF secondary).

## 7. Validate + verify
- [ ] 7.1 `openspec validate add-dev-environment-doctor --strict` passes.
- [ ] 7.2 `shellcheck --shell=sh scripts/doctor.sh` clean (catches bashisms that
  break the bare-`sh` guarantee).
- [ ] 7.3 `uv run --extra test pytest tests/unit/test_doctor.py
  tests/unit/test_makefile_doctor.py tests/unit/test_supabase_version_pin.py
  tests/unit/test_dev_setup_doc.py tests/unit/test_ci_dev_stack_smoke.py` green;
  then `uv run pre-commit run --all-files`.
- [ ] 7.4 Fresh-clone note: on a WSL2 ext4 clone `make doctor` exits 0 (clean env);
  on a `/mnt/c` clone it errors. (The underlying behaviors were validated live in
  the 2026-07-09 reproduction; capture the doctor's own output.)
- [ ] 7.5 Run `/pre-merge`; mark every task `- [x]`. File the deferred
  `.claude/commands/docs-review.md` + `.serena/memories/suggested_commands.md`
  stale-doc follow-ups (human-applied). **Post-approval, at merge:** close PR #424
  unmerged with a `Supersedes` comment; add `Supersedes #424` to this PR body.
