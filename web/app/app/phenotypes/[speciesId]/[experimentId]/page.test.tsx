// @vitest-environment jsdom
/**
 * The experiment page mounts its pipeline-runs panel (add-cyl-pipeline-ui
 * task 8.2) and offers "Run experiment", plus "Run wave" per wave when there
 * is more than one (task 11.5).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const panel = vi.hoisted(() => ({ props: null as Record<string, unknown> | null }));
const db = vi.hoisted(() => ({ waves: [] as { id: number; number: number; name: null; experiment_id: number; cyl_plants: [] }[] }));
const buttons = vi.hoisted(() => ({ props: [] as Record<string, unknown>[] }));

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
              cyl_waves: db.waves,
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
vi.mock("@/components/cyl-pipeline/RunPipelineButton", () => ({
  RunPipelineButton: (props: { label: string; target: unknown }) => {
    buttons.props.push(props);
    return <button data-target={JSON.stringify(props.target)}>{props.label}</button>;
  },
}));

import Experiment from "./page";

afterEach(() => {
  cleanup();
  panel.props = null;
  buttons.props = [];
  db.waves = [wave(11, 1)];
});

function wave(id: number, number: number) {
  return { id, number, name: null, experiment_id: 5, cyl_plants: [] as [] };
}
db.waves = [wave(11, 1)];

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

  it("offers Run experiment, and no Run wave for a single-wave experiment", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    render(await page("5"));
    const run = screen.getByRole("button", { name: "Run experiment" });
    expect(JSON.parse(run.dataset.target!)).toEqual({ target_level: "experiment", target_id: 5 });
    expect(screen.queryByRole("button", { name: "Run wave" })).toBeNull();
  });

  it("offers Run wave in each wave's title row when there is more than one wave", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    db.waves = [wave(12, 2), wave(11, 1)];
    render(await page("5"));
    const waves = screen.getAllByRole("button", { name: "Run wave" });
    expect(waves.map((b) => JSON.parse(b.dataset.target!))).toEqual([
      { target_level: "wave", target_id: 11 },
      { target_level: "wave", target_id: 12 },
    ]);
    expect(waves[0].closest(".table-row")!.textContent).toContain("Wave 1");
    expect(screen.getAllByRole("button", { name: "Run experiment" })).toHaveLength(1);
  });

  it("offers no run action for an id that isn't one", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    render(await page("abc"));
    expect(screen.queryByRole("button", { name: /^Run / })).toBeNull();
  });
});
