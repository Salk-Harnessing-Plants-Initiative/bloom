---
name: Pre-Merge Checks
description: Comprehensive pre-merge workflow for TypeScript + Python PRs
category: Git Workflow
tags: [pr, merge, ci, review, checklist]
---

# Pre-Merge Checks

Comprehensive pre-merge workflow that runs all necessary checks, reviews PR comments (including GitHub Copilot), and ensures the code is ready for merging.

## What This Command Does

This command orchestrates a complete pre-merge workflow:

1. **Run Local CI Checks** - Verify all tests and linting pass (TypeScript + Python)
2. **Check PR Status** - Review GitHub CI status and check for failures
3. **Review PR Comments** - Analyze all comments including GitHub Copilot suggestions
4. **Verify Infrastructure** - Check Docker builds, database migrations, environment setup
5. **Address Concerns** - Use planning mode to fix any issues found
6. **Verify Ready to Merge** - Confirm all checks pass and concerns are addressed

## Workflow Steps

### Phase 1: Local CI Checks ✓

Run the full CI suite locally to catch issues before they hit GitHub:

```bash
# TypeScript linting and type checking
pnpm lint
pnpm type-check

# Python linting and type checking
cd flask
uv run black --check .
uv run ruff check .
uv run mypy .

# TypeScript tests
pnpm test:coverage

# Python tests
cd flask
uv run pytest --cov --cov-fail-under=80

# Pre-commit hooks
uv run pre-commit run --all-files
```

**Or use the comprehensive command:**

```bash
/run-ci-locally
```

**Expected outcome:** All checks pass locally

### Phase 2: Infrastructure Checks ✓

Verify Docker, database, and services are working:

```bash
# Check Docker builds
docker-compose -f docker-compose.prod.yml build

# Verify Supabase migrations
supabase migration list
supabase db diff  # Should show no drift

# Check MinIO is accessible
curl http://localhost:9000/minio/health/live

# Verify services are running
docker-compose -f docker-compose.dev.yml ps
```

**Or use:**

```bash
/validate-env
```

**Expected outcome:** All infrastructure checks pass

### Phase 3: PR Status Check ✓

Check the PR's CI status and review any failures:

```bash
# View PR checks
gh pr checks

# View detailed PR status
gh pr view

# Check for failing workflows
gh pr checks --watch
```

**Expected outcome:** All GitHub Actions checks pass (green checkmarks)

**Common CI jobs to verify:**

- `lint-typescript` - ESLint + Prettier
- `lint-python` - Black + Ruff + mypy
- `type-check` - TypeScript compilation
- `test-unit-frontend` - Vitest/Jest tests
- `test-unit-backend` - pytest tests
- `build-docker` - Production Docker builds

### Phase 4: Review PR Comments ✓

Analyze all PR comments, including bot feedback:

```bash
# Get all PR comments
gh pr view --json comments --jq '.comments[] | "\(.author.login): \(.body)"'

# Get review comments on specific lines
gh pr view --json reviews --jq '.reviews[] | "\(.author.login) (\(.state)): \(.body)"'

# Check for Copilot comments
gh pr view --json comments --jq '.comments[] | select(.author.login | contains("copilot")) | .body'
```

**What to look for:**

- GitHub Copilot suggestions for improvements
- Codecov coverage reports
- Security vulnerability warnings (Dependabot)
- Reviewer feedback and requested changes
- Bot comments (pre-commit.ci, etc.)
- OpenSpec validation results (if applicable)

### Phase 5: Code Quality Verification ✓

Ensure code quality standards are met:

```bash
# Check TypeScript coverage
pnpm test:coverage
# Look for: Coverage > 70%

# Check Python coverage
cd flask && uv run pytest --cov
# Look for: Coverage > 80%

# Check for TODO/FIXME comments
grep -r "TODO\|FIXME" web/src flask/app
# Verify all are documented or addressed

# Check for console.log statements (should use proper logging)
grep -r "console\.log" web/src
# Should be minimal in production code

# Check for commented-out code
# Review manually - should be removed or explained
```

