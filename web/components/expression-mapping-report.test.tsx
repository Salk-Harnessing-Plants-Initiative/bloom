// @vitest-environment jsdom
/**
 * The Mapping report tab reads the run's Cell Ranger report when it opens and shows it in a
 * sandboxed frame without network access, or says why it can't.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const read = vi.hoisted(() => ({ result: null as unknown, calls: [] as unknown[][] }));

vi.mock("@/lib/supabase/client", () => ({ createClientSupabaseClient: () => "browser-client" }));
vi.mock("@/lib/rnaseq-reports", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/rnaseq-reports")>()),
  readReport: async (...args: unknown[]) => {
    read.calls.push(args);
    if (read.result instanceof Error) throw read.result;
    return read.result;
  },
}));

import { ExpressionMappingReport } from "./expression-mapping-report";

afterEach(() => {
  cleanup();
  read.calls = [];
});

describe("ExpressionMappingReport", () => {
  it("shows the run's web summary in a sandboxed frame that can't reach the network", async () => {
    read.result = { text: "<html><head></head><body>Mapped 92.5%</body></html>" };
    render(<ExpressionMappingReport runId={4} />);
    const frame = await screen.findByTitle("Cell Ranger report for run 4");
    expect(read.calls).toEqual([["browser-client", 4, "web_summary.html"]]);
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-popups allow-popups-to-escape-sandbox");
    const doc = frame.getAttribute("srcdoc") ?? "";
    expect(doc).toContain("Content-Security-Policy");
    expect(doc).toContain("default-src 'none'");
    expect(doc).toContain("Mapped 92.5%");
  });

  it("links to the report's own page", () => {
    read.result = { text: "<html></html>" };
    render(<ExpressionMappingReport runId={4} />);
    expect(screen.getByRole("link", { name: "Open on its own page" }).getAttribute("href"))
      .toBe("/app/rnaseq-runs/4/report");
  });

  it("says why when the report can't be read", async () => {
    read.result = { error: "storage error" };
    render(<ExpressionMappingReport runId={4} />);
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't load the report: storage error");
  });

  it("says why when reading the report throws", async () => {
    read.result = new Error("network down");
    render(<ExpressionMappingReport runId={4} />);
    expect((await screen.findByRole("alert")).textContent).toBe("Couldn't load the report: network down");
  });

  it("says when the run stored no report", async () => {
    read.result = { missing: true };
    render(<ExpressionMappingReport runId={4} />);
    expect(await screen.findByText("No Cell Ranger report was stored for this run.")).toBeTruthy();
  });
});
