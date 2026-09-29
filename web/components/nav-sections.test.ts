/** The app navigation (add-cyl-pipeline-ui task 5.3). */

import { describe, expect, it } from "vitest";
import { navSections } from "./nav-sections";

describe("navSections", () => {
  it("links Pipeline runs at /app/cyl-pipeline-runs", () => {
    const items = navSections.flatMap((s) => s.items);
    expect(items.filter((i) => i.name === "Pipeline runs")).toEqual([
      { name: "Pipeline runs", href: "/app/cyl-pipeline-runs" },
    ]);
  });

  it("keeps every other entry as it was", () => {
    const withoutRuns = navSections.map((s) => ({ ...s, items: s.items.filter((i) => i.name !== "Pipeline runs") }));
    expect(withoutRuns).toEqual([
      { heading: null, items: [{ name: "Home", href: "/app" }] },
      {
        heading: "Data",
        items: [
          { name: "Cylinder\nPhenotypes", href: "/app/phenotypes" },
          { name: "Plate\nPhenotypes", href: "/app/plate-phenotypes" },
          { name: "Traits", href: "/app/traits" },
          { name: "Genes", href: "/app/genes" },
          { name: "Expression", href: "/app/expression" },
          { name: "Multi-dataset\nIntegration", href: "/app/integrations" },
        ],
      },
      {
        heading: "Tools",
        items: [
          { name: "OrthoBrowser", href: "/app/orthofinder" },
          { name: "OrthoVec", href: "/app/embedtree" },
        ],
      },
      {
        heading: "Resources",
        items: [
          { name: "Timeline", href: "/app/timeline" },
          { name: "Translation", href: "/app/translation" },
          { name: "Software", href: "/app/software" },
        ],
      },
    ]);
  });

  it("has no entry for /app/pipelines", () => {
    expect(navSections.flatMap((s) => s.items).some((i) => i.href.startsWith("/app/pipelines"))).toBe(false);
  });
});