### Phase 6: Documentation Check ✓

Verify documentation is up to date:

```bash
# Check README is current
cat README.md
# Verify setup instructions match current state

# Check for new files that need documentation
git diff main --name-only
# Ensure significant changes are documented

# Verify OpenSpec proposals are complete (if applicable)
openspec validate --strict

# Run docs review
/docs-review
```

### Phase 7: Database Migration Verification ✓

If PR includes database changes:

```bash
# Check migration files are present
ls supabase/migrations/

# Verify migrations apply cleanly
supabase db reset
supabase db push

# Test migrations with data
# Insert test data, run migration, verify integrity

# Check for RLS policy changes
git diff main supabase/migrations/ | grep "POLICY"
# Verify security implications
```

### Phase 8: Address Concerns (Planning Mode) ✓

Use planning mode to systematically address all identified issues:

```markdown
## Issues to Address:

### From Copilot Comments:

- [ ] Issue 1: [Description]
- [ ] Issue 2: [Description]

### From CI Failures:

- [ ] Failing test: [Test name]
- [ ] Linting error: [Error description]
- [ ] Type error: [Error description]
- [ ] Docker build failure: [Error]

### From Code Review:

- [ ] Reviewer comment 1
- [ ] Reviewer comment 2

### From Coverage Report:

- [ ] Low coverage areas (< 70% TS, < 80% Python)
- [ ] Untested edge cases

### From Infrastructure:

- [ ] Database migration issue
- [ ] Docker build optimization needed
- [ ] Environment variable missing
```

**Planning mode approach:**

1. Categorize all issues by type (critical, important, nice-to-have)
2. Create a plan to address each issue
3. Implement fixes systematically
4. Re-run checks after each fix
5. Verify all issues resolved

### Phase 9: Final Verification ✓

Before declaring ready to merge, verify everything:

```bash
# Re-run local CI
/run-ci-locally

# Push any fixes
git add -u
git commit -m "fix: address pre-merge feedback"
git push

# Wait for CI to complete
gh pr checks --watch

# Get final PR status
gh pr view
```

**Merge criteria:**

- ✅ All local tests pass (TypeScript + Python)
- ✅ All GitHub CI checks pass (lint, test, build, coverage)
- ✅ No unresolved review comments
- ✅ No Copilot warnings unaddressed
- ✅ Coverage maintained or improved (TS > 70%, Python > 80%)
- ✅ All conversations resolved
- ✅ Docker builds succeed
- ✅ Database migrations tested (if applicable)
- ✅ Documentation updated (if needed)
- ✅ OpenSpec proposals validated (if applicable)

## Common Issue Categories

### 1. GitHub Copilot Comments

Copilot typically flags:

- **Code quality issues**: Complex functions, potential bugs
- **Security concerns**: Unsafe patterns, credential exposure, SQL injection
- **Performance issues**: Inefficient algorithms, unnecessary loops, N+1 queries
- **Best practices**: Missing error handling, unclear variable names
- **TypeScript specific**: Missing type annotations, `any` types
- **Python specific**: Missing type hints, unsafe dict access

**Action:** Review each suggestion and either:

- Implement the suggested improvement
- Add a comment explaining why the suggestion doesn't apply
- Request clarification if the suggestion is unclear

### 2. CI Failures

Common CI failures:

**TypeScript:**

- **Formatting**: Prettier errors
- **Linting**: ESLint errors
- **Type errors**: TypeScript compilation failures
- **Tests**: Vitest/Jest failures
- **Coverage**: Coverage drop below 70%

**Python:**

- **Formatting**: Black errors
- **Linting**: Ruff errors
- **Type errors**: mypy failures
- **Tests**: pytest failures
- **Coverage**: Coverage drop below 80%

**Infrastructure:**

- **Docker**: Build failures, layer caching issues
- **Database**: Migration failures, RLS policy errors
- **Services**: Supabase/MinIO connection issues

