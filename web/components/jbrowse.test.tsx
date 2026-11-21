// @vitest-environment jsdom
/** JBrowse on the page: made once per page config, opening the link's genome, or its placed picks
 * through the session, and handing its session to the gene search. */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes";
import type { GenePlace } from "@/lib/jbrowse/gene-links";

let query = "";
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(query) }));

const evaluate = vi.fn();
const createViewState = vi.fn((opts: { config: unknown }) => ({
  config: opts.config,
  session: { views: [], id: Math.random() },
  pluginManager: { evaluateAsyncExtensionPoint: evaluate },
}));
vi.mock("@jbrowse/react-app2", () => ({
  createViewState: (opts: { config: unknown }) => createViewState(opts),
  JBrowseApp: () => <div>JBrowse app</div>,
}));
vi.mock("@jbrowse/core/util", () => ({
  getEnv: (state: { pluginManager: unknown }) => ({ pluginManager: state.pluginManager }),
}));

const openPicks = vi.fn();
vi.mock("@/lib/jbrowse/browser-session", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/jbrowse/browser-session")>()),
  openPicks: (...args: unknown[]) => openPicks(...args),
}));

import JBrowse from "./jbrowse";

const loadedWith = () =>
  ({
    config: { assemblies: [{ name: "a.v1" }] },
    bloomGenomes: ["a.v1"],
    bloomBuilds: { "a.v1": 4 },
    error: null,
  }) as unknown as LoadedBrowserConfig;
const gene: GenePlace = { gene: "G1", name: null, assembly: "a.v1", label: "a v1", contig: "Chr1", start: 10, end: 90 };
const TRACKS = ["a.v1-genes", "a.v1-gene-density"];
const made = () => createViewState.mock.results.map((r) => r.value);

beforeEach(() => {
  vi.clearAllMocks();
  query = "";
  evaluate.mockResolvedValue(undefined);
  openPicks.mockResolvedValue(undefined);
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("JBrowse", () => {
  it("makes JBrowse from the page's config and hands its session over", async () => {
    const onSession = vi.fn();
    const onChange = vi.fn();
    const loaded = loadedWith();
    render(<JBrowse loaded={loaded} picks={null} onSession={onSession} onChange={onChange} />);
    expect(await screen.findByText("JBrowse app")).toBeTruthy();
    expect(createViewState.mock.calls[0][0].config).toBe(loaded.config);
    expect(onSession).toHaveBeenCalledWith(made()[0].session);
    (createViewState.mock.calls[0][0] as unknown as { onChange: () => void }).onChange();
    expect(onChange).toHaveBeenCalled();
  });

  it("opens the link's genome at its location with its genes and density", async () => {
    query = "genome=a.v1&loc=Chr1:1..9";
    render(<JBrowse loaded={loadedWith()} picks={null} />);
    await waitFor(() => expect(evaluate).toHaveBeenCalled());
    expect(evaluate).toHaveBeenCalledWith("LaunchView-LinearGenomeView", {
      tracks: TRACKS,
      loc: "Chr1:1..9",
      assembly: "a.v1",
      session: made()[0].session,
    });
    expect(openPicks).not.toHaveBeenCalled();
  });

  it("opens placed picks through the session instead", async () => {
    query = "genome=a.v1&genes=G1";
    render(<JBrowse loaded={loadedWith()} picks={{ assembly: "a.v1", places: [gene], notes: [] }} />);
    await waitFor(() => expect(openPicks).toHaveBeenCalled());
    expect(openPicks).toHaveBeenCalledWith(made()[0].session, "a.v1", [gene], TRACKS);
    expect(evaluate).not.toHaveBeenCalled();
  });

  it("says when the genome could not be opened", async () => {
    query = "genome=a.v1&genes=G1";
    openPicks.mockRejectedValue(new Error("boom"));
    render(<JBrowse loaded={loadedWith()} picks={{ assembly: "a.v1", places: [gene], notes: [] }} />);
    expect(await screen.findByText("a.v1 could not be opened: boom")).toBeTruthy();
  });

  it("keeps the browser when only the address changes", async () => {
    const loaded = loadedWith();
    const { rerender } = render(<JBrowse loaded={loaded} picks={null} />);
    await screen.findByText("JBrowse app");
    query = "genome=a.v1&genes=G1";
    rerender(<JBrowse loaded={loaded} picks={null} />);
    await screen.findByText("JBrowse app");
    expect(createViewState).toHaveBeenCalledTimes(1);
  });

  it("makes the browser again for a new page config, dropping a load it overtook", async () => {
    const onSession = vi.fn();
    const { rerender } = render(<JBrowse loaded={loadedWith()} picks={null} onSession={onSession} />);
    const second = loadedWith();
    rerender(<JBrowse loaded={second} picks={null} onSession={onSession} />);
    await screen.findByText("JBrowse app");
    await waitFor(() => expect(onSession).toHaveBeenCalled());
    expect(createViewState).toHaveBeenCalledTimes(1);
    expect(createViewState.mock.calls[0][0].config).toBe(second.config);
  });
});
