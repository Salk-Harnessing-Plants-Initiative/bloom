// @vitest-environment jsdom
/** The genome browser page: no text above the browser unless something is wrong, and a link's
 * picks placed before the browser opens. */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes";
import type { PicksPlan } from "@/lib/jbrowse/several-genes";

const redirect = vi.fn((to: string) => {
  throw new Error(`redirect ${to}`);
});
vi.mock("next/navigation", () => ({ redirect: (to: string) => redirect(to) }));
vi.mock("@/lib/supabase/server", () => ({ createServerSupabaseClient: async () => ({}) }));

const loadBrowserConfig = vi.fn();
const geneLinkRedirect = vi.fn();
const placePicks = vi.fn();
const whyNotOffered = vi.fn();
vi.mock("@/lib/jbrowse/bloom-genomes-server", () => ({
  loadBrowserConfig: (...a: unknown[]) => loadBrowserConfig(...a),
  geneLinkRedirect: (...a: unknown[]) => geneLinkRedirect(...a),
  placePicks: (...a: unknown[]) => placePicks(...a),
  whyNotOffered: (...a: unknown[]) => whyNotOffered(...a),
}));

let viewPicks: PicksPlan | null | undefined;
vi.mock("@/components/genome-browser-view", () => ({
  GenomeBrowserView: ({ picks }: { picks: PicksPlan | null }) => {
    viewPicks = picks;
    return <div>browser</div>;
  },
}));

import GenomeBrowser from "./page";

const loaded = (over: Partial<LoadedBrowserConfig> = {}) =>
  ({
    config: { assemblies: [{ name: "a.v1" }] },
    bloomGenomes: ["a.v1"],
    bloomBuilds: { "a.v1": 4 },
    error: null,
    ...over,
  }) as unknown as LoadedBrowserConfig;

async function show(params: Record<string, string>) {
  render(await GenomeBrowser({ searchParams: Promise.resolve(params) }));
}
const notes = () => screen.queryAllByText(/./, { selector: "p" }).map((p) => p.textContent);

beforeEach(() => {
  vi.clearAllMocks();
  viewPicks = undefined;
  loadBrowserConfig.mockResolvedValue(loaded());
  geneLinkRedirect.mockResolvedValue(null);
  placePicks.mockResolvedValue(null);
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the genome browser page", () => {
  it("shows the browser with no text above it when everything loads", async () => {
    await show({ genome: "a.v1" });
    expect(screen.getByText("browser")).toBeTruthy();
    expect(notes()).toEqual([]);
  });

  it("shows no text when Bloom holds no browsable genome", async () => {
    loadBrowserConfig.mockResolvedValue(loaded({ bloomGenomes: [], bloomBuilds: {} }));
    await show({});
    expect(notes()).toEqual([]);
  });

  it("says Bloom's genomes could not be read, still showing the browser", async () => {
    loadBrowserConfig.mockResolvedValue(loaded({ error: "Bloom's genomes could not be loaded." }));
    await show({});
    expect(notes()).toEqual(["Bloom's genomes could not be loaded. The reference genomes below still work."]);
    expect(screen.getByText("browser")).toBeTruthy();
  });

  it("hands the placed picks to the browser and names the missing ones", async () => {
    const picks = { assembly: "a.v1", places: [], notes: ["NOPE is not among a.v1's genes, so it was left out."] };
    placePicks.mockResolvedValue(picks);
    await show({ genome: "a.v1", genes: "NOPE" });
    expect(placePicks).toHaveBeenCalledWith({}, loaded(), "a.v1", "NOPE");
    expect(viewPicks).toEqual(picks);
    expect(notes()).toEqual(picks.notes);
  });

  it("says the picks could not be looked up when that fails", async () => {
    placePicks.mockRejectedValue(new Error("down"));
    await show({ genome: "a.v1", genes: "G1" });
    expect(notes()).toEqual(["The picked genes could not be looked up in a.v1."]);
    expect(viewPicks).toBeNull();
  });

  it("sends a gene link on to the gene's place", async () => {
    geneLinkRedirect.mockResolvedValue("/app/jbrowse?genome=a.v1&loc=Chr1%3A1..9");
    await expect(show({ genome: "a.v1", loc: "G1" })).rejects.toThrow("redirect /app/jbrowse?genome=a.v1&loc=Chr1%3A1..9");
  });
});
