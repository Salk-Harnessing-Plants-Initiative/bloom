// @vitest-environment jsdom
/**
 * The cockpit keeps a tab on the page once it has been opened, hidden while
 * another is shown, so leaving a tab and coming back keeps its choices and
 * reads nothing again.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/** How many times each tab's body has been put on the page. */
const mounts = vi.hoisted(() => ({ report: 0, umap: 0, genes: 0, de: 0, go: 0 }));

vi.mock("@/components/expression-mapping-report", async () => {
  const { useEffect } = await import("react");
  return {
    ExpressionMappingReport: ({ runId }: { runId: number }) => {
      useEffect(() => void mounts.report++, []);
      return <div data-testid="report-body">run {runId}</div>;
    },
  };
});

vi.mock("@/components/expression-view", async () => {
  const { useEffect } = await import("react");
  return {
    ExpressionView: () => {
      useEffect(() => void mounts.umap++, []);
      return <div data-testid="umap-body" />;
    },
  };
});

vi.mock("@/components/expression-genes-by-cell-type", async () => {
  const { useEffect } = await import("react");
  return {
    ExpressionGenesByCellType: () => {
      useEffect(() => void mounts.genes++, []);
      return <div data-testid="genes-body" />;
    },
  };
});

vi.mock("@/components/expression-differential-analysis", async () => {
  const { useEffect } = await import("react");
  return {
    default: () => {
      useEffect(() => void mounts.de++, []);
      return <div data-testid="de-body" />;
    },
  };
});

vi.mock("@/components/expression-go-pathways", async () => {
  const { useEffect } = await import("react");
  return {
    ExpressionGoPathways: () => {
      useEffect(() => void mounts.go++, []);
      return <div data-testid="go-body" />;
    },
  };
});

import { ExpressionCockpit } from "./expression-cockpit";

beforeEach(() => {
  mounts.report = 0;
  mounts.umap = 0;
  mounts.genes = 0;
  mounts.de = 0;
  mounts.go = 0;
});
afterEach(cleanup);

const tab = (name: string) => fireEvent.click(screen.getByRole("button", { name }));
const isShown = (testId: string) => !screen.getByTestId(testId).closest("[hidden]");

describe("ExpressionCockpit", () => {
  it("opens on the map and puts no other tab on the page until it is chosen", () => {
    render(<ExpressionCockpit datasetId={1} datasetName="ds" />);
    expect(isShown("umap-body")).toBe(true);
    expect(screen.queryByTestId("genes-body")).toBeNull();
    expect(screen.queryByTestId("de-body")).toBeNull();
    expect(screen.queryByTestId("go-body")).toBeNull();
  });

  it("opens on the tab a link names, and on the map for an unknown one", () => {
    render(<ExpressionCockpit datasetId={1} datasetName="ds" initialTab="go" />);
    expect(isShown("go-body")).toBe(true);
    expect(screen.queryByTestId("umap-body")).toBeNull();
    cleanup();
    render(<ExpressionCockpit datasetId={1} datasetName="ds" initialTab="nope" />);
    expect(isShown("umap-body")).toBe(true);
  });

  it("keeps a tab and everything in it when you leave it and come back", () => {
    render(<ExpressionCockpit datasetId={1} datasetName="ds" />);
    tab("Genes by cell type");
    tab("Differential expression");
    tab("GO & pathways");
    tab("Genes by cell type");
    tab("GO & pathways");
    tab("UMAP");

    expect(mounts).toEqual({ report: 0, umap: 1, genes: 1, de: 1, go: 1 });
    expect(isShown("umap-body")).toBe(true);
    expect(isShown("genes-body")).toBe(false);
    expect(isShown("de-body")).toBe(false);
    expect(isShown("go-body")).toBe(false);
  });
});

describe("ExpressionCockpit's Mapping report tab", () => {
  const tabNames = () => screen.getAllByRole("button").map((b) => b.textContent);

  it("comes first, before UMAP, when the dataset's run stored a report", () => {
    render(<ExpressionCockpit datasetId={1} datasetName="ds" reportRunId={4} />);
    expect(tabNames()).toEqual([
      "Mapping report", "UMAP", "Genes by cell type", "Differential expression", "GO & pathways",
    ]);
  });

  it("isn't there without a report, even when a link asks for it", () => {
    render(<ExpressionCockpit datasetId={1} datasetName="ds" initialTab="report" />);
    expect(tabNames()).not.toContain("Mapping report");
    expect(isShown("umap-body")).toBe(true);
  });

  it("still opens on UMAP, and reads the report only once its tab is chosen", () => {
    render(<ExpressionCockpit datasetId={1} datasetName="ds" reportRunId={4} />);
    expect(isShown("umap-body")).toBe(true);
    expect(screen.queryByTestId("report-body")).toBeNull();
    tab("Mapping report");
    expect(isShown("report-body")).toBe(true);
    expect(screen.getByTestId("report-body").textContent).toBe("run 4");
    tab("UMAP");
    tab("Mapping report");
    expect(mounts.report).toBe(1);
  });

  it("opens on the report when a link names it", () => {
    render(<ExpressionCockpit datasetId={1} datasetName="ds" reportRunId={4} initialTab="report" />);
    expect(isShown("report-body")).toBe(true);
    expect(screen.queryByTestId("umap-body")).toBeNull();
  });
});
