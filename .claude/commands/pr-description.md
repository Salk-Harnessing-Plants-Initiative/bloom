---
name: PR Description Template
description: Template for creating comprehensive pull request descriptions
category: Git Workflow
tags: [pr, github, template]
---

# PR Description Template

Use this template when creating pull requests to ensure comprehensive documentation.

## Quick Commands

```bash
# View current PR
gh pr view

# View PR diff
gh pr diff

# List changed files
gh pr diff --name-only

# View specific file changes
gh pr diff <file-path>

# Create PR with template
gh pr create --title "feat: add video generation endpoint" --body "$(cat PR_TEMPLATE.md)"

# Edit existing PR description
gh pr edit --body "$(cat updated_description.md)"
```

## PR Description Template

```markdown
## Summary

[Brief 1-2 sentence description of what this PR does]

## Changes

- [Bullet point list of specific changes]
- [Group related changes together]
- [Use present tense: "Add X", "Fix Y", "Update Z"]

## Testing

- [ ] All existing tests pass (`cd flask && uv run pytest`)
- [ ] Added new tests for new functionality
- [ ] Coverage meets 70% threshold (`uv run pytest --cov`)
- [ ] Manually tested [describe manual testing if applicable]

## Type Checking & Linting

- [ ] Python: Black, Ruff, mypy pass (`cd flask && uv run black . && uv run ruff check . && uv run mypy .`)
- [ ] TypeScript: type-check passes (`pnpm type-check`)
- [ ] ESLint passes (`pnpm lint`)
- [ ] Prettier formatting applied (`pnpm format`)
- [ ] Pre-commit hooks pass (`pre-commit run --all-files`)

## Coverage

- [ ] Coverage meets 70% threshold
- [ ] Coverage report reviewed (`cd flask && uv run pytest --cov --cov-report=html`)
- [ ] Critical paths have 100% coverage (video generation, S3 operations, auth)

## Breaking Changes

- [ ] No breaking changes
- [ ] Breaking changes documented below

[If breaking changes, describe migration path]

## Related Issues

Closes #[issue number]
Related to #[issue number]

## Screenshots/Examples

[If UI changes, include screenshots]
[If API changes, include example requests/responses]

## Deployment Notes

- [ ] No environment variable changes
- [ ] Environment variables documented below
- [ ] No database migrations needed
- [ ] Database migrations documented below
- [ ] No Docker config changes
- [ ] Docker changes documented below

[Document any deployment considerations]

## Reviewer Notes

[Specific areas you want reviewers to focus on]
[Any concerns or questions you have]
```

## Monorepo Package-Specific Checklists

### Flask API Changes

```markdown
## Flask API Changes

### Code Quality

- [ ] Type hints added to all functions
- [ ] Error handling for edge cases
- [ ] Logging added for debugging
- [ ] No hardcoded secrets or credentials

### Testing

- [ ] Unit tests for core logic (100% coverage target)
- [ ] Integration tests for endpoints (80% coverage target)
- [ ] Mocked external services (S3, Supabase)
- [ ] Error cases tested (404, 401, 500)

### S3/Storage

- [ ] Bucket names from environment variables
- [ ] Proper error handling for network failures
- [ ] File cleanup after processing
- [ ] Access control verified

### Authentication

- [ ] JWT validation on protected endpoints
- [ ] Proper 401/403 responses
- [ ] User permissions checked
- [ ] No authentication bypass vulnerabilities
```

### Next.js Frontend Changes

```markdown
## Next.js Frontend Changes

### Code Quality

- [ ] TypeScript strict mode compliant
- [ ] No `any` types (except where justified)
- [ ] PropTypes or TypeScript interfaces for components
- [ ] Accessibility: ARIA labels, keyboard navigation

### Performance

- [ ] No unnecessary re-renders
- [ ] Proper use of useMemo/useCallback
- [ ] Images optimized (Next.js Image component)
- [ ] Code splitting where appropriate

### Styling

- [ ] Material-UI components used correctly
- [ ] Responsive design (mobile + desktop)
- [ ] Consistent with design system
- [ ] No inline styles (use sx prop or styled)

### Testing

- [ ] Component tests (once Jest configured)
- [ ] Integration tests for user flows
- [ ] Accessibility tests
```

### Database/Supabase Changes

```markdown
## Database/Supabase Changes

### Schema

- [ ] Migration file created in `supabase/migrations/`
- [ ] Migration tested locally
- [ ] Rollback strategy documented
- [ ] No data loss in migration

### Security

- [ ] RLS (Row Level Security) policies updated
- [ ] User permissions verified
- [ ] Sensitive data encrypted (pgcrypto for birth data equivalent)
- [ ] No SQL injection vulnerabilities

### Performance

- [ ] Indexes added for queried columns
- [ ] Query performance tested
- [ ] No N+1 queries
- [ ] Connection pooling considered
```

### Docker/Infrastructure Changes

