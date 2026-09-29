// @vitest-environment jsdom
/**
 * The job form as a scientist uses it: the choices it offers, that a run starts only
 * once the sample, reference, species and dataset name are given, what it sends, and
 * what it says when the start succeeds or is refused.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import ScrnaJobSubmit from "./scrna-job-submit";
import type { RnaseqReference, RnaseqSample } from "@/lib/scrna-jobs";
import type { SpeciesOption } from "@/lib/species-options";

const SAMPLES: RnaseqSample[] = [
  { name: "tinygex", source: "s3", fastq_count: 4, total_bytes: 31_400_000_000 },
  { name: "root_rep2", source: "sra", fastq_count: null, total_bytes: null },
];
const REFERENCES: RnaseqReference[] = [
  { name: "tiny_ref", description: "Arabidopsis TAIR10" },
  { name: "GRCh38", description: null },
];

const SPECIES: SpeciesOption[] = [
  { id: 1, label: "Arabidopsis (Arabidopsis thaliana)" },
  { id: 4, label: "Rice (Oryza sativa)" },
];

let fetchSpy: ReturnType<typeof vi.fn>;

function respond(body: unknown, status = 201) {
  fetchSpy.mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  );
}

function openForm(samples = SAMPLES, references = REFERENCES) {
  render(<ScrnaJobSubmit samples={samples} references={references} species={SPECIES} />);
  fireEvent.click(screen.getByRole("button", { name: "Submit scRNA job" }));
}

function type(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function choose(sample: string, reference: string) {
  type("Sample", sample);
  type("Reference genome", reference);
  type("Species", "1");
  type("Dataset name", "Col-0 root tip");
}

beforeEach(() => {
  fetchSpy = vi.fn();
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the form", () => {
  it("starts closed and opens from the button", () => {
    render(<ScrnaJobSubmit samples={SAMPLES} references={REFERENCES} species={SPECIES} />);
    expect(screen.queryByLabelText("Sample")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Submit scRNA job" }));
    expect(screen.getByLabelText("Sample")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByLabelText("Sample")).toBeNull();
  });

  it("lists samples with their file count and size, and references with their description", () => {
    openForm();
    expect(screen.getByRole("option", { name: "tinygex (4 FASTQs, 31.4 GB)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "root_rep2" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "tiny_ref — Arabidopsis TAIR10" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "GRCh38" })).toBeTruthy();
  });

  it("offers Cell Ranger count as the only job type", () => {
    openForm();
    const jobType = screen.getByLabelText("Job type") as HTMLSelectElement;
    expect(jobType.disabled).toBe(true);
    expect(jobType.value).toBe("cellranger-count");
  });

  it("keeps Start run disabled until the run and the required details are given", () => {
    openForm();
    const start = screen.getByRole("button", { name: "Start run" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    type("Sample", "tinygex");
    type("Reference genome", "tiny_ref");
    expect(start.disabled).toBe(true);
    expect(screen.getByText("Choose a species.")).toBeTruthy();
    type("Species", "1");
    expect(start.disabled).toBe(true);
    expect(screen.getByText("Enter a dataset name.")).toBeTruthy();
    type("Dataset name", "   ");
    expect(start.disabled).toBe(true);
    type("Dataset name", "Col-0 root tip");
    expect(start.disabled).toBe(false);
  });

  it("lists the species it was given", () => {
    openForm();
    expect(screen.getByRole("option", { name: "Rice (Oryza sativa)" })).toBeTruthy();
  });

  it("refuses an extra field with a value but no name, or a name used twice", () => {
    openForm();
    choose("tinygex", "tiny_ref");
    const start = screen.getByRole("button", { name: "Start run" }) as HTMLButtonElement;
    type("Field 1 value", "root");
    expect(start.disabled).toBe(true);
    expect(screen.getByText("Every extra field needs a name.")).toBeTruthy();
    type("Field 1 name", "tissue");
    expect(start.disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "+ Add field" }));
    type("Field 2 name", "tissue");
    expect(start.disabled).toBe(true);
    expect(screen.getByText('The field "tissue" is listed twice.')).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove field 2" }));
    expect(start.disabled).toBe(false);
  });

  it("says who adds samples when none are registered", () => {
    openForm([], REFERENCES);
    expect(screen.getByRole("option", { name: "No samples registered yet" })).toBeTruthy();
    expect((screen.getByLabelText("Sample") as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getByText(/added by a Bloom admin/)).toBeTruthy();
  });
});

describe("starting a run", () => {
  it("posts the chosen names with the dataset details and shows the queued run", async () => {
    respond({ run_id: 12, sample: "tinygex", reference: "tiny_ref", run_key: "k" });
    openForm();
    choose("tinygex", "tiny_ref");
    type(/Accession or genotype/, " Col-0 ");
    type("Field 1 name", "tissue");
    type("Field 1 value", "root tip");
    fireEvent.click(screen.getByRole("button", { name: "+ Add field" }));
    type("Field 2 name", "days_after_germination");
    type("Field 2 value", "7");
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    const status = await screen.findByRole("status");
    expect(status.textContent).toBe("Run 12 queued: tinygex against tiny_ref.");
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/scrna/cellranger/runs");
    expect(JSON.parse(init.body)).toEqual({
      sample: "tinygex",
      reference: "tiny_ref",
      metadata: {
        species_id: 1,
        dataset_name: "Col-0 root tip",
        accession: "Col-0",
        origin: "hpi",
        attributes: { tissue: "root tip", days_after_germination: "7" },
      },
    });
  });

  it("asks for a public dataset's source link and sends it with the citation", async () => {
    respond({ run_id: 13, sample: "tinygex", reference: "tiny_ref", run_key: "k" });
    openForm();
    choose("tinygex", "tiny_ref");
    expect(screen.queryByLabelText("Source link")).toBeNull();
    fireEvent.click(screen.getByLabelText("Public dataset"));

    const start = screen.getByRole("button", { name: "Start run" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    expect(screen.getByText("Enter the public dataset's source link.")).toBeTruthy();
    type("Source link", "doi.org/10.1016/x");
    expect(
      screen.getByText("The source link must be a web address starting with https://.")
    ).toBeTruthy();
    type("Source link", "https://doi.org/10.1016/j.devcel.2022.01.008");
    type(/Citation/, "Shahan et al. 2022");
    expect(start.disabled).toBe(false);
    fireEvent.click(start);

    await screen.findByRole("status");
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body).metadata).toEqual({
      species_id: 1,
      dataset_name: "Col-0 root tip",
      origin: "public",
      source_url: "https://doi.org/10.1016/j.devcel.2022.01.008",
      citation: "Shahan et al. 2022",
    });
  });

  it("defaults to HPI and drops the source when switched back", async () => {
    respond({ run_id: 14, sample: "tinygex", reference: "tiny_ref", run_key: "k" });
    openForm();
    choose("tinygex", "tiny_ref");
    expect((screen.getByLabelText("HPI") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByLabelText("Public dataset"));
    type("Source link", "https://example.org/geo");
    fireEvent.click(screen.getByLabelText("HPI"));
    expect(screen.queryByLabelText("Source link")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    await screen.findByRole("status");
    const metadata = JSON.parse(fetchSpy.mock.calls[0][1].body).metadata;
    expect(metadata.origin).toBe("hpi");
    expect(metadata).not.toHaveProperty("source_url");
  });

  it("shows the service's reason when it refuses the names", async () => {
    respond({ detail: "Reference 'tiny_ref' has no reference.json" }, 422);
    openForm();
    choose("tinygex", "tiny_ref");
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Reference 'tiny_ref' has no reference.json"
    );
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("falls back to a fixed message when there's no detail", async () => {
    respond({ detail: null }, 503);
    openForm();
    choose("tinygex", "tiny_ref");
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The job service isn't available right now."
    );
  });

  it("says so when the service can't be reached", async () => {
    fetchSpy.mockRejectedValue(new TypeError("Failed to fetch"));
    openForm();
    choose("tinygex", "tiny_ref");
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not reach the job service."
    );
  });

  it("rejects a success body it doesn't recognise", async () => {
    respond({ run_id: "12" });
    openForm();
    choose("tinygex", "tiny_ref");
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The job service returned an unexpected response."
    );
  });

  it("disables the form while the start is in flight", async () => {
    let finish: (r: Response) => void = () => {};
    fetchSpy.mockReturnValue(new Promise<Response>((resolve) => (finish = resolve)));
    openForm();
    choose("tinygex", "tiny_ref");
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    const start = screen.getByRole("button", { name: "Starting…" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    // The fieldset disables its fields, which `.disabled` on each one doesn't report.
    expect(screen.getByLabelText("Sample").matches(":disabled")).toBe(true);
    expect(screen.getByLabelText("Dataset name").matches(":disabled")).toBe(true);
    fireEvent.click(start);
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    finish(new Response(JSON.stringify({ run_id: 1, sample: "tinygex", reference: "tiny_ref", run_key: "k" }), { status: 201 }));
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
  });
});
