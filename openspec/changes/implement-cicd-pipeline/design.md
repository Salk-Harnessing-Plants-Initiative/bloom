# Design: CI/CD Pipeline Implementation

## Context

The Bloom project is a Turborepo-managed monorepo with:
- **Web application** (Next.js/TypeScript/React)
- **Flask API** (Python)
- **Shared packages** (bloom-js, bloom-fs)
- **Docker Compose deployment** (dev/prod)
- **Git workflow** with main branch

Currently lacks automated quality assurance, leading to:
- Manual testing and deployment
- Inconsistent code style
- No visibility into test coverage
- Slower development cycle
- Higher risk of production bugs

## Goals / Non-Goals

**Goals:**
- Establish comprehensive CI/CD pipeline for all components
- Migrate Python package management to modern uv tooling
- Enforce code quality standards with linting and formatting
- Achieve 70% test coverage across all components
- Automate Docker image builds and deployments
- Provide fast feedback to developers (pre-commit hooks + CI)
- 100% type annotation coverage in Python code with Google-style docstrings

**Non-Goals:**
- Kubernetes deployment (stick with Docker Compose for now)
- Multi-environment deployments beyond dev/prod
- Custom test framework development (use industry-standard tools)
- Coverage requirements >70% initially (can adjust later)
- Migrating from Turborepo to another monorepo tool

## Decisions

### Decision 1: Use uv for Python Package Management

**What:** Replace pip/requirements.txt with uv and pyproject.toml

**Why:**
- **10-100x faster** than pip for dependency resolution and installation
- **Lockfile support** (uv.lock) ensures reproducible builds
- **Modern PEP 621 support** (pyproject.toml) is the Python standard
- **All-in-one tool** replaces pip, pip-tools, virtualenv
- **Compatible** with existing pip workflows (drop-in replacement)
- **Tool configuration centralization** (black, ruff, mypy, pytest all in pyproject.toml)

**Alternatives considered:**
1. **Stay with pip**: Too slow, no lockfile, fragmented tooling
2. **Poetry**: Good but slower than uv, more complex configuration
3. **Pipenv**: Slower than uv, less active development

**Configuration approach:**
- All dependencies in `[project.dependencies]`
- Dev dependencies in `[project.optional-dependencies.dev]`
- Tool configs in `[tool.black]`, `[tool.ruff]`, `[tool.mypy]`, etc.
- Single source of truth for all Python tooling

### Decision 2: Jest + React Testing Library + Playwright for Frontend

**What:** Use Jest for unit/integration tests, Playwright for E2E tests

**Why:**
- **Jest**: De facto standard for React testing, excellent Next.js integration
- **React Testing Library**: Encourages testing best practices (user-centric)
- **Playwright**: Modern E2E framework, better than Cypress for multi-browser testing
- **Coverage built-in**: Jest has excellent coverage reporting
- **Fast**: Can run tests in parallel with good performance

**Alternatives considered:**
1. **Vitest**: Faster but less mature ecosystem for React
2. **Cypress**: Good but Playwright is more modern and flexible
3. **Testing Library alone**: Need test runner (Jest provides this)

### Decision 3: pytest for Backend Testing

**What:** Use pytest with plugins for Flask testing

**Why:**
- **Industry standard**: Most popular Python testing framework
- **Rich plugin ecosystem**: pytest-flask, pytest-cov, pytest-mock, responses
- **Excellent fixtures**: Clean test setup/teardown
- **Coverage integration**: pytest-cov works seamlessly
- **Type annotation compatible**: Works well with mypy

**Plugins selected:**
- `pytest-flask`: Flask-specific test utilities
- `pytest-cov`: Coverage reporting
- `pytest-mock`: Easier mocking with pytest
- `responses`: Mock HTTP requests
- `faker`: Generate test data

### Decision 4: Black + Ruff + Mypy for Python Code Quality

**What:** Use black for formatting, ruff for linting, mypy for type checking

**Why:**
- **Black**: Uncompromising formatter, eliminates style debates, Google docstring compatible
- **Ruff**: 10-100x faster than flake8/pylint, combines multiple tools (isort, flake8, etc.)
- **Mypy**: Industry standard for static type checking
- **All configured in pyproject.toml**: Single configuration file
- **Pre-commit compatible**: Can run in git hooks

**Line length:** 88 characters (black default, widely accepted)

**Mypy strict mode:** Full type annotations required, no implicit Any

### Decision 5: ESLint + Prettier for TypeScript/JavaScript

**What:** Use ESLint for linting, Prettier for formatting

**Why:**
- **Next.js integration**: `eslint-config-next` handles most rules
- **TypeScript support**: `@typescript-eslint` plugins
- **Prettier integration**: `eslint-config-prettier` disables conflicting rules
- **Industry standard**: Used by most TypeScript projects
- **Fast**: Can run in parallel across packages with Turbo

**Configuration:**
- Extend `next/core-web-vitals` for React/Next.js best practices
- Enable TypeScript recommended rules
- Disable rules that conflict with Prettier

