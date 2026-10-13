// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const species = vi.hoisted(() => ({ value: null as unknown }));
// The RNA-seq runs the page may look up, and the ids it asked for.
const runs = vi.hoisted(() => ({ rows: [] as { id: number; metadata: unknown }[], failed: false, asked: [] as number[][] }));
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({
    from: (table: string) =>
      table === "rnaseq_runs"
        ? {
            select: () => ({
              in: async (_column: string, ids: number[]) => {
                runs.asked.push(ids);
                return runs.failed
                  ? { data: null, error: { message: "denied" } }
                  : { data: runs.rows.filter((r) => ids.includes(r.id)), error: null };
              },
            }),
          }
        : {
            select: () => ({ eq: () => ({ single: async () => ({ data: species.value, error: null }) }) }),
          },
  }),
  getUser: async () => null,
}));
vi.mock("mixpanel", () => ({ default: { init: vi.fn() } }));
vi.mock("@/components/illustration", () => ({ default: () => null }));

import Species from "./page";

afterEach(() => {
  cleanup();
  runs.rows = [];
  runs.failed = false;
  runs.asked = [];
});

const dataset = (
  id: number,
  source_checksum: string | null,
  ingested_at: string | null,
  metadata: object | null = {},
) => ({
  id, name: `dataset ${id}`, kind: "full", assembly: null, annotation: null, strain: null,
  people: null, metadata, source_checksum, ingested_at,
});

const done = "2026-10-05T00:00:00Z";

async function renderSpecies(origin?: string) {
  species.value = {
    id: 2, common_name: "cress", genus: "a", species: "b", illustration_path: null,
    scrna_datasets: [
      dataset(1, "abc", done, { source: { origin: "public", rnaseq_run_id: 8 } }),
      dataset(2, "def", done, { source: { origin: "hpi" } }),
      dataset(3, null, null, null),
    ],
  };
  render(
    await Species({
      params: Promise.resolve({ speciesId: 2 }),
      searchParams: Promise.resolve(origin ? { origin } : {}),
    }),
  );
}

function datasetLinks() {
  return screen
    .getAllByRole("link")
    .filter((a) => /^\/app\/expression\/2\/\d+$/.test(a.getAttribute("href") ?? ""));
}