```markdown
## Docker/Infrastructure Changes

### Configuration

- [ ] `docker-compose.dev.yml` updated
- [ ] `docker-compose.prod.yml` updated
- [ ] Environment variables documented
- [ ] Volume mounts correct

### Testing

- [ ] Dev environment tested (`make dev-up`)
- [ ] Prod environment tested (`make prod-up`)
- [ ] Services communicate correctly
- [ ] Logs checked for errors

### Networking

- [ ] Ports documented
- [ ] nginx config updated (if needed)
- [ ] Kong routes updated (if needed)
- [ ] Service health checks pass
```

## Examples

### Feature PR Example

```markdown
## Summary

Add video generation endpoint to Flask API that creates MP4 videos from cylindrical scan image sequences stored in MinIO.

## Changes

- **flask/app.py**: Add `/api/video/generate` POST endpoint
- **flask/videoWriter.py**: Implement VideoWriter class with decimation support
- **flask/tests/test_video.py**: Add comprehensive test suite for video generation
- **flask/pyproject.toml**: Add opencv-python dependency

## Testing

- [x] All existing tests pass
- [x] Added 12 new test cases for VideoWriter class
- [x] Coverage: 85% overall (up from 70%), 100% on VideoWriter
- [x] Manually tested with test scanner data (scanner_id=42)

## Type Checking & Linting

- [x] Black formatting applied
- [x] Ruff checks pass
- [x] mypy type checking pass (added type hints to all functions)
- [x] Pre-commit hooks pass

## Coverage

- [x] VideoWriter class: 100% coverage
- [x] Video generation endpoint: 95% coverage
- [x] Error handling tested (missing files, network failures)

## Deployment Notes

**Environment Variables**: None

**Dependencies**: Added `opencv-python>=4.8.0` to pyproject.toml

**Testing**: Run `cd flask && uv sync` to install new dependencies

## Reviewer Notes

Please focus on:

- Error handling in VideoWriter.process_image() (lines 45-60)
- Memory efficiency of frame buffering (potential for large videos)
- JWT validation in video endpoint (lines 120-125)
```

### Bug Fix PR Example

```markdown
## Summary

Fix S3 upload retry logic that was causing failed uploads to not retry correctly.

## Changes

- **flask/app.py**: Add exponential backoff to S3 upload with 3 retries
- **flask/tests/test_s3.py**: Add test for retry logic with network failures

## Testing

- [x] All tests pass
- [x] Added regression test that reproduces the bug
- [x] Manually tested with intermittent network connection

## Type Checking & Linting

- [x] All checks pass

## Related Issues

Closes #42

## Reviewer Notes

The bug was caused by boto3's default retry logic not handling ConnectionError. Now we explicitly catch and retry with exponential backoff (1s, 2s, 4s).
```

### Database Migration PR Example

````markdown
## Summary

Add RLS policies to `cyl_images` table to ensure users can only access their own scan data.

## Changes

- **supabase/migrations/20250107_add_rls_policies.sql**: Add RLS policies for cyl_images
- **supabase/migrations/20250107_add_rls_policies_rollback.sql**: Rollback script

## Testing

- [x] Migration tested locally (`make dev-up`)
- [x] Verified users can only see their own images
- [x] Verified admin users can see all images
- [x] Rollback tested successfully

## Security

- [x] RLS enabled on cyl_images table
- [x] Policies tested with different user roles
- [x] No unauthorized data access possible

## Deployment Notes

**Migration**: Run migration on production:

```bash
docker exec -it supabase-db psql -U postgres -d postgres -f /docker-entrypoint-initdb.d/migrations/20250107_add_rls_policies.sql
```
````

**Rollback**: If needed:

```bash
docker exec -it supabase-db psql -U postgres -d postgres -f /docker-entrypoint-initdb.d/migrations/20250107_add_rls_policies_rollback.sql
```

## Reviewer Notes

Please verify the RLS policies are correct for all user roles (admin, researcher, viewer).

````

## GitHub CLI Tips

```bash
# Create PR with title and body
gh pr create --title "feat: add video generation" --body "Summary of changes..."

# Create PR and open in browser for editing
gh pr create --web

# Add labels
gh pr edit --add-label "area:flask" --add-label "type:feature"

# Request review
gh pr edit --add-reviewer @username

# Check CI status
gh pr checks

# View PR comments
gh pr view --comments

# Merge PR (after approval)
gh pr merge --squash

# Close PR without merging
gh pr close
````

## Tips for Writing Good PR Descriptions

1. **Be specific**: "Add video generation endpoint" not "Add new feature"
2. **Explain why**: Include motivation and context, not just what changed
3. **Link to issues**: Use "Closes #42" to auto-close issues
4. **Include examples**: Show request/response, screenshots, or code samples
5. **Note breaking changes**: Make them obvious with **BREAKING:** prefix
6. **Test thoroughly**: Check all boxes honestly, don't skip testing
7. **Think about reviewers**: What context do they need to review effectively?
8. **Update as you go**: Add new changes to description as you push commits

## Related Commands

- `/pre-merge` - Comprehensive pre-merge checklist before merging
- `/run-ci-locally` - Run all CI checks before creating PR
- `/lint` - Check code style before PR
- `/coverage` - Verify test coverage meets thresholds
- `/review-pr` - Review PR checklist