### Decision 6: Pre-commit Hooks for Fast Feedback

**What:** Use pre-commit framework to run checks before commits

**Why:**
- **Fast feedback**: Catch issues before they reach CI
- **Automatic formatting**: Can auto-fix many issues
- **Language-agnostic**: Supports Python, JavaScript, YAML, etc.
- **Community hooks**: Large library of pre-built hooks
- **Selective running**: Only runs on changed files by default

**Hooks included:**
- Trailing whitespace, end-of-file-fixer
- YAML/TOML validation
- Large file prevention
- Black, ruff, mypy for Python
- Prettier for JavaScript/TypeScript

### Decision 7: GitHub Actions for CI/CD

**What:** Use GitHub Actions with parallel job execution

**Why:**
- **Native integration**: Built into GitHub, no external service
- **Free tier generous**: 2000 minutes/month for private repos
- **Matrix builds**: Easy parallel execution for packages
- **Caching**: Built-in caching for dependencies and builds
- **Community actions**: Rich marketplace of reusable actions
- **Container support**: Can build and push Docker images

**CI workflow structure:**
1. **Parallel lint jobs**: Frontend and backend lint separately
2. **Parallel test jobs**: Frontend, backend, packages test separately
3. **Build jobs**: Only run if lint/test pass (depends on)
4. **E2E tests**: Run after builds complete
5. **Coverage upload**: Send to Codecov for tracking

**CD workflow triggers:**
- Push to main branch: Build and push `latest` tag
- Git tags (`v*`): Build and push semantic version tags
- Automatic GHCR (GitHub Container Registry) push

### Decision 8: 70% Coverage Threshold

**What:** Require minimum 70% code coverage across all metrics (branches, functions, lines, statements)

**Why:**
- **Industry standard**: 70-80% is typical for production codebases
- **Not too strict**: Allows pragmatic exceptions for hard-to-test code
- **Meaningful**: Forces thinking about test scenarios
- **Achievable**: Can be reached incrementally over 3-4 weeks

**Enforced in:**
- jest.config.js (frontend)
- pyproject.toml [tool.pytest.ini_options] (backend)
- CI workflow (fails if below threshold)
- Codecov PR comments (shows coverage change)

**Exceptions allowed:**
- Test files themselves (excluded from coverage)
- Generated code (if any)
- Trivial getters/setters (can document why excluded)

### Decision 9: Docker Multi-stage Builds with uv

**What:** Use multi-stage Dockerfile with uv for Python builds

**Why:**
- **Faster builds**: uv is 10-100x faster than pip
- **Smaller images**: Can separate dev and prod dependencies
- **Reproducible**: uv.lock ensures exact dependency versions
- **Cacheable**: Docker layers cache uv downloads
- **Official uv image**: `ghcr.io/astral-sh/uv:latest` provides uv binary

**Dockerfile structure:**
```dockerfile
FROM python:3.11-slim as base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
# Install prod dependencies with uv sync --frozen --no-dev
FROM base as dev
# Install all dependencies with uv sync --frozen
```

### Decision 10: Turbo Task Configuration

**What:** Add test, lint, and coverage tasks to turbo.json

**Why:**
- **Parallel execution**: Turbo runs tasks across packages in parallel
- **Caching**: Turbo caches task outputs to skip unchanged work
- **Dependencies**: Can specify task dependencies (build before test)
- **Monorepo efficiency**: Essential for multi-package repos

**Tasks added:**
- `lint`: Run linters (depends on build)
- `lint:fix`: Auto-fix linting issues
- `test`: Run tests (depends on build, no cache)
- `test:coverage`: Run tests with coverage
- `test:watch`: Watch mode for development (no cache, persistent)

## Implementation Phases

Detailed implementation roadmap is in [tasks.md](./tasks.md).

**Summary:**
1. **Phase 1 (Week 1-2)**: Foundation - uv migration, linting setup, pre-commit hooks
2. **Phase 2 (Week 3-4)**: Testing infrastructure setup for all components
3. **Phase 3 (Week 5)**: CI/CD pipeline and Codecov integration
4. **Phase 4 (Week 5)**: Turbo configuration updates
5. **Phase 5 (Week 6)**: Type annotations and documentation
6. **Phase 6 (Week 6-8)**: Test writing to achieve 70% coverage
7. **Phase 7 (Week 9)**: Team training and rollout

**Total timeline**: 9 weeks (~6 developer-weeks of effort)

## Risks / Trade-offs

### Risk: Developer Resistance to uv

**Risk:** Team may resist learning new Python tooling

**Mitigation:**
- uv commands are very similar to pip (low learning curve)
- Provide uv cheatsheet and training session
- Show performance benefits (10-100x faster)
- uv is gaining rapid adoption in Python community

### Risk: Initial Test Writing Slowdown

**Risk:** Writing tests for existing code takes time, may slow feature development

