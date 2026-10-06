// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

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

const dataset = (id: number, source_checksum: string | null, ingested_at: string | null) => ({
  id, name: `dataset ${id}`, kind: "full", assembly: null, annotation: null, strain: null,
  people: null, metadata: {}, source_checksum, ingested_at,
});

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
});
