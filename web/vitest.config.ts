import { defineConfig } from "vitest/config";
import tsconfigPaths from "vite-tsconfig-paths";

/**
 * Vitest config for the web/ workspace.
 *
 * - environment: node — these are pure data-shaping / utility tests; nothing
 *   needs jsdom yet. Add `environment: 'jsdom'` (and @testing-library) later
 *   when we start unit-testing React components.
 * - tsconfigPaths plugin resolves `@/…` imports from web/tsconfig.json so test
 *   files can use the same path aliases as the rest of the codebase.
 * - include defaults to colocated `*.test.ts` / `*.test.tsx` files under
 *   lib/, components/, app/. E2E tests in e2e/ stay with Playwright.
 */
// tsconfigPaths()'s vite Plugin return type doesn't exactly match the slot
// expected by defineConfig() across vite/vitest version pairings — the cast
// is only a TS-stub fix; runtime is correct (tests run + pass).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const plugins = [tsconfigPaths()] as any;

export default defineConfig({
  plugins,
  test: {
    environment: "node",
    include: [
      "lib/**/*.test.ts",
      "components/**/*.test.{ts,tsx}",
      "app/**/*.test.{ts,tsx}",
    ],
    exclude: ["node_modules", ".next", "e2e"],
  },
});
