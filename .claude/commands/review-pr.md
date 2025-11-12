---
name: Review Pull Request
description: Systematic workflow for reviewing PRs with comprehensive checklist
category: Git Workflow
tags: [pr, review, github]
---

# Review Pull Request

Systematic workflow for reviewing PRs and responding to comments.

## Quick Review Commands

```bash
# List open PRs
gh pr list

# View specific PR
gh pr view <number>

# View PR diff
gh pr diff <number>

# Checkout PR locally
gh pr checkout <number>

# View PR checks (CI status)
gh pr checks <number>

# Add review comment
gh pr review <number> --comment --body "Great work!"

# Approve PR
gh pr review <number> --approve --body "LGTM! Ready to merge."

# Request changes
gh pr review <number> --request-changes --body "Please address the following..."

# View review comments
gh pr view <number> --comments
```

## Review Checklist

### 1. Initial Review

- [ ] **Read PR description** - Understand purpose, scope, and context
- [ ] **Check CI status** - Don't review if tests/linting are failing
- [ ] **Review linked issues** - Verify PR addresses the issue correctly
- [ ] **Check files changed** - Ensure changes are scoped appropriately

### 2. Code Quality

#### General

- [ ] Code follows TypeScript/Python conventions
- [ ] Functions have clear, single responsibilities
- [ ] Variable/function names are descriptive and consistent
- [ ] No commented-out code or debug logs
- [ ] Error handling is appropriate and comprehensive
- [ ] No hardcoded values that should be environment variables

#### Python (Flask)

- [ ] Type hints on all function signatures
- [ ] Proper use of snake_case naming
- [ ] Docstrings for complex functions
- [ ] No `print()` statements (use logging)
- [ ] Proper exception handling with specific exception types

#### TypeScript (Next.js)

- [ ] No `any` types (except where absolutely necessary with justification)
- [ ] Proper use of interfaces/types
- [ ] Const assertions where appropriate
- [ ] Proper async/await error handling

### 3. Type Safety

#### Python

- [ ] mypy type checking passes
- [ ] Type hints match actual usage
- [ ] No `# type: ignore` comments without explanation

#### TypeScript

- [ ] Strict null checks enforced
- [ ] Type exports in appropriate locations
- [ ] No implicit `any` in functions
- [ ] Generics used correctly

### 4. Testing

- [ ] New features have test coverage
- [ ] Tests are clear and descriptive
- [ ] Edge cases are tested
- [ ] Error paths are tested
- [ ] Coverage meets 70% threshold (Flask)
- [ ] Critical paths have 100% coverage (video generation, S3, auth)
- [ ] Tests don't have hardcoded credentials or secrets
- [ ] External services are properly mocked

#### Flask Testing

- [ ] pytest fixtures used appropriately
- [ ] Mock boto3 for S3 operations
- [ ] Mock Supabase client for database operations
- [ ] Test both success and failure cases

#### Frontend Testing (when configured)

- [ ] Component tests for new components
- [ ] Integration tests for user workflows
- [ ] Accessibility tests included

### 5. Documentation

- [ ] Complex algorithms have comments explaining approach
- [ ] Public API functions have docstrings/JSDoc
- [ ] README updated if new features/commands added
- [ ] Breaking changes clearly documented
- [ ] Environment variables documented
- [ ] Deployment notes included if infrastructure changes

### 6. Monorepo Structure

- [ ] Changes are in correct package (flask/, web/, packages/)
- [ ] Dependencies properly declared in package.json/pyproject.toml
- [ ] No circular dependencies introduced
- [ ] turbo.json updated if new tasks added
- [ ] Build order is correct (dependsOn in turbo.json)

### 7. Security & Privacy

- [ ] No sensitive data logged
- [ ] No secrets in code or committed files
- [ ] Authentication required on protected endpoints
- [ ] Proper JWT validation
- [ ] RLS policies maintained (if Supabase changes)
- [ ] User permissions checked
- [ ] Input validation on all user inputs
- [ ] SQL injection prevention (parameterized queries)

#### Flask Security

- [ ] JWT token validation on protected routes
- [ ] Proper 401/403 responses for unauthorized access
- [ ] No SQL injection vulnerabilities
- [ ] S3 bucket permissions correct

