# deploy-env-parity Specification

## Purpose
TBD - created by archiving change update-env-parity-check. Update Purpose after archive.
## Requirements
### Requirement: Env Block Discovery

The parity checker SHALL locate env-file heredoc bodies inside `.github/workflows/deploy.yml` by matching any line whose content satisfies `cat\s*>\s*[^<]*\.env\.(prod|staging)\s*<<\s*'([A-Z_]+)'`. The captured env name drives which block the body feeds, and the captured terminator is matched against stripped lines of subsequent content until the closing marker is found. The regex intentionally matches only **unquoted** paths — paths wrapped in quotes (e.g. `cat > "/opt/bloom/.env.prod" << 'EOF'`) are not supported. If deploy.yml ever needs quoted paths, this requirement MUST be amended alongside the regex; do not silently widen the regex to tolerate quotes without updating the spec.

#### Scenario: Prod block detected with dynamic terminator

- **GIVEN** `deploy.yml` contains a line `cat > ${{ secrets.PROD_DEPLOY_PATH }}/.env.prod << 'ENVEOF'`
- **WHEN** the checker runs
- **THEN** it captures every line between that marker and the next line whose stripped content equals `ENVEOF` as the prod block body

#### Scenario: Alternate terminator name accepted

- **GIVEN** `deploy.yml` contains `cat > /opt/bloom/production/.env.prod << 'PROD_ENV_END'`
- **AND** a closing line with stripped content `PROD_ENV_END`
- **WHEN** the checker runs
- **THEN** the body between the two markers is captured as the prod block (terminator name is dynamic, not hardcoded)

#### Scenario: Malformed heredoc fails fast

- **GIVEN** `deploy.yml` contains a prod heredoc start without a matching terminator line
- **WHEN** the checker runs
- **THEN** it exits with a non-zero code and an error naming the unclosed block and its start line

### Requirement: Exactly Two Env Blocks

The parity checker SHALL require exactly one prod block and exactly one staging block in `deploy.yml`. Any other count — zero, one, three, the presence of an `.env.<other>` heredoc, or a duplicate of either `.env.prod` or `.env.staging` — SHALL cause the checker to exit non-zero. Duplicate blocks of the same env name MUST NOT be silently collapsed; the drift they mask is exactly the class of bug this check exists to catch.

#### Scenario: Zero env blocks fails

- **GIVEN** `deploy.yml` contains no lines matching the env-heredoc pattern
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error stating `0 env blocks found, expected 2`

#### Scenario: Third env block fails

- **GIVEN** `deploy.yml` contains a `.env.dev` heredoc in addition to `.env.prod` and `.env.staging`
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error naming `.env.dev` as an unexpected env block, with its line number

#### Scenario: Duplicate prod block fails

- **GIVEN** `deploy.yml` contains TWO `.env.prod` heredocs (e.g. from a bad copy-paste) and one `.env.staging` heredoc
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error identifying the second `.env.prod` heredoc as a duplicate, with its start line number
- **AND** the first `.env.prod` heredoc is NOT silently overwritten by the second

#### Scenario: Duplicate staging block fails

- **GIVEN** `deploy.yml` contains one `.env.prod` heredoc and TWO `.env.staging` heredocs
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error identifying the second `.env.staging` heredoc as a duplicate, with its start line number

#### Scenario: Exactly one of each passes discovery

- **GIVEN** `deploy.yml` contains exactly one `.env.prod` heredoc and one `.env.staging` heredoc
- **WHEN** the checker runs
- **THEN** discovery passes (subject to other requirements)

### Requirement: LHS Variable Parity

The parity checker SHALL assert that the set of variable names declared on the left of `=` is identical between the prod and staging blocks. Blank lines and lines whose first non-whitespace character is `#` SHALL be ignored. A variable name that appears on the LHS more than once within a single block SHALL be reported as a duplicate — not silently collapsed — since the later declaration masks the earlier one and any divergence between them is invisible to downstream parity checks.

#### Scenario: Missing var in staging fails

