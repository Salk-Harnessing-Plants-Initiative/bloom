// @vitest-environment jsdom
/** A run's Cell Ranger report page: the report in a sandboxed frame, and a download link. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const state = vi.hoisted(() => ({ report: null as string | null, signed: [] as unknown[] }));
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({
    storage: {
      from: () => ({
        download: async () =>
          state.report === null ? { data: null, error: { message: "not found" } } : { data: { text: async () => state.report as string }, error: null },
        createSignedUrl: async (...args: unknown[]) => {
          state.signed.push(args);
          return { data: { signedUrl: "https://bloom.test/signed/web_summary.html" }, error: null };
        },
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
  state.signed = [];
});

async function show(runId = "8") {
  render(await RnaseqRunReportPage({ params: Promise.resolve({ runId }) }));
}

describe("a run's Cell Ranger report page", () => {
  it("shows the report inline in a frame whose scripts can't reach Bloom", async () => {
    state.report = "<html><script>draw()</script>cells</html>";
    await show();
    const frame = screen.getByTitle("Cell Ranger report for run 8") as HTMLIFrameElement;
    expect(frame.getAttribute("srcdoc")).toBe(state.report);
    // Scripts may run to draw the plots, but never as Bloom's own origin.
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("src")).toBeNull();
  });

  it("offers a short-lived download of the report", async () => {
    state.report = "<html></html>";
    await show();
    const link = screen.getByRole("link", { name: "Download the report" });
    expect(link.getAttribute("href")).toBe("https://bloom.test/signed/web_summary.html");
    expect(state.signed).toEqual([["reports/8/web_summary.html", 600, { download: "run-8-web_summary.html" }]]);
  });

  it("says so when the run stored no report, with no frame or download", async () => {
    state.report = null;
    await show();
    expect(screen.getByText("No Cell Ranger report was stored for this run.")).toBeTruthy();
    expect(screen.queryByTitle(/Cell Ranger report for run/)).toBeNull();
    expect(screen.queryByRole("link", { name: "Download the report" })).toBeNull();
  });

  it("links back to the run, and is not found for a bad run id", async () => {
    state.report = null;
    await show();
    expect(screen.getByRole("link", { name: "← Run 8" }).getAttribute("href")).toBe("/app/rnaseq-runs/8");
    await expect(RnaseqRunReportPage({ params: Promise.resolve({ runId: "x" }) })).rejects.toThrow("not found");
  });
});
