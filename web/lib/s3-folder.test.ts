/** The S3 folder helpers the form uses before and after the job service checks a folder. */

import { describe, expect, it } from "vitest";
import {
  folderCheckErrorMessage,
  folderSummary,
  folderUrlProblem,
  isFolderChangedRefusal,
  isFolderCheck,
  looksLikeFolder,
  normaliseFolderUrl,
} from "./s3-folder";

describe("the folder URL", () => {
  it("drops surrounding spaces and adds the trailing slash", () => {
    expect(normaliseFolderUrl("  s3://lab-data/run42 ")).toBe("s3://lab-data/run42/");
    expect(normaliseFolderUrl("")).toBe("");
  });

  it.each(["s3://lab-data/run42", "s3://lab-data/a/b/c/", "s3://lab.data-1/x_y(2)/"])(
    "accepts %s",
    (url) => expect(looksLikeFolder(url)).toBe(true)
  );

  it.each([
    "s3://lab-data/", "s3://Lab/run42/", "s3://lab-data/run 42/", "s3://lab-data/a/../b/",
    "https://lab-data.s3.amazonaws.com/run42/", "lab-data/run42/",
  ])("refuses %s", (url) => expect(looksLikeFolder(url)).toBe(false));

  it("says why a URL can't be checked, and nothing while the box is empty", () => {
    expect(folderUrlProblem("")).toBeNull();
    expect(folderUrlProblem("s3://lab-data/run42")).toBeNull();
    expect(folderUrlProblem("lab-data/run42")).toBe("Enter an S3 folder, starting with s3://");
    expect(folderUrlProblem("s3://lab-data/")).toBe(
      "Give the folder inside the bucket, e.g. s3://bucket/folder/"
    );
  });

  it("says which kind of URL was given instead of a folder", () => {
    expect(folderUrlProblem("https://lab-data.s3.us-west-2.amazonaws.com/run42/")).toMatch(
      /Use the s3:\/\/ form/
    );
    expect(folderUrlProblem("s3://lab-data/run42/col0_S1_L001_R1_001.fastq.gz")).toMatch(
      /That's a file/
    );
    expect(folderUrlProblem("s3://lab-data/run+1/")).toMatch(/letters, digits and !_\.\*'\(\)-/);
    expect(folderUrlProblem("s3://lab-data/date=2026-01-01/")).toMatch(/no spaces or other characters/);
  });

  it("takes a URL of exactly 1024 characters and refuses 1025", () => {
    const head = "s3://lab-data/";
    const ofLength = (n: number) => head + "a".repeat(n - head.length - 1) + "/";
    expect(looksLikeFolder(ofLength(1024))).toBe(true);
    expect(looksLikeFolder(ofLength(1025))).toBe(false);
    expect(folderUrlProblem(ofLength(1025))).toMatch(/at most 1024 characters/);
  });

  it("refuses . and .. folder names", () => {
    expect(looksLikeFolder("s3://lab-data/a/./b/")).toBe(false);
    expect(looksLikeFolder("s3://lab-data/a/../b/")).toBe(false);
  });
});

describe("the check", () => {
  const check = {
    fastq_url: "s3://lab-data/run42/",
    sample: "col0",
    lanes: [1],
    files: [{ name: "col0_S1_L001_R1_001.fastq.gz", size: 1, etag: '"a"' }],
    file_count: 1,
    total_bytes: 999,
  };

  it("recognises the service's answer", () => {
    expect(isFolderCheck(check)).toBe(true);
    expect(isFolderCheck({ ...check, files: [{ name: "x" }] })).toBe(false);
    expect(isFolderCheck({ ...check, lanes: ["1"] })).toBe(false);
    expect(isFolderCheck(null)).toBe(false);
    for (const field of ["fastq_url", "sample", "file_count", "total_bytes"] as const) {
      const { [field]: _dropped, ...rest } = check;
      expect(isFolderCheck(rest)).toBe(false);
    }
    expect(isFolderCheck({ ...check, files: [{ name: "x", size: 1 }] })).toBe(false);
  });

  it("tells the folder-changed 409 from other refusals", () => {
    const changed = "s3://lab-data/run42/ changed since it was checked; check it again, then start the run";
    expect(isFolderChangedRefusal(409, changed)).toBe(true);
    expect(isFolderChangedRefusal(409, "col0 against tair10 has already been processed (run 9).")).toBe(false);
    expect(isFolderChangedRefusal(422, changed)).toBe(false);
    expect(isFolderChangedRefusal(409, null)).toBe(false);
  });

  it("sums it up in one line", () => {
    expect(folderSummary(check)).toBe("col0 · 1 lane · 1 file · 999 B");
    expect(folderSummary({ ...check, lanes: [1, 2], file_count: 4, total_bytes: 38_200_000_000 })).toBe(
      "col0 · 2 lanes · 4 files · 38.2 GB"
    );
  });

  it("prefers the service's reason, then a fixed message", () => {
    expect(folderCheckErrorMessage(422, "No FASTQs")).toBe("No FASTQs");
    expect(folderCheckErrorMessage(429, null)).toMatch(/Too many checks/);
    expect(folderCheckErrorMessage(500, null)).toMatch(/Couldn't check the folder/);
  });
});