- **GIVEN** prod block declares `NEW_VAR=${{ secrets.PROD_NEW_VAR }}`
- **AND** staging block has no `NEW_VAR=` line
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error listing `NEW_VAR` as missing from staging

#### Scenario: Missing var in prod fails

- **GIVEN** staging block declares `NEW_VAR=${{ secrets.STAGING_NEW_VAR }}`
- **AND** prod block has no `NEW_VAR=` line
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error listing `NEW_VAR` as missing from prod

#### Scenario: Identical var sets pass

- **GIVEN** both blocks declare exactly `{POSTGRES_DB, POSTGRES_USER, JWT_SECRET}` on their LHS
- **WHEN** the checker runs
- **THEN** LHS parity passes (subject to other requirements)

#### Scenario: Comment and blank lines ignored

- **GIVEN** either block contains lines starting with `#` or blank lines interspersed with var lines
- **WHEN** the checker runs
- **THEN** the comment and blank lines contribute nothing to the LHS set and no error is raised for them

#### Scenario: Duplicate LHS within one block fails

- **GIVEN** the prod block declares `POSTGRES_DB=...` on two different lines
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error identifying the second occurrence of `POSTGRES_DB` as a duplicate in the prod block, with its line number
- **AND** the same requirement applies symmetrically when the duplicate is in the staging block

### Requirement: RHS Prefix Correctness Per Block

The parity checker SHALL scan every line of each block for all occurrences of `\$\{\{\s*secrets\.([^}\s]+)\s*\}\}` and validate each captured name against the canonical pattern. Prod block refs MUST match `^PROD_[A-Z][A-Z0-9_]*$`; staging block refs MUST match `^STAGING_[A-Z][A-Z0-9_]*$`. A name that matches the outer capture but fails the canonical pattern SHALL be treated as malformed and raise an error — never silently ignored.

#### Scenario: Cross-prefix leak inside staging block fails

- **GIVEN** the staging block contains the line `POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}`
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error reporting the file:line, the exact offending line content, and naming `PROD_POSTGRES_DB` as the wrong-prefix secret

#### Scenario: Cross-prefix leak inside prod block fails

- **GIVEN** the prod block contains the line `POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}`
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error reporting the file:line and the exact offending line content

#### Scenario: Lowercase prefix is rejected as malformed

- **GIVEN** a line contains `${{ secrets.prod_POSTGRES_DB }}`
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error identifying the reference as malformed (fails the canonical `^PROD_...$` pattern), not silently skipped

#### Scenario: Correctly-prefixed references pass

- **GIVEN** every secret reference in the prod block matches `^PROD_[A-Z][A-Z0-9_]*$`
- **AND** every secret reference in the staging block matches `^STAGING_[A-Z][A-Z0-9_]*$`
- **WHEN** the checker runs
- **THEN** the prefix-correctness check passes (subject to other requirements)

### Requirement: RHS Suffix Parity Between Blocks

The parity checker SHALL assert that the set of suffixes extracted from `PROD_<suffix>` references in the prod block equals the set of suffixes extracted from `STAGING_<suffix>` references in the staging block.

#### Scenario: Suffix present in prod but missing in staging fails

- **GIVEN** prod block references `${{ secrets.PROD_NEW_VAR }}`
- **AND** staging block contains no `${{ secrets.STAGING_NEW_VAR }}` reference
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error naming suffix `NEW_VAR` as missing from staging

#### Scenario: Suffix present in staging but missing in prod fails

- **GIVEN** staging block references `${{ secrets.STAGING_EXTRA }}`
- **AND** prod block contains no `${{ secrets.PROD_EXTRA }}` reference
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error naming suffix `EXTRA` as missing from prod

#### Scenario: Aligned suffixes pass

- **GIVEN** prod suffix set equals staging suffix set
- **WHEN** the checker runs
- **THEN** suffix parity passes (subject to other requirements)

### Requirement: Per-LHS Secret-Ref Consistency

