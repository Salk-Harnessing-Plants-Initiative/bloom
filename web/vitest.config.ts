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
export default defineConfig({
  plugins: [tsconfigPaths()],
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
