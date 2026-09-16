// @vitest-environment jsdom
/**
 * The volcano plot redraws at its visible width. Drawn while its tab is hidden,
 * it has no width to measure; when the tab is shown, or the window resized, it
 * must redraw at the width it now has, and a hidden tab's zero width is ignored.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { zoomIdentity, type ZoomTransform } from "d3";

import Panel from "./expression-differential-analysis";

const COMPARISON = {
  id: 1, cluster_id: "Cortex", contrast: "pFACT_vs_Col-0", group1: "pFACT", group2: "Col-0",
  n_group1: 120, n_group2: 80, n_genes_tested: 1, tested: true,
};
const RUN = { id: 5, method: "scanpy-wilcoxon", completed_at: "2026-09-11T00:00:00Z", params: {} };

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    from: (table: string) => {
      const filters: Record<string, unknown> = {};
      let start = 0;
      const query = {
        select: () => query,
        eq: (column: string, value: unknown) => { filters[column] = value; return query; },
        in: () => { filters.summary = true; return query; },
        lt: () => query,
        or: () => query,
        order: () => query,
        limit: () => query,
        range: (from: number) => { start = from; return query; },
        abortSignal: () => query,
        then: (resolve: (v: unknown) => unknown, reject?: (e: unknown) => unknown) => {
          const answer = (() => {
            if (table === "scrna_de_runs") return { data: [RUN], error: null };
            if (table === "scrna_de") return { data: [COMPARISON], error: null };
            if (filters.summary || start > 0) return { data: [], error: null };
            return {
              data: [{ log2fc: 2, pvalue: 0.001, fdr: 0.01, pct_1: 0.5, pct_2: 0.1,
                       scrna_genes: { gene_name: "AT1G01010" } }],
              error: null,
            };
          })();
          return Promise.resolve(answer).then(resolve, reject);
        },
      };
      return query;
    },
  }),
}));

type Watcher = (entries: unknown[]) => void;

/** The width jsdom reports for the plot, and the size watchers to tell of a change. */
let plotWidth = 0;
let watchers: Watcher[] = [];

beforeEach(() => {
  plotWidth = 0;
  watchers = [];
  Object.defineProperty(SVGElement.prototype, "clientWidth", {
    configurable: true,
    get: () => plotWidth,
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      private readonly callback: Watcher;
      constructor(callback: Watcher) {
        this.callback = callback;
      }
      observe() {
        watchers.push(this.callback);
      }
      unobserve() {}
      disconnect() {
        watchers = watchers.filter((w) => w !== this.callback);
      }
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete (SVGElement.prototype as { clientWidth?: number }).clientWidth;
});

const plot = () => document.querySelector("svg[width]") as SVGSVGElement | null;

/** Tells the plot its width is now `width`, as a real observer would. */
function resizeTo(width: number) {
  plotWidth = width;
  act(() => watchers.forEach((w) => w([])));
}

async function drawnAt(width: number) {
  render(<Panel file_id={1} />);
  await screen.findAllByText("AT1G01010");
  await waitFor(() => expect(plot()).not.toBeNull());
  resizeTo(width);
  await waitFor(() => expect(plot()!.getAttribute("width")).toBe(String(width)));
}

describe("the volcano plot", () => {
  it("redraws at the width it has once it can be measured", async () => {
    render(<Panel file_id={1} />);
    await screen.findAllByText("AT1G01010");
    await waitFor(() => expect(plot()).not.toBeNull());
    const hiddenWidth = plot()!.getAttribute("width");

    resizeTo(720);
    await waitFor(() => expect(plot()!.getAttribute("width")).toBe("720"));
    expect(hiddenWidth).not.toBe("720");
  });

  it("ignores the zero width it reports while its tab is hidden", async () => {
    await drawnAt(720);
    resizeTo(0);
    await act(async () => {});
    expect(plot()!.getAttribute("width")).toBe("720");
  });

  it("starts a redraw from the unzoomed view, so the next scroll cannot snap back", async () => {
    await drawnAt(720);
    const node = plot()! as unknown as { __zoom: ZoomTransform };
    node.__zoom = zoomIdentity.scale(3).translate(40, 40);

    resizeTo(800);
    await waitFor(() => expect(plot()!.getAttribute("width")).toBe("800"));
    expect(node.__zoom.k).toBe(1);
    expect([node.__zoom.x, node.__zoom.y]).toEqual([0, 0]);
  });
});
