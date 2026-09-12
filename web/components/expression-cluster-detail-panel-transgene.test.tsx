// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("./expression-lib/cluster-markers", () => ({
  fetchClusterStats: vi.fn(async () => null),
}));

import { ExpressionClusterDetailPanel } from "./expression-cluster-detail-panel";

afterEach(cleanup);

describe("the cluster panel's transgene tile", () => {
  it("says how many of the cluster's cells carry the transgene", () => {
    render(
      <ExpressionClusterDetailPanel
        datasetId={1}
        clusterId="A"
        clusterName="Phellem"
        clusterColor="#112233"
        transgene={{ positive: 82, total: 227 }}
      />,
    );
    const tile = screen.getByTestId("cluster-transgene").textContent ?? "";
    expect(tile).toContain("Transgene-positive cells");
    expect(tile).toContain("82");
    expect(tile).toContain("of 227 · 36.1%");
  });

  it("is left out for a dataset that records no transgene status", () => {
    render(
      <ExpressionClusterDetailPanel datasetId={1} clusterId="A" clusterName="Phellem" clusterColor={null} />,
    );
    expect(screen.queryByTestId("cluster-transgene")).toBeNull();
  });
});
