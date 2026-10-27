/**
 * Static guard for design D6 (add-cyl-pipeline-ui task 8.4): the pipeline-run
 * UI never matches traits or sources to a run. Provenance run ids are
 * unpopulated (bloom#864), so any run-keyed trait read would be empty or, once
 * populated, would list unrequested writes (sleap-roots-pipeline#71).
 *
 * The guarded directories must not contain the forbidden surfaces literally,
 * this file included, so every pattern is assembled from parts.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { collectFiles, findViolations, FORBIDDEN, importSpecifiers, resolveImport, type GuardFs } from "./__fixtures__/provenance-guard";

const u = "_";
const fn = ["get", "scan", "traits"].join(u);
const sourceView = ["cyl", "scan", "traits", "source"].join(u);
const latestView = ["cyl", "scan", "traits", "latest"].join(u);
const runIdArg = ["run", "id", ""].join(u);
const provenanceRead = `metadata ->> '${["pipeline", "run", "id"].join(u)}'`;

function memoryFs(files: Record<string, string>): GuardFs {
  return {
    webRoot: "/web",
    readFile: (p) => {
      if (!(p in files)) throw new Error(`no such file ${p}`);
      return files[p];
    },
    exists: (p) => p in files,
  };
}

describe("the guard itself (self-checks)", () => {
  it("names the five forbidden surfaces", () => {
    expect(FORBIDDEN.map((f) => f.name)).toEqual([fn, sourceView, latestView, runIdArg, provenanceRead]);
  });

  it.each([
    [fn, `supabase.rpc("${fn}", { scan_id: 1 })`],
    [sourceView, `.from("${sourceView}")`],
    [latestView, `.from("${latestView}")`],
    [runIdArg, `{ ${runIdArg}: 91 }`],
    [provenanceRead, `select metadata  ->>'${["pipeline", "run", "id"].join(u)}' from x`],
  ])("matches %s in its fixture", (name, fixture) => {
    const pattern = FORBIDDEN.find((f) => f.name === name)!.pattern;
    expect(pattern.test(fixture)).toBe(true);
  });

  it("does not match the run-scans foreign key name, or run_id alone", () => {
    const fkey = ["cyl", "pipeline", "run", "scans", "run", "id", "fkey"].join(u);
    for (const { pattern } of FORBIDDEN) {
      expect(pattern.test(fkey)).toBe(false);
      expect(pattern.test(`.eq("run_id", 91)`)).toBe(false);
      expect(pattern.test(`cyl_pipeline_run_scans`)).toBe(false);
    }
  });

  it("reads static, dynamic and side-effect import specifiers", () => {
    const source = [
      `import { a } from "./a";`,
      `import type { B } from '@/lib/b';`,
      `export { c } from "../c";`,
      `const d = await import("./d");`,
      `import "./e.css";`,
      `import x from "react";`,
    ].join("\n");
    expect(importSpecifiers(source)).toEqual(["./a", "@/lib/b", "../c", "./d", "./e.css", "react"]);
  });

  it("resolves @/ and relative specifiers to .ts, .tsx and index files, and ignores packages", () => {
    const fs = memoryFs({
      "/web/lib/b.ts": "",
      "/web/lib/dir/index.tsx": "",
      "/web/components/x/c.tsx": "",
    });
    expect(resolveImport("/web/components/x/a.tsx", "@/lib/b", fs)).toBe("/web/lib/b.ts");
    expect(resolveImport("/web/components/x/a.tsx", "@/lib/dir", fs)).toBe("/web/lib/dir/index.tsx");
    expect(resolveImport("/web/components/x/a.tsx", "./c", fs)).toBe("/web/components/x/c.tsx");
    expect(resolveImport("/web/lib/q/z.ts", "../b", fs)).toBe("/web/lib/b.ts");
    expect(resolveImport("/web/components/x/a.tsx", "react", fs)).toBeNull();
    expect(resolveImport("/web/components/x/a.tsx", "./missing", fs)).toBeNull();
  });

  it("detects a forbidden surface two hops away: a → @/lib/b → ./c", () => {
    const fs = memoryFs({
      "/web/components/cyl-pipeline/a.tsx": `import { b } from "@/lib/b";`,
      "/web/lib/b.ts": `export { c } from "./c";`,
      "/web/lib/c.ts": `export const c = () => client.rpc("${fn}");`,
    });
    const files = collectFiles(["/web/components/cyl-pipeline/a.tsx"], fs, new Set());
    expect(files).toEqual(["/web/components/cyl-pipeline/a.tsx", "/web/lib/b.ts", "/web/lib/c.ts"]);
    expect(findViolations(files, fs)).toEqual([{ file: "/web/lib/c.ts", name: fn }]);
  });

  it("follows web/lib only, and skips excluded files", () => {
    const fs = memoryFs({
      "/web/app/app/cyl-pipeline-runs/p.tsx": `import "@/components/other"; import "@/lib/database.types"; import "./q";`,
      "/web/app/app/cyl-pipeline-runs/q.ts": ``,
      "/web/components/other.tsx": `rpc("${fn}")`,
      "/web/lib/database.types.ts": `${fn}: { Args: { ${runIdArg}: number } }`,
    });
    const files = collectFiles(["/web/app/app/cyl-pipeline-runs/p.tsx"], fs, new Set(["/web/lib/database.types.ts"]));
    expect(files).toEqual(["/web/app/app/cyl-pipeline-runs/p.tsx"]);
    expect(findViolations(files, fs)).toEqual([]);
  });
});

describe("the real tree (characterization)", () => {
  const webRoot = join(process.cwd()).replace(/\\/g, "/");
  const guarded = ["lib/cyl-pipeline", "components/cyl-pipeline", "app/app/cyl-pipeline-runs"].map((d) => `${webRoot}/${d}`);
  const walk = (dir: string): string[] =>
    readdirSync(dir).flatMap((name) => {
      const path = `${dir}/${name}`;
      return statSync(path).isDirectory() ? walk(path) : /\.(ts|tsx|js|mjs)$/.test(name) ? [path] : [];
    });
  const fs: GuardFs = { webRoot, readFile: (p) => readFileSync(p, "utf8"), exists: (p) => existsSync(p) && statSync(p).isFile() };
  const exclude = new Set([`${webRoot}/lib/database.types.ts`, `${webRoot}/types/database.types.ts`, `${webRoot}/lib/cyl-pipeline/no-provenance-joins.test.ts`]);

  it("has source in each of the three guarded directories", () => {
    for (const dir of guarded) expect(walk(dir).length, dir).toBeGreaterThan(0);
  });

  it("finds no forbidden surface in them or the @/lib modules they import", () => {
    const roots = guarded.flatMap(walk).filter((f) => !exclude.has(f));
    const files = collectFiles(roots, fs, exclude);
    expect(files.some((f) => f.endsWith("/lib/supabase/client.ts"))).toBe(true);
    expect(files.some((f) => f.endsWith("/lib/database.types.ts"))).toBe(false);
    expect(findViolations(files, fs)).toEqual([]);
  });
});
