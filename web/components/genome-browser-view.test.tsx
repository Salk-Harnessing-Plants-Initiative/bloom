// @vitest-environment jsdom
/** The browser page's client side: the gene search moves JBrowse's own views, opens picks without
 * reloading the browser, and says when it could not. */

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes";
import type { BrowserSession } from "@/lib/jbrowse/browser-session";
import type { GenePlace } from "@/lib/jbrowse/gene-links";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const goToGene = vi.fn();
const openPicks = vi.fn();
vi.mock("@/lib/jbrowse/browser-session", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/jbrowse/browser-session")>()),
  goToGene: (...args: unknown[]) => goToGene(...args),
  openPicks: (...args: unknown[]) => openPicks(...args),
}));

type BrowserProps = { onSession: (s: BrowserSession) => void; onChange: () => void };
let browser: BrowserProps;
vi.mock("@/components/jbrowse-client", () => ({
  default: (props: BrowserProps) => {
    browser = props;
    return null;
  },
}));

type SearchProps = {
  openGenomes: string[];
  onGoTo: (p: GenePlace) => void;
  onOpenTogether: (a: string, p: GenePlace[]) => Promise<void>;
};
let search: SearchProps | undefined;
vi.mock("@/components/genome-gene-search", () => ({
  GenomeGeneSearch: (props: SearchProps) => {
    search = props;
    return null;
  },
}));

import { GenomeBrowserView } from "./genome-browser-view";

const loaded = {
  config: { assemblies: [] },
  bloomGenomes: ["a.v1"],
  bloomBuilds: { "a.v1": 4 },
  error: null,
} as unknown as LoadedBrowserConfig;
const gene: GenePlace = { gene: "G1", name: null, assembly: "a.v1", label: "a v1", contig: "Chr1", start: 10, end: 90 };
const TRACKS = ["a.v1-genes", "a.v1-gene-density"];
const session = (assemblies: string[]): BrowserSession => ({
  views: assemblies.map((a) => ({ type: "LinearGenomeView", assemblyNames: [a] })),
});

beforeEach(() => {
  vi.clearAllMocks();
  goToGene.mockResolvedValue(undefined);
  openPicks.mockResolvedValue(undefined);
  vi.spyOn(console, "error").mockImplementation(() => {});
  render(<GenomeBrowserView loaded={loaded} picks={null} />);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("GenomeBrowserView", () => {
  it("offers no gene search when Bloom holds no browsable genome", () => {
    cleanup();
    search = undefined;
    render(<GenomeBrowserView loaded={{ ...loaded, bloomGenomes: [], bloomBuilds: {} }} picks={null} />);
    expect(search).toBeUndefined();
  });

  it("searches the genomes open in JBrowse, kept up as its views change", () => {
    const s = session(["a.v1"]);
    act(() => browser.onSession(s));
    expect(search!.openGenomes).toEqual(["a.v1"]);
    s.views.push({ type: "LinearGenomeView", assemblyNames: ["b.v2"] });
    act(() => browser.onChange());
    expect(search!.openGenomes).toEqual(["a.v1", "b.v2"]);
  });

  it("opens a gene's link before JBrowse has a session", () => {
    act(() => search!.onGoTo(gene));
    expect(push).toHaveBeenCalledWith("/app/jbrowse?genome=a.v1&loc=Chr1%3A10..90");
    expect(goToGene).not.toHaveBeenCalled();
  });

  it("moves JBrowse to a gene, with the genome's tracks for a new view", () => {
    const s = session(["a.v1"]);
    act(() => browser.onSession(s));
    act(() => search!.onGoTo(gene));
    expect(goToGene).toHaveBeenCalledWith(s, gene, TRACKS);
    expect(push).not.toHaveBeenCalled();
  });

  it("says when a gene could not be shown", async () => {
    goToGene.mockRejectedValue(new Error("no such contig"));
    act(() => browser.onSession(session([])));
    act(() => search!.onGoTo(gene));
    expect(await screen.findByText("G1 could not be shown: no such contig")).toBeTruthy();
  });

  it("opens picks in JBrowse's views without reloading it, and keeps the link", async () => {
    const replace = vi.spyOn(window.history, "replaceState");
    const s = session(["a.v1", "b.v2"]);
    act(() => browser.onSession(s));
    await act(() => search!.onOpenTogether("a.v1", [gene]));
    expect(openPicks).toHaveBeenCalledWith(s, "a.v1", [gene], TRACKS);
    expect(replace).toHaveBeenCalledWith(null, "", "/app/jbrowse?genome=a.v1&genes=G1");
    expect(push).not.toHaveBeenCalled();
  });

  it("says when picks could not be opened", async () => {
    openPicks.mockRejectedValue(new Error("Assembly not found"));
    act(() => browser.onSession(session([])));
    await act(() => search!.onOpenTogether("a.v1", [gene]));
    expect(screen.getByText("The picked genes could not be opened: Assembly not found")).toBeTruthy();
  });

  it("opens the picks' link before JBrowse has a session", async () => {
    await act(() => search!.onOpenTogether("a.v1", [gene]));
    expect(push).toHaveBeenCalledWith("/app/jbrowse?genome=a.v1&genes=G1");
  });

  it("clears an earlier problem on the next action", async () => {
    goToGene.mockRejectedValueOnce(new Error("x"));
    act(() => browser.onSession(session([])));
    act(() => search!.onGoTo(gene));
    await screen.findByText("G1 could not be shown: x");
    act(() => search!.onGoTo(gene));
    expect(screen.queryByText("G1 could not be shown: x")).toBeNull();
  });
});
