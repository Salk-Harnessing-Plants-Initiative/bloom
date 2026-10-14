// @vitest-environment jsdom
/** The dataset page tells "no such dataset" apart from "the database failed". */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

/** What the dataset lookup answers in each test. */
const lookup = vi.hoisted(() => ({
  result: { data: null as unknown, error: null as { message: string } | null },
}));

vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({
    from: () => ({
      select: () => ({
        // Only maybeSingle: `.single()` turns "no such dataset" into an error.
        eq: () => ({ maybeSingle: async () => lookup.result }),
      }),
    }),
  }),
  getUser: async () => null,
}));
vi.mock("mixpanel", () => ({ default: { init: vi.fn() } }));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));
vi.mock("@/components/expression-cockpit", () => ({
  ExpressionCockpit: () => <div data-testid="cockpit" />,
}));
vi.mock("@/components/expression-dataset-banner", () => ({ default: () => null }));
vi.mock("@/components/scientist-badge", () => ({ default: () => null }));
// The reports each run stored, and the files read.
const reports = vi.hoisted(() => ({ stored: new Map<number, Map<string, string>>(), asked: [] as number[] }));
vi.mock("@/lib/rnaseq-reports", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/rnaseq-reports")>()),
  storedReports: async (_c: unknown, runId: number) => {
    reports.asked.push(runId);
    return new Set(reports.stored.get(runId)?.keys() ?? []);
  },
  readReport: async (_c: unknown, runId: number, file: string) => reports.stored.get(runId)?.get(file) ?? null,
}));

import Dataset from "./page";

async function renderPage() {
  render(await Dataset({ params: Promise.resolve({ datasetId: "1", speciesId: "2" }) }));
}

beforeEach(() => {
  lookup.result = { data: null, error: null };
});
afterEach(cleanup);

describe("the dataset page", () => {
  it("says the dataset could not be loaded when the database fails, not that it does not exist", async () => {
    lookup.result = { data: null, error: { message: "connection refused" } };
    await renderPage();
    expect(screen.getByText("Could not load this dataset: connection refused")).toBeTruthy();
    expect(screen.queryByText("Dataset not found.")).toBeNull();
  });

  it("says a dataset that does not exist was not found", async () => {
    await renderPage();
    expect(screen.getByText("Dataset not found.")).toBeTruthy();
  });

  it("shows the cockpit for a dataset that exists", async () => {
    lookup.result = {
      data: { id: 1, name: "MYB41", kind: "full", species: { common_name: "thale cress" }, people: null },
      error: null,
    };
    await renderPage();
    expect(screen.getByTestId("cockpit")).toBeTruthy();
  });

  describe("the Cell Ranger QC section", () => {
    const fromRun = (metadata: object | null) => ({
      data: { id: 1, name: "TEST SRA sc_71 root", kind: "full", metadata, people: null, species: { common_name: "Arabidopsis" } },
      error: null,
    });
    const CSV = 'Estimated Number of Cells,Median Genes per Cell,Number of Reads\n"5,066","1,642","137,437,089"\n';

    it("shows the key metrics and the report of the run that made the dataset", async () => {
      reports.stored = new Map([[8, new Map([["web_summary.html", "<html/>"], ["metrics_summary.csv", CSV]])]]);
      lookup.result = fromRun({ rnaseq_run_id: 8 });
      await renderPage();
      const section = screen.getByRole("region", { name: "Quality control · Cell Ranger" });
      expect(section.textContent).toContain("Estimated Number of Cells5,066");
      expect(section.textContent).toContain("Median Genes per Cell1,642");
      expect(section.textContent).not.toContain("Number of Reads");
      const links = Array.from(section.querySelectorAll("a")).map((a) => [a.textContent, a.getAttribute("href")]);
      expect(links).toEqual([
        ["Open the full report", "/app/rnaseq-runs/8/report"],
        ["Run 8", "/app/rnaseq-runs/8"],
      ]);
    });

    it("shows nothing for a dataset no run made, without asking for reports", async () => {
      reports.asked = [];
      lookup.result = fromRun({});
      await renderPage();
      expect(screen.queryByRole("region", { name: "Quality control · Cell Ranger" })).toBeNull();
      expect(reports.asked).toEqual([]);
    });

    it("shows nothing when the run stored no reports", async () => {
      reports.stored = new Map();
      lookup.result = fromRun({ rnaseq_run_id: 9 });
      await renderPage();
      expect(screen.queryByRole("region", { name: "Quality control · Cell Ranger" })).toBeNull();
    });
  });
});