**Action:** Use `/ci-debug` for CI failures, `/fix-formatting` for style issues

### 3. Code Review Comments

Reviewer feedback may include:

- Architecture suggestions
- Logic errors or edge cases
- Documentation improvements
- Test coverage requests
- Security concerns
- Performance optimizations
- Database schema concerns
- API design feedback

**Action:** Address each comment and respond with your approach

### 4. Coverage Issues

Codecov may report:

- Decreased overall coverage
- New code not covered by tests
- Missing edge case tests
- Untested error handlers

**Action:** Use `/coverage` to identify gaps and write tests

### 5. Infrastructure Issues

Common infrastructure concerns:

- Docker images too large
- Missing environment variables
- Database migration not reversible
- RLS policies too permissive or too restrictive
- MinIO bucket permissions incorrect

**Action:** Review infrastructure changes carefully, test locally

## Example Workflows

### Scenario 1: PR Ready for Final Review

```bash
# 1. Check PR number
gh pr status
# Shows: #145 (current branch)

# 2. Run local checks
/run-ci-locally
# ✅ All pass

# 3. Check infrastructure
/validate-env
# ✅ All services running

# 4. Check CI status
gh pr checks
# ✅ All checks passing

# 5. Review comments
gh pr view --json comments --jq '.comments[] | "\(.author.login): \(.body)"'
# Shows:
# - codecov: Coverage increased by 2.3%
# - No Copilot warnings
# - Reviewer: "LGTM! Approve after CI passes"

# 6. Verify conversations resolved
gh pr view --json reviews
# All conversations marked as resolved

# ✅ Ready to merge!
```

### Scenario 2: Issues Found - TypeScript Type Errors

```bash
# 1. Run checks - finds issues
pnpm type-check
# ❌ FAIL: Type error in web/src/lib/api.ts:45

# 2. Check comments
gh pr view --json comments
# Copilot: "Missing type annotation for response"
# Reviewer: "Please add proper types to API client"

# 3. Create action plan (Planning Mode)
## Plan:
# 1. Fix type error in api.ts
# 2. Add proper type definitions
# 3. Re-run type checking
# 4. Re-run tests to ensure types are correct

# 4. Execute plan
# [Edit api.ts to add proper types]
pnpm type-check  # ✅ Pass

# 5. Verify fixes
pnpm test
git add web/src/lib/api.ts
git commit -m "fix: add proper types to API client"
git push

# 6. Wait for CI
gh pr checks --watch

# 7. Confirm ready
gh pr view
# ✅ All checks pass, all comments addressed
```

### Scenario 3: Issues Found - Python Coverage Drop

```bash
# 1. Run tests with coverage
cd flask && uv run pytest --cov
# ❌ Coverage: 76% (below 80% threshold)

# 2. Identify untested code
uv run pytest --cov --cov-report=html
open htmlcov/index.html
# See: flask/app/video.py has low coverage

# 3. Check comments
gh pr view --json comments
# codecov: "Coverage decreased by 4.2%"
# Reviewer: "Please add tests for new video processing function"

# 4. Write tests
# [Create tests/test_video_processing.py]
uv run pytest tests/test_video_processing.py
# ✅ 8 tests pass

# 5. Verify coverage improved
uv run pytest --cov
# ✅ Coverage: 82% (above threshold)

# 6. Commit and push
git add tests/test_video_processing.py
git commit -m "test: add coverage for video processing"
git push
```

### Scenario 4: Issues Found - Database Migration

```bash
# 1. Run migration locally
supabase db push
# ❌ Error: syntax error in migration

# 2. Check migration file
cat supabase/migrations/20250112_add_trials_table.sql
# Found: Missing semicolon on line 45

# 3. Fix migration
# [Edit migration file]

# 4. Test migration on fresh database
supabase db reset
supabase db push
# ✅ All migrations applied

# 5. Test with data
psql postgresql://postgres:postgres@localhost:54322/postgres
# INSERT test data, verify schema works

# 6. Commit fix
git add supabase/migrations/
git commit -m "fix: correct syntax in trials table migration"
git push
```

