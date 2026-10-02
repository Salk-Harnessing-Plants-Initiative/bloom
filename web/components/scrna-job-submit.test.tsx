// @vitest-environment jsdom
/**
 * The job form as a scientist uses it: the choices it offers, that a run starts only
 * once the S3 folder has passed its check and the reference, species and dataset name are
 * given, what it sends, and what it says when the start succeeds or is refused.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/lib/supabase/client", () => ({ createClientSupabaseClient: () => ({}) }));
// The folder is checked without the form's typing delay.
vi.mock("@/lib/s3-folder", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/s3-folder")>()),
  FOLDER_CHECK_DELAY_MS: 0,
}));
vi.mock("@/lib/species-options", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/species-options")>()),
  addSpecies: vi.fn(),
}));

import ScrnaJobSubmit from "./scrna-job-submit";
import { addSpecies } from "@/lib/species-options";
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

const FOLDER_URL = "s3://lab-data/tinygex/";
const FOLDER = {
  fastq_url: FOLDER_URL,
  sample: "tinygex",
  lanes: [1],
  files: [
    { name: "tinygex_S1_L001_R1_001.fastq.gz", size: 1_000_000_000, etag: '"a"' },
    { name: "tinygex_S1_L001_R2_001.fastq.gz", size: 3_400_000_000, etag: '"b"' },
  ],
  file_count: 2,
  total_bytes: 4_400_000_000,
};
const FOLDER_SUMMARY = "tinygex · 1 lane · 2 files · 4.4 GB";

let fetchSpy: ReturnType<typeof vi.fn>;
// What the start and the folder check answer; each test can replace them.
let startReply: () => Promise<Response>;
let folderReply: () => Promise<Response>;

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function respond(body: unknown, status = 201) {
  startReply = async () => json(body, status);
}

/** The calls that started a run, not the folder checks. */
function startCalls() {
  return fetchSpy.mock.calls.filter(([url]) => url === "/api/scrna/cellranger/runs");
}

function openForm(samples = SAMPLES, references = REFERENCES) {
  render(
    <ScrnaJobSubmit
      samples={samples}
      references={references}
      species={SPECIES}
      startedBy="scientist@salk.edu"
    />
  );
  fireEvent.click(screen.getByRole("button", { name: "Submit scRNA job" }));
}

