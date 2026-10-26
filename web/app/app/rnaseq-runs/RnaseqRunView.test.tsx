// @vitest-environment jsdom
/** One RNA-seq run's view: its steps, details and how it ended, kept current. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

vi.mock("@/lib/cyl-pipeline/use-live-sync", async () => ({
  useLiveSync: (await import("./live-sync-mock")).useLiveSyncMock,
}));
vi.mock("@/lib/supabase/client", () => ({ createClientSupabaseClient: () => ({}) }));
const router = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

import RnaseqRunView from "./RnaseqRunView";
import { emit } from "./live-sync-mock";
import type { RnaseqRun } from "@/lib/rnaseq-runs";

afterEach(() => {
  cleanup();
  router.refresh.mockClear();
});

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
  genome_version_id: null,
  dataset_id: null,
};

function renderView(run: RnaseqRun = RUN, genomeLabel: string | null = null) {
  render(
    <RnaseqRunView
      initialRun={run}
      startedBy="another member · 4965b3af"
      speciesLabel="Arabidopsis (Arabidopsis thaliana)"
      genomeLabel={genomeLabel}
    />
  );
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
      "/app/rnaseq-runs"
    );
  });

  it("shows the S3 folder a run reads from", () => {
    renderView({
      ...RUN,
      params: { sample: "col0", reference: "tiny_ref", fastq_url: "s3://lab-data/run42/" },
    });
    expect(screen.getByText("s3://lab-data/run42/")).toBeTruthy();
    expect(screen.getByText(/Reads from/)).toBeTruthy();
  });

  it("shows the library type as detected by Cell Ranger when the run named no chemistry", () => {
    renderView();
    expect(screen.getByText("Library type")).toBeTruthy();
    expect(screen.getByText("10x gene expression (detected by Cell Ranger)")).toBeTruthy();
  });

  it("shows a Multiome RNA run's library type", () => {
    renderView({ ...RUN, params: { ...(RUN.params as object), chemistry: "ARC-v1" } } as RnaseqRun);
    expect(screen.getByText("10x Multiome — RNA part")).toBeTruthy();
  });

  it("doesn't mention a folder for a run without one", () => {
    renderView();
    expect(screen.queryByText(/Reads from/)).toBeNull();
  });

  it("shows the dataset details and who started it", () => {
    renderView();
    expect(screen.getByText("TEST tinygex pipeline check")).toBeTruthy();
    expect(screen.getByText("Arabidopsis (Arabidopsis thaliana)")).toBeTruthy();
    expect(screen.getByText("Public dataset · https://doi.org/10.1016/x · Shahan et al. 2022")).toBeTruthy();
    expect(screen.getByText("root")).toBeTruthy();
    expect(screen.getByText("another member · 4965b3af")).toBeTruthy();
    expect(screen.queryByText(/@/)).toBeNull();
    expect(screen.getByText("scrna-cellranger-staging-1-7ef9275d")).toBeTruthy();
    // The source link is shown as text, never made a link.
    expect(screen.queryByRole("link", { name: /doi\.org/ })).toBeNull();
  });

  it("shows the name the dataset was loaded under, not only the one typed on the form", () => {
    render(
      <RnaseqRunView
        initialRun={{ ...RUN, status: "succeeded", dataset_id: 24 }}
        startedBy="you"
        speciesLabel={null}
        loadedDatasetName="TEST tinygex pipeline check_v2"
      />
    );
    const datasetRow = screen.getByText("Dataset").nextElementSibling as HTMLElement;
    expect(datasetRow.textContent).toBe("TEST tinygex pipeline check_v2");
    expect(screen.getByRole("link", { name: "Explore the dataset" }).getAttribute("href")).toBe(
      "/app/expression/1/24"
    );
  });

  it("puts one Explore the dataset button at the top, beside the run's title", () => {
    render(
      <RnaseqRunView initialRun={{ ...RUN, status: "succeeded", dataset_id: 24 }} startedBy="you" speciesLabel={null} />
    );
    const [button, ...others] = screen.getAllByRole("link", { name: "Explore the dataset" });
    expect(others).toEqual([]);
    const title = screen.getByRole("heading", { name: "Run 1" });
    expect(button.parentElement).toBe(title.parentElement);
    expect(button.className).toContain("rounded-full");
    expect(button.className).toContain("bg-lime-700");
  });

  it("keeps the typed name when the loaded name couldn't be read", () => {
    render(
      <RnaseqRunView initialRun={{ ...RUN, status: "succeeded", dataset_id: 24 }} startedBy="you" speciesLabel={null} />
    );
    expect(screen.getByText(/TEST tinygex pipeline check/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Explore the dataset" })).toBeTruthy();
  });

  it("links the loaded dataset once the run is linked to it, without a reload", () => {
    renderView();
    expect(screen.queryByRole("link", { name: "Explore the dataset" })).toBeNull();
    emit({ id: 1, status: "succeeded", dataset_id: 24 });
    expect(screen.getByRole("link", { name: "Explore the dataset" }).getAttribute("href")).toBe(
      "/app/expression/1/24"
    );
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

describe("the genome a run used", () => {
  it("names a run's genome version with its assembly and annotation", () => {
    renderView(
      { ...RUN, params: { sample: "tinygex", reference: "athaliana_col0.v2" }, genome_version_id: 3 },
      "athaliana_col0 v2 — TAIR10 / Araport11"
    );
    expect(screen.getByText("tinygex against athaliana_col0 v2 — TAIR10 / Araport11")).toBeTruthy();
  });

  it("shows the reference name for a run without a genome version", () => {
    renderView(RUN, null);
    expect(screen.getByText("tinygex against tiny_ref")).toBeTruthy();
  });

  it("links the run's Cell Ranger report when there is one", () => {
    render(
      <RnaseqRunView
        initialRun={RUN}
        startedBy="you"
        speciesLabel={null}
        reportHref="/app/rnaseq-runs/1/report"
      />
    );
    expect(screen.getByRole("link", { name: "Open the report" }).getAttribute("href")).toBe(
      "/app/rnaseq-runs/1/report"
    );
  });

  it("says the report isn't ready while the run is going", () => {
    renderView();
    expect(screen.getByText("Not ready yet")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Open the report" })).toBeNull();
  });

  it("says no report was stored for a finished run without one", () => {
    renderView({ ...RUN, status: "succeeded", completed_at: "2026-09-30T02:00:00Z" });
    expect(screen.getByText("Not stored for this run")).toBeTruthy();
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("looks for the report again once the open run finishes", () => {
    renderView();
    expect(router.refresh).not.toHaveBeenCalled();
    emit({ ...RUN, status: "succeeded", completed_at: "2026-09-30T02:00:00Z" });
    expect(router.refresh).toHaveBeenCalledTimes(1);
    emit({ ...RUN, status: "succeeded", message: "Finished: loaded into Bloom" });
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });
});
