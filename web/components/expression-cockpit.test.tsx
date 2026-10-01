// @vitest-environment jsdom
/**
 * The cockpit keeps a tab on the page once it has been opened, hidden while
 * another is shown, so leaving a tab and coming back keeps its choices and
 * reads nothing again.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/** How many times each tab's body has been put on the page. */
const mounts = vi.hoisted(() => ({ umap: 0, genes: 0, de: 0 }));

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

import { ExpressionCockpit } from "./expression-cockpit";

beforeEach(() => {
  mounts.umap = 0;
  mounts.genes = 0;
  mounts.de = 0;
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
  });

  it("keeps a tab and everything in it when you leave it and come back", () => {
    render(<ExpressionCockpit datasetId={1} datasetName="ds" />);
    tab("Genes by cell type");
    tab("Differential expression");
    tab("Genes by cell type");
    tab("UMAP");

    expect(mounts).toEqual({ umap: 1, genes: 1, de: 1 });
    expect(isShown("umap-body")).toBe(true);
    expect(isShown("genes-body")).toBe(false);
    expect(isShown("de-body")).toBe(false);
  });
});
