/**
 * The app navigation. It lives outside `app/app/layout.tsx` because Next's
 * build type-check rejects extra named exports from a layout, and the test
 * needs to import the list.
 */

// `children` are smaller links shown indented under their item, e.g. a page's panels.
export type NavItem = { name: string; href: string; children?: NavItem[] };
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
      // Named for its pipeline: each pipeline has runs of its own.
      { name: "Cylinder\nPipeline Runs", href: "/app/cyl-pipeline-runs" },
      { name: "RNA-seq\nPipeline Runs", href: "/app/rnaseq-runs" },
      // Not a pipeline: a queue of plate and cylinder scan video requests.
      { name: "Video Jobs", href: "/app/video-jobs" },
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
      { name: "Species", href: "/app/species" },
    ],
  },
];
