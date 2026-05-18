## Why

The current `verify-env-parity` job in `.github/workflows/pr-checks.yml` only validates the **left side** of each `VAR=...` line in the prod/staging env blocks of `deploy.yml`. It ignores the secret reference on the right side, so three classes of bugs can slip through:

1. **Cross-environment secret leak.** A line like `POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}` placed inside the **staging** env block would make staging read production credentials. The LHS (`POSTGRES_DB`) matches in both blocks, so the current check reports OK. Consequence: users logged into staging operate on what they believe is staging data but is actually prod — data corruption, RLS boundary violation, or a scientist writing real experiment writes to a staging DB that gets reset.

2. **Composite-value blind spot.** Composite values are live in `deploy.yml` today: `LANGCHAIN_POSTGRES_URL=postgresql://${{ secrets.PROD_POSTGRES_USER }}:${{ secrets.PROD_POSTGRES_PASSWORD }}@${{ secrets.PROD_POSTGRES_HOST }}:${{ secrets.PROD_POSTGRES_PORT }}/${{ secrets.PROD_POSTGRES_DB }}` at [`.github/workflows/deploy.yml:101`](.github/workflows/deploy.yml#L101) (prod) and `:261` (staging). Five refs per line. The current regex `^[[:space:]]+[A-Z_]+=\$\{\{ secrets\.PROD_` does not match composite lines (the character after `=` is `p`, not `$`) — they are invisible to parity today. Any PR that edits one of the five embedded secret names without updating the matching staging line drifts silently.

3. **Hardcoded literal in the wrong block.** A PR author writing `POSTGRES_DB=bloom_prod` (no `${{ secrets... }}` wrapper) inside the staging block passes every check based solely on secret references — but produces the same cross-env write as bug (1). The parity check must also assert per-LHS that if one block uses a `secrets.X` ref for a given variable, the matching line in the other block does too.

Elizabeth flagged items (1) and (2) as issue #138 item 10. Item (3) was surfaced in OpenSpec review — same blast radius, same data-integrity risk. Must-fix before Monday's first production deploy.

## What Changes

- Replace the inline bash check (~13 lines of grep|awk|diff in `pr-checks.yml`) with a Python script `scripts/verify_env_parity.py` invoked by the same `verify-env-parity` job
- The script parses `.github/workflows/deploy.yml`, locates env heredoc bodies by matching the target filename (`.env.prod` / `.env.staging`) and capturing the heredoc terminator dynamically, and validates:
  1. **Exactly two env blocks.** Prod block and staging block present; no extras (e.g. a stray `.env.dev` heredoc). Fail fast otherwise.
  2. **LHS parity** — the set of variable names is identical in both blocks (preserves existing check, gives actionable diff of missing var names).
  3. **RHS prefix correctness** — every `${{ secrets.<NAME> }}` reference inside the prod block has `<NAME>` matching `^PROD_[A-Z][A-Z0-9_]*$`; every one inside the staging block matches `^STAGING_[A-Z][A-Z0-9_]*$`. Malformed references (lowercase, unknown prefix, or names that do not match the canonical pattern) fail explicitly — never silently skipped.
  4. **RHS suffix parity** — the set of suffixes from `PROD_<suffix>` in the prod block equals the set of suffixes from `STAGING_<suffix>` in the staging block. Catches the case where a secret exists in one env but is forgotten in the other even when the LHS var name matches for unrelated reasons.
  5. **Per-LHS secret-ref consistency** — for every LHS var that uses at least one `secrets.X` reference in one block, the matching LHS in the other block must also use a `secrets.X` reference. Prevents a hardcoded literal in one block from masking a missing secret in the other.
  6. **Multiple references per line** — every `${{ secrets... }}` on a line contributes to prefix, suffix, and consistency checks; composite values are fully covered.
  7. **LHS-only literal lines allowed** — a line whose RHS contains zero `${{ secrets... }}` references (e.g. `POSTGRES_HOST_PORT=5432`, `CADDY_HTTP_PORT=`) participates in LHS parity but is skipped by prefix/suffix/consistency checks.
- Errors are emitted as GitHub Actions annotations (`::error file=<path>,line=<n>::<msg>`) so failures surface inline on the PR diff.
- Add pytest tests under `tests/unit/test_verify_env_parity.py` — new `tests/unit/` directory for pure-logic tests (distinct from `tests/integration/` which requires the Docker stack). ~12 test cases mapping 1:1 to the spec scenarios.

## Impact

- Affected specs: new capability `deploy-env-parity`
- Affected code:
  - [`.github/workflows/pr-checks.yml`](.github/workflows/pr-checks.yml#L55-L75) — replace `verify-env-parity` job body (keep the job NAME identical — branch protection references it)
  - `scripts/verify_env_parity.py` — new file (~100 lines, stdlib-only)
  - `tests/unit/test_verify_env_parity.py` — new file (~12 test cases)
  - `tests/unit/` — new directory, empty `__init__.py`
- Deploy pipeline: PRs that touch `deploy.yml` now fail on cross-prefix, malformed-ref, suffix-drift, or literal-in-place-of-secret errors with actionable line-level annotations. Non-deploy PRs are unaffected.
- Zero runtime impact on the deployed stack — this is a CI-only check.
- **Merge sequencing note:** `origin/feat/deploy-migration-runner` (PR #146) adds `POSTGRES_HOST_PORT=5432/5433` literal lines to both heredocs and ~75 lines of migration steps inside deploy.yml. Whichever merges first, the other rebases. The new parity check is designed to accept those literal lines (requirement 7), so merge order does not affect correctness.

## Non-Goals

- **Detecting hardcoded prod values in `.env.example`, `.env.ci`, or committed `.env.*` files.** Out of scope — handled by `.gitignore` + secret-scanning.
- **Verifying that every `secrets.PROD_X` referenced in `deploy.yml` actually exists in the GitHub Secrets UI.** A deploy-time concern; the parity check is a build-time structural check.
- **Detecting hardcoded literals that happen to be valid production values** (e.g. `POSTGRES_DB=bloom_prod`) without a parity counterpart. Requirement 5 catches the asymmetric case (secret-ref in one block, literal in the other); a uniformly-literal pair that encodes prod values in both blocks is a semantic bug no structural checker can distinguish from a legitimate shared literal.
- **Three-environment (dev/staging/prod) support.** Current scope is exactly two blocks; extending later is a separate proposal.
