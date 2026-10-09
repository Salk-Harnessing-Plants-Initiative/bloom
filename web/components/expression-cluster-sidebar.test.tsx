// @vitest-environment jsdom
/**
 * Each cell type says which reference its label was transferred from, because a
 * label is worth less without knowing where it came from.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ExpressionClusterSidebar } from "./expression-cluster-sidebar";
import type { Database } from "@/lib/database.types";
import type { PredictedSource } from "@/components/expression-lib/cell-type-labels";

type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

afterEach(cleanup);

function cluster(ordinal: number, name: string, source: string | null): Cluster {
  return {
    id: ordinal + 1, dataset_id: 1, ordinal, cluster_id: name, name,
    color: "#112233", source,
  } as unknown as Cluster;
}

function renderSidebar(clusters: Cluster[], predicted?: Map<number, PredictedSource[]>) {
  render(
    <ExpressionClusterSidebar
      clusters={clusters}
      predicted={predicted}
      hiddenOrdinals={new Set()}
      onVisibilityChange={() => {}}
      onSolo={() => {}}
      onShowAll={() => {}}
      onHideAll={() => {}}
    />,
  );
}

describe("ExpressionClusterSidebar label sources", () => {
  it("says which reference each cell type's label came from", () => {
    renderSidebar([
      cluster(0, "Xylem", "shahan"),
      cluster(1, "Pericycle", "shahan (73%), nuclei (27%)"),
    ]);
    expect(screen.getByText("from shahan")).toBeTruthy();
    expect(screen.getByText("from shahan (73%), nuclei (27%)")).toBeTruthy();
  });

  it("says nothing for a cell type with no recorded source, or a blank one", () => {
    renderSidebar([cluster(0, "Xylem", null), cluster(1, "Phloem", "   ")]);
    expect(screen.queryByText(/^from /)).toBeNull();
    expect(screen.getByText("Xylem")).toBeTruthy();
  });
});

describe("ExpressionClusterSidebar predicted cell types", () => {
  it("lists one cell type per source, the source in brackets", () => {
    renderSidebar([cluster(0, "C14", null)], new Map([[0, [
      { key: "curated", label: "Young phellem" },
      { key: "shahan_atlas", label: "Columella" },
    ]]]));
    expect(screen.getByText("Young phellem (Curated)")).toBeTruthy();
    expect(screen.getByText("Columella (Shahan atlas)")).toBeTruthy();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("shows nothing extra for a cluster without predictions", () => {
    renderSidebar([cluster(0, "Xylem", "shahan")], new Map());
    expect(screen.queryByLabelText("Predicted cell types")).toBeNull();
    expect(screen.getByText("from shahan")).toBeTruthy();
  });
});
