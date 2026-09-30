// @vitest-environment jsdom
/**
 * Importing a sample from SRA in the job form: the run IDs and new name it asks for, what it
 * refuses, and what it sends.
 */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ScrnaJobSubmit from "./scrna-job-submit";
import type { RnaseqReference, RnaseqSample } from "@/lib/scrna-jobs";
import type { SpeciesOption } from "@/lib/species-options";

const SAMPLES: RnaseqSample[] = [
  { name: "tinygex", source: "s3", fastq_count: 6, total_bytes: 12_000_000 },
];
const REFERENCES: RnaseqReference[] = [{ name: "tiny_ref", description: null }];
const SPECIES: SpeciesOption[] = [{ id: 1, label: "Arabidopsis thaliana" }];

let fetchSpy: ReturnType<typeof vi.fn>;

function openForm() {
  render(
    <ScrnaJobSubmit
      samples={SAMPLES}
      references={REFERENCES}
      species={SPECIES}
      startedBy="scientist@salk.edu"
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Submit scRNA job" }));
  fireEvent.click(screen.getByLabelText("Import from SRA"));
}

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function fillTheRest() {
  type("Reference genome", "tiny_ref");
  type("Species", "1");
  type("Dataset name", "Root tip sc_71");
}

function startButton() {
  return screen.getByRole("button", { name: "Start run" }) as HTMLButtonElement;
}

beforeEach(() => {
  // A fresh response per call; a body can only be read once.
  fetchSpy = vi.fn().mockImplementation(async () =>
    new Response(
      JSON.stringify({
        run_id: 7,
        sample: "root_tip_sc71",
        reference: "tiny_ref",
        run_key: "k",
      }),
      {
        status: 202,
        headers: { "Content-Type": "application/json" },
      },
    ),
  );
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("importing from SRA", () => {
  it("swaps the registered-sample list for run IDs and a new name", () => {
    openForm();
    expect(screen.queryByLabelText("Registered sample")).toBeNull();
    expect(screen.getByLabelText("SRA run IDs")).toBeTruthy();
    expect(screen.getByLabelText("Sample name")).toBeTruthy();
    expect(screen.getByRole("link", { name: "SRA's Run Selector" })).toBeTruthy();
  });

  it("marks the data as public, with the first run's page as its source link", () => {
    openForm();
    expect((screen.getByLabelText("Public dataset") as HTMLInputElement).checked).toBe(
      true,
    );
    type("SRA run IDs", "SRR28503597\nSRR28503598");
    expect((screen.getByLabelText("Source link") as HTMLInputElement).value).toBe(
      "https://www.ncbi.nlm.nih.gov/sra/SRR28503597",
    );
  });

  it("keeps a source link the scientist typed", () => {
    openForm();
    type("Source link", "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE262840");
    type("SRA run IDs", "SRR28503597");
    expect((screen.getByLabelText("Source link") as HTMLInputElement).value).toContain(
      "GSE262840",
    );
  });

  it("shows each run as a lane, in order", () => {
    openForm();
    type("SRA run IDs", "SRR28503597\nSRR28503598");
    const lanes = within(screen.getByRole("list", { name: "Lanes" })).getAllByRole(
      "listitem",
    );
    expect(lanes.map((l) => l.textContent)).toEqual([
      "L001 SRR28503597",
      "L002 SRR28503598",
    ]);
  });

  it("names the sample after the first run until the name is edited", () => {
    openForm();
    type("SRA run IDs", "SRR28503597");
    expect((screen.getByLabelText("Sample name") as HTMLInputElement).value).toBe(
      "SRR28503597",
    );
    type("Sample name", "root_tip_sc71");
    type("SRA run IDs", "SRR28503598");
    expect((screen.getByLabelText("Sample name") as HTMLInputElement).value).toBe(
      "root_tip_sc71",
    );
  });

  it("explains a GEO sample ID and keeps Start run off", () => {
    openForm();
    fillTheRest();
    type("SRA run IDs", "GSM8180251");
    expect(screen.getByRole("alert").textContent).toContain(
      "GSM8180251 is a GEO sample, not a run",
    );
    expect(startButton().disabled).toBe(true);
  });

  it("refuses a name that's already registered", () => {
    openForm();
    fillTheRest();
    type("SRA run IDs", "SRR28503597");
    type("Sample name", "tinygex");
    expect(screen.getByRole("alert").textContent).toBe(
      "tinygex is already a registered sample; choose another name.",
    );
    expect(startButton().disabled).toBe(true);
  });

  it("sends the new name and the runs in order", async () => {
    openForm();
    fillTheRest();
    type("SRA run IDs", "SRR28503598, SRR28503597");
    type("Sample name", "root_tip_sc71");
    expect(startButton().disabled).toBe(false);
    fireEvent.click(startButton());
    expect((await screen.findByRole("status")).textContent).toContain("Run 7 queued");
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.sample).toBe("root_tip_sc71");
    expect(body.reference).toBe("tiny_ref");
    expect(body.sra_runs).toEqual(["SRR28503598", "SRR28503597"]);
    expect(body.metadata.origin).toBe("public");
    expect(body.metadata.source_url).toBe("https://www.ncbi.nlm.nih.gov/sra/SRR28503598");
  });

  it("sends no run IDs for a registered sample", async () => {
    openForm();
    type("SRA run IDs", "SRR28503597");
    fireEvent.click(screen.getByLabelText("A registered sample"));
    type("Registered sample", "tinygex");
    fillTheRest();
    fireEvent.click(startButton());
    expect((await screen.findByRole("status")).textContent).toContain("Run 7 queued");
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.sample).toBe("tinygex");
    expect(body).not.toHaveProperty("sra_runs");
    // Switching back undoes the import's origin and source link.
    expect(body.metadata.origin).toBe("hpi");
    expect(body.metadata).not.toHaveProperty("source_url");
  });

  it("keeps a source link typed by hand when switching back", async () => {
    openForm();
    type("SRA run IDs", "SRR28503597");
    type("Source link", "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE123");
    fireEvent.click(screen.getByLabelText("A registered sample"));
    fireEvent.click(screen.getByLabelText(/Public/));
    type("Registered sample", "tinygex");
    fillTheRest();
    fireEvent.click(startButton());
    await screen.findByRole("status");
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.metadata.source_url).toBe(
      "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE123"
    );
  });

  it("starts the next import with a fresh name and source link", async () => {
    openForm();
    type("SRA run IDs", "SRR28503597");
    type("Source link", "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM1");
    fillTheRest();
    fireEvent.click(startButton());
    await screen.findByRole("status");
    fireEvent.click(screen.getByRole("button", { name: "Start another run" }));
    type("SRA run IDs", "SRR28503598");
    type("Dataset name", "Root tip lane 2");
    fireEvent.click(startButton());
    await screen.findByRole("status");
    const body = JSON.parse(fetchSpy.mock.calls[1][1].body);
    expect(body.sample).toBe("SRR28503598");
    expect(body.sra_runs).toEqual(["SRR28503598"]);
    expect(body.metadata.origin).toBe("public");
    expect(body.metadata.source_url).toBe("https://www.ncbi.nlm.nih.gov/sra/SRR28503598");
  });
});
