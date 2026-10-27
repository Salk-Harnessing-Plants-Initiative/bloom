// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const db = vi.hoisted(() => ({
  dataset: null as Record<string, unknown> | null,
  calls: [] as { table: string; cols: string }[],
}));

// A query builder that records what was asked and answers the dataset query with db.dataset.
function builder(table: string, cols: string) {
  db.calls.push({ table, cols });
  const result = () =>
    table === "scrna_datasets" && cols.includes("source_checksum")
      ? { data: db.dataset, error: null }
      : { data: [], error: null };
  const query: Record<string, unknown> = {};
  for (const method of ["eq", "is", "neq", "order", "limit"]) query[method] = () => query;
  query.single = async () => result();
  query.then = (resolve: (v: unknown) => unknown, reject: (e: unknown) => unknown) =>
    Promise.resolve(result()).then(resolve, reject);
  return query;
}

vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({
    from: (table: string) => ({ select: (cols: string) => builder(table, cols) }),
  }),
}));
vi.mock("@/components/illustration", () => ({ default: () => null }));

import Banner from "./expression-dataset-banner";

const BASE = {
  id: 1, name: "MYB41", n_cells: null, assembly: null, annotation: null, strain: null,
  species_id: 2, species: null, people: null, metadata: null,
};

beforeEach(() => {
  db.calls = [];
});
afterEach(cleanup);

async function renderBanner(row: Record<string, unknown>) {
  db.dataset = { ...BASE, ...row };
  render(await Banner({ datasetId: 1, speciesId: 2 }));
}

describe("the dataset banner's incomplete-upload notice", () => {
  it("reads the two columns the rule needs", async () => {
    await renderBanner({ source_checksum: null, ingested_at: null });
    const columns = db.calls.find((c) => c.table === "scrna_datasets")!.cols;
    expect(columns).toMatch(/\bsource_checksum\b/);
    expect(columns).toMatch(/\bingested_at\b/);
  });

  it("is shown for an upload that has not finished", async () => {
    await renderBanner({ source_checksum: "abc", ingested_at: null });
    expect(screen.getByRole("note").textContent).toContain("incomplete or is still being uploaded");
  });

  it("is not shown for a finished upload", async () => {
    await renderBanner({ source_checksum: "abc", ingested_at: "2026-10-05T00:00:00Z" });
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("is not shown for a dataset from a loader that records neither", async () => {
    await renderBanner({ source_checksum: null, ingested_at: null });
    expect(screen.queryByRole("note")).toBeNull();
  });
});
