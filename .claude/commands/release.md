---
name: Release Process
description: Complete release workflow with semantic versioning and npm publishing
category: Deployment
tags: [release, deployment, npm, versioning]
---

# Release Process for Bloom

Comprehensive workflow for releasing a new version of Bloom packages to npm.

## Purpose

This command guides you through the complete release process, ensuring:

1. All pre-release checks pass (tests, coverage, linting, CI)
2. Version is bumped correctly following semantic versioning
3. Changes are documented and committed properly
4. GitHub release is created with appropriate notes
5. Packages are published to npm registry (if configured)
6. Release is verified and documented

## Prerequisites

Before starting a release, ensure:

- You are on the `main` branch with latest changes
- All PRs intended for this release are merged
- CI is passing on main branch (once GitHub Actions configured)
- You have maintainer permissions for the repository
- `gh` CLI is authenticated
- npm publishing is configured (if publishing packages)

## Usage

```bash
# Interactive release workflow
/release

# Or specify version type
/release patch   # 1.2.3 → 1.2.4
/release minor   # 1.2.3 → 1.3.0
/release major   # 1.2.3 → 2.0.0
```

## Release Workflow

### Step 1: Pre-Release Validation

Verify the project is ready for release:

```bash
# Check we're on main branch
git branch --show-current  # Should be 'main'

# Ensure working directory is clean
git status

# Pull latest changes
git pull origin main

# Verify CI is passing on main (once GitHub Actions configured)
gh run list --branch main --limit 5
```

**Run validation commands** (use our Claude commands):

```bash
# Run linting checks (see /lint command)
/lint

# Or manually:
cd flask && uv run black --check . && uv run ruff check . && uv run mypy .
pnpm lint && pnpm type-check

# Run coverage analysis (see /coverage command)
/coverage

# Or manually (Phase 2+):
cd flask && uv run pytest --cov --cov-fail-under=70

# Review documentation (see /docs-review command)
/docs-review --check
```

**Build and validate packages:**

```bash
# Build Next.js app
pnpm build

# Build Docker images
docker-compose -f docker-compose.prod.yml build

# Verify images
docker images | grep bloom
```

**Stop if any checks fail.** Fix issues before proceeding.

**Tip**: Use `/lint`, `/coverage`, and `/docs-review` commands for detailed guidance.

### Step 2: Determine Version Number and Update Changelog

