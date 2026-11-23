// @vitest-environment jsdom
/** The gene search: it covers the genomes open in the browser, labels each gene with its genome
 * version, and picks several genes from one genome to open together. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { GenePlace } from "@/lib/jbrowse/gene-links";

const searchGenes = vi.fn();
vi.mock("@/lib/jbrowse/gene-links", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/jbrowse/gene-links")>()),
  searchGenes: (...args: unknown[]) => searchGenes(...args),
}));
vi.mock("@/lib/supabase/client", () => ({ createClientSupabaseClient: () => ({}) }));

import { GenomeGeneSearch } from "./genome-gene-search";

const builds = { "a.v1": 4, "b.v2": 7 };
const place = (gene: string, assembly: string): GenePlace => ({
  gene,
  name: null,
  assembly,
  label: assembly.replace(".v", " v"),
  contig: "Chr1",
  start: 100,
  end: 200,
});

function renderSearch(openGenomes: string[] = [], onOpenTogether = vi.fn(async () => {})) {
  const onGoTo = vi.fn();
  render(
    <GenomeGeneSearch bloomBuilds={builds} openGenomes={openGenomes} onGoTo={onGoTo} onOpenTogether={onOpenTogether} />,
  );
  return { onGoTo, onOpenTogether };
}

const type = (text: string) => fireEvent.change(screen.getByRole("searchbox"), { target: { value: text } });
const lastScope = () => searchGenes.mock.calls.at(-1)?.[3];
const lastLimit = () => searchGenes.mock.calls.at(-1)?.[2];

beforeEach(() => {
  searchGenes.mockReset();
  searchGenes.mockResolvedValue([place("AT1G01010", "a.v1"), place("AT1G01020", "a.v1"), place("AT1G01010", "b.v2")]);
});
afterEach(cleanup);

describe("GenomeGeneSearch", () => {
  it("searches every Bloom genome when none is open, 100 matches shared between them", async () => {
    renderSearch();
    type("AT1G0101");
    await waitFor(() => expect(lastScope()).toEqual([4, 7]));
    expect(lastLimit()).toBe(50);
  });

  it("searches the genomes open in the browser, naming them", async () => {
    renderSearch(["b.v2", "Athaliana.Col-0.HPIv02"]);
    type("AT1G0101");
    await waitFor(() => expect(lastScope()).toEqual([7]));
    expect(lastLimit()).toBe(100);
    expect(screen.getByText("Searching b v2.")).toBeTruthy();
  });

  it("says which genomes had no match", async () => {
    searchGenes.mockResolvedValue([]);
    renderSearch(["b.v2"]);
    type("NOPE");
    expect(await screen.findByText("No gene in b v2 matches.")).toBeTruthy();
  });

  it("says no Bloom genome matches when all were searched", async () => {
    searchGenes.mockResolvedValue([]);
    renderSearch();
    type("NOPE");
    expect(await screen.findByText("No gene in Bloom's genomes matches.")).toBeTruthy();
  });

  it("says when a genome's matches were cut at its share", async () => {
    searchGenes.mockResolvedValue(Array.from({ length: 50 }, (_, i) => place(`G${i}`, "a.v1")));
    renderSearch();
    type("Gx");
    expect(await screen.findByText("Showing the first 50 per genome; type more to narrow it down.")).toBeTruthy();
  });

  it("lists matches under their genome version", async () => {
    renderSearch();
    type("AT1G0101");
    expect(await screen.findByText("a v1")).toBeTruthy();
    expect(screen.getByText("b v2")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "AT1G01010" })).toHaveLength(2);
  });

  it("moves the browser to a gene when its name is clicked", async () => {
    const { onGoTo } = renderSearch();
    type("AT1G0101");
    fireEvent.click((await screen.findAllByRole("button", { name: "AT1G01010" }))[1]);
    expect(onGoTo).toHaveBeenCalledWith(place("AT1G01010", "b.v2"));
  });

  it("keeps to the picked genome, then opens the picks together", async () => {
    const { onOpenTogether } = renderSearch();
    type("AT1G0101");
    fireEvent.click((await screen.findAllByRole("checkbox"))[0]);
    await waitFor(() => expect(lastScope()).toEqual([4]));
    expect(screen.getByText(/Picking from a v1/)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Pick AT1G01020 in a v1"));
    fireEvent.click(screen.getByRole("button", { name: "Open together" }));
    expect(onOpenTogether).toHaveBeenCalledWith("a.v1", [place("AT1G01010", "a.v1"), place("AT1G01020", "a.v1")]);
  });

  it("shows the picks opening, and cannot open them twice meanwhile", async () => {
    let finish = () => {};
    const onOpenTogether = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    renderSearch([], onOpenTogether);
    type("AT1G0101");
    fireEvent.click((await screen.findAllByRole("checkbox"))[0]);
    fireEvent.click(screen.getByRole("button", { name: "Open together" }));
    const opening = screen.getByRole("button", { name: "Opening…" }) as HTMLButtonElement;
    expect(opening.disabled).toBe(true);
    fireEvent.click(opening);
    expect(onOpenTogether).toHaveBeenCalledTimes(1);
    finish();
    expect(await screen.findByRole("button", { name: "Open together" })).toBeTruthy();
  });

  it("does not offer another genome's genes once one is picked", async () => {
    renderSearch();
    type("AT1G0101");
    fireEvent.click((await screen.findAllByRole("checkbox"))[0]);
    await waitFor(() => expect(lastScope()).toEqual([4]));
    expect(screen.queryByLabelText("Pick AT1G01010 in b v2")).toBeNull();
  });

  it("searches the open genomes again once the picks are cleared", async () => {
    renderSearch();
    type("AT1G0101");
    fireEvent.click((await screen.findAllByRole("checkbox"))[0]);
    await waitFor(() => expect(lastScope()).toEqual([4]));
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(lastScope()).toEqual([4, 7]));
  });

  it("stops picking at 20 genes", async () => {
    searchGenes.mockResolvedValue(Array.from({ length: 21 }, (_, i) => place(`G${i}`, "a.v1")));
    renderSearch();
    type("Gx");
    const boxes = await screen.findAllByRole("checkbox");
    for (const box of boxes.slice(0, 20)) fireEvent.click(box);
    expect((screen.getByLabelText("Pick G20 in a v1") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("At most 20 genes can be opened together.")).toBeTruthy();
  });
});