The parity checker SHALL assert that for every LHS variable X, if the line for X in one block contains at least one `${{ secrets.Y }}` reference, then the line for X in the other block MUST also contain at least one `${{ secrets.Z }}` reference. A literal-only value on one side paired with a secret ref on the other is a parity failure.

#### Scenario: Literal in staging, secret in prod fails

- **GIVEN** prod block line `POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}`
- **AND** staging block line `POSTGRES_DB=bloom_prod`
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error naming `POSTGRES_DB` as inconsistent (one side uses a secret ref, the other uses a literal)

#### Scenario: Both literal passes

- **GIVEN** prod block line `POSTGRES_HOST_PORT=5432`
- **AND** staging block line `POSTGRES_HOST_PORT=5433`
- **WHEN** the checker runs
- **THEN** consistency passes (both sides literal, no secret-ref/literal mismatch)

#### Scenario: Both secret-ref passes

- **GIVEN** prod block line `POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}`
- **AND** staging block line `POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}`
- **WHEN** the checker runs
- **THEN** consistency passes (subject to other requirements)

### Requirement: Multiple Secret References Per Line

The parity checker SHALL count every `${{ secrets.<NAME> }}` occurrence on each line, not only the first, so that composite values contribute all their references to prefix, suffix, and consistency checks.

#### Scenario: Composite value with multiple correct refs passes

- **GIVEN** prod block contains `LANGCHAIN_POSTGRES_URL=postgresql://${{ secrets.PROD_POSTGRES_USER }}:${{ secrets.PROD_POSTGRES_PASSWORD }}@${{ secrets.PROD_POSTGRES_HOST }}:${{ secrets.PROD_POSTGRES_PORT }}/${{ secrets.PROD_POSTGRES_DB }}`
- **AND** staging block contains the same line with `STAGING_` prefixes
- **WHEN** the checker runs
- **THEN** all five references per line are considered, and the check passes

#### Scenario: Composite value with one leaked ref fails

- **GIVEN** staging block contains `LANGCHAIN_POSTGRES_URL=postgresql://${{ secrets.STAGING_POSTGRES_USER }}:${{ secrets.PROD_POSTGRES_PASSWORD }}@host/db`
- **WHEN** the checker runs
- **THEN** it exits non-zero with an error naming `PROD_POSTGRES_PASSWORD` as the leaked reference, on the named line

### Requirement: Literal-Only Lines Are Legal

The parity checker SHALL allow lines whose RHS contains zero `${{ secrets... }}` references (literal-only lines). Such lines participate in LHS parity but are skipped by prefix, suffix, and consistency checks.

#### Scenario: Literal port in both blocks passes

- **GIVEN** prod block contains `POSTGRES_HOST_PORT=5432`
- **AND** staging block contains `POSTGRES_HOST_PORT=5433`
- **WHEN** the checker runs
- **THEN** `POSTGRES_HOST_PORT` contributes to LHS parity (present in both), and no prefix/suffix/consistency error is raised

#### Scenario: Empty-value line in both blocks passes

- **GIVEN** both blocks contain `CADDY_HTTP_PORT=` (empty RHS)
- **WHEN** the checker runs
- **THEN** LHS parity passes and no ref-based error is raised

### Requirement: Actionable Failure Output

The parity checker SHALL emit every error both as a human-readable stderr line of the form `<path>:<line>: <description>` and as a GitHub Actions workflow annotation of the form `::error file=<path>,line=<line>::<description>`, including the exact offending line content. A PR author MUST be able to locate and fix the problem from the annotation alone.

#### Scenario: Stderr format

- **WHEN** any validation fails
- **THEN** stderr includes a line matching `.github/workflows/deploy.yml:\d+: .+` followed by the offending line content

#### Scenario: GitHub annotation emitted

- **WHEN** any validation fails
- **THEN** stdout includes a line matching `::error file=\.github/workflows/deploy\.yml,line=\d+::.+`

#### Scenario: Happy-path summary

- **WHEN** all validations pass
- **THEN** stdout includes a line matching `OK — \d+ vars, \d+ secret refs, prod and staging aligned` and the exit code is 0

