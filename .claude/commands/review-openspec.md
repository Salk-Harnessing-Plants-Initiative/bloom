---
name: Review OpenSpec Proposal
description: Critically review an OpenSpec change proposal with a team of specialized subagents before approval.
category: Development
tags: [openspec, review, subagents, workflow]
---

# OpenSpec Proposal Review — Subagent Team

You are a senior scientific programmer reviewing an OpenSpec proposal for the Bloom monorepo,
a plant phenotyping web platform (Next.js + Supabase + FastAPI/LangGraph + FastMCP + Docker). You value
testing, code quality, reproducibility, data integrity, traceability, scientific accuracy, correctness, and
documentation that is clear, succinct, and DRY.

This skill launches **5 specialized subagents in parallel** to critically review an OpenSpec proposal.
Each subagent has a distinct review lens and is instructed to be **adversarial** — finding gaps, not rubber-stamping.
After all subagents return, you synthesize their findings into a unified review verdict.

**Arguments:** `$ARGUMENTS` (the change-id to review)

## Step 1: Identify the Proposal

Determine which proposal to review:

- If the user specifies a change ID via `$ARGUMENTS`, use it directly
- Otherwise, run `openspec list` to find active proposals and ask the user which one to review
- Read the proposal's `proposal.md`, `tasks.md`, `design.md` (if exists), and all delta spec files under `specs/`

## Step 2: Gather Context

Before launching subagents, collect essential context that each agent will need:

1. Read the full proposal files (proposal.md, tasks.md, design.md, delta specs)
2. Read the CURRENT specs being modified (from `openspec/specs/`)
3. Read `openspec/AGENTS.md` for OpenSpec conventions
4. Read `openspec/project.md` for project conventions
5. Note the affected code files listed in the Impact section (which service: `web/`, `langchain/`, `bloommcp/`, `services/`, etc.)
6. Note any related GitHub issues mentioned

Embed the full proposal text, current spec text, and file lists into each subagent prompt.

## Step 3: Launch Subagent Review Team

Launch ALL 5 subagents **in a single message** (parallel execution). Each subagent gets the full proposal
text embedded in its prompt. Each agent MUST read the actual files it needs — do not rely on summaries.

---

### Subagent 1: Spec Quality & OpenSpec Best Practices

```
subagent_type: "general-purpose"
description: "Review OpenSpec format quality"
```

**Prompt template:**

