---
name: Review Pull Request
description: 5-subagent parallel PR review for code quality, testing, scientific rigor, security, and behavioural correctness
category: Git Workflow
tags: [pr, review, github, subagent]
---

# PR Code Review — Subagent Team

You are a senior scientific programmer reviewing a pull request for bloom
(Next.js + Supabase + FastAPI/LangGraph + FastMCP + Docker), a plant phenotyping
web platform used in research environments. You value testing, code quality,
reproducibility, data integrity, and UX above all else.

## How This Skill Works

This skill launches **5 specialized subagents in parallel** to critically review the PR.
Each subagent has a distinct review lens and is instructed to be adversarial — finding
gaps, not rubber-stamping. After all subagents return, synthesize findings into a unified
review and post it to GitHub.

## Step 1: Gather PR Context

Run the following in parallel to collect everything the subagents need:

```bash
# Get PR metadata
gh pr view $PR_NUMBER --json title,body,baseRefName,headRefName,author,labels,files

# Get the full diff
gh pr diff $PR_NUMBER

# Get CI status
gh pr checks $PR_NUMBER

# Get any existing Copilot review comments
gh api graphql -f query='
query {
  repository(owner: "Salk-Harnessing-Plants-Initiative", name: "bloom") {
    pullRequest(number: '$PR_NUMBER') {
      reviews(first: 10) {
        nodes {
          author { login }
          comments(first: 50) {
            nodes { path line body }
          }
        }
      }
    }
  }
}
' --jq '.data.repository.pullRequest.reviews.nodes[] | select(.author.login | contains("opilot")) | .comments.nodes[] | "File: \(.path):\(.line)\n\(.body)"'
```

Also read any OpenSpec proposal linked in the PR body (look for `openspec/changes/` paths).

## Step 2: Launch Subagent Review Team

Launch ALL 5 subagents in a single message (parallel execution). Embed the full diff,
PR description, CI status, and Copilot comments in each prompt.

---

### Subagent 1: Code Quality & Architecture

```
subagent_type: "general-purpose"
description: "Review code quality and architecture"
```

**Prompt:**

> You are reviewing a pull request for bloom, a plant phenotyping web platform.
> Your role: **Code Quality & Architecture Reviewer**.
> Be adversarial. Read actual source files. Find real problems, not hypotheticals.
>
> Architecture overview:
>
> - Next.js app (`web/`) — React 19, server/client components, Supabase SSR
> - Supabase — auth (GoTrue), database (PostgreSQL 15), storage, realtime
> - LangGraph agent (`langchain/`) — FastAPI + Uvicorn, LangChain tools
> - FastMCP server (`bloommcp/`) — data analysis, pandas/numpy/scipy
> - Docker Compose — 16 services, Caddy reverse proxy with auto-HTTPS
> - Shared packages: `packages/bloom-js`, `packages/bloom-fs`, `packages/bloom-nextjs-auth`
>
> **Check:**
>
> 1. Naming: camelCase TS, snake_case Python, kebab-case filenames — any violations?
> 2. Magic numbers/strings — are constants named and co-located?
> 3. TypeScript: any `any` types? Are API responses fully typed?
> 4. Server vs client component boundaries — does client code access server-only APIs?
> 5. Supabase client usage — SSR pattern where appropriate? RLS-aware queries?
> 6. Error handling — are errors surfaced to the user or silently swallowed?
> 7. Are there ripple effects in files NOT changed by the PR? (read them)
> 8. Does the PR introduce dead code, unreachable branches, or stale comments?
> 9. Are Docker networking changes consistent across dev/prod compose files?
> 10. Are there any `eslint-disable` comments added? Are they justified?
>
> Return: BLOCKING, IMPORTANT, SUGGESTIONS, and overall score 1-10.

---

### Subagent 2: Testing Strategy

```
subagent_type: "general-purpose"
description: "Review testing strategy"
```

**Prompt:**

> You are reviewing a pull request for bloom.
> Your role: **Testing Strategy Reviewer**.
> Be adversarial. Check every claim.
>
> **Testing infrastructure:**
>
> - **pytest** integration tests: `tests/integration/`, `uv run --with pytest pytest tests/integration/ -v`
> - **CI**: `compose-health-check` job runs tests after full Docker stack is healthy
> - **NO unit tests exist yet** — but TDD is the standard going forward
> - **NO coverage thresholds enforced** in CI yet
>
> **TDD expectations:** New features SHOULD follow TDD (write tests before implementation).
> Integration tests are the minimum. Unit tests (Vitest for TS, pytest for Python) should
> be added for pure logic, data transformations, and utilities. Do NOT flag the absence of
> the test infrastructure itself as BLOCKING, but DO flag if the PR adds complex logic
> without any tests.
>
> **Check:**
>
> 1. Were tests written before implementation (TDD)? Evidence: test files in earlier commits?
> 2. Does new behavior have test coverage (integration or unit)?
> 3. Are tests specific enough? ("returns 200 with valid data" not "works correctly")
> 4. Missing tests — error paths, boundary values, concurrent access, NaN/null data?
> 5. Will tests pass in CI? (integration tests require full Docker stack)
> 6. Do existing tests break due to the PR? (read `tests/integration/`)
> 7. For data transformation logic (trait calculations, expression counts): is there a
>    unit test verifying correctness, or is it only tested end-to-end?
> 8. Is there a 1:1 mapping between spec scenarios and tests?
>
> Return: BLOCKING, IMPORTANT, SUGGESTIONS.

---

### Subagent 3: Scientific Rigor, Data Integrity & UX

```
subagent_type: "general-purpose"
description: "Review scientific rigor, data integrity, and UX"
```

