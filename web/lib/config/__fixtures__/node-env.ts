/**
 * Test helper for setting `NODE_ENV`.
 *
 * Next's bundled types declare `ProcessEnv.NODE_ENV` as readonly, so a direct
 * `process.env.NODE_ENV = "..."` fails to type check. Assigning through the
 * index signature is the same path vitest.setup.ts already uses to restore
 * env keys. Restoring is not this helper's job — vitest.setup.ts snapshots
 * and rolls back `process.env` around every test.
 */
export function setNodeEnv(value: string | undefined): void {
  const env = process.env as Record<string, string | undefined>;
  if (value === undefined) {
    delete env["NODE_ENV"];
  } else {
    env["NODE_ENV"] = value;
  }
}
