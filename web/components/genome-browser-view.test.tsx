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

type BrowserProps = { onSession: (s: BrowserSession) => void; onChange: () => void; onDefaultOpened: (a: string) => void };
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
type PickerProps = { value: string; disabled?: boolean; onChoose: (name: string) => void; choices: unknown };
let picker: PickerProps | undefined;
vi.mock("@/components/genome-picker", () => ({
  GenomePicker: (props: PickerProps) => {
    picker = props;
    return null;
  },
}));
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
const TRACKS = ["a.v1-genes"];
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

  it("searches every genome while only the default view is open, then the open ones", () => {
    act(() => {
      browser.onDefaultOpened("a.v1");
      browser.onSession(session(["a.v1"]));
    });
    expect(search?.openGenomes).toEqual([]);
    const both = session(["a.v1", "b.v2"]);
    act(() => browser.onSession(both));
    expect(search?.openGenomes).toEqual(["a.v1", "b.v2"]);
  });

  describe("the genome selector", () => {
    const REGIONS = { regions: [{ refName: "Chr1", start: 0, end: 30_427_671 }] };
    const choosing = (regions: unknown = REGIONS) => {
      const old = { type: "LinearGenomeView", assemblyNames: ["b.v2"] };
      const s = {
        views: [old],
        addView: vi.fn(),
        removeView: vi.fn(),
        assemblyManager: { waitForAssembly: vi.fn(async () => regions) },
      } as unknown as BrowserSession & { addView: ReturnType<typeof vi.fn>; removeView: ReturnType<typeof vi.fn> };
      act(() => browser.onSession(s));
      return { s, old };
    };

    it("is off until JBrowse is ready, then follows the one open genome", () => {
      expect(picker?.disabled).toBe(true);
      act(() => browser.onSession(session(["a.v1"])));
      expect(picker?.disabled).toBe(false);
      expect(picker?.value).toBe("a.v1");
      act(() => browser.onSession(session(["a.v1", "b.v2"])));
      expect(picker?.value).toBe("");
    });

    it("replaces the views with a chosen Bloom genome's views, narrows the search and records the address", async () => {
      vi.spyOn(window.history, "replaceState").mockImplementation(() => {});
      act(() => browser.onDefaultOpened("a.v1"));
      const { s, old } = choosing();
      await act(async () => picker!.onChoose("a.v1"));
      expect(s.removeView).toHaveBeenCalledWith(old);
      expect(s.addView.mock.calls).toEqual([
        ["LinearGenomeView", { init: { assembly: "a.v1", loc: "Chr1:1..30427671", tracks: ["a.v1-gene-density"] } }],
        ["LinearGenomeView", { init: { assembly: "a.v1", loc: "Chr1:1..50000", tracks: ["a.v1-sequence", "a.v1-genes"] } }],
      ]);
      expect(window.history.replaceState).toHaveBeenCalledWith(null, "", "/app/jbrowse?genome=a.v1&loc=Chr1%3A1..50000");
      act(() => browser.onSession({ views: [{ type: "LinearGenomeView", assemblyNames: ["a.v1"] }] }));
      expect(search?.openGenomes).toEqual(["a.v1"]);
    });

    it("names a chosen genome whose chromosomes cannot be read, keeping the views", async () => {
      const { s } = choosing({ regions: [] });
      await act(async () => picker!.onChoose("a.v1"));
      expect(screen.getByText("a.v1 could not be opened: its chromosomes could not be read.")).toBeTruthy();
      expect(s.removeView).not.toHaveBeenCalled();
      expect(s.addView).not.toHaveBeenCalled();
    });
  });
});
