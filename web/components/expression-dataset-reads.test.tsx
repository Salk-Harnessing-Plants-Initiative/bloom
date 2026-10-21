// @vitest-environment jsdom
/**
 * A pipeline dataset's banner says where its reads came from: SRA, with the run IDs and the
 * form's source link, or an S3 folder and who to ask for the raw reads.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { RnaseqRun } from "@/lib/rnaseq-runs";
import { DatasetReads, readsSource } from "./expression-dataset-reads";

afterEach(cleanup);

const REQUESTER = "1a2b3c4d-0000-0000-0000-000000000000";

function run(params: Record<string, unknown>, metadata: Record<string, unknown> = {}): RnaseqRun {
  return { id: 4, params, metadata, requested_by: REQUESTER } as unknown as RnaseqRun;
}

const sra = run({ sra_runs: ["SRR28503594"] }, { source_url: "https://www.ncbi.nlm.nih.gov/sra/SRR28503594" });
const s3 = run({ fastq_url: "s3://lab-bucket/sc_169_2/" });

describe("readsSource", () => {
  it("names the SRA runs and the form's source link for an SRA run", () => {
    expect(readsSource(sra, null)).toEqual({
      kind: "sra", runId: 4, sraRuns: ["SRR28503594"],
      sourceUrl: "https://www.ncbi.nlm.nih.gov/sra/SRR28503594",
    });
  });

  it("names who started an S3 run, as the runs page does", () => {
    expect(readsSource(s3, null)).toEqual({ kind: "s3", runId: 4, requester: "another member · 1a2b3c4d" });
    expect(readsSource(s3, REQUESTER)).toEqual({ kind: "s3", runId: 4, requester: "you" });
  });

  it("keeps only a web address as the source link", () => {
    const sneaky = run({ sra_runs: ["SRR1"] }, { source_url: "javascript:alert(1)" });
    expect(readsSource(sneaky, null)).toMatchObject({ sourceUrl: null });
  });

  it("has nothing to say for a run with neither SRA runs nor an S3 folder", () => {
    expect(readsSource(run({}), null)).toBeNull();
  });
});

describe("DatasetReads", () => {
  const hrefs = () => screen.getAllByRole("link").map((a) => a.getAttribute("href"));

  it("links each SRA run and the run, without repeating a source link that is the same", () => {
    render(<DatasetReads source={readsSource(sra, null)!} />);
    expect(hrefs()).toEqual(["https://www.ncbi.nlm.nih.gov/sra/SRR28503594", "/app/rnaseq-runs/4"]);
  });

  it("adds the form's source link when it points elsewhere", () => {
    const paper = run({ sra_runs: ["SRR1", "SRR2"] }, { source_url: "https://doi.org/10.1/x" });
    render(<DatasetReads source={readsSource(paper, null)!} />);
    expect(hrefs()).toEqual([
      "https://www.ncbi.nlm.nih.gov/sra/SRR1", "https://www.ncbi.nlm.nih.gov/sra/SRR2",
      "https://doi.org/10.1/x", "/app/rnaseq-runs/4",
    ]);
  });

  it("tells others to contact whoever started an S3 run, and doesn't show the folder", () => {
    const { container } = render(<DatasetReads source={readsSource(s3, null)!} />);
    expect(container.textContent).toBe(
      "Reads from an S3 folder; for the raw reads, contact the person who started run 4 (another member · 1a2b3c4d)",
    );
    expect(container.textContent).not.toContain("s3://");
  });

  it("tells the person who started it that the reads are their own S3 folder", () => {
    const { container } = render(<DatasetReads source={readsSource(s3, REQUESTER)!} />);
    expect(container.textContent).toBe("Reads from your S3 folder · run 4");
  });
});
