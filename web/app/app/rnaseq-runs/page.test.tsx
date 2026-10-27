// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({}),
  getUser: async () => ({ id: "me" }),
}));
vi.mock("@/lib/rnaseq-runs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/rnaseq-runs")>()),
  fetchRuns: async () => [],
}));
vi.mock("./species-labels", () => ({ speciesLabelMap: async () => ({}) }));
vi.mock("./RnaseqRunsLive", () => ({ default: () => <p>Runs list</p> }));

import RnaseqRunsPage from "./page";

afterEach(cleanup);

describe("the RNA-seq pipeline runs page", () => {
  it("is a page of its own, titled like Cylinder pipeline runs", async () => {
    render(await RnaseqRunsPage());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("RNA-seq pipeline runs");
    expect(screen.getByText("Runs list")).toBeTruthy();
  });
});