> You are reviewing an OpenSpec proposal for the Bloom monorepo, a plant phenotyping web platform.
> Your role: **Spec Quality & OpenSpec Best Practices Reviewer**.
>
> IMPORTANT: Be critical. Find problems. Do NOT rubber-stamp.
>
> First, read `openspec/AGENTS.md` to understand the full OpenSpec format rules.
> Then read the proposal files and current specs being modified.
>
> **Format rules to check:**
>
> - Delta sections MUST use: `## ADDED Requirements`, `## MODIFIED Requirements`, `## REMOVED Requirements`
> - Requirements use `### Requirement: Name` (3 hashtags)
> - Scenarios use `#### Scenario: Name` (4 hashtags)
> - Every requirement MUST have at least one scenario
> - Scenarios MUST use **WHEN**/**THEN** format with bold markers
> - MODIFIED requirements MUST include the FULL existing text (partial deltas lose detail at archive)
> - Requirements use SHALL/MUST for normative language
>
> **Proposal rules:**
>
> - `proposal.md` must have: ## Why, ## What Changes, ## Impact
> - ## Why should be 1-2 sentences explaining the problem/opportunity
> - ## Impact must list: affected specs AND affected code files (note which service: web/, langchain/, bloommcp/, services/)
> - BREAKING changes must be marked with **BREAKING**
> - Change ID must be verb-led kebab-case
>
> **Tasks rules:**
>
> - Must follow TDD order: tests FIRST, then implementation, then verification
> - Tasks must be small, verifiable work items (suitable for atomic commits)
> - Each task must have a checkbox `- [ ]`
> - Task groups should map to logical commit boundaries
>
> **Check for:**
>
> 1. Are any scenarios vague or untestable? (e.g., "should work correctly")
> 2. Are WHEN/THEN conditions specific enough to write a test from?
> 3. Do MODIFIED requirements include the FULL original text or just fragments?
> 4. Are there requirements without scenarios?
> 5. Are there missing edge case scenarios? (error paths, boundary values, empty states)
> 6. Does the Impact section list ALL affected specs and code files?
> 7. Could any requirements be split into smaller, more focused requirements?
> 8. Is the change ID appropriate (verb-led, descriptive)?
> 9. Run `openspec validate {CHANGE_ID} --strict` and report the result
>
> **Proposal to review:**
> {PROPOSAL_MD}
>
> **Tasks:**
> {TASKS_MD}
>
> **Delta specs:**
> {DELTA_SPECS}
>
> **Current specs being modified:**
> {CURRENT_SPECS}
>
> Return a structured review with:
> - PASS/FAIL verdict for each check
> - Specific issues found with suggested rewrites
> - Overall quality score (1-10) with justification

---

### Subagent 2: TDD & Testing Strategy

```
subagent_type: "general-purpose"
description: "Review TDD and testing plan"
```

**Prompt template:**

> You are reviewing an OpenSpec proposal's testing strategy for the Bloom monorepo.
> Your role: **TDD & Testing Strategy Reviewer**.
>
> IMPORTANT: Be critical. The test plan must be concrete, complete, and CI-feasible.
>
> **Project testing infrastructure** (READ the actual files — do not assume):
>
> - Bloom is a polyglot monorepo. Determine which service(s) the proposal touches and read their test setup.
> - **Python services** (`langchain/`, `bloommcp/`, `services/*`) use `uv` + pytest. Tests run per-service
>   (`cd <service> && uv run pytest`) or from the repo root (`uv run --extra test pytest`; the root
>   pyproject.toml is `bloom-tests`).
> - **TypeScript/JS** (`web/`) uses the configured JS test tooling — read `package.json` to confirm.
> - **Code quality**: pre-commit runs black + ruff + ruff-format (Python) and prettier (JS/TS/MD), plus gitleaks.
> - Read `.github/workflows/` and `.pre-commit-config.yaml` to learn what CI actually enforces vs. what is local-only.
> - Read any `docs/` testing guide and the relevant `conftest.py` files for fixtures and markers.
>
> **Review the tasks.md for:**
>
> 1. **TDD ordering**: Are tests written BEFORE implementation? The tasks.md should have:
>    - Write failing test → Implement feature → Verify test passes
>    - NOT: Implement feature → Write tests after
> 2. **Test specificity**: Is each test specific enough to implement? Not vague like "verify it works"
> 3. **Correct test framework**: Are the right tools used for the right layer?
>    - Pure functions / config validation → pytest (Python) or the JS unit runner
>    - API routes / MCP tools / LangGraph nodes → integration tests against the service
>    - Infrastructure (CI workflows, Docker, packaging) → what CAN be tested locally vs only in CI?
>    - CLI / entry points → subprocess or isolated-run tests
> 4. **Missing tests**:
>    - Error paths and validation failures
>    - Backward compatibility (old configs, missing fields, schema/migration changes)
>    - Edge cases specific to the changes being made
>    - Regression tests for any bugs being fixed
> 5. **CI feasibility**: Will these tests run in CI?
>    - Do any tests require network access, a real database, or external services?
>    - Are tests cross-platform safe? (path separators, line endings)
>    - Will new CI workflow changes break existing tests?
> 6. **Scenario-to-test mapping**: Do delta spec scenarios map 1:1 to tests in tasks.md?
>    - Every scenario SHOULD have a corresponding test
>    - Flag any scenarios without tests
> 7. **Verification section completeness**: Does the tasks.md verification section include the relevant subset of:
>    - `uv run pytest` (per service) and/or `uv run --extra test pytest` (root) for Python
>    - The JS test/build commands for `web/` changes
>    - `uv run black --check` and `uv run ruff check` (Python formatting/lint)
>    - `uv run pre-commit run --all-files`
>    - Any build/packaging or migration verification the change requires
>
> **Tasks to review:**
> {TASKS_MD}
>
> **Delta specs (scenarios to match against tests):**
> {DELTA_SPECS}
>
> **Proposal summary:**
> {PROPOSAL_MD}
>
> Report:
> - Missing tests (with concrete descriptions of what to add)
> - TDD ordering violations (where implementation comes before tests)
> - Scenarios without corresponding tests (gap analysis)
> - Verification checklist gaps
> - Suggested additional test tasks with exact wording

---

### Subagent 3: CI/CD & Build Infrastructure

```
subagent_type: "general-purpose"
description: "Review CI/CD and build changes"
```

**Prompt template:**

> You are reviewing an OpenSpec proposal for the Bloom monorepo.
> Your role: **CI/CD & Build Infrastructure Reviewer**.
>
> IMPORTANT: Be critical. Read the ACTUAL workflow and config files. Find real problems.
>
> **Current CI/build infrastructure** (READ the actual files — do not assume):
>
> - Read everything under `.github/workflows/` to learn the real jobs, triggers, and matrix.
> - Read `.pre-commit-config.yaml` (black + ruff + ruff-format + prettier + gitleaks).
> - Read the root `pyproject.toml` (`bloom-tests`) and each affected service's `pyproject.toml`.
> - Read `package.json` / Turborepo config for the JS side, and any `Dockerfile` / compose files for services.
> - Confirm what CI ENFORCES vs. what is local-only (e.g., Python lint may be local-only; verify before claiming).
>
> **Review the proposal for:**
>
> 1. **Workflow changes**: Are proposed CI changes correct and minimal?
>    - Do new/changed jobs run in the right service directory?
>    - Are matrix/OS assumptions valid? Are there race conditions or unhandled failure modes?
>    - Is dependency resolution reproducible (e.g., `uv sync --frozen`, `uv.lock` freshness, lockfile drift checks)?
> 2. **Build/publish changes**: If the change touches packaging, images, or deploys, is the flow complete and safe?
>    - Are validation steps present before any publish/deploy?
>    - Are secrets/credentials scoped to the right job with least privilege?
> 3. **GitHub Actions versions**: Are action versions pinned appropriately and reasonably current?
> 4. **Cross-platform safety**: Do workflow scripts work across the CI runners in use? (shell, path separators, line endings)
> 5. **Failure handling**: What happens when each step fails? Is there a sensible stop/retry/rollback path?
> 6. **Migration risk**: Could these changes break the build if merged out of order, or if a workflow run is
>    triggered against the old config before the new one lands?
>
> Read these files (and any others the proposal touches):
> - `.github/workflows/*`
> - `.pre-commit-config.yaml`
> - root `pyproject.toml` and the affected service's `pyproject.toml`
> - `package.json` and any `Dockerfile` / compose files for affected services
>
> **Proposal to review:**
> {PROPOSAL_MD}
>
> **Tasks:**
> {TASKS_MD}
>
> Report:
> - Incorrect assumptions about CI behavior
> - Missing failure handling
> - Security concerns (token/secret exposure, permission scope)
> - Compatibility issues
> - Suggested workflow improvements with concrete YAML

---

### Subagent 4: Documentation Quality (Clear, Succinct, DRY)

```
subagent_type: "general-purpose"
description: "Review documentation impact"
```

**Prompt template:**

> You are reviewing an OpenSpec proposal for the Bloom monorepo.
> Your role: **Documentation Quality Reviewer** — you enforce clear, succinct, DRY documentation.
>
> IMPORTANT: Be critical. Read the ACTUAL documentation files. Find real inconsistencies.
>
> **Documentation to read and check** (READ what exists — do not assume a fixed file list):
>
> - Top-level `README.md` and any per-service `README.md` (`web/`, `langchain/`, `bloommcp/`, `services/*`)
> - Anything under `docs/` relevant to the change (setup, testing, release, architecture)
> - `openspec/project.md` — project conventions
> - Any CHANGELOG present
> - `.claude/commands/*` whose accuracy depends on the proposed change
> - Setup/env docs (e.g., `.env.example`, contributor/onboarding guides)
>
> **Review for:**
>
> 1. **Completeness**: Does the proposal identify ALL documentation that needs updating?
>    - Trace where the changed facts appear (versions, env vars, commands, service layout) and confirm each is covered.
> 2. **DRY violations**: Where is the same information stated in multiple places?
>    - Should any docs be consolidated or cross-referenced instead of duplicated?
> 3. **Accuracy after changes**: Will the proposed changes introduce NEW inconsistencies?
>    - If commands, env vars, or service boundaries change, do all docs that reference them get updated?
> 4. **Succinctness**: Are any docs verbose or redundant? Could sections be removed because the source of truth is elsewhere?
> 5. **CHANGELOG quality** (if a changelog exists): Check for duplicate headers, placeholder dates,
>    license/version mismatches, and entries filed in the wrong section.
>
> **Proposal to review:**
> {PROPOSAL_MD}
>
> **Tasks:**
> {TASKS_MD}
>
> Report:
> - Documentation files the proposal MISSED (needs updating but not listed)
> - DRY violations that should be addressed
> - Inaccuracies that will be introduced by the proposed changes
> - Suggested fixes with concrete rewrites

---

### Subagent 5: Git Workflow & Commit Strategy

```
subagent_type: "general-purpose"
description: "Review git workflow plan"
```

**Prompt template:**

> You are reviewing an OpenSpec proposal for the Bloom monorepo.
> Your role: **Git Workflow & Commit Strategy Reviewer**.
>
> IMPORTANT: Be critical. Commits should be small, focused, and CI-safe.
>
> **Project git conventions** (READ the actual history — run `git log --oneline -20`):
>
> - Confirm the commit message style in use (e.g., conventional prefixes: `chore:`, `fix:`, `feat:`, `docs:`).
> - Confirm the integration branch model (this repo is staging-first: feature PRs target `staging`).
> - Confirm what CI runs on PRs and which paths trigger it.
> - OpenSpec changes are archived after merge.
>
> **Review the tasks.md for commit strategy:**
>
> 1. **Atomic commits**: Can each task group be committed independently with CI staying green after each?
>    - Bad: one giant commit touching multiple services + workflows + docs at once
>    - Good: focused commits per concern (one service / one workflow / docs)
> 2. **Commit ordering**: Are there dependencies between tasks that dictate ordering?
> 3. **CI safety**: Will CI stay green between commits? Identify any intermediate state that would fail.
> 4. **Suggested commit plan**: Propose a sequence of small commits with:
>    - Clear conventional commit messages
>    - Files affected per commit
>    - CI state after each commit (green/yellow/red)
>    - Dependencies noted
> 5. **PR strategy**: Single PR or multiple? If single, is it reviewable? If multiple, what's the merge order?
>    Confirm the PR targets `staging` (not `main`).
> 6. **Risk mitigation**: What if a workflow change breaks the build? Is there a rollback plan per commit?
>
> **Tasks to review:**
> {TASKS_MD}
>
> **Proposal summary:**
> {PROPOSAL_MD}
>
> **Recent commit style** (run `git log --oneline -20`):
> Check the repo for actual commit message conventions.
>
> Report:
> - Tasks that are too large for a single commit
> - Ordering dependencies the proposal missed
> - CI breakage risks at each step
> - Concrete commit plan with messages and file lists
> - PR strategy recommendation

---

## Step 4: Synthesize Review

After ALL subagents return, synthesize their findings:

1. **Deduplicate**: Merge overlapping findings from multiple reviewers
2. **Prioritize**: Categorize issues as:
   - **BLOCKING** — Must fix before approval (spec errors, missing tests, data integrity risks, CI breakage)
   - **IMPORTANT** — Should fix before implementation (missing edge cases, unclear scenarios, doc gaps)
   - **SUGGESTION** — Nice to have (style improvements, additional context)
3. **Create a unified review** with this structure:

```markdown
# OpenSpec Review: {change-id}

## Verdict: APPROVED / NEEDS REVISION / BLOCKED

## Summary
[2-3 sentence overall assessment]

## Blocking Issues
[Issues that MUST be resolved before approval]

## Important Issues
[Issues that SHOULD be resolved before implementation]

## Suggestions
[Optional improvements]

## Proposed Commit Plan
1. `type: message` — [files affected, CI state after]
2. `type: message` — [files affected, CI state after]
...

## TDD Plan
For each testable change:
1. Test to write first → expected failure → implementation to pass it

## Risk Assessment
- CI breakage risk: LOW/MEDIUM/HIGH — [explanation]
- Regression risk: LOW/MEDIUM/HIGH — [explanation]
- Documentation drift risk: LOW/MEDIUM/HIGH — [explanation]

## Review Details by Agent
### 1. Spec Quality
### 2. TDD & Testing
### 3. CI/CD & Build
### 4. Documentation
### 5. Git Workflow
```

## Step 5: Present and Iterate

Present the synthesized review and ask:

1. Do you want to address blocking issues now (update proposal, tasks, and specs)?
2. Do you want to approve with important issues noted as additional tasks?
3. Do you want to revise the proposal first?

If revising, update `proposal.md`, `tasks.md`, and delta specs based on the agreed-upon changes.
Run `openspec validate {change-id} --strict` after any updates.

## Integration

- Run `/new-feature` for the end-to-end feature workflow that invokes this review
- Run `/tdd` when implementing the approved change
- Run `/openspec:apply` to implement, `/openspec:archive` after merge
