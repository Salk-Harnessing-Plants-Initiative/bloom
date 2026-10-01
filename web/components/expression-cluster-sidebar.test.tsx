// @vitest-environment jsdom
/**
 * Each cell type says which reference its label was transferred from, because a
 * label is worth less without knowing where it came from.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ExpressionClusterSidebar } from "./expression-cluster-sidebar";
import type { Database } from "@/lib/database.types";

type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

afterEach(cleanup);

function cluster(ordinal: number, name: string, source: string | null): Cluster {
  return {
    id: ordinal + 1, dataset_id: 1, ordinal, cluster_id: name, name,
    color: "#112233", source,
  } as unknown as Cluster;
}

function renderSidebar(clusters: Cluster[]) {
  render(
    <ExpressionClusterSidebar
      clusters={clusters}
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
