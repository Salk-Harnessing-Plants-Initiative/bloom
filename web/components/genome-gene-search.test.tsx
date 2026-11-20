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

function renderSearch(openGenomes: string[] = []) {
  const onGoTo = vi.fn();
  const onOpenTogether = vi.fn();
  render(
    <GenomeGeneSearch bloomBuilds={builds} openGenomes={openGenomes} onGoTo={onGoTo} onOpenTogether={onOpenTogether} />,
  );
  return { onGoTo, onOpenTogether };
}

const type = (text: string) => fireEvent.change(screen.getByRole("searchbox"), { target: { value: text } });
const lastScope = () => searchGenes.mock.calls.at(-1)?.[3];

beforeEach(() => {
  searchGenes.mockReset();
  searchGenes.mockResolvedValue([place("AT1G01010", "a.v1"), place("AT1G01020", "a.v1"), place("AT1G01010", "b.v2")]);
});
afterEach(cleanup);

describe("GenomeGeneSearch", () => {
  it("searches every Bloom genome when none is open, up to 100 matches", async () => {
    renderSearch();
    type("AT1G0101");
    await waitFor(() => expect(searchGenes).toHaveBeenCalled());
    expect(searchGenes.mock.calls[0][2]).toBe(100);
    expect(lastScope()).toBeNull();
  });

  it("searches the genomes open in the browser", async () => {
    renderSearch(["b.v2", "Athaliana.Col-0.HPIv02"]);
    type("AT1G0101");
    await waitFor(() => expect(lastScope()).toEqual([7]));
    expect(screen.getByText(/Searching b\.v2/)).toBeTruthy();
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
    expect(onOpenTogether).toHaveBeenCalledWith("a.v1", ["AT1G01010", "AT1G01020"]);
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
    await waitFor(() => expect(lastScope()).toBeNull());
  });

  it("stops picking at 20 genes", async () => {
    searchGenes.mockResolvedValue(Array.from({ length: 21 }, (_, i) => place(`G${i}`, "a.v1")));
    renderSearch();
    type("G");
    type("Gx");
    const boxes = await screen.findAllByRole("checkbox");
    for (const box of boxes.slice(0, 20)) fireEvent.click(box);
    expect((screen.getByLabelText("Pick G20 in a v1") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("At most 20 genes can be opened together.")).toBeTruthy();
  });
});
