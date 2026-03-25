# PR #24 Pre-Merge Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 high-impact bugs/security issues identified in PR #24 review before merging setup-prep-v2 → dev → main.

**Architecture:** All fixes are surgical, 1-5 line changes in existing files. No new files except one Supabase migration. Each task is independent and can be committed separately.

**Tech Stack:** Python (FastAPI, FastMCP), TypeScript (Next.js/React), SQL (Supabase/PostgreSQL)

---

### Task 1: Fix bad import in cluster_visualization.py

**Files:**
- Modify: `bloommcp/source/cluster_visualization.py:51`

- [ ] **Step 1: Fix the import**

Change line 51 from:
```python
        from sleap_roots_analyze.pca import perform_pca_analysis
```
to:
```python
        from .pca import perform_pca_analysis
```

This is a lazy import inside `create_cluster_scatter_pca()`. The module `sleap_roots_analyze` doesn't exist — it's a leftover from before the code was moved into bloommcp. The correct relative import is `.pca` since `pca.py` lives in the same `bloommcp/source/` directory.

- [ ] **Step 2: Verify the import target exists**

Run: `ls bloommcp/source/pca.py`
Expected: file exists

- [ ] **Step 3: Commit**

```bash
git add bloommcp/source/cluster_visualization.py
git commit -m "fix: relative import in cluster_visualization.py

from sleap_roots_analyze.pca → from .pca — the old package name
doesn't exist, causing runtime crash when PCA scatter is requested."
```

---

### Task 2: Timing-safe API key comparison in bloommcp

**Files:**
- Modify: `bloommcp/server.py:41` (add `import hmac` at top)

- [ ] **Step 1: Add hmac import**

At the top of `bloommcp/server.py`, add `import hmac` after `import os` (line 15):
```python
import hmac
import os
```

- [ ] **Step 2: Fix the comparison**

Change line 41 from:
```python
            if token == API_KEY:
```
to:
```python
            if hmac.compare_digest(token, API_KEY):
```

`hmac.compare_digest()` runs in constant time, preventing timing attacks that can brute-force the API key character by character.

- [ ] **Step 3: Commit**

```bash
git add bloommcp/server.py
git commit -m "security: use hmac.compare_digest for API key comparison

String == leaks key length/content via timing side-channel."
```

---

### Task 3: Restrict CORS origins in langchain server

**Files:**
- Modify: `langchain/server.py:103`

- [ ] **Step 1: Replace wildcard with env-configurable origins**

Change lines 101-107 from:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
to:
```python
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

This defaults to `http://localhost:3000` for dev and can be set to the production frontend URL via env var. `allow_origins=["*"]` with `allow_credentials=True` is invalid per the CORS spec — browsers reject it.

- [ ] **Step 2: Add CORS_ORIGINS to dev and prod compose files**

In `docker-compose.dev.yml`, add to langchain-agent environment:
```yaml
CORS_ORIGINS: "http://localhost:3000"
```

In `docker-compose.prod.yml`, add to langchain-agent environment:
```yaml
CORS_ORIGINS: "${CORS_ORIGINS}"
```

- [ ] **Step 3: Commit**

```bash
git add langchain/server.py docker-compose.dev.yml docker-compose.prod.yml
git commit -m "security: replace CORS wildcard with configurable origins

allow_origins=['*'] with allow_credentials=True is invalid per CORS spec.
Now defaults to localhost:3000, configurable via CORS_ORIGINS env var."
```

---

### Task 4: Fix response body double-consumption in chat UI

**Files:**
- Modify: `web/components/mcp-chat-client.tsx:337-351`

- [ ] **Step 1: Fix the response parsing**

Change lines 337-351 from:
```typescript
        if (!resp.ok) {
          const errBody = await resp.text();
          throw new Error(`Agent request failed ${resp.status}: ${errBody}`);
        }
        let data: any = null;
        let rawBody: string | null = null;
        try {
          data = await resp.json();
        } catch (jsonErr) {
          try {
            rawBody = await resp.text();
          } catch (e) {
            rawBody = String(jsonErr ?? e ?? "<no body>");
          }
        }
```
to:
```typescript
        const rawBody = await resp.text();
        if (!resp.ok) {
          throw new Error(`Agent request failed ${resp.status}: ${rawBody}`);
        }
        let data: any = null;
        try {
          data = JSON.parse(rawBody);
        } catch {
          // rawBody already captured above
        }
```

