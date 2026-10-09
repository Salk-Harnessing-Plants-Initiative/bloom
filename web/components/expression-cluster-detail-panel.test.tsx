// @vitest-environment jsdom
/**
 * The panel's three outcomes, and the reset between clusters.
 *
 * It loads one cluster's stats and has to end somewhere every time: the data, an
 * error, or an empty state. A defect landed here in each of the two prior review
 * rounds — once a swallowed error, once a load with no failure branch that left
 * the panel saying "Loading…" forever — and both were caught by reading rather
 * than by a test.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ExpressionClusterDetailPanel } from "@/components/expression-cluster-detail-panel";

const fetchClusterStats = vi.hoisted(() => vi.fn());
const findOneVsRest = vi.hoisted(() =>
  vi.fn<(...args: unknown[]) => Promise<{ deId: number; contrast: string | null } | null>>(
    async () => null,
  ),
);
const fetchDeMarkers = vi.hoisted(() => vi.fn());
vi.mock("@/components/expression-lib/cluster-markers", () => ({
  fetchClusterStats, findOneVsRest, fetchDeMarkers,
}));

const STATS = {
  dataset_id: 1,
  cluster_id: "Phellem",
  cell_count: 164,
  pct: 1.9,
  markers: { top: [{ gene: "AT5G09530", log2fc: 4.15, q: 0.008, pct_1: 0.88, pct_2: 0.38 }], n_significant: 1 },
};

function panel(clusterId = "Phellem") {
  return (
    <ExpressionClusterDetailPanel
      datasetId={1}
      clusterId={clusterId}
      clusterName={null}
      clusterColor="#000"
    />
  );
}

afterEach(() => {
  cleanup();
  fetchClusterStats.mockReset();
  findOneVsRest.mockReset().mockResolvedValue(null);
  fetchDeMarkers.mockReset();
});

describe("ExpressionClusterDetailPanel", () => {
  it("renders the markers once the stats arrive", async () => {
    fetchClusterStats.mockResolvedValue(STATS);
    render(panel());
    expect(screen.getByText("Loading…")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("AT5G09530")).toBeTruthy());
    expect(screen.getByText(/164 cells/)).toBeTruthy();
    // No buttons that do nothing.
    expect(screen.queryByRole("button", { name: "Rename" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Run DE vs. all" })).toBeNull();
  });

  it("says so when the load fails, instead of loading forever", async () => {
    fetchClusterStats.mockRejectedValue(new Error("permission denied"));
    render(panel());
    await waitFor(() =>
      expect(screen.getByText("Could not load this cluster.")).toBeTruthy(),
    );
    expect(screen.queryByText("Loading…")).toBeNull();
    // The header must not read as a cluster that legitimately has no cells.
    expect(screen.getByText(/could not load/)).toBeTruthy();
  });

  it("distinguishes no stats row from a failure", async () => {
    fetchClusterStats.mockResolvedValue(null);
    render(panel());
    await waitFor(() =>
      expect(screen.getByText(/No markers stored for this cell type/)).toBeTruthy(),
    );
    expect(screen.queryByText("Could not load this cluster.")).toBeNull();
  });

  it("counts the significant markers and gives each marker's share in the cell type", async () => {
    fetchClusterStats.mockResolvedValue(STATS);
    render(panel());
    await waitFor(() => expect(screen.getByText("AT5G09530")).toBeTruthy());
    expect(screen.getByText("Significant markers")).toBeTruthy();
    expect(screen.getByText("88%")).toBeTruthy();
  });

  it("shows a marker's symbol beside its gene id", async () => {
    fetchClusterStats.mockResolvedValue({
      ...STATS,
      markers: { top: [{ ...STATS.markers.top[0], symbol: "PELPK1" }], n_significant: 1 },
    });
    render(panel());
    await waitFor(() => expect(screen.getByText("PELPK1")).toBeTruthy());
    expect(screen.getByText("AT5G09530")).toBeTruthy();
  });

  it("names the atlas only where a cell type's markers come from more than one", async () => {
    const top = STATS.markers.top[0];
    fetchClusterStats.mockResolvedValue({
      ...STATS,
      markers: {
        top: [{ ...top, gene: "A", source: "nuclei" }, { ...top, gene: "B", source: "shahan" }],
        n_significant: 2,
      },
    });
    const { rerender } = render(panel("Pericycle"));
    await waitFor(() => expect(screen.getByText("nuclei")).toBeTruthy());
    expect(screen.getByText("shahan")).toBeTruthy();

    fetchClusterStats.mockResolvedValue({
      ...STATS,
      markers: { top: [{ ...top, gene: "A", source: "nuclei" }], n_significant: 1 },
    });
    rerender(panel("LRC"));
    await waitFor(() => expect(screen.getByText("A")).toBeTruthy());
    expect(screen.queryByText("nuclei")).toBeNull();
  });

  it("clears the previous cluster when the selection changes", async () => {
    fetchClusterStats.mockResolvedValue(STATS);
    const { rerender } = render(panel("Phellem"));
    await waitFor(() => expect(screen.getByText("AT5G09530")).toBeTruthy());

    let release: (v: unknown) => void = () => {};
    fetchClusterStats.mockReturnValue(new Promise((r) => { release = r; }));
    rerender(panel("Cortex"));

    // The header is not gated on loading, so without a reset it would show the
    // previous cluster's cell count under the new cluster's name.
    await waitFor(() => expect(screen.getByText(/cluster_id Cortex/)).toBeTruthy());
    expect(screen.queryByText(/164 cells/)).toBeNull();
    expect(screen.getByText("Loading…")).toBeTruthy();
    release(STATS);
  });
});

describe("ExpressionClusterDetailPanel without stored markers", () => {
  const DE = { deId: 9, contrast: "vs_rest" };
  const DE_MARKERS = {
    top: [{ gene: "AT5G09530", log2fc: 5.46, q: 3e-86, pct_1: 0.78, pct_2: 0.15 }],
    n_significant: 282,
  };

  it("lists the top of the cluster's one-vs-rest analysis instead", async () => {
    fetchClusterStats.mockResolvedValue(null);
    findOneVsRest.mockResolvedValue(DE);
    fetchDeMarkers.mockResolvedValue(DE_MARKERS);
    render(
      <ExpressionClusterDetailPanel
        datasetId={1} clusterId="c14" clusterName="C14" clusterColor="#000" cellCount={371}
      />,
    );
    await waitFor(() => expect(screen.getByText("AT5G09530")).toBeTruthy());
    expect(fetchDeMarkers).toHaveBeenCalledWith(9);
    expect(screen.getByText("282")).toBeTruthy();
    expect(screen.getByText(/371 cells/)).toBeTruthy();
  });

  it("opens the full list for that comparison", async () => {
    fetchClusterStats.mockResolvedValue(null);
    findOneVsRest.mockResolvedValue(DE);
    fetchDeMarkers.mockResolvedValue(DE_MARKERS);
    const onShowFullList = vi.fn();
    render(
      <ExpressionClusterDetailPanel
        datasetId={1} clusterId="c14" clusterName="C14" clusterColor="#000"
        onShowFullList={onShowFullList}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Get the full list/ }));
    expect(onShowFullList).toHaveBeenCalledWith({ deId: 9, contrast: "vs_rest", clusterId: "c14" });
  });

  it("offers no full list when the cluster has no one-vs-rest analysis", async () => {
    fetchClusterStats.mockResolvedValue(null);
    render(
      <ExpressionClusterDetailPanel
        datasetId={1} clusterId="c14" clusterName="C14" clusterColor="#000"
        onShowFullList={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText(/No markers stored for this cell type/)).toBeTruthy(),
    );
    expect(screen.queryByRole("button", { name: /Get the full list/ })).toBeNull();
  });

  it("keeps stored markers rather than asking the analysis", async () => {
    fetchClusterStats.mockResolvedValue(STATS);
    findOneVsRest.mockResolvedValue(DE);
    render(panel());
    await waitFor(() => expect(screen.getByText("AT5G09530")).toBeTruthy());
    expect(fetchDeMarkers).not.toHaveBeenCalled();
  });
});
