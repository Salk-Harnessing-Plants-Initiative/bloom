// @vitest-environment jsdom
/** The runs list page renders its server snapshot, and says so when it can't. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { mockClient, queriesFor, resetSupabaseMock, supabaseMock } from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";
import { at, ME, runRow } from "@/lib/cyl-pipeline/__fixtures__/rows";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => mockClient,
  getUser: async () => ({ id: "4965b3af-ccfe-40f5-814b-447e1f726e1b" }),
}));

import PipelineRunsPage from "./page";

beforeEach(() => resetSupabaseMock());
afterEach(cleanup);

describe("the runs list page", () => {
  it("renders the newest runs with their experiments, attributed to the signed-in user", async () => {
    supabaseMock.respond = (q) =>
      q.table === "cyl_pipeline_runs"
        ? { data: [runRow(92, at(2)), runRow(91, at(1), { requested_by: null })], error: null }
        : { data: [{ run_id: 92, experiment_id: 5, created_at: at(2), cyl_experiments: { name: "exp-five", species_id: 2 } }], error: null };
    render(await PipelineRunsPage());
    expect(screen.getByRole("heading", { name: "Pipeline runs" })).toBeTruthy();
    expect(screen.getByTestId("run-92").textContent).toContain("you");
    expect(screen.getByTestId("run-92").textContent).toContain("exp-five");
    expect(screen.getByTestId("run-91")).toBeTruthy();
    expect(queriesFor("cyl_pipeline_run_experiments")[0].arg("in")).toEqual(["run_id", [92, 91]]);
    expect(ME).toBe("4965b3af-ccfe-40f5-814b-447e1f726e1b");
  });

  it("shows the error when the snapshot fails, not an empty list", async () => {
    supabaseMock.respond = () => ({ data: null, error: { message: "connection refused" } });
    render(await PipelineRunsPage());
    expect(screen.getByRole("alert").textContent).toContain("connection refused");
    expect(screen.queryByText("No pipeline runs yet")).toBeNull();
  });

  it("still lists the runs when only the experiment names fail", async () => {
    supabaseMock.respond = (q) =>
      q.table === "cyl_pipeline_runs" ? { data: [runRow(91, at(1))], error: null } : { data: null, error: { message: "view missing" } };
    render(await PipelineRunsPage());
    expect(screen.getByTestId("run-91")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
