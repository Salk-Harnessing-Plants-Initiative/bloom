/** The app navigation (add-cyl-pipeline-ui task 5.3). */

import { describe, expect, it } from "vitest";
import { navSections } from "./nav-sections";

describe("navSections", () => {
  it("links Cylinder Pipeline Runs at /app/cyl-pipeline-runs, on two lines like the other cylinder entry", () => {
    const items = navSections.flatMap((s) => s.items);
    expect(items.filter((i) => i.href === "/app/cyl-pipeline-runs")).toEqual([
      { name: "Cylinder\nPipeline Runs", href: "/app/cyl-pipeline-runs" },
    ]);
  });

  it("lists RNA-seq Pipeline Runs in Tools right below Cylinder Pipeline Runs, and not under Timeline", () => {
    const tools = navSections.find((s) => s.heading === "Tools")!.items.map((i) => i.href);
    const at = tools.indexOf("/app/cyl-pipeline-runs");
    expect(tools[at + 1]).toBe("/app/rnaseq-runs");
    const rnaseq = navSections.flatMap((s) => s.items).find((i) => i.href === "/app/rnaseq-runs");
    expect(rnaseq?.name).toBe("RNA-seq\nPipeline Runs");
    const timeline = navSections.flatMap((s) => s.items).find((i) => i.name === "Timeline");
    expect(timeline?.children?.map((c) => c.href)).toEqual([
      "/app/timeline?panel=cylinder",
      "/app/timeline?panel=plate",
    ]);
  });

  it("has no bare Pipeline runs entry, which wouldn't say which pipeline", () => {
    const names = navSections.flatMap((s) => s.items).map((i) => i.name.replace(/\n/g, " ").toLowerCase());
    expect(names).not.toContain("pipeline runs");
  });

  it("keeps every other entry as it was", () => {
    const runs = new Set(["/app/cyl-pipeline-runs", "/app/rnaseq-runs"]);
    const withoutRuns = navSections.map((s) => ({ ...s, items: s.items.filter((i) => !runs.has(i.href)) }));
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
          {
            name: "Timeline",
            href: "/app/timeline",
            children: [
              { name: "Cylinder scanner usage", href: "/app/timeline?panel=cylinder" },
              { name: "Plate scanner usage", href: "/app/timeline?panel=plate" },
            ],
          },
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
