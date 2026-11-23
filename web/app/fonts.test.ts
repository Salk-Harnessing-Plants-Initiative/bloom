/** Fonts ship with the app, so a build never downloads one. */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(__dirname, "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

describe("fonts", () => {
  it("no page or component loads a font from Google", () => {
    const fromGoogle = ["app", "components", "lib"]
      .flatMap((dir) => sourceFiles(join(WEB, dir)))
      .filter((file) => readFileSync(file, "utf8").includes("next/font/google"))
      .map((file) => relative(WEB, file));
    expect(fromGoogle).toEqual([]);
  });

  it("ships Fraunces with its licence", () => {
    expect(existsSync(join(WEB, "app/fonts/fraunces-italic-latin.woff2"))).toBe(true);
    expect(readFileSync(join(WEB, "app/fonts/OFL.txt"), "utf8")).toContain("SIL Open Font License");
  });
});