### Scenario 5: Issues Found - Docker Build Failure

```bash
# 1. Check CI logs
gh pr checks
# ❌ build-docker failed

# 2. Debug locally
docker-compose -f docker-compose.prod.yml build
# Error: Could not find package 'new-dependency'

# 3. Identify issue
# Missing package in package.json

# 4. Fix
cd web && pnpm add new-dependency
pnpm install

# 5. Test build
docker-compose -f docker-compose.prod.yml build
# ✅ Build succeeds

# 6. Commit
git add package.json pnpm-lock.yaml
git commit -m "fix: add missing dependency to package.json"
git push
```

## Integration with Other Commands

This command orchestrates these other commands:

- `/run-ci-locally` - Run all CI checks locally
- `/validate-env` - Validate environment setup
- `/coverage` - Analyze test coverage
- `/lint` - Check code style
- `/fix-formatting` - Auto-fix style issues
- `/ci-debug` - Debug CI failures
- `/database-migration` - Verify database migrations
- `/review-pr` - Comprehensive PR review
- `/docs-review` - Review documentation

## Planning Mode Template

When using planning mode to address issues, use this template:

````markdown
# Pre-Merge Action Plan

## Current Status

- Branch: [branch-name]
- PR: #[number]
- Target: [main]

## Issues Found

### Critical (Must Fix - Blocks Merge)

1. [ ] [Issue description]
   - Impact: [description]
   - Fix: [approach]
   - Command: [command to run]

### Important (Should Fix - Improves Quality)

1. [ ] [Issue description]
   - Impact: [description]
   - Fix: [approach]
   - Command: [command to run]

### Nice-to-Have (Optional - Can Defer)

1. [ ] [Issue description]
   - Impact: [description]
   - Fix: [approach or defer reason]

## Implementation Plan

### Step 1: Fix TypeScript Issues

- Action: [what to do]
- Commands:
  ```bash
  pnpm type-check
  pnpm lint --fix
  pnpm test
  ```
````

- Verification: All TypeScript checks pass

### Step 2: Fix Python Issues

- Action: [what to do]
- Commands:
  ```bash
  cd flask
  uv run black .
  uv run ruff check --fix .
  uv run pytest --cov
  ```
- Verification: All Python checks pass

### Step 3: Fix Infrastructure Issues

- Action: [what to do]
- Commands:
  ```bash
  docker-compose -f docker-compose.prod.yml build
  supabase db push
  ```
- Verification: Docker builds, migrations apply

### Step 4: Address Review Comments

- Action: [what to do]
- Files: [files to change]
- Verification: Respond to comments, mark as resolved

## Verification Checklist

- [ ] TypeScript: lint + type-check pass
- [ ] Python: Black + Ruff + mypy pass
- [ ] Tests: All tests pass (TS + Python)
- [ ] Coverage: TS > 70%, Python > 80%
- [ ] Docker: Production builds succeed
- [ ] Database: Migrations tested
- [ ] Pre-commit: All hooks pass
- [ ] GitHub CI: All checks green
- [ ] Comments: All addressed
- [ ] Documentation: Updated (if needed)
- [ ] OpenSpec: Validated (if applicable)

## Ready to Merge

- [ ] All critical issues fixed
- [ ] All important issues addressed or deferred with reason
- [ ] All checks green
- [ ] Approved by reviewer(s)
- [ ] Conversations resolved

````

## Advanced Checks

### Security Scan

```bash
# Check for security vulnerabilities (Python)
cd flask
uv run pip-audit

# Check for secrets in code
git secrets --scan

# Check for hardcoded credentials
grep -r "password\|secret\|key" --exclude-dir={node_modules,.git,dist} .

# Check npm packages
pnpm audit

# Check for exposed .env files
git ls-files | grep "\.env"
# Should NOT include .env.dev or .env.local
````

### Dependency Check

```bash
# TypeScript: Verify lockfile is up to date
pnpm install --frozen-lockfile

