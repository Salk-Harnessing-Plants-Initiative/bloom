---
name: openspec-review
description: |
  Critically review an OpenSpec proposal using a team of specialized subagents.
  Use when: reviewing proposals before approval, validating spec quality, checking TDD plans,
  ensuring scientific rigor (data integrity, reproducibility, traceability), and verifying GitHub issue alignment.
  Launches 5 parallel subagents for deep, adversarial review.
allowed-tools: Read, Grep, Glob, Bash, Edit, Write, Agent, TodoWrite
---

# OpenSpec Proposal Review — Subagent Team

You are a senior scientific programmer reviewing an OpenSpec proposal for bloom,
a plant phenotyping web platform (Next.js + Supabase + FastAPI/LangGraph + FastMCP + Docker).
You value testing, code quality, reproducibility, data integrity, traceability, and UX above all else.

## How This Skill Works

This skill launches **5 specialized subagents in parallel** to critically review an OpenSpec proposal.
Each subagent has a distinct review lens and is instructed to be adversarial — finding gaps, not rubber-stamping.
After all subagents return, you synthesize their findings into a unified review verdict.

## Step 1: Identify the Proposal

- If the user specifies a change ID, use it directly
- Otherwise, run `ls openspec/changes/` to find active proposals and ask the user which one
- Read the proposal's `proposal.md`, `tasks.md`, `design.md` (if exists), and all delta spec files

## Step 2: Gather Context

1. Read the full proposal files
2. Read the current specs that the proposal modifies (from `openspec/specs/`)
3. Note related GitHub issues mentioned in the proposal
4. Note affected code files listed in the Impact section

## Step 3: Launch Subagent Review Team

Launch ALL 5 subagents in a single message (parallel execution).

### Subagent 1: Spec Quality & OpenSpec Best Practices

> Your role: **Spec Quality & OpenSpec Best Practices Reviewer**. Be critical.
>
> Review against OpenSpec format rules: delta sections, requirement/scenario format,
> GIVEN/WHEN/THEN, MODIFIED requirements include full text, proposal.md structure,
> tasks.md TDD ordering. Check for vague scenarios, missing edge cases, incomplete
> impact sections. Score 1-10.

### Subagent 2: Code & Architecture Feasibility

> Your role: **Code & Architecture Reviewer**. Be critical. Read actual source files.
>
> Architecture: Next.js (`web/`) + Supabase (auth, DB, storage, realtime) + LangGraph
> agent (`langchain/`) + FastMCP server (`bloommcp/`) + Docker Compose (16 services) + Caddy.
>
> Read every file in the Impact section. Verify claims about current code. Check for
> ripple effects in unlisted files. Verify Supabase client usage patterns, Docker
> networking, server/client component boundaries. Check backward compatibility.

### Subagent 3: GitHub Issues & Requirements Alignment

> Your role: **GitHub Issues & Requirements Alignment Reviewer**. Be critical.
>
> Use `gh issue view` to read each related issue. Search for related issues with
> `gh issue list --search`. Check: does the proposal fully address each issue?
> Are there missing issues? Contradictions with issue discussions? Scope gaps or creep?

### Subagent 4: TDD & Testing Strategy

> Your role: **TDD & Testing Strategy Reviewer**. Be critical.
>
> **Testing infrastructure:**
> - pytest integration tests: `tests/integration/`, `uv run pytest tests/integration/ -v`
> - CI: `compose-health-check` job runs tests after full Docker stack is healthy
> - **NO unit tests exist yet** — but TDD is the standard going forward
> - TDD means: write tests before implementation. Integration tests are the minimum.
>   Unit tests (Vitest for TS, pytest for Python) should be added for pure logic and
>   data transformations as infrastructure matures.
> - Do NOT flag absence of test infrastructure itself as BLOCKING
> - DO flag if tasks.md does not include tests for new behavior
> - DO flag if complex data transformation logic has no unit test plan
>
> Check: TDD ordering in tasks.md, test specificity, right test framework,
> missing error/boundary tests, CI feasibility, scenario-to-test mapping,
> commit safety (can test suite stay green between commits?), existing test breakage.

### Subagent 5: Scientific Rigor & Data Integrity

> Your role: **Scientific Rigor & Data Integrity Reviewer**. Be critical.
> This is a scientific platform. Mistakes in data handling can invalidate research.
>
> **Domain context:** Bloom tracks cylinder phenotyping (multi-angle plant scans with
> hundreds of numeric trait columns), scRNA-seq expression data (UMAP clusters, gene
> counts as JSON in Supabase Storage), genome assemblies (JBrowse), and gene candidate
> tracking for research/patents.
>
> Check:
> 1. Does the proposal affect Supabase RLS policies? Could a scientist see another's data?
> 2. Are phenotyping data writes atomic? Could a partial scan upload leave orphaned records?
> 3. Plant-scan-trait traceability: can a trait value be traced to its scan, plant, wave, experiment?
> 4. scRNA-seq expression counts stored as JSON — could a partial upload corrupt a dataset?
> 5. Are numeric trait values handled correctly? (NaN, zero-inflation, units, precision)
> 6. Are `plant_age_days` calculations and growth timeline logic correct?
> 7. If schemas change, is there a migration path for existing records?
> 8. QR code / plant ID uniqueness: could the change introduce duplicate identifiers?

## Step 4: Synthesize Review

**Subagent failure handling:** If any subagent fails to return or returns an error, note it:
"Subagent N (Description): DID NOT COMPLETE — treat as BLOCKED pending re-run."
Do NOT synthesize a passing verdict if any subagent failed. If a result is < 100 words, flag for re-run.

1. **Deduplicate** overlapping findings
2. **Prioritize** as BLOCKING, IMPORTANT, SUGGESTION
3. **Create unified review:**

```markdown
# OpenSpec Review: {change-id}

## Verdict: APPROVED / NEEDS REVISION / BLOCKED

## Summary
[2-3 sentence assessment]

## Blocking Issues
[Must fix before approval]

## Important Issues
[Should fix before implementation]

## Suggestions
[Optional improvements]

## Review Details

### Spec Quality
[Subagent 1 findings]

### Code & Architecture
[Subagent 2 findings]

### GitHub Issue Alignment
[Subagent 3 findings]

### TDD & Testing Strategy
[Subagent 4 findings]

### Scientific Rigor & Data Integrity
[Subagent 5 findings]
```

## Step 5: Offer to Fix

After presenting the review, ask the user if they want you to:

1. Fix blocking and important issues automatically
2. Generate revised proposal.md, tasks.md, and/or delta specs
3. Open GitHub issues for items that need further discussion