// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const species = vi.hoisted(() => ({ value: null as unknown }));
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({
    from: () => ({
      select: () => ({ eq: () => ({ single: async () => ({ data: species.value, error: null }) }) }),
    }),
  }),
  getUser: async () => null,
}));
vi.mock("mixpanel", () => ({ default: { init: vi.fn() } }));
vi.mock("@/components/illustration", () => ({ default: () => null }));

import Species from "./page";

afterEach(cleanup);

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
    render(await Species({ params: Promise.resolve({ speciesId: 2 }) }));
    const badges = screen.getAllByText("Incomplete upload");
    expect(badges).toHaveLength(1);
    expect(badges[0].closest("a")!.getAttribute("href")).toBe("/app/expression/2/2");
  });

  it("pills each dataset HPI or Public, counting one with no recorded origin as HPI", async () => {
    await renderSpecies();
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
    expect(screen.getByText("No public datasets for this species.")).toBeTruthy();
  });
});
