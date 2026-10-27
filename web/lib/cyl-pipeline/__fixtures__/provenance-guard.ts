/**
 * The static guard behind no-provenance-joins.test.ts (design D6): collect the
 * guarded files plus the `@/lib` modules they import transitively, and report
 * any run-id matching surface. The file system is injected so the self-checks
 * run in memory.
 *
 * This file sits in a guarded directory, so each pattern is assembled from
 * parts rather than written out.
 */

import { posix } from "node:path";

export interface GuardFs {
  /** The web/ workspace root, with forward slashes. */
  webRoot: string;
  readFile(path: string): string;
  /** True only for a file that exists. */
  exists(path: string): boolean;
}

export interface Forbidden {
  name: string;
  pattern: RegExp;
}

const u = "_";
const words = (...parts: string[]) => parts.join(u);

/** The spec's run-id matching surfaces (Requirement: Run results link only through requested scans). */
export const FORBIDDEN: Forbidden[] = [
  { name: words("get", "scan", "traits"), pattern: new RegExp(words("get", "scan", "traits")) },
  { name: words("cyl", "scan", "traits", "source"), pattern: new RegExp(words("cyl", "scan", "traits", "source")) },
  { name: words("cyl", "scan", "traits", "latest"), pattern: new RegExp(words("cyl", "scan", "traits", "latest")) },
  { name: words("run", "id", ""), pattern: new RegExp(`\\b${words("run", "id", "")}\\b`) },
  {
    name: `metadata ->> '${words("pipeline", "run", "id")}'`,
    pattern: new RegExp(`->>\\s*'${words("pipeline", "run", "id")}'`),
  },
];

const SPECIFIER =
  /\bfrom\s*["']([^"']+)["']|\bimport\s*\(\s*["']([^"']+)["']\s*\)|\bimport\s+["']([^"']+)["']/g;

export function importSpecifiers(source: string): string[] {
  return [...source.matchAll(SPECIFIER)].map((m) => m[1] ?? m[2] ?? m[3]);
}

const CANDIDATES = ["", ".ts", ".tsx", "/index.ts", "/index.tsx"];

/** The file a specifier names, or null for a package or an unresolvable path. */
export function resolveImport(fromFile: string, specifier: string, fs: GuardFs): string | null {
  let base: string;
  if (specifier.startsWith("@/")) base = posix.join(fs.webRoot, specifier.slice(2));
  else if (specifier.startsWith(".")) base = posix.join(posix.dirname(fromFile), specifier);
  else return null;
  for (const suffix of CANDIDATES) {
    const path = base + suffix;
    if (fs.exists(path)) return path;
  }
  return null;
}

const CODE = /\.(ts|tsx|js|mjs)$/;

/** The roots, then every web/lib module they reach, breadth first. */
export function collectFiles(roots: string[], fs: GuardFs, exclude: Set<string>): string[] {
  const lib = `${fs.webRoot}/lib/`;
  const seen = new Set<string>(roots);
  const queue = [...roots];
  for (let i = 0; i < queue.length; i++) {
    for (const specifier of importSpecifiers(fs.readFile(queue[i]))) {
      const target = resolveImport(queue[i], specifier, fs);
      if (!target || seen.has(target) || exclude.has(target) || !target.startsWith(lib) || !CODE.test(target)) continue;
      seen.add(target);
      queue.push(target);
    }
  }
  return queue;
}

export function findViolations(files: string[], fs: GuardFs): { file: string; name: string }[] {
  return files.flatMap((file) => {
    const source = fs.readFile(file);
    return FORBIDDEN.filter((f) => f.pattern.test(source)).map((f) => ({ file, name: f.name }));
  });
}
