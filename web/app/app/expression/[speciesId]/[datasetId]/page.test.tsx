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
});
