/**
 * Next.js 16+ instrumentation hook for `bloom-web`.
 *
 * Spec: openspec/changes/add-ghcr-image-publishing/specs/frontend-runtime-config/spec.md
 *   - Requirement: Cross-Environment Configuration Fence — URL Hostname Mapping
 *
 * Next.js calls `register()` exactly once at process boot, BEFORE any
 * request is served. We hook in here to run `validateOnBoot()` so a
 * misconfigured production container (missing `SUPABASE_URL_HOSTS_ALLOWED`,
 * missing `NEXT_PUBLIC_SUPABASE_URL`, malformed allow-list, etc.) crashes
 * immediately rather than serving misconfigured responses to researchers.
 *
 * In dev mode (NODE_ENV !== 'production') the validator returns
 * immediately — see web/lib/config/validate-on-boot.ts for the rationale.
 *
 * If `validateOnBoot()` throws, the throw propagates out of `register()`
 * and Next.js fails the boot. The Playwright e2e in §11.7 of PR-3
 * exercises this path against a real container.
 *
 * Reference: https://nextjs.org/docs/app/api-reference/file-conventions/instrumentation
 */

import { validateOnBoot } from "@/lib/config/validate-on-boot";

export async function register(): Promise<void> {
  await validateOnBoot();
}