#### Supabase Security

- [ ] RLS policies enabled on all tables
- [ ] User-scoped queries (users see only their data)
- [ ] Sensitive data encrypted
- [ ] No direct SQL string concatenation

### 8. Performance

#### Backend

- [ ] Database queries are efficient
- [ ] Proper indexing on queried columns
- [ ] No N+1 query problems
- [ ] Large file operations chunked appropriately
- [ ] Connection pooling considered

#### Frontend

- [ ] No unnecessary re-renders (check useEffect deps)
- [ ] Proper use of React.memo/useMemo/useCallback
- [ ] Images optimized (Next.js Image component)
- [ ] Code splitting where appropriate
- [ ] No blocking operations in render

### 9. Flask API Specific

- [ ] Routes follow RESTful conventions
- [ ] Proper HTTP status codes (200, 201, 400, 401, 404, 500)
- [ ] Request validation (input types, required fields)
- [ ] Response format consistent (JSON)
- [ ] Pagination for large datasets
- [ ] Rate limiting considered for public endpoints

### 10. Next.js Frontend Specific

- [ ] Server-side rendering used appropriately
- [ ] Client-side state management clean (hooks, context)
- [ ] API calls error handling
- [ ] Loading states shown to users
- [ ] Material-UI components used correctly
- [ ] Responsive design (mobile + desktop)
- [ ] Accessibility: ARIA labels, keyboard navigation

### 11. Docker/Infrastructure

- [ ] docker-compose.dev.yml and docker-compose.prod.yml in sync
- [ ] Environment variables documented
- [ ] Volume mounts correct
- [ ] Service dependencies correct (depends_on)
- [ ] Health checks configured
- [ ] Port conflicts avoided

### 12. Database Migrations

- [ ] Migration file follows naming convention
- [ ] Migration tested locally
- [ ] Rollback script provided
- [ ] No data loss in migration
- [ ] Indexes added for performance
- [ ] RLS policies updated

## Review Response Workflow

### As a Reviewer

1. **Read the PR description** - Understand the purpose and scope
2. **Check CI status** - Don't review if CI is failing
3. **Review diff file by file** - Start with test files to understand intent
4. **Checkout locally** - Run tests and try the feature
5. **Leave specific comments** - Reference line numbers, suggest alternatives
6. **Approve or request changes** - Be clear and constructive

### As a PR Author

1. **Address all comments** - Don't ignore any feedback
2. **Respond to each comment** - Explain your reasoning or agree to change
3. **Push fixes** - Make requested changes in new commits
4. **Mark resolved** - Resolve conversations after addressing
5. **Request re-review** - Notify reviewers when ready (`gh pr review --request`)

## Example Review Comments

### Good Comments

```markdown
**Line 42 (app.py)**: Consider using `Path.exists()` instead of try/except for
file existence check - it's more explicit and easier to read.

**Line 87 (videoWriter.py)**: The decimation factor of 4 is hardcoded here.
Should this be a parameter to allow different decimation rates?

**tests/test_video.py**: Great test coverage on the VideoWriter class! Could you
add a test case for when the S3 bucket is unavailable to verify retry logic?

**General**: Excellent work on the video generation feature! The code is clean and
well-tested. Just a few minor suggestions above regarding error handling.
```

### Less Helpful Comments

```markdown
This doesn't look right. ❌
Why did you do it this way? ❌
Use Path instead. ❌
Too much code. ❌
```

**Better versions:**

```markdown
This error handling might not catch all cases. Consider also handling OSError. ✓
What's the reasoning behind using recursion here? Iterative might be clearer. ✓
Using Path.exists() would be more explicit than try/except here. ✓
This function is doing multiple things. Consider splitting into smaller functions. ✓
```

## GitHub CLI Review Examples

```bash
# Start a review
gh pr review 27 --comment --body "Starting review..."

# Approve with message
gh pr review 27 --approve --body "LGTM! Great test coverage on the video generation logic. The error handling looks solid."

# Request changes
gh pr review 27 --request-changes --body "Please address the comments about error handling in videoWriter.py and add tests for S3 retry logic."

# View review comments
gh pr view 27 --comments

# Respond to review as author
gh pr comment 27 --body "✅ Addressed all review comments. Added S3 retry tests and improved error handling."
```