Follow semantic versioning (https://semver.org):

**MAJOR.MINOR.PATCH** (e.g., 1.2.3)

- **PATCH** (1.2.3 → 1.2.4): Bug fixes, documentation updates, minor improvements
- **MINOR** (1.2.3 → 1.3.0): New features, backward-compatible changes
- **MAJOR** (1.2.3 → 2.0.0): Breaking changes, incompatible API changes

Current version: Read from `package.json` line with `"version"`

**Review changes and update CHANGELOG.md** (see `/changelog` command for details):

```bash
# Review changes since last release
LAST_TAG=$(gh release list --limit 1 --json tagName --jq '.[0].tagName')
echo "Last release: $LAST_TAG"
git log $LAST_TAG..HEAD --oneline --no-merges

# Or if no releases yet:
git log --oneline --no-merges | head -20
```

**Update CHANGELOG.md**:

1. Move items from `[Unreleased]` section to new versioned section
2. Add today's date in YYYY-MM-DD format
3. Update comparison links at bottom
4. Save and commit with version bump

**Tip**: Use `/changelog` command for Keep a Changelog format guidelines.

**Decision Matrix:**

- New video generation features? → MINOR bump
- Just bug fixes? → PATCH bump
- Breaking API changes? → MAJOR bump

### Step 3: Create Release Branch

```bash
# Determine new version
CURRENT_VERSION=$(node -p "require('./package.json').version")
echo "Current version: $CURRENT_VERSION"

# Get new version (example: 1.3.0 for minor bump)
NEW_VERSION="1.3.0"  # Replace based on Step 2 decision

# Create release branch
git checkout -b release/v$NEW_VERSION
```

### Step 4: Update Version Number

Use npm version command to update all packages:

```bash
# Bump version in package.json and all workspace packages
npm version $NEW_VERSION --no-git-tag-version --workspaces --include-workspace-root

# This updates:
# - package.json (root)
# - web/package.json
# - packages/bloom-fs/package.json
# - packages/bloom-js/package.json
# - packages/bloom-nextjs-auth/package.json
```

**Verify version updates:**

```bash
# Check all package.json files were updated
grep -r '"version":' package.json web/package.json packages/*/package.json
```

### Step 5: Update Documentation (if needed)

Check if README or other docs need updates:

- Installation instructions
- Version-specific examples
- Breaking changes documentation
- Migration guides (for MAJOR versions)
- API documentation (if API changes)

### Step 6: Build and Test Release Artifacts

```bash
# Install dependencies with exact versions
pnpm install

# Build all packages
pnpm build

# Build Docker images for deployment
docker-compose -f docker-compose.prod.yml build

# Verify Docker images
docker images | grep bloom

# Test prod image runs
docker-compose -f docker-compose.prod.yml up -d
# ... test functionality ...
docker-compose -f docker-compose.prod.yml down
```

### Step 7: Commit Version Bump and Changelog

```bash
# Stage changes (version and changelog)
git add package.json web/package.json packages/*/package.json CHANGELOG.md pnpm-lock.yaml

# Commit with standard message format
git commit -m "chore: bump version to v$NEW_VERSION

- Update version in package.json files
- Update CHANGELOG.md with release notes

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"

# Push release branch
git push origin release/v$NEW_VERSION
```

### Step 8: Create and Merge Version Bump PR

**Tip**: Use `/pr-description` command for the full PR template.

```bash
# Create PR with detailed description
gh pr create \
  --title "Release v$NEW_VERSION" \
  --body "$(cat <<EOF
## Release v$NEW_VERSION

### Version Bump

- Bumps version from $CURRENT_VERSION to $NEW_VERSION

### Changes Since Last Release

$(git log v$CURRENT_VERSION..HEAD --oneline --no-merges | head -20)

### Pre-Release Checklist

- [x] All linting checks pass
- [x] Type checking passes
- [x] Documentation reviewed
- [x] Docker builds succeed
- [x] CHANGELOG.md updated
- [x] All package versions bumped consistently

### Release Type

- [ ] PATCH - Bug fixes and minor improvements
- [ ] MINOR - New features, backward-compatible
- [ ] MAJOR - Breaking changes

### Post-Merge Steps

1. Create GitHub release with tag v$NEW_VERSION
2. Deploy to production (if applicable)
3. Test deployed version
4. Announce release

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"

# Wait for review and CI checks
gh pr checks --watch

# After approval, merge
echo "Request review from maintainers, then merge when approved"
gh pr merge --squash --delete-branch
```

### Step 9: Create GitHub Release

After PR is merged to main:

```bash
# Switch to main and pull
git checkout main
git pull origin main

# Create GitHub release
gh release create v$NEW_VERSION \
  --title "Bloom v$NEW_VERSION" \
  --generate-notes \
  --notes "$(cat <<EOF
## Installation

### Docker Deployment

\`\`\`bash
# Pull latest images
docker-compose -f docker-compose.prod.yml pull

# Restart services
docker-compose -f docker-compose.prod.yml up -d
\`\`\`

### Development Setup

\`\`\`bash
# Clone and install
git clone <repo-url>
cd bloom
pnpm install

# Start development servers
make dev-up
\`\`\`

## What's Changed

$(gh pr list --search "is:merged is:pr merged:>$(date -v-30d +%Y-%m-%d)" --limit 10 --json title,number --jq '.[] | "- #\(.number): \(.title)"')

**Full Changelog**: https://github.com/Salk-Harnessing-Plants-Initiative/bloom/compare/v$CURRENT_VERSION...v$NEW_VERSION

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Step 10: Deploy Release (if applicable)

```bash
# Deploy to production server
# Example for Docker-based deployment:

# SSH to production server
ssh production-server

# Pull latest code
cd /path/to/bloom
git pull origin main

# Rebuild and restart services
docker-compose -f docker-compose.prod.yml build
docker-compose -f docker-compose.prod.yml up -d

# Verify services are running
docker-compose -f docker-compose.prod.yml ps

# Check logs for errors
docker-compose -f docker-compose.prod.yml logs --tail=50 flask-app
docker-compose -f docker-compose.prod.yml logs --tail=50 web
```

### Step 11: Verify Release

Monitor the release:

```bash
# Verify GitHub release created
gh release view v$NEW_VERSION

# Test deployed application
curl https://your-domain.com/api/  # Flask health check
curl https://your-domain.com/      # Next.js app

# Or test locally with prod images
docker-compose -f docker-compose.prod.yml up -d
open http://localhost:3000
```

### Step 12: Post-Release Tasks

```bash
# Update any version badges or links if needed
# Announce release in relevant channels
# Close any resolved issues
# Update project board if used

echo "✅ Release v$NEW_VERSION complete!"
echo "🐙 GitHub: https://github.com/Salk-Harnessing-Plants-Initiative/bloom/releases/tag/v$NEW_VERSION"
```

## Rollback Procedures

### If Release Fails Before Deployment

```bash
# Delete GitHub release
gh release delete v$NEW_VERSION --yes

# Delete tag
git tag -d v$NEW_VERSION
git push origin :refs/tags/v$NEW_VERSION

# Revert version bump on main
git revert HEAD
git push origin main
```

### If Release Fails After Deployment

```bash
# Roll back to previous version
PREVIOUS_VERSION=$CURRENT_VERSION

# On production server
git checkout v$PREVIOUS_VERSION
docker-compose -f docker-compose.prod.yml build
docker-compose -f docker-compose.prod.yml up -d

# Mark release as pre-release on GitHub
gh release edit v$NEW_VERSION --prerelease

# Or delete release entirely
gh release delete v$NEW_VERSION --yes

# Release a patch version with fixes
# Follow normal release process with incremented version
```

### If Application is Broken in Production

```bash
# Immediate rollback to last known good version
ssh production-server
cd /path/to/bloom
git checkout v$PREVIOUS_VERSION
docker-compose -f docker-compose.prod.yml up -d --force-recreate

# Mark release as problematic
gh release edit v$NEW_VERSION --prerelease \
  --notes "⚠️ WARNING: Critical bug found. Use v$PREVIOUS_VERSION instead."

# Fix issues and release patch version ASAP
```

## Safety Checks and Validation

### Critical Validations

Before each release, verify:

1. **Linting Passes**: All code style checks pass

   ```bash
   /lint
   ```

2. **Type Checking**: No TypeScript or mypy errors

   ```bash
   pnpm type-check
   cd flask && uv run mypy .
   ```

3. **Documentation**: All docs up to date

   ```bash
   /docs-review --check
   ```

4. **Docker Builds**: Production images build successfully

   ```bash
   docker-compose -f docker-compose.prod.yml build
   ```

5. **Clean Working Tree**: No uncommitted changes
   ```bash
   git status --porcelain
   # Should be empty
   ```

### Release Checklist

Copy this checklist for each release:

```
## Release v{VERSION} Checklist

### Pre-Release

- [ ] All intended PRs merged to main
- [ ] CI passing on main branch
- [ ] Linting checks pass (/lint)
- [ ] Type checking passes
- [ ] Version number determined (MAJOR.MINOR.PATCH)
- [ ] CHANGELOG reviewed (git log since last release)
- [ ] CHANGELOG.md updated

### Version Bump

- [ ] Release branch created
- [ ] All package.json files updated with new version
- [ ] CHANGELOG.md updated with release date and version
- [ ] Documentation updated (if needed)
- [ ] Docker images build successfully
- [ ] pnpm-lock.yaml updated

### PR and Review

- [ ] Version bump PR created
- [ ] PR description includes changes since last release
- [ ] CI checks pass on PR (once configured)
- [ ] Code review approved
- [ ] PR merged to main

### GitHub Release

- [ ] Switched to main branch
- [ ] Pulled latest changes
- [ ] GitHub release created with tag
- [ ] Release notes generated and reviewed
- [ ] Release published

### Deployment (if applicable)

- [ ] Deployed to production
- [ ] Services restarted successfully
- [ ] Health checks pass
- [ ] Application functionality verified

### Post-Release

- [ ] Release announced (if applicable)
- [ ] Related issues closed
- [ ] Project board updated
- [ ] README badges updated (if needed)
```

## Versioning Examples

### Patch Release (1.2.3 → 1.2.4)

**When to use:**

- Bug fixes
- Documentation improvements
- Test additions
- Code refactoring without API changes
- Performance improvements (non-breaking)

**Example changes:**

- Fix video generation encoding bug
- Update README installation instructions
- Add missing test cases
- Optimize S3 presigned URL generation

### Minor Release (1.2.3 → 1.3.0)

**When to use:**

- New features
- New API endpoints
- Backward-compatible additions
- Significant improvements

**Example changes:**

- Add new database tables for experiments
- New video processing options
- Enhanced authentication flow
- Additional export formats

### Major Release (1.2.3 → 2.0.0)

**When to use:**

- Breaking API changes
- Incompatible database schema changes
- Major architecture rewrites
- Removal of deprecated features

**Example changes:**

- Change REST API response format
- Remove or rename public endpoints
- Require new authentication method
- Upgrade to Next.js 17 (if breaking)

## Integration with Other Commands

### Before Releasing (Pre-Release Validation)

```bash
/lint              # Check code style
/coverage          # Verify test coverage (Phase 2+)
/docs-review       # Review documentation
/run-ci-locally    # Run full CI suite locally
```

### During Release (Changelog)

```bash
/changelog         # Update CHANGELOG.md with Keep a Changelog format
```

### Creating Version Bump PR

```bash
/pr-description    # Use comprehensive PR template
```

### After Merging

```bash
# Continue with GitHub release creation
# Deployment happens manually or via CI/CD
```

### Post-Release Cleanup

```bash
/cleanup-merged    # Clean up release branch after merge
```

## Common Issues and Solutions

### Issue: CI Fails on Release PR

**Solution:**

```bash
# Pull latest changes from main
git checkout release/v$NEW_VERSION
git merge origin/main

# Fix any conflicts
# Run checks locally
/run-ci-locally

# Push updates
git push origin release/v$NEW_VERSION
```

### Issue: Docker Build Fails

**Solution:**

```bash
# Check Docker build logs
docker-compose -f docker-compose.prod.yml build --no-cache

# Common causes:
# 1. Dependency version conflicts (check package.json, pyproject.toml)
# 2. Missing environment variables
# 3. File permissions in Docker context

# Test build locally before pushing
docker-compose -f docker-compose.prod.yml build
```

### Issue: Version Mismatch Between Packages

**Prevention:**

```bash
# Always use npm version with --workspaces flag
npm version $NEW_VERSION --no-git-tag-version --workspaces --include-workspace-root

# Verify all updated
grep -r '"version":' package.json web/package.json packages/*/package.json
```

### Issue: Deployment Fails

**Solution:**

```bash
# Check deployment logs
ssh production-server
docker-compose -f docker-compose.prod.yml logs --tail=100

# Common issues:
# 1. Environment variables not set (.env.prod)
# 2. Ports already in use
# 3. Database migration needed
# 4. MinIO bucket not initialized

# Roll back if needed
git checkout v$PREVIOUS_VERSION
docker-compose -f docker-compose.prod.yml up -d --force-recreate
```

## Best Practices

1. **Release Often**: Small, frequent releases are easier to manage than large ones
2. **Test Thoroughly**: Always run full validation before releasing
3. **Document Changes**: Use git log and PR descriptions to generate release notes
4. **Verify Deployment**: Test deployed version before announcing
5. **Communicate**: Announce releases to users and stakeholders
6. **Tag Consistently**: Always use `v` prefix for tags (v1.2.3)
7. **Never Force Push**: Release tags should be immutable
8. **Keep Changelog**: Maintain CHANGELOG.md using Keep a Changelog format
9. **Version Consistency**: All workspace packages should have same version
10. **Backup First**: Take database backup before major upgrades

## Related Documentation

- [Semantic Versioning](https://semver.org)
- [npm Publishing](https://docs.npmjs.com/cli/v8/commands/npm-publish)
- [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github)
- [Docker Compose](https://docs.docker.com/compose/)
- [Keep a Changelog](https://keepachangelog.com/)
- [Turborepo Versioning](https://turbo.build/repo/docs/handbook/publishing-packages/versioning-and-publishing)

## Related Commands

- `/lint` - Run code style checks
- `/coverage` - Run test coverage analysis
- `/docs-review` - Review and update documentation
- `/changelog` - Maintain CHANGELOG.md following Keep a Changelog format
- `/pr-description` - Template for creating comprehensive PRs
- `/review-pr` - Systematic PR review checklist
- `/run-ci-locally` - Run CI checks locally before pushing
- `/cleanup-merged` - Clean up merged branch and archive OpenSpec changes
- `/validate-env` - Validate development environment setup
