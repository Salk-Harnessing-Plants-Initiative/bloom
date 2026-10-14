// @vitest-environment jsdom
/** A run's Cell Ranger report page: the report in a sandboxed frame. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const state = vi.hoisted(() => ({
  report: null as string | null,
  error: { message: "Object not found", statusCode: "404" } as { message: string; statusCode?: string },
}));
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({
    storage: {
      from: () => ({
        download: async () =>
          state.report === null
            ? { data: null, error: state.error }
            : { data: { text: async () => state.report as string }, error: null },
      }),
    },
  }),
}));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("not found");
  },
}));

import RnaseqRunReportPage from "./page";

afterEach(() => {
  cleanup();
  state.report = null;
  state.error = { message: "Object not found", statusCode: "404" };
});

async function show(runId = "8") {
  render(await RnaseqRunReportPage({ params: Promise.resolve({ runId }) }));
}

describe("a run's Cell Ranger report page", () => {
  it("shows the report inline in a frame whose scripts can't reach Bloom", async () => {
    state.report = "<html><head><title>r</title></head><script>draw()</script>cells</html>";
    await show();
    const frame = screen.getByTitle("Cell Ranger report for run 8") as HTMLIFrameElement;
    const doc = frame.getAttribute("srcdoc") ?? "";
    expect(doc).toContain("<script>draw()</script>cells");
    // Scripts may run to draw the plots, and links open in a new tab, but never as Bloom's own origin.
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-popups allow-popups-to-escape-sandbox");
    expect(frame.getAttribute("src")).toBeNull();
    // And its scripts can't send anything over the network.
    expect(doc).toMatch(/<head><meta http-equiv="Content-Security-Policy" content="default-src 'none';/);
  });

  it("offers no download, only the way back to the run", async () => {
    state.report = "<html></html>";
    await show();
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(["← Run 8"]);
  });

  it("says so when the run stored no report, with no frame", async () => {
    await show();
    expect(screen.getByText("No Cell Ranger report was stored for this run.")).toBeTruthy();
    expect(screen.queryByTitle(/Cell Ranger report for run/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says the report couldn't be loaded when storage fails, not that there is none", async () => {
    state.error = { message: "gateway timeout" };
    await show();
    expect(screen.getByRole("alert").textContent).toBe("Couldn't load the report: gateway timeout");
    expect(screen.queryByText("No Cell Ranger report was stored for this run.")).toBeNull();
    expect(screen.queryByTitle(/Cell Ranger report for run/)).toBeNull();
  });

  it("links back to the run, and is not found for a bad run id", async () => {
    await show();
    expect(screen.getByRole("link", { name: "← Run 8" }).getAttribute("href")).toBe("/app/rnaseq-runs/8");
    await expect(RnaseqRunReportPage({ params: Promise.resolve({ runId: "x" }) })).rejects.toThrow("not found");
  });
});
