---
name: Update Changelog
description: Maintain CHANGELOG.md following Keep a Changelog format
category: Documentation
tags: [changelog, release, documentation]
---

# Update Changelog

Maintain CHANGELOG.md following [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.

## Quick Commands

```bash
# View recent changes
git log --oneline --decorate -10

# View changes since last tag
git log $(git describe --tags --abbrev=0)..HEAD --oneline

# View changes by author
git log --author="<name>" --oneline

# View changes to specific package
git log --oneline -- langchain/ bloommcp/
git log --oneline -- web/

# View current version (if package.json exists)
cat package.json | grep version
```

## Changelog Format

The CHANGELOG.md follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) principles:

- **Guiding Principle**: Changelogs are for humans, not machines
- **Latest First**: Most recent version at the top
- **One Version Per Release**: Each release gets a section
- **Same Date Format**: YYYY-MM-DD
- **Semantic Versioning**: Version numbers follow [SemVer](https://semver.org/)

### Change Categories

- **Added**: New features
- **Changed**: Changes to existing functionality
- **Deprecated**: Soon-to-be removed features
- **Removed**: Removed features
- **Fixed**: Bug fixes
- **Security**: Security vulnerability fixes

## Template

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- New feature description

### Changed

- Change description

### Fixed

- Bug fix description

## [1.2.0] - 2025-01-15

### Added

- LangGraph agent endpoint for plant image analysis
- Supabase migration for new cyl_experiments table
- JWT authentication for protected LangGraph endpoints

### Changed

- Updated Supabase configuration for self-hosted deployment
- Improved error handling in FastAPI services

### Fixed

- Fixed S3 upload retry logic for network failures

## [1.1.0] - 2025-01-10

### Added

- LangGraph agent service for AI-powered data analysis
- FastMCP server for plant data queries
- Next.js frontend with Material-UI components
- PostgreSQL database via self-hosted Supabase
- MinIO S3-compatible object storage
- Docker Compose setup for dev and prod environments

## [1.0.0] - 2024-12-20

### Added

- Initial monorepo setup with Turborepo
- Basic project structure (web/, langchain/, bloommcp/, packages/)
- Database schema for cylindrical scan data
- Development initialization scripts

[Unreleased]: https://github.com/username/bloom/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/username/bloom/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/username/bloom/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/username/bloom/releases/tag/v1.0.0
```

## Workflow: Adding Changes to Changelog

### Step 1: Identify Changes Since Last Release

```bash
# Find last version tag (if using tags)
git tag -l | sort -V | tail -1

# List commits since last tag
git log v1.1.0..HEAD --oneline

# Or view detailed diff
git log v1.1.0..HEAD --pretty=format:"%h %s" --reverse

# If no tags, view recent commits
git log --oneline -20
```

### Step 2: Categorize Each Change

Group commits by category:

- **Added**: New features, new endpoints, new components

  - "Add LangGraph agent endpoint for plant analysis"
  - "Add S3 integration for image storage"
  - "Add JWT authentication middleware"

- **Changed**: Refactors, performance improvements, dependency updates

  - "Update Docker Compose configuration"
  - "Improve error handling in FastAPI services"
  - "Migrate from pip to uv for Python packages"

- **Fixed**: Bug fixes, error handling improvements

  - "Fix S3 upload retry logic"
  - "Fix CORS headers in FastAPI service"
  - "Fix RLS policies on cyl_images table"

- **Security**: Security patches, dependency security updates
  - "Update FastAPI dependencies (CVE-2024-XXXX)"
  - "Add RLS policies to prevent unauthorized data access"

### Step 3: Update CHANGELOG.md

Add changes to the `[Unreleased]` section:

```markdown
## [Unreleased]

### Added

- LangGraph agent endpoint for plant image analysis with configurable parameters (#42)
- S3 integration for cylindrical scan image storage (#45)
- Pre-commit hooks for code quality (black, ruff, mypy, prettier) (#48)

### Changed

- Migrated Python package management from pip to uv (#50)
- Updated Caddy configuration for Supabase dashboard subpath (#38)

### Fixed

- Fixed S3 upload retry logic for network failures (#43)
- Fixed static asset loading in Supabase dashboard (#40)
```

### Step 4: When Releasing a Version

Move `[Unreleased]` to a versioned section:

```markdown
## [Unreleased]

## [1.2.0] - 2025-01-15

### Added

- LangGraph agent endpoint for plant image analysis (#42)
- S3 integration for cylindrical scan image storage (#45)
- Pre-commit hooks for code quality (#48)

### Changed

- Migrated Python package management from pip to uv (#50)
- Updated Caddy configuration for Supabase dashboard subpath (#38)

### Fixed

- Fixed S3 upload retry logic for network failures (#43)
```

Update the links at the bottom:

```markdown
[Unreleased]: https://github.com/username/bloom/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/username/bloom/compare/v1.1.0...v1.2.0
```

## Monorepo Considerations

Since Bloom is a monorepo, label changes by package:

```markdown
### Added

- **langchain**: LangGraph agent endpoint for plant analysis (#42)
- **web**: Scan data visualization dashboard (#44)
- **bloommcp**: FastMCP server for structured data queries (#45)
```

Or organize by package:

```markdown
## [1.2.0] - 2025-01-15

### langchain

#### Added

- LangGraph agent endpoint for plant image analysis (#42)
- S3 integration for image storage (#45)

#### Fixed

- S3 upload retry logic for network failures (#43)

### bloommcp

#### Added

- FastMCP server for plant data queries (#46)

### web

#### Added

- Scan data visualization dashboard (#44)
- Material-UI theming and design system (#46)

### infrastructure

#### Changed

- Updated Caddy configuration for Supabase dashboard subpath (#38)
- Added pre-commit hooks for code quality (#48)
```

## Writing Good Changelog Entries

### Good Examples

```markdown
### Added

- LangGraph agent endpoint analyzes plant scan sequences with configurable parameters
- S3 integration for MinIO storage with retry logic and exponential backoff for network failures
- Pre-commit hooks enforce code quality: Black, Ruff, mypy for Python; Prettier, ESLint for TypeScript

### Changed

- Migrated Python package management from pip to uv (10-100x faster, PEP 621 compliant)
- Updated Caddy reverse proxy to serve Supabase dashboard at /supabase_kong/ subpath with correct asset paths

### Fixed

- S3 upload retry logic now correctly handles ConnectionError with exponential backoff (1s, 2s, 4s)
- Supabase dashboard static assets (CSS, JS) now load correctly when served through Caddy reverse proxy
```

### Bad Examples

```markdown
### Added

- New stuff ❌ (too vague)
- Updated code ❌ (not informative)
- Various improvements ❌ (not specific)

### Fixed

- Bug fixes ❌ (which bugs?)
- Fixed issue ❌ (which issue?)
```

## Bloom-Specific Examples

### LangGraph Agent Changes

```markdown
### Added

- **langchain**: LangGraph agent processes plant scan image sequences
  - Configurable analysis parameters
  - Streaming response support
  - Tool-calling integration with FastMCP server
```

### Scan Data Changes

```markdown
### Added

- **web**: Scan data table with filtering, sorting, and pagination
- **web**: Real-time scan status updates via Supabase subscriptions
- **langchain**: Batch image analysis endpoint for scan sequences
```

### Supabase/Database Changes

```markdown
### Added

- **database**: RLS policies on cyl_images table for user-scoped access
- **database**: Indexes on scanner_id and created_at for query performance

### Security

- **database**: Enabled Row Level Security on all user data tables
- **database**: Encrypted sensitive scan metadata using pgcrypto
```

### Infrastructure Changes

```markdown
### Changed

- **docker**: Updated docker-compose.prod.yml Caddy configuration for subdirectory serving
- **docker**: Added NEXT_PUBLIC_BASE_PATH environment variable for Supabase Studio
- **caddy**: Configured X-Forwarded-Prefix headers for correct asset URL generation
```

## Tips

1. **Update continuously**: Add to `[Unreleased]` as you merge PRs, don't batch at release time
2. **Link to issues/PRs**: Include `(#42)` references for traceability
3. **Be user-focused**: Write for users, not developers
   - Good: "Added LangGraph agent endpoint for plant image analysis"
   - Bad: "Implemented agent graph with tool nodes and state management"
4. **Note breaking changes**: Clearly mark with `**BREAKING:**`
5. **Skip internal changes**: Don't include CI config tweaks, test refactors, or minor internal changes
6. **Group related changes**: If a feature required changes across multiple commits, summarize as one entry

## Breaking Changes

If a change is breaking, mark it clearly:

```markdown
### Changed

- **BREAKING - langchain**: LangGraph agent endpoint now requires `scanner_id` parameter (previously optional)
  - Migration: Include `scanner_id` in all `/api/agent/analyze` requests
  - Old: `POST /api/agent/analyze { "decimation": 4 }`
  - New: `POST /api/agent/analyze { "scanner_id": 123, "decimation": 4 }`
```

## Release Checklist

Before cutting a release:

- [ ] All changes moved from `[Unreleased]` to versioned section
- [ ] Version number follows SemVer (major.minor.patch)
- [ ] Date is today's date in YYYY-MM-DD format
- [ ] Links at bottom are updated
- [ ] Breaking changes are clearly marked
- [ ] Notable changes are user-friendly and descriptive
- [ ] Package-specific changes are labeled (**langchain**, **bloommcp**, **web**, etc.)

## Semantic Versioning Quick Reference

Given a version number `MAJOR.MINOR.PATCH`:

- **MAJOR**: Breaking changes (1.x.x → 2.0.0)

  - API endpoint signature changes
  - Required new environment variables
  - Database schema breaking changes
  - Removing features

- **MINOR**: New features, backwards-compatible (1.1.x → 1.2.0)

  - New API endpoints
  - New optional parameters
  - New UI features
  - Performance improvements

- **PATCH**: Bug fixes, backwards-compatible (1.1.1 → 1.1.2)
  - Bug fixes
  - Security patches
  - Documentation updates
  - Dependency updates (non-breaking)

## Examples for Bloom

### Version 1.0.0 (Initial Release)

```markdown
## [1.0.0] - 2024-12-20

### Added

- Initial monorepo setup with Turborepo and Docker Compose
- Next.js frontend with Material-UI design system
- LangGraph agent service for AI-powered plant data analysis
- FastMCP server for structured data queries
- Self-hosted Supabase for database, auth, and storage
- MinIO S3-compatible object storage
- Database schema for cylindrical scan data (cyl_scanners, cyl_images tables)
- Development initialization scripts (dev_init.ts, setup-env.sh)
- Docker Compose configurations for dev and prod environments
```

### Version 1.1.0 (New Features)

```markdown
## [1.1.0] - 2025-01-10

### Added

- **langchain**: LangGraph agent endpoint for plant scan analysis
- **langchain**: S3 integration with retry logic for network failures
- **langchain**: JWT authentication middleware for protected endpoints
- **bloommcp**: FastMCP server with plant data query tools
- **web**: Scan data visualization dashboard with real-time updates
- **web**: Image upload interface for scan sequences
- **database**: RLS policies for user-scoped data access

### Changed

- **langchain**: Updated Supabase client to v2.22.2 for improved performance
- **docker**: Optimized Caddy configuration for static asset serving
```

### Version 1.1.1 (Bug Fix)

```markdown
## [1.1.1] - 2025-01-12

### Fixed

- **langchain**: S3 upload retry logic now correctly handles ConnectionError (#43)
- **web**: Supabase dashboard static assets load correctly through Caddy reverse proxy (#40)
- **langchain**: Agent endpoint no longer crashes on empty image sequences (#47)
```

### Version 2.0.0 (Breaking Change)

```markdown
## [2.0.0] - 2025-02-01

### Changed

- **BREAKING - langchain**: LangGraph agent endpoint now requires authentication
  - All `/api/agent/analyze` requests must include valid JWT token
  - Migration: Add `Authorization: Bearer <token>` header to all requests
- **BREAKING - database**: Renamed `cyl_images.s3_path` to `cyl_images.s3_key`
  - Migration: Run migration script `supabase/migrations/20250201_rename_s3_path.sql`

### Added

- **langchain**: Agent progress tracking with real-time updates
- **langchain**: Batch processing for large scan sequences
- **web**: Progress indicators for agent analysis jobs
```

## Maintaining CHANGELOG.md

```bash
# 1. Before each PR merge
# Add your changes to [Unreleased] section

# 2. When preparing a release
# Move [Unreleased] to new version section
# Update version links at bottom

# 3. Commit changelog
git add CHANGELOG.md
git commit -m "docs: update changelog for v1.2.0 release"

# 4. Tag the release
git tag -a v1.2.0 -m "Release version 1.2.0"
git push origin v1.2.0
```