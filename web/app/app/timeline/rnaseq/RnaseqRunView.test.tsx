// @vitest-environment jsdom
/** One RNA-seq run's view: its steps, details and how it ended, kept current. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

vi.mock("@/lib/cyl-pipeline/use-live-sync", async () => ({
  useLiveSync: (await import("./live-sync-mock")).useLiveSyncMock,
}));
vi.mock("@/lib/supabase/client", () => ({ createClientSupabaseClient: () => ({}) }));

import RnaseqRunView from "./RnaseqRunView";
import { emit } from "./live-sync-mock";
import type { RnaseqRun } from "@/lib/rnaseq-runs";

afterEach(cleanup);

const RUN: RnaseqRun = {
  id: 1,
  workflow_type: "scrna-cellranger",
  status: "running",
  current_step: "qc",
  params: { sample: "tinygex", reference: "tiny_ref" },
  metadata: {
    species_id: 1,
    dataset_name: "TEST tinygex pipeline check",
    origin: "public",
    source_url: "https://doi.org/10.1016/x",
    citation: "Shahan et al. 2022",
    attributes: { tissue: "root" },
  },
  requested_by: "me",
  argo_workflow_name: "scrna-cellranger-staging-1-7ef9275d",
  step_pods: { "stage-reference": "p1", stage: "p2", qc: "p3" },
  exit_code: null,
  message: null,
  created_at: "2026-09-30T00:03:00Z",
  submitted_at: "2026-09-30T00:03:54Z",
  completed_at: null,
};

function renderView(run: RnaseqRun = RUN) {
  render(<RnaseqRunView initialRun={run} startedBy="bfernando@salk.edu" speciesLabel="Arabidopsis (Arabidopsis thaliana)" />);
}

function step(label: string) {
  return within(screen.getByText(label).closest("li") as HTMLElement);
}

describe("RnaseqRunView", () => {
  it("shows the run, its steps and a log link for each started step", () => {
    renderView();
    expect(screen.getByRole("heading", { name: "Run 1" })).toBeTruthy();
    expect(screen.getByText("tinygex against tiny_ref")).toBeTruthy();
    expect(step("Stage reference").getByText("Done")).toBeTruthy();
    expect(step("FastQC").getByText("Running")).toBeTruthy();
    expect(step("Cell Ranger count").getByText("Waiting")).toBeTruthy();
    expect(step("FastQC").getByRole("button", { name: "View log" })).toBeTruthy();
    expect(step("Cell Ranger count").queryByRole("button", { name: "View log" })).toBeNull();
    expect(screen.getByRole("link", { name: "← All RNA-seq runs" }).getAttribute("href")).toBe(
      "/app/timeline?panel=rnaseq"
    );
  });

  it("shows the dataset details and who started it", () => {
    renderView();
    expect(screen.getByText("TEST tinygex pipeline check")).toBeTruthy();
    expect(screen.getByText("Arabidopsis (Arabidopsis thaliana)")).toBeTruthy();
    expect(screen.getByText("Public dataset · https://doi.org/10.1016/x · Shahan et al. 2022")).toBeTruthy();
    expect(screen.getByText("root")).toBeTruthy();
    expect(screen.getByText("bfernando@salk.edu")).toBeTruthy();
    expect(screen.getByText("scrna-cellranger-staging-1-7ef9275d")).toBeTruthy();
    // The source link is shown as text, never made a link.
    expect(screen.queryByRole("link", { name: /doi\.org/ })).toBeNull();
  });

  it("follows the run as the poller reports it", () => {
    renderView();
    emit({ id: 1, status: "running", current_step: "count", step_pods: { ...RUN.step_pods as object, count: "p4" } });
    expect(step("FastQC").getByText("Done")).toBeTruthy();
    expect(step("Cell Ranger count").getByText("Running")).toBeTruthy();
    expect(step("Cell Ranger count").getByRole("button", { name: "View log" })).toBeTruthy();
    emit({
      id: 1,
      status: "succeeded",
      current_step: "cleanup",
      step_pods: { ...RUN.step_pods as object, count: "p4", cleanup: "p8" },
      completed_at: "2026-09-30T00:20:00Z",
    });
    expect(screen.getByText("Succeeded")).toBeTruthy();
    expect(step("Clean up").getByText("Done")).toBeTruthy();
  });

  it("says in plain words why a run failed", () => {
    renderView({ ...RUN, status: "failed", current_step: "stage", exit_code: 4, message: "no fastqs" });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Exit 4:");
    expect(alert.textContent).toContain("No FASTQ files were found in the sample's folder.");
    expect(alert.textContent).toContain("no fastqs");
    expect(step("Stage FASTQs").getByText("Failed")).toBeTruthy();
    expect(step("FastQC").getByText("Not run")).toBeTruthy();
  });

  it("explains a skipped run", () => {
    renderView({ ...RUN, status: "skipped", current_step: "stage" });
    expect(screen.getByText(/results for this sample and reference already existed/)).toBeTruthy();
  });
});
