/** The S3 folder helpers the form uses before and after the job service checks a folder. */

import { describe, expect, it } from "vitest";
import {
  folderCheckErrorMessage,
  folderSummary,
  folderUrlProblem,
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
    expect(folderUrlProblem("s3://lab-data/")).toMatch(/s3:\/\/bucket\/folder\//);
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