## Responding to Review Comments

```bash
# View PR with comments
gh pr view 27 --comments

# Checkout PR to make fixes
gh pr checkout 27

# Make changes, commit, push
git add .
git commit -m "fix: address review comments on error handling"
git push

# Notify reviewer
gh pr comment 27 --body "✅ Addressed all review comments:
- Added Path.exists() checks (line 42)
- Made decimation factor configurable (line 87)
- Added S3 retry test (test_video.py:156)

Ready for re-review!"
```

## Common Review Patterns for Bloom

### Pattern 1: Flask API Changes

When reviewing Flask API changes:

1. **Check authentication** - All protected endpoints validate JWT
2. **Verify S3 operations** - Proper error handling, retry logic, cleanup
3. **Test coverage** - Core logic has 100%, endpoints have 80%+
4. **Type hints** - All functions have proper type annotations
5. **Error responses** - Proper HTTP status codes and error messages

### Pattern 2: Video Generation Changes

When reviewing video generation code:

1. **Memory efficiency** - Large videos don't consume too much memory
2. **File cleanup** - Temporary files are deleted after processing
3. **S3 integration** - Images fetched correctly, videos uploaded successfully
4. **Error handling** - Missing images, network failures, codec errors
5. **Configuration** - FPS, decimation, resolution are configurable

### Pattern 3: Database Schema Changes

When reviewing Supabase migrations:

1. **RLS policies** - User-scoped access maintained
2. **Migrations** - Up and down migrations provided
3. **Data integrity** - Foreign keys, constraints properly defined
4. **Performance** - Indexes on queried columns
5. **Security** - Sensitive data encrypted, no SQL injection

### Pattern 4: Next.js Component Changes

When reviewing React components:

1. **Type safety** - Props interfaces defined, no implicit `any`
2. **Accessibility** - ARIA labels, keyboard navigation, semantic HTML
3. **Responsive** - Works on mobile and desktop
4. **Material-UI** - Uses theme properly, follows design system
5. **Performance** - No unnecessary re-renders, proper memoization

### Pattern 5: Docker/Infrastructure Changes

When reviewing Docker changes:

1. **Both environments** - dev and prod configs updated
2. **Environment variables** - All required vars documented
3. **Testing** - Changes tested with `make dev-up` and `make prod-up`
4. **Networking** - Services communicate correctly, ports documented
5. **Volumes** - Data persistence correct, no data loss

## When to Request Changes vs Comment

### Request Changes (Blocking Issues)

- Type errors or linting failures
- Failing tests or missing test coverage
- Security vulnerabilities
- Incorrect algorithms or logic errors
- Breaking changes without migration path
- Missing RLS policies on database tables

### Comment (Non-Blocking Suggestions)

- Style improvements (unless violating standards)
- Performance optimizations (unless critical)
- Refactoring suggestions
- Nice-to-have features
- Questions about approach

## Escalation

If a PR discussion is getting stuck:

1. **Jump on a call** - Discuss synchronously
2. **Create a GitHub Discussion** - For architectural questions
3. **Update project.md or CLAUDE.md** - Document decision for future reference
4. **Bring in another reviewer** - Get third opinion

## Tips for Effective Reviews

1. **Be timely** - Review within 24 hours if possible
2. **Be specific** - Reference line numbers, suggest concrete alternatives
3. **Be kind** - Assume positive intent, use constructive language
4. **Test locally** - Don't just read code, run it
5. **Focus on substance** - Don't nitpick style (Prettier handles that)
6. **Explain why** - Help the author learn, don't just point out issues
7. **Approve quickly** - If it's good, say so and approve
8. **Ask questions** - "Why X?" is better than "X is wrong"

## Related Commands

- `/pre-merge` - Comprehensive pre-merge checklist for PR authors
- `/pr-description` - Generate comprehensive PR description
- `/run-ci-locally` - Run CI checks before requesting review
- `/docs-review` - Review documentation changes
