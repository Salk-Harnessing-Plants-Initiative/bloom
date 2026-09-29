// @vitest-environment jsdom
/** The experiment page mounts its pipeline-runs panel (add-cyl-pipeline-ui task 8.2). */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const panel = vi.hoisted(() => ({ props: null as Record<string, unknown> | null }));

vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({
    from: () => ({
      select: () => ({
        eq: () => ({
          single: async () => ({
            data: {
              id: 5,
              name: "exp-five",
              species: { id: 2, common_name: "pennycress" },
              people: null,
              cyl_waves: [{ id: 11, number: 1, name: null, experiment_id: 5, cyl_plants: [] }],
            },
          }),
        }),
      }),
    }),
  }),
  getUser: async () => null,
}));
vi.mock("mixpanel", () => ({ default: { init: vi.fn() } }));
vi.mock("@/components/scientist-badge", () => ({ default: () => null }));
vi.mock("@/components/experiment-description", () => ({ default: () => null }));
vi.mock("@/components/cyl-pipeline/ExperimentRunsPanel", () => ({
  ExperimentRunsPanel: (props: Record<string, unknown>) => {
    panel.props = props;
    return <div data-testid="runs-panel" />;
  },
}));

import Experiment from "./page";

afterEach(() => {
  cleanup();
  panel.props = null;
});

const page = (experimentId: string) => Experiment({ params: Promise.resolve({ speciesId: "2", experimentId }) });

describe("the experiment page", () => {
  it("gives the runs panel the experiment's id", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    render(await page("5"));
    expect(screen.getByTestId("runs-panel")).toBeTruthy();
    expect(panel.props).toEqual({ experimentId: 5 });
  });

  it("mounts no panel for an id that isn't one", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    render(await page("abc"));
    expect(screen.queryByTestId("runs-panel")).toBeNull();
  });
});
