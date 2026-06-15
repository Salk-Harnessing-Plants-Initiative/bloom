/**
 * Source-text test for `web/instrumentation.ts`.
 *
 * Spec: openspec/changes/add-ghcr-image-publishing/specs/frontend-runtime-config/spec.md
 *   - Requirement: Cross-Environment Configuration Fence — URL Hostname Mapping
 *     (validateOnBoot wiring via the Next.js 16+ instrumentation hook)
 *
 * Why source-grep instead of runtime exercise:
 *   - vitest can't easily simulate Next.js's actual `register()` invocation
 *     path (that's an integration concern handled by the Playwright e2e in
 *     §11.7 of PR-3).
 *   - But we DO need a CI-fired regression guard so a refactor that
 *     accidentally drops the `validateOnBoot()` call still trips the
 *     suite. A source-text regex is the right level of test for this:
 *     deterministic, fast, no Next runtime needed.
 *
 * The regex patterns are deliberately tolerant to whitespace and import
 * ordering but strict on the AWAITED CALL — a `validateOnBoot` token
 * inside a JSDoc comment or as a bare reference would NOT satisfy them.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const INSTRUMENTATION_PATH = resolve(__dirname, "instrumentation.ts");

describe("web/instrumentation.ts", () => {
  let source: string;

  it("file exists", () => {
    expect(() => {
      source = readFileSync(INSTRUMENTATION_PATH, "utf-8");
    }).not.toThrow();
  });

  it("imports validateOnBoot from the config module (whitespace-tolerant)", () => {
    source ??= readFileSync(INSTRUMENTATION_PATH, "utf-8");
    // Matches:
    //   import { validateOnBoot } from "...";
    //   import { foo, validateOnBoot, bar } from "...";
    //   import {\n  validateOnBoot,\n  ...\n} from "...";
    expect(source).toMatch(/import\s*\{[^}]*\bvalidateOnBoot\b[^}]*\}/);
  });

  it("calls validateOnBoot as an awaited expression (NOT just a JSDoc reference)", () => {
    source ??= readFileSync(INSTRUMENTATION_PATH, "utf-8");
    // Strict regex: require `await` immediately before, and `(` immediately
    // after — won't false-positive on a comment like `// see validateOnBoot()`
    // or a JSDoc `{@link validateOnBoot}` reference.
    expect(source).toMatch(/\bawait\s+validateOnBoot\s*\(/);
  });

  it("exports an async register function", () => {
    source ??= readFileSync(INSTRUMENTATION_PATH, "utf-8");
    expect(source).toMatch(/export\s+async\s+function\s+register\s*\(/);
  });
});