The problem: `Response.text()` and `Response.json()` both consume the body stream. Calling `resp.json()` then `resp.text()` fails silently because the stream is already consumed. Fix: read body as text once, then parse with `JSON.parse()`.

- [ ] **Step 2: Verify no other references to rawBody are broken**

Search for `rawBody` usage after this block — it's used around line 355-360 to construct the bot message. The variable name is preserved so downstream code is unaffected.

- [ ] **Step 3: Commit**

```bash
git add web/components/mcp-chat-client.tsx
git commit -m "fix: response body double-consumption in chat client

Read body as text once, then JSON.parse(). Calling resp.json() then
resp.text() fails silently because the stream is already consumed."
```

---

### Task 5: Update RLS policy for soft-delete filtering

**Files:**
- Create: `supabase/migrations/20260318110000_fix_rls_soft_delete.sql`

- [ ] **Step 1: Create the migration**

```sql
-- Fix RLS policies to filter out soft-deleted threads.
-- Without this, soft-deleted threads are still visible via PostgREST/Supabase client.

-- Drop and recreate SELECT policy with deleted_at filter
DROP POLICY IF EXISTS "Users can view own chat threads" ON public.chat_threads;
CREATE POLICY "Users can view own chat threads"
    ON public.chat_threads
    FOR SELECT
    USING (auth.uid() = user_id AND deleted_at IS NULL);

-- Update UPDATE policy to prevent modifying soft-deleted threads
DROP POLICY IF EXISTS "Users can update own chat threads" ON public.chat_threads;
CREATE POLICY "Users can update own chat threads"
    ON public.chat_threads
    FOR UPDATE
    USING (auth.uid() = user_id AND deleted_at IS NULL);

-- Update DELETE policy to only allow deleting non-deleted threads
DROP POLICY IF EXISTS "Users can delete own chat threads" ON public.chat_threads;
CREATE POLICY "Users can delete own chat threads"
    ON public.chat_threads
    FOR DELETE
    USING (auth.uid() = user_id AND deleted_at IS NULL);

-- Replace composite index with partial index for better performance
DROP INDEX IF EXISTS idx_chat_threads_user_deleted;
CREATE INDEX IF NOT EXISTS idx_chat_threads_active_user
    ON public.chat_threads(user_id) WHERE deleted_at IS NULL;
```

- [ ] **Step 2: Commit**

```bash
git add supabase/migrations/20260318110000_fix_rls_soft_delete.sql
git commit -m "fix: RLS policies exclude soft-deleted chat threads

SELECT and DELETE policies now filter deleted_at IS NULL.
Replace composite index with more efficient partial index."
```

---

### Task 6: Fix file handle leak in experiment_utils.py

**Files:**
- Modify: `bloommcp/source/experiment_utils.py:85`

- [ ] **Step 1: Fix the open() call**

Change line 85 from:
```python
            row_count = sum(1 for _ in open(csv_path)) - 1  # fast line count
```
to:
```python
            with open(csv_path) as f:
                row_count = sum(1 for _ in f) - 1  # fast line count
```

The original code opens a file handle in a generator expression that is never explicitly closed. Under load (scanning many CSVs), this leaks file descriptors.

- [ ] **Step 2: Commit**

```bash
git add bloommcp/source/experiment_utils.py
git commit -m "fix: close file handle in experiment_utils.py

open() in generator expression leaked file descriptors.
Use context manager to ensure cleanup."
```

---

### Task 7: Merge flow

- [ ] **Step 1: Push all fixes to setup-prep-v2**

```bash
git push origin setup-prep-v2
```

- [ ] **Step 2: Merge setup-prep-v2 → dev**

Via GitHub PR or direct merge, depending on team workflow.

- [ ] **Step 3: Merge dev → main**

After verifying dev is stable.