# Python: Verify lockfile is up to date
cd flask && uv sync --frozen

# Check for outdated dependencies
pnpm outdated
cd flask && uv pip list --outdated
```

### Performance Check

```bash
# Check Docker image sizes
docker images | grep bloom
# Flag if images > 1GB

# Check bundle sizes (Next.js)
pnpm build
# Review .next/analyze or webpack stats

# Check for N+1 queries
# Review database query patterns in new code
grep -r "SELECT" flask/app/

# Check for memory leaks
# Review useEffect cleanup in React components
grep -r "useEffect" web/src/
```

### Accessibility Check (Frontend)

```bash
# Check for accessibility issues
# Run lighthouse or axe-core on UI changes

# Verify ARIA labels
grep -r "aria-" web/src/components/

# Check for alt text on images
grep -r "<img" web/src/ | grep -v "alt="
# Should be empty or have good reason
```

## Automation Opportunities

### Pre-commit Hook

Bloom already has pre-commit hooks configured. Ensure they're installed:

```bash
uv run pre-commit install
```

### GitHub Branch Protection

Recommended settings:

- ✅ Require pull request reviews (1 approval minimum)
- ✅ Require status checks (all CI jobs)
- ✅ Require branches to be up to date
- ✅ Require conversation resolution
- ✅ Require linear history (optional)
- ✅ Do not allow bypassing (except for admins in emergencies)

### GitHub Actions Pre-Merge Check

Add workflow that comments on PRs with pre-merge checklist (future enhancement).

## Troubleshooting

### Issue: "TypeScript checks keep failing"

**Debug:**

```bash
# Clear cache
rm -rf .next .turbo node_modules/.cache
pnpm install

# Re-run type check with verbose
pnpm type-check --verbose

# Check specific file
npx tsc --noEmit web/src/lib/api.ts
```

**Use:** `/ci-debug` for detailed troubleshooting

### Issue: "Python tests pass locally, fail in CI"

**Possible causes:**

- Different Python version
- Different package versions
- Environment variables missing
- Database state different

**Debug:**

```bash
# Check Python version matches CI
python --version

# Check package versions
uv pip list

# Run with same env as CI
CI=true uv run pytest
```

**Use:** `/ci-debug` for CI-specific issues

### Issue: "Copilot comment unclear"

**Actions:**

- Ask reviewer for clarification in PR comment
- Check Copilot documentation for similar suggestions
- Make best judgment and document decision in code comment
- Respond to Copilot comment explaining your decision

### Issue: "Coverage decreased significantly"

**Actions:**

```bash
# Identify untested code
/coverage

# Write tests for new functionality
# Focus on:
# - New functions/components
# - Edge cases
# - Error handling
# - Integration points

# Verify coverage improved
pnpm test:coverage
cd flask && uv run pytest --cov
```

### Issue: "Merge conflicts"

**Resolution:**

```bash
# Update from main
git fetch origin
git rebase origin/main

# Resolve conflicts
# [Edit conflicting files]

# Verify resolution
git diff

# Fix formatting (conflicts often break it)
/fix-formatting

# Re-run all checks
/run-ci-locally

# Continue rebase
git rebase --continue
```

### Issue: "Docker build fails in CI but not locally"

**Debug:**

```bash
# Build with same platform as CI (usually linux/amd64)
docker buildx build --platform linux/amd64 -f Dockerfile .

# Check .dockerignore
cat .dockerignore

# Build without cache
docker-compose -f docker-compose.prod.yml build --no-cache
```

**Use:** `/ci-debug` for Docker-specific issues

### Issue: "Database migration fails in CI"

**Debug:**

```bash
# Test on fresh database
supabase db reset
supabase db push

# Check for syntax errors
cat supabase/migrations/<latest>.sql