**Prompt:**

> You are reviewing a pull request for bloom, a scientific web platform for plant phenotyping.
> Your role: **Scientific Rigor, Data Integrity & UX Reviewer**.
> Be adversarial. Mistakes in data handling can invalidate research.
>
> **Domain context:** Bloom tracks cylinder phenotyping (multi-angle plant scans with
> hundreds of numeric trait columns per scan), scRNA-seq expression data (UMAP clusters,
> gene counts stored as JSON in Supabase Storage), genome assemblies (JBrowse), and
> gene candidate tracking for research/patents.
>
> **Check:**
>
> 1. Does the PR affect Supabase RLS policies? Could a user see another scientist's data?
> 2. Are phenotyping data writes atomic? Can a partial scan upload leave orphaned records
>    in `cyl_scans`/`cyl_images`/`cyl_scan_traits`?
> 3. Plant-scan-trait traceability: can a trait value be traced back to its scan, plant,
>    wave, and experiment? Are foreign keys maintained?
> 4. scRNA-seq expression counts are stored as JSON in Supabase Storage — could a partial
>    upload or failed request corrupt a dataset's count files?
> 5. Are numeric trait values handled correctly? (NaN, zero-inflation, units, precision)
> 6. Are destructive actions (delete experiment, remove plant, clear QC labels) guarded?
> 7. Are `plant_age_days` calculations and growth timeline logic correct?
> 8. If the PR changes data schemas, is there a migration path for existing records?
> 9. Are error messages meaningful to scientists (not just developers)?
> 10. QR code / plant ID uniqueness: could the PR introduce duplicate identifiers?
>
> Return: BLOCKING, IMPORTANT, SUGGESTIONS.

---

### Subagent 4: Security

```
subagent_type: "general-purpose"
description: "Review security"
```

**Prompt:**

> You are reviewing a pull request for bloom.
> Your role: **Security Reviewer**.
> Be adversarial. Check every input, every policy, every container config.
>
> **Check:**
>
> 1. Supabase RLS: Are new/modified tables protected? Do policies enforce user scoping?
> 2. JWT validation: Are protected API routes validating tokens?
> 3. Docker security: `no-new-privileges`, `cap_drop: ALL`, `read_only` maintained?
> 4. Caddy TLS: Any changes to Caddyfile that weaken HTTPS?
> 5. API input validation: Are user inputs sanitized before database queries?
> 6. MinIO presigned URLs: Are they scoped correctly? Do they expire?
> 7. Are secrets or credentials logged or exposed in API responses?
> 8. Are there any new `shell.exec()` or subprocess calls with user input?
>
> Return: BLOCKING, IMPORTANT, SUGGESTIONS.

---

### Subagent 5: Behavioural Correctness & Edge Cases

```
subagent_type: "general-purpose"
description: "Review behavioural correctness and edge cases"
```

**Prompt:**

> You are reviewing a pull request for bloom.
> Your role: **Behavioural Correctness & Edge Case Reviewer**.
> Be adversarial. Play adversarial user. Try to break the feature.
>
> **Check:**
>
> 1. Read the PR description's stated behaviour. Does the code actually implement it?
> 2. Trace the full call chain: Next.js → Supabase → FastAPI → response → render
> 3. What happens if:
>    - The feature is triggered by multiple concurrent users?
>    - A Supabase realtime subscription delivers stale data during an update?
>    - The LangGraph agent returns an error or streams a partial response?
>    - A file upload is interrupted midway?
>    - A Docker container restarts during an operation?
> 4. Are cleanup functions (useEffect returns, abort controllers) correct?
> 5. Are there state machine violations — impossible states reachable?
> 6. Does the Copilot review raise any issues not yet addressed?
>
> Return: BLOCKING, IMPORTANT, SUGGESTIONS.

---

## Step 3: Synthesize and Post Review

After ALL subagents return:

**Subagent failure handling:** If any subagent fails to return or returns an error, note it
explicitly: "Subagent N (Description): DID NOT COMPLETE — treat as BLOCKED pending re-run."
Do NOT synthesize a passing verdict if any subagent failed. If a subagent result is
suspiciously short (< 100 words), flag it for re-run.

1. **Deduplicate** overlapping findings
2. **Prioritize**:
   - **BLOCKING** — must fix before merge
   - **IMPORTANT** — should fix before merge
   - **SUGGESTION** — optional improvements
3. **Pre-APPROVE check**: Before posting APPROVE, verify:
   - At least one subagent checked Supabase RLS for any schema changes
   - No subagent result was empty or suspiciously short
   - All 5 subagents returned successfully
4. **Determine verdict**:
   - `APPROVE` — no blocking issues
   - `COMMENT` — no blocking issues but important items
   - `REQUEST_CHANGES` — any blocking issues

5. **Post the review to GitHub**:

> **Note:** GitHub does not allow requesting changes or approving your own PRs.
> Always attempt the desired action first; if it fails, fall back to `--comment`.

```bash
BODY="$(cat <<'EOF'
## Review Summary

[2-3 sentence overall assessment]

## Blocking Issues

[Must fix before merge]

## Important Issues

[Should fix before merge]

## Suggestions

[Optional improvements]

---
*Review by Claude Code subagent team (Code Quality · Testing · Scientific Rigor · Security · Behavioural Correctness)*
EOF
)"

gh pr review $PR_NUMBER --request-changes -b "$BODY" 2>&1 || \
gh pr review $PR_NUMBER --comment -b "$(printf '> **Verdict: REQUEST_CHANGES** (posted as comment — GitHub does not allow requesting changes on your own PR)\n\n%s' "$BODY")"
```

6. After posting, show the user the full synthesized review and the GitHub link.