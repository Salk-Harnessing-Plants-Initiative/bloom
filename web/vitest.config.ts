import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

/**
 * Vitest config for the web/ workspace.
 *
 * - environment: node — these are pure data-shaping / utility tests; nothing
 *   needs jsdom by default. Per-file `// @vitest-environment jsdom` directive
 *   overrides this for the route-handler and React-component tests added by
 *   openspec/changes/add-ghcr-image-publishing (e.g. app/api/config/route.test.ts
 *   and lib/config/use-public-config.test.tsx) without flipping the workspace
 *   default.
 * - tsconfigPaths plugin resolves `@/…` imports from web/tsconfig.json so test
 *   files can use the same path aliases as the rest of the codebase.
 * - include defaults to colocated `*.test.ts` / `*.test.tsx` files under
 *   lib/, components/, app/, and the workspace root (instrumentation.test.ts).
 *   E2E tests in e2e/ stay with Playwright.
 * - setupFiles wires the per-TEST `process.env` snapshot/restore helper so
 *   the runtime-config tests can mutate env vars without leaking across
 *   each other within a worker. Required because vitest's `pool: 'forks'`
 *   isolates workers (each worker is a child process), but does NOT
 *   isolate individual test files OR tests within a worker — multiple
 *   files routed to the same worker run sequentially and share state.
 *   The setup file's beforeEach/afterEach therefore handles per-test
 *   isolation; `pool: 'forks'` handles per-worker (cross-file) isolation.
 * - pool: 'forks' — per-worker process isolation via child processes
 *   (not the default `threads` pool which shares state via worker_threads).
 *   Existing colocated tests (lib/queries/*.test.ts,
 *   components/.../format-times.test.ts) don't mutate process.env, so
 *   the switch is safe and the new openspec tests need the cross-file
 *   isolation. See openspec/changes/add-ghcr-image-publishing
 *   tasks.md §1.2.
 * - exclude adds lib/**\/__fixtures__/** so helper modules like
 *   lib/config/__fixtures__/jwt.ts aren't auto-discovered as tests.
 */
// tsconfigPaths()'s vite Plugin return type doesn't exactly match the slot
// expected by defineConfig() across vite/vitest version pairings — the cast
// is only a TS-stub fix; runtime is correct (tests run + pass).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const plugins = [tsconfigPaths()] as any;

export default defineConfig({
  plugins,
  // web/ pins react exactly while the workspace root hoists a newer patch, so
  // two copies exist. @testing-library/react installs at the root and binds to
  // the root copy; a component importing web/'s copy would then render against
  // a different react than the renderer, and every hook reads a null
  // dispatcher. Point both at the root copy so there is one react in a test
  // run. (Tests therefore run a patch ahead of what Next builds — the fix that
  // removes the split is relaxing web/'s exact pin, which is an app-dependency
  // change, not a test one.)
  resolve: {
    dedupe: ["react", "react-dom"],
    alias: {
      react: fileURLToPath(new URL("../node_modules/react", import.meta.url)),
      "react-dom": fileURLToPath(
        new URL("../node_modules/react-dom", import.meta.url)
      ),
    },
  },
  test: {
    environment: "node",
    pool: "forks",
    setupFiles: ["./vitest.setup.ts"],
    include: [
      "lib/**/*.test.ts",
      "lib/**/*.test.tsx",
      "components/**/*.test.{ts,tsx}",
      "app/**/*.test.{ts,tsx}",
      "instrumentation.test.ts",
    ],
    exclude: [
      "node_modules",
      ".next",
      "e2e",
      "lib/**/__fixtures__/**",
    ],
  },
});
