/**
 * Vitest setup — `process.env` snapshot/restore between tests.
 *
 * The runtime-config tests (lib/config/public-config.test.ts,
 * validate-on-boot.test.ts, etc.) mutate `process.env.NEXT_PUBLIC_*`,
 * `SUPABASE_URL`, `SUPABASE_URL_HOSTS_ALLOWED`, and `NODE_ENV` to exercise
 * the deferred-read semantics of `getPublicConfig()` and the boot-fence
 * branches of `validateOnBoot()`. Without this snapshot/restore, those
 * mutations leak between tests within the same worker process — flaky
 * tests, especially the "all env vars unset → undefined" cases.
 *
 * Why per-process AND per-test isolation:
 *   - vitest.config.ts sets `pool: 'forks'` which gives us a fresh process
 *     per test FILE (so env state can't leak across files).
 *   - This `beforeEach` snapshot adds per-TEST isolation within a file so
 *     ordering doesn't matter.
 *
 * Restore semantics: any key that exists now but did not exist in the
 * snapshot is deleted; any key in the snapshot is restored to its original
 * value (or deleted if it was undefined). This handles both additions and
 * mutations symmetrically.
 *
 * Owned by openspec/changes/add-ghcr-image-publishing tasks.md §1.3.
 */

import { afterEach, beforeEach } from "vitest";

let snapshot: Record<string, string | undefined>;

beforeEach(() => {
  snapshot = { ...process.env } as Record<string, string | undefined>;
});

afterEach(() => {
  for (const key of Object.keys(process.env)) {
    if (!(key in snapshot)) {
      delete process.env[key];
    }
  }
  for (const [key, value] of Object.entries(snapshot)) {
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }
});
