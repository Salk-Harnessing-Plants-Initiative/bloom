// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ExpressionClusterSidebar } from "./expression-cluster-sidebar";
import type { Database } from "@/lib/database.types";

type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];

afterEach(cleanup);

const CLUSTERS = [
  { ordinal: 0, cluster_id: "A", name: "Phellem", color: "#112233", source: null },
  { ordinal: 1, cluster_id: "B", name: "Phloem", color: "#445566", source: null },
] as unknown as Cluster[];

function renderWith(transgene?: Map<number, { positive: number; total: number }>) {
  render(
    <ExpressionClusterSidebar
      clusters={CLUSTERS}
      hiddenOrdinals={new Set()}
      transgene={transgene}
      onVisibilityChange={vi.fn()}
      onSolo={vi.fn()}
      onShowAll={vi.fn()}
      onHideAll={vi.fn()}
    />,
  );
}

describe("the cluster list's transgene badge", () => {
  it("marks each cluster with transgene-positive cells, with how many and what share", () => {
    renderWith(new Map([[0, { positive: 82, total: 227 }], [1, { positive: 0, total: 10 }]]));
    expect(screen.getByText(/82 transgene\+/).textContent).toContain("36.1%");
    expect(screen.getAllByText(/transgene\+/)).toHaveLength(1);
  });

  it("shows none for a dataset that records no transgene status", () => {
    renderWith(undefined);
    expect(screen.queryByText(/transgene\+/)).toBeNull();
  });
});
