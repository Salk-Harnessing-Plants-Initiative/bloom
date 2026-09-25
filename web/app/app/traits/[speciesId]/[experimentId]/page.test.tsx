// @vitest-environment jsdom
/** The traits page passes a pipeline run's `?wave=&age=` through to the explorer, parsed. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

const explorerProps = vi.hoisted(() => ({ last: null as Record<string, unknown> | null }));

vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({
    from: (table: string) => ({
      select: () => ({
        eq: () => ({
          single: async () => ({
            data:
              table === "cyl_experiments"
                ? { id: 5, name: "exp", species: { common_name: "pennycress" }, people: null }
                : null,
          }),
          order: async () => ({ data: [{ trait_name: "trait_a" }] }),
        }),
      }),
    }),
  }),
  getUser: async () => null,
}));
vi.mock("mixpanel", () => ({ default: { init: vi.fn() } }));
vi.mock("@/components/scientist-badge", () => ({ default: () => null }));
vi.mock("./TraitExplorer", () => ({
  default: (props: Record<string, unknown>) => {
    explorerProps.last = props;
    return <div data-testid="explorer" />;
  },
}));

import Experiment from "./page";

afterEach(() => {
  cleanup();
  explorerProps.last = null;
});

async function renderPage(searchParams: Record<string, string | string[] | undefined>) {
  render(
    await Experiment({
      params: Promise.resolve({ speciesId: "2", experimentId: "5" }),
      searchParams: Promise.resolve(searchParams),
    }),
  );
}

describe("the traits page", () => {
  it("passes a valid wave and age to the explorer as numbers", async () => {
    await renderPage({ wave: "1", age: "14" });
    expect(explorerProps.last).toMatchObject({ initialWave: 1, initialAge: 14 });
  });

  it("passes nothing for missing or invalid values", async () => {
    await renderPage({ wave: ["1", "2"], age: "abc" });
    expect(explorerProps.last?.initialWave).toBeUndefined();
    expect(explorerProps.last?.initialAge).toBeUndefined();
  });
});