describe("the species dataset list", () => {
  it("badges only the dataset whose upload has not finished", async () => {
    species.value = {
      id: 2, common_name: "cress", genus: "a", species: "b", illustration_path: null,
      scrna_datasets: [
        dataset(1, "abc", "2026-10-05T00:00:00Z"),
        dataset(2, "def", null),
        dataset(3, null, null),
      ],
    };
    render(await Species({ params: Promise.resolve({ speciesId: 2 }), searchParams: Promise.resolve({}) }));
    const badges = screen.getAllByText("Incomplete upload");
    expect(badges).toHaveLength(1);
    expect(badges[0].closest("a")!.getAttribute("href")).toBe("/app/expression/2/2");
  });

  it("pills each dataset HPI or Public, counting one with no recorded origin as HPI", async () => {
    await renderSpecies();
    const filter = screen.getByRole("navigation", { name: "Filter datasets by origin" });
    expect(within(filter).getByRole("link", { current: "page" }).textContent).toBe("All 3");
    const pills = datasetLinks().map((a) => within(a).getByText(/^(HPI|Public)$/).textContent);
    expect(pills).toEqual(["Public", "HPI", "HPI"]);
  });

  it("filters to one origin, with each choice's count", async () => {
    await renderSpecies("public");
    expect(datasetLinks().map((a) => a.getAttribute("href"))).toEqual(["/app/expression/2/1"]);
    const filter = screen.getByRole("navigation", { name: "Filter datasets by origin" });
    const choices = within(filter).getAllByRole("link");
    expect(choices.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["All 3", "/app/expression/2"],
      ["HPI 2", "/app/expression/2?origin=hpi"],
      ["Public 1", "/app/expression/2?origin=public"],
    ]);
    expect(within(filter).getByRole("link", { current: "page" }).textContent).toBe("Public 1");
  });

  it("shows HPI's datasets, and all of them for an unknown filter", async () => {
    await renderSpecies("hpi");
    expect(datasetLinks()).toHaveLength(2);
    cleanup();
    await renderSpecies("nonsense");
    expect(datasetLinks()).toHaveLength(3);
  });

  it("says so when no dataset has the chosen origin", async () => {
    species.value = {
      id: 2, common_name: "cress", genus: "a", species: "b", illustration_path: null,
      scrna_datasets: [dataset(2, "def", done)],
    };
    render(
      await Species({
        params: Promise.resolve({ speciesId: 2 }),
        searchParams: Promise.resolve({ origin: "public" }),
      }),
    );
    expect(screen.getByText("No Public datasets for this species.")).toBeTruthy();
    // The filter stays, so the other datasets are one click away.
    const filter = screen.getByRole("navigation", { name: "Filter datasets by origin" });
    expect(within(filter).getByRole("link", { name: "All 1" }).getAttribute("href")).toBe("/app/expression/2");
  });

  describe("a pipeline dataset whose origin isn't recorded on it yet", () => {
    // Loading for run 8: the run is linked, and its origin copied, only once the load finishes.
    const loading = (metadata: object = { rnaseq_run_id: 8 }) => ({
      id: 4, common_name: "cress", genus: "a", species: "b", illustration_path: null,
      scrna_datasets: [dataset(4, "abc", null, metadata), dataset(2, "def", done, { source: { origin: "hpi" } })],
    });

    async function renderLoading(metadata?: object, origin?: string) {
      species.value = loading(metadata);
      render(
        await Species({
          params: Promise.resolve({ speciesId: 4 }),
          searchParams: Promise.resolve(origin ? { origin } : {}),
        }),
      );
    }

    const pillOf = (id: number) =>
      within(screen.getAllByRole("link").find((a) => a.getAttribute("href") === `/app/expression/4/${id}`)!)
        .getByText(/^(HPI|Public)$/).textContent;

    it("takes its run's origin, so a public import isn't shown as HPI while it loads", async () => {
      runs.rows = [{ id: 8, metadata: { origin: "public" } }];
      await renderLoading(undefined, "public");
      expect(pillOf(4)).toBe("Public");
      expect(screen.getByRole("link", { name: "Public 1" })).toBeTruthy();
      expect(runs.asked).toEqual([[8]]);
    });

    it("keeps the origin recorded on the dataset over its run's", async () => {
      runs.rows = [{ id: 8, metadata: { origin: "public" } }];
      await renderLoading({ rnaseq_run_id: 8, source: { origin: "hpi" } });
      expect(pillOf(4)).toBe("HPI");
      expect(runs.asked).toEqual([]);
    });

    it.each([
      ["the run can't be read", () => (runs.failed = true)],
      ["the run gave no origin", () => (runs.rows = [{ id: 8, metadata: {} }])],
      ["the run isn't found", () => (runs.rows = [])],
    ])("is HPI when %s", async (_why, setup) => {
      setup();
      await renderLoading();
      expect(pillOf(4)).toBe("HPI");
    });

    it("asks for no run when every dataset has its origin recorded", async () => {
      await renderSpecies();
      expect(runs.asked).toEqual([]);
    });
  });

  it("counts and lists neither reference atlases nor the placeholder dataset", async () => {
    species.value = {
      id: 2, common_name: "cress", genus: "a", species: "b", illustration_path: null,
      scrna_datasets: [
        dataset(1, "abc", done, { source: { origin: "public" } }),
        { ...dataset(5, "ref", done, { source: { origin: "public" } }), kind: "reference" },
        { ...dataset(6, null, null), name: "NULL_DATASET" },
      ],
    };
    render(await Species({ params: Promise.resolve({ speciesId: 2 }), searchParams: Promise.resolve({}) }));
    const filter = screen.getByRole("navigation", { name: "Filter datasets by origin" });
    expect(within(filter).getAllByRole("link").map((a) => a.textContent)).toEqual(["All 1", "HPI 0", "Public 1"]);
    expect(datasetLinks().map((a) => a.getAttribute("href"))).toEqual(["/app/expression/2/1"]);
  });
});