**Mitigation:**
- Phase the rollout (don't require 70% immediately)
- Pair junior developers with seniors for test writing
- Focus on high-value tests (critical paths) first
- Can temporarily lower threshold for legacy code with documentation

### Risk: Pre-commit Hooks Frustrate Developers

**Risk:** Hooks that block commits may frustrate developers

**Mitigation:**
- Hooks mostly auto-fix issues (black, prettier)
- Developers can bypass with `git commit --no-verify` if urgent (document when this is acceptable)
- Provide clear error messages and fixing instructions
- Most issues caught by hooks are trivial (formatting)

### Risk: CI Pipeline Flakiness

**Risk:** E2E tests or integration tests may be flaky, causing false failures

**Mitigation:**
- Start with simple unit tests, add E2E gradually
- Implement retry logic for known-flaky tests
- Monitor test reliability metrics (<1% flaky test rate goal)
- Quarantine flaky tests until fixed

### Risk: Coverage Requirements Too Strict

**Risk:** 70% coverage may be hard to achieve for some modules

**Mitigation:**
- Allow exceptions with documentation (e.g., config files)
- Can adjust threshold per package if needed
- Focus on meaningful tests over coverage numbers
- Legacy code can have lower threshold temporarily

### Trade-off: CI Build Time vs Thoroughness

**Trade-off:** More comprehensive testing increases CI build time

**Current approach:**
- Parallel jobs to maximize throughput
- Caching for dependencies and builds
- Target: <15 minutes for full CI pipeline
- Can optimize further if needed (split E2E to separate workflow)

**Fallback:**
- If CI is too slow, can make E2E tests optional (manual trigger)
- Can reduce Playwright browser matrix (test only Chrome in CI)

## Migration Plan

### Pre-migration

1. **Communicate changes** to team (1 week notice)
2. **Create migration PR** with all infrastructure changes
3. **Test locally** on all developers' machines
4. **Document new workflows** in CONTRIBUTING.md

### Migration steps

1. **Merge infrastructure PR** (Phase 1 completion)
2. **Team training session** (2 hours)
3. **Gradual test coverage increase** (Phases 2-6)
4. **Monitor and adjust** (Phase 7)

### Rollback plan

If CI/CD causes major issues:
1. **Disable pre-commit hooks**: Developers can still commit
2. **Make CI checks non-blocking**: Allow merging despite failures
3. **Revert infrastructure changes**: Git revert to before migration
4. **Address issues**: Fix problems offline, re-roll out

**Likelihood:** Low (established tooling, proven practices)

## Cost-Benefit Analysis

### Costs
- **Initial setup**: 6 developer-weeks (~$12,000-18,000)
- **Infrastructure**: $120/year (Codecov, if private repo)
- **Ongoing maintenance**: 2-4 hours/week (~$5,000/year)
- **Total first year**: ~$17,000-23,000

### Benefits
- **Bug prevention**: Catch bugs in CI before production (~20-30 hours/month saved) = ~$60,000/year
- **Faster reviews**: Automated checks reduce review time by 30% = ~$15,000/year
- **Reduced tech debt**: Consistent code quality = ~$10,000/year
- **Faster dependency management**: uv speed improvements = ~$5,000/year
- **Developer confidence**: Fewer production incidents = priceless

**ROI**: 4-5x in first year

## Success Criteria

Implementation is successful when:

1. **All metrics green**:
   - ✅ Code coverage ≥70% across all components
   - ✅ CI build time <15 minutes
   - ✅ Zero linting errors in main branch
   - ✅ <1% flaky test rate

2. **Process improvements**:
   - ✅ PRs with automated checks get faster reviews
   - ✅ 50% of bugs caught in CI (measured over 3 months)
   - ✅ Deployment frequency increased

3. **Team satisfaction**:
   - ✅ Developer confidence survey shows >80% confidence
   - ✅ No major complaints about CI/CD process
   - ✅ Team actively writes tests for new features

## Open Questions

1. **Codecov pricing**: If repo is private, need to budget for Codecov (~$10/month)
   - Can start with free tier, upgrade if needed
   - Alternative: Use GitHub's built-in code coverage (less feature-rich)

2. **E2E test environment**: Should E2E tests run against real Supabase or mocked?
   - Recommendation: Use Supabase local (supabase-studio container)
   - Avoids external dependencies in CI

3. **Deployment automation**: Should CD automatically deploy to production or just build images?
   - Current proposal: Just build and push images
   - Manual deployment trigger for production (safer)
   - Can add automatic deployment later

4. **Type annotation enforcement**: Should mypy errors block commits or just warn?
   - Recommendation: Block commits (pre-commit + CI)
   - Ensures 100% type coverage
   - Can disable for specific lines with `# type: ignore` if needed

5. **Test data management**: How to handle test fixtures and seed data?
   - Recommendation: Use faker for generated data
   - Store critical fixtures in `tests/fixtures/` directories
   - Document test data strategy in testing guide
