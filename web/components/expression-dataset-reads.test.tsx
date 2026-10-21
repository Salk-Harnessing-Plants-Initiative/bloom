// @vitest-environment jsdom
/**
 * A pipeline dataset's banner says where its reads came from: SRA, with the run IDs and the
 * form's source link, or that they were uploaded via S3, without naming the folder.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { RnaseqRun } from "@/lib/rnaseq-runs";
import { DatasetReads, readsSource } from "./expression-dataset-reads";

afterEach(cleanup);

function run(params: Record<string, unknown>, metadata: Record<string, unknown> = {}): RnaseqRun {
  return { id: 4, params, metadata, requested_by: "1a2b3c4d-0000" } as unknown as RnaseqRun;
}

const sra = run({ sra_runs: ["SRR28503594"] }, { source_url: "https://www.ncbi.nlm.nih.gov/sra/SRR28503594" });
const s3 = run({ fastq_url: "s3://lab-bucket/sc_169_2/" });

describe("readsSource", () => {
  it("names the SRA runs and the form's source link for an SRA run", () => {
    expect(readsSource(sra)).toEqual({
      kind: "sra", runId: 4, sraRuns: ["SRR28503594"],
      sourceUrl: "https://www.ncbi.nlm.nih.gov/sra/SRR28503594",
    });
  });

  it("only says an S3 run's reads came from S3", () => {
    expect(readsSource(s3)).toEqual({ kind: "s3", runId: 4 });
  });

  it("prefers SRA when a run records both", () => {
    expect(readsSource(run({ sra_runs: ["SRR1"], fastq_url: "s3://b/x/" }))).toMatchObject({ kind: "sra" });
  });

  it("keeps only a web address as the source link", () => {
    const sneaky = run({ sra_runs: ["SRR1"] }, { source_url: "javascript:alert(1)" });
    expect(readsSource(sneaky)).toMatchObject({ sourceUrl: null });
  });

  it("has nothing to say for a run with neither SRA runs nor an S3 folder", () => {
    expect(readsSource(run({}))).toBeNull();
    expect(readsSource(run({ sra_runs: [], fastq_url: "" }))).toBeNull();
  });
});

describe("DatasetReads", () => {
  const hrefs = () => screen.getAllByRole("link").map((a) => a.getAttribute("href"));

  it("links each SRA run and the run, without repeating a source link that is the same", () => {
    render(<DatasetReads source={readsSource(sra)!} />);
    expect(hrefs()).toEqual(["https://www.ncbi.nlm.nih.gov/sra/SRR28503594", "/app/rnaseq-runs/4"]);
  });

  it("adds the form's source link when it points elsewhere", () => {
    const paper = run({ sra_runs: ["SRR1", "SRR2"] }, { source_url: "https://doi.org/10.1/x" });
    render(<DatasetReads source={readsSource(paper)!} />);
    expect(hrefs()).toEqual([
      "https://www.ncbi.nlm.nih.gov/sra/SRR1", "https://www.ncbi.nlm.nih.gov/sra/SRR2",
      "https://doi.org/10.1/x", "/app/rnaseq-runs/4",
    ]);
  });

  it("says an S3 run's reads were uploaded via S3, without the folder or who started it", () => {
    const { container } = render(<DatasetReads source={readsSource(s3)!} />);
    expect(container.textContent).toBe("Reads uploaded via S3 · run 4");
    expect(hrefs()).toEqual(["/app/rnaseq-runs/4"]);
  });
});