# Test with PostgreSQL directly
psql postgresql://postgres:postgres@localhost:54322/postgres < supabase/migrations/<latest>.sql

# Check for RLS policy conflicts
supabase db inspect rls
```

**Use:** `/database-migration` for migration help

## Success Criteria

PR is ready to merge when:

### ✅ All Automated Checks Pass

**TypeScript:**

- Lint (ESLint + Prettier)
- Type check (tsc)
- Tests (Vitest/Jest)
- Coverage (> 70%)

**Python:**

- Lint (Black + Ruff + mypy)
- Tests (pytest)
- Coverage (> 80%)

**Infrastructure:**

- Docker builds (production images)
- Database migrations (if applicable)
- Pre-commit hooks

### ✅ All Comments Addressed

- Reviewer feedback implemented or discussed with response
- Copilot suggestions reviewed and addressed or explained
- Bot comments (codecov, dependabot) acknowledged
- All conversations marked as resolved

### ✅ Code Quality Verified

- No obvious bugs or issues
- Edge cases tested
- Error handling comprehensive
- Documentation updated (if needed)
- Follows Bloom conventions (see CONTRIBUTING.md)
- OpenSpec proposals validated (if applicable)

### ✅ Infrastructure Changes Verified

- Docker images build successfully
- Database migrations tested with data
- RLS policies reviewed for security
- Environment variables documented
- Services integration tested

### ✅ Approvals Obtained

- Required reviewers approved (minimum 1)
- No outstanding requested changes
- Technical lead approval (for major changes)

## Related Commands

- `/run-ci-locally` - Local CI checks (TypeScript + Python)
- `/validate-env` - Environment validation
- `/review-pr` - PR review workflow
- `/coverage` - Coverage analysis
- `/lint` - Linting checks
- `/fix-formatting` - Auto-fix formatting
- `/ci-debug` - Debug CI failures
- `/database-migration` - Database migration help
- `/docs-review` - Documentation review

## Tips

1. **Start early**: Run pre-merge checks before requesting review
2. **Be systematic**: Address issues in order of priority (critical → important → nice-to-have)
3. **Communicate**: Respond to comments promptly and clearly
4. **Test thoroughly**: Don't skip edge cases, especially for API endpoints and database operations
5. **Document decisions**: Explain why you made certain choices, especially for security or architecture
6. **Keep it clean**: Consider rebasing and cleaning up commit history for complex PRs
7. **Be responsive**: Address feedback quickly to keep momentum
8. **Think holistically**: Consider both TypeScript and Python changes together
9. **Security first**: Be extra careful with database migrations and API changes
10. **Ask for help**: If stuck, ask teammates or use `/ci-debug`

## Bloom-Specific Considerations

### Monorepo Structure

- Changes may span multiple workspaces (web, packages/\*)
- Ensure Turborepo cache works correctly
- Test workspace dependencies

### Dual Stack (TypeScript + Python)

- Both stacks must pass checks independently
- Consider integration points (API contracts)
- Maintain consistency in patterns

### Docker + Supabase

- Test Docker builds locally before pushing
- Verify Supabase migrations with `supabase db reset`
- Check MinIO integration for file uploads

### OpenSpec Integration

- If using OpenSpec, ensure proposals are validated
- Follow OpenSpec workflow: proposal → apply → archive
- Keep specs in sync with implementation

## Next Steps After Merge

1. **Delete branch**:
   ```bash
   gh pr close --delete-branch
   ```
2. **Update local**:
   ```bash
   git checkout main && git pull
   ```
3. **Monitor deployment**: Check Docker Compose services after deployment
4. **Verify database**: Check migrations applied in production (when deployed)
5. **Monitor logs**: Watch for any issues in production
6. **Celebrate**: 🎉 Your code is merged!

---

**Remember**: The goal is not just to pass checks, but to ensure the code is high quality, secure, maintainable, and ready for production use in Bloom's complex dual-stack architecture.
