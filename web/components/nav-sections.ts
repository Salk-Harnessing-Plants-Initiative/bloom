/**
 * The app navigation. It lives outside `app/app/layout.tsx` because Next's
 * build type-check rejects extra named exports from a layout, and the test
 * needs to import the list.
 */

export type NavItem = { name: string; href: string };
export type NavSection = { heading: string | null; items: NavItem[] };

export const navSections: NavSection[] = [
  {
    heading: null,
    items: [{ name: "Home", href: "/app" }],
  },
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
      // Temporarily disabled — Bloom Assistant is a work in progress.
      // { name: "Bloom Assistant", href: "/chat" },
      { name: "OrthoBrowser", href: "/app/orthofinder" },
      { name: "OrthoVec", href: "/app/embedtree" },
      // Named for its pipeline: other pipelines (RNA-seq) have runs of their own.
      { name: "Cylinder\nPipeline Runs", href: "/app/cyl-pipeline-runs" },
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
];