function type(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function enterFolder(url = FOLDER_URL) {
  type("S3 folder URL", url);
  await screen.findByText(FOLDER_SUMMARY);
}

async function choose(reference = "tiny_ref") {
  await enterFolder();
  type("Reference genome", reference);
  type("Species", "1");
  type("Dataset name", "Col-0 root tip");
}

beforeEach(() => {
  startReply = async () => json({ detail: "no reply set" }, 500);
  folderReply = async () => json(FOLDER, 200);
  fetchSpy = vi.fn((url: string) =>
    url === "/api/scrna/cellranger/folder-check" ? folderReply() : startReply()
  );
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the form", () => {
  it("starts closed and opens from the button", () => {
    render(
      <ScrnaJobSubmit samples={SAMPLES} references={REFERENCES} species={SPECIES} startedBy={null} />
    );
    expect(screen.queryByLabelText("S3 folder URL")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Submit scRNA job" }));
    expect(screen.getByLabelText("S3 folder URL")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByLabelText("S3 folder URL")).toBeNull();
  });

  it("reads from an S3 folder by default, with no registered-sample list", () => {
    openForm();
    expect((screen.getByLabelText("S3 folder") as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByLabelText("Registered sample")).toBeNull();
  });

  it("lists references with their description", () => {
    openForm();
    expect(screen.getByRole("option", { name: "tiny_ref — Arabidopsis TAIR10" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "GRCh38" })).toBeTruthy();
  });

  it("offers Cell Ranger count as the only job type", () => {
    openForm();
    const jobType = screen.getByLabelText("Job type") as HTMLSelectElement;
    expect(jobType.disabled).toBe(true);
    expect(jobType.value).toBe("cellranger-count");
  });

  it("keeps Start run disabled until the run and the required details are given", async () => {
    openForm();
    const start = screen.getByRole("button", { name: "Start run" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    await enterFolder();
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

  it("refuses an extra field with a value but no name, or a name used twice", async () => {
    openForm();
    await choose();
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

  it("says who adds references when none are registered", () => {
    openForm(SAMPLES, []);
    expect(screen.getByText(/References are added by a Bloom admin/)).toBeTruthy();
  });
});

describe("starting a run", () => {
  it("posts the chosen names with the dataset details and shows the queued run", async () => {
    respond({ run_id: 12, sample: "tinygex", reference: "tiny_ref", run_key: "k" });
    openForm();
    await choose();
    type(/Accession or genotype/, " Col-0 ");
    type("Field 1 name", "tissue");
    type("Field 1 value", "root tip");
    fireEvent.click(screen.getByRole("button", { name: "+ Add field" }));
    type("Field 2 name", "days_after_germination");
    type("Field 2 value", "7");
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Run 12 queued");
    expect(status.textContent).toContain(
      "tinygex against tiny_ref · Col-0 root tip (Arabidopsis (Arabidopsis thaliana))"
    );
    const [[, init]] = startCalls();
    expect(JSON.parse(init.body)).toEqual({
      fastq_url: FOLDER_URL,
      fastq_files: FOLDER.files,
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
    await choose();
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
    expect(JSON.parse(startCalls()[0][1].body).metadata).toEqual({
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
    await choose();
    expect((screen.getByLabelText("HPI") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByLabelText("Public dataset"));
    type("Source link", "https://example.org/geo");
    fireEvent.click(screen.getByLabelText("HPI"));
    expect(screen.queryByLabelText("Source link")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    await screen.findByRole("status");
    const metadata = JSON.parse(startCalls()[0][1].body).metadata;
    expect(metadata.origin).toBe("hpi");
    expect(metadata).not.toHaveProperty("source_url");
  });

  it("shows the service's reason when it refuses the names", async () => {
    respond({ detail: "Reference 'tiny_ref' has no reference.json" }, 422);
    openForm();
    await choose();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Reference 'tiny_ref' has no reference.json"
    );
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("checks the folder again when the service says it changed since its check", async () => {
    respond(
      { detail: "s3://lab-data/tinygex/ changed since it was checked; check it again, then start the run" },
      409
    );
    openForm();
    await choose();
    const checks = () =>
      fetchSpy.mock.calls.filter(([url]) => url === "/api/scrna/cellranger/folder-check").length;
    const before = checks();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    expect((await screen.findByRole("alert")).textContent).toContain("changed since it was checked");
    await waitFor(() => expect(checks()).toBe(before + 1));
  });

  it("falls back to a fixed message when there's no detail", async () => {
    respond({ detail: null }, 503);
    openForm();
    await choose();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The job service isn't available right now."
    );
  });

  it("says so when the service can't be reached", async () => {
    startReply = () => Promise.reject(new TypeError("Failed to fetch"));
    openForm();
    await choose();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not reach the job service."
    );
  });

  it("rejects a success body it doesn't recognise", async () => {
    respond({ run_id: "12" });
    openForm();
    await choose();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The job service returned an unexpected response."
    );
  });

  it("disables the form while the start is in flight", async () => {
    let finish: (r: Response) => void = () => {};
    startReply = () => new Promise<Response>((resolve) => (finish = resolve));
    openForm();
    await choose();
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    const start = screen.getByRole("button", { name: "Starting…" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    // The fieldset disables its fields, which `.disabled` on each one doesn't report.
    expect(screen.getByLabelText("S3 folder URL").matches(":disabled")).toBe(true);
    expect(screen.getByLabelText("Dataset name").matches(":disabled")).toBe(true);
    fireEvent.click(start);
    expect(startCalls()).toHaveLength(1);

    finish(new Response(JSON.stringify({ run_id: 1, sample: "tinygex", reference: "tiny_ref", run_key: "k" }), { status: 201 }));
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
  });
});

describe("after a run is queued", () => {
  async function queueRun() {
    respond({ run_id: 12, sample: "tinygex", reference: "tiny_ref", run_key: "k" });
    openForm();
    await choose();
    type(/Accession or genotype/, "Col-0");
    fireEvent.click(screen.getByLabelText("Public dataset"));
    type("Source link", "https://doi.org/10.1016/x");
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    await screen.findByRole("button", { name: "Start another run" });
  }

  it("replaces the form, so the run can't be started twice", async () => {
    await queueRun();
    expect(screen.getByText("Started by scientist@salk.edu")).toBeTruthy();
    expect(screen.getByRole("link", { name: "View run" }).getAttribute("href")).toBe(
      "/app/timeline/rnaseq/12"
    );
    expect(screen.getByText(/saved with the run/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start run" })).toBeNull();
    expect(screen.queryByLabelText("S3 folder URL")).toBeNull();
    expect(startCalls()).toHaveLength(1);
  });

  it("starts another run with the sample and dataset name cleared and the rest kept", async () => {
    await queueRun();
    fireEvent.click(screen.getByRole("button", { name: "Start another run" }));

    expect((screen.getByLabelText("S3 folder URL") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Dataset name") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Reference genome") as HTMLSelectElement).value).toBe("tiny_ref");
    expect((screen.getByLabelText("Species") as HTMLSelectElement).value).toBe("1");
    expect((screen.getByLabelText(/Accession or genotype/) as HTMLInputElement).value).toBe("Col-0");
    expect((screen.getByLabelText("Public dataset") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Source link") as HTMLInputElement).value).toBe(
      "https://doi.org/10.1016/x"
    );
    expect(
      (screen.getByRole("button", { name: "Start run" }) as HTMLButtonElement).disabled
    ).toBe(true);
  });

  it("opens on a fresh form after Close", async () => {
    await queueRun();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit scRNA job" }));
    expect(screen.queryByRole("button", { name: "Start another run" })).toBeNull();
    expect((screen.getByLabelText("S3 folder URL") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Reference genome") as HTMLSelectElement).value).toBe("tiny_ref");
  });
});

describe("a species added from the form", () => {
  it("is still listed and selected after the form is closed and reopened", async () => {
    vi.mocked(addSpecies).mockResolvedValue({
      kind: "added",
      option: { id: 9, label: "Maize (Zea mays)" },
    });
    openForm();
    type("Species", "add-new");
    type("Genus", "Zea");
    type("Species name", "mays");
    type("Common name", "Maize");
    fireEvent.click(screen.getByRole("button", { name: "Add species" }));
    await screen.findByText("Added Maize (Zea mays).");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit scRNA job" }));
    const select = screen.getByLabelText("Species") as HTMLSelectElement;
    expect(select.value).toBe("9");
    expect(screen.getAllByRole("option", { name: "Maize (Zea mays)" })).toHaveLength(1);
  });
});
