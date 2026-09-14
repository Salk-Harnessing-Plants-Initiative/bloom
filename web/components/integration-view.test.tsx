// @vitest-environment jsdom
/**
 * The joint map page against fake data: what it shows, what it hands the map,
 * and that leaving it cancels every request it started.
 *
 * The map draws with WebGL, which jsdom lacks, so it is a stand-in that records
 * its props; the database is a fake client whose requests can be held open.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { IntegrationUmapProps } from "./integration-umap";
import {
  CELL_TYPE_KEY,
  datasetRow,
  packColours,
  palette,
} from "./integration-lib/joint-map";
import { IntegrationView, type IntegrationMember } from "./integration-view";

const umapProps = vi.hoisted(() => [] as IntegrationUmapProps[]);
vi.mock("./integration-umap", () => ({
  IntegrationUmap: (props: IntegrationUmapProps) => {
    umapProps.push(props);
    return <div data-testid="umap" />;
  },
}));

const client = vi.hoisted(() => ({
  fetchJointArrays: vi.fn(),
  fetchLabelCodes: vi.fn(),
  fetchPoint: vi.fn(),
}));
vi.mock("./integration-lib/joint-client", () => client);

const MEMBERS: IntegrationMember[] = [
  { ordinal: 0, name: "Atlas", role: "reference", datasetId: 10, speciesId: 1, kind: "reference", nPoints: 2 },
  { ordinal: 1, name: "MYB41", role: "query", datasetId: 20, speciesId: 1, kind: "full", nPoints: 2 },
];
const ORDINALS = [0, 0, 1, 1];
const ARRAYS = { x: [0, 1, 2, 3], y: [0, 1, 2, 3], memberOrdinals: ORDINALS };

// Each dataset keeps its cell types in its own label; both call one type Cortex.
const LABELS: Record<string, { levels: string[]; codes: number[] }> = {
  atlas_cell_type: { levels: ["Cortex", "Xylem"], codes: [0, 1, -1, -1] },
  myb41_cell_type: { levels: ["Cortex", "Stele"], codes: [-1, -1, 0, 1] },
  genotype: { levels: ["Col-0", "pFACT"], codes: [-1, -1, 0, 1] },
  transgene_pos: { levels: ["False", "True"], codes: [-1, -1, 0, 1] },
};
const LABEL_KEYS = Object.keys(LABELS);
const CELL_TYPE_LABELS: [number, string][] = [
  [0, "atlas_cell_type"],
  [1, "myb41_cell_type"],
];

const never = () => new Promise<never>(() => {});

function view(embeddingId: number) {
  return (
    <IntegrationView
      embeddingId={embeddingId}
      members={MEMBERS}
      labelKeys={LABEL_KEYS}
      cellTypeLabels={CELL_TYPE_LABELS}
    />
  );
}

const lastMap = () => umapProps[umapProps.length - 1];
const coloursOf = (row: Parameters<typeof packColours>[0]) =>
  Array.from(packColours(row, palette(row.levels.length)));

async function renderLoaded() {
  const rendered = render(view(1));
  await waitFor(() =>
    expect((screen.getByRole("button", { name: "Cell type" }) as HTMLButtonElement).disabled).toBe(false),
  );
  return rendered;
}

/** The cancel signal of every map and label request made since the given call counts. */
function signalsSince(arrays = 0, labels = 0): AbortSignal[] {
  return [
    ...client.fetchJointArrays.mock.calls.slice(arrays).map((call) => call[1]),
    ...client.fetchLabelCodes.mock.calls.slice(labels).map((call) => call[2]),
  ];
}

beforeEach(() => {
  umapProps.length = 0;
  client.fetchJointArrays.mockReset().mockResolvedValue(ARRAYS);
  client.fetchLabelCodes.mockReset().mockImplementation(async (_id: number, key: string) => LABELS[key]);
  client.fetchPoint.mockReset().mockResolvedValue({ barcode: "AAAC-1", datasetId: 20, cellId: 5 });
});

afterEach(() => cleanup());

describe("what the page shows", () => {
  it("colours the map by dataset first", async () => {
    await renderLoaded();
    expect(Array.from(lastMap().colours)).toEqual(coloursOf(datasetRow(MEMBERS, ORDINALS)));
    expect(screen.getByText("Showing 4 of 4 cells")).toBeTruthy();
  });

  it("colours by cell type across both datasets, one colour per name", async () => {
    await renderLoaded();
    fireEvent.click(screen.getByRole("button", { name: "Cell type" }));
    const combined = { key: CELL_TYPE_KEY, levels: ["Cortex", "Stele", "Xylem"], codes: [0, 2, 0, 1] };
    expect(Array.from(lastMap().colours)).toEqual(coloursOf(combined));
  });

  it("counts transgene-positive cells over the cells that record it", async () => {
    await renderLoaded();
    const summary = await screen.findByTestId("transgene-summary");
    expect(within(summary).getByText("1 transgene+")).toBeTruthy();
    expect(within(summary).getByText("of 2 cells that record it (50.0%)")).toBeTruthy();
  });

  it("gives each label with few values a row of filters", async () => {
    await renderLoaded();
    const row = (label: string) => within(screen.getByText(label, { selector: "span" }).parentElement!);
    expect(row("genotype").getByRole("button", { name: /^Col-0/ })).toBeTruthy();
    expect(row("genotype").getByRole("button", { name: /^pFACT/ })).toBeTruthy();
    expect(row("Cell type").getByRole("button", { name: /^Stele/ })).toBeTruthy();
  });

  it("recolours the map by another label when it is chosen", async () => {
    await renderLoaded();
    fireEvent.change(screen.getByLabelText("Colour by another label"), { target: { value: "genotype" } });
    expect(Array.from(lastMap().colours)).toEqual(coloursOf({ key: "genotype", ...LABELS.genotype }));
  });
});

describe("cancelling requests", () => {
  it("cancels every outstanding request when the page is left", () => {
    client.fetchJointArrays.mockImplementation(never);
    client.fetchLabelCodes.mockImplementation(never);
    const rendered = render(view(1));

    const signals = signalsSince();
    expect(signals).toHaveLength(1 + LABEL_KEYS.length);
    for (const signal of signals) {
      expect(signal).toBeInstanceOf(AbortSignal);
      expect(signal.aborted).toBe(false);
    }

    rendered.unmount();
    for (const signal of signals) expect(signal.aborted).toBe(true);
  });

  it("cancels the previous map's requests when another map is opened", () => {
    client.fetchJointArrays.mockImplementation(never);
    client.fetchLabelCodes.mockImplementation(never);
    const rendered = render(view(1));
    const first = signalsSince();
    const arraysBefore = client.fetchJointArrays.mock.calls.length;
    const labelsBefore = client.fetchLabelCodes.mock.calls.length;

    rendered.rerender(view(2));
    const second = signalsSince(arraysBefore, labelsBefore);

    expect(second).toHaveLength(1 + LABEL_KEYS.length);
    for (const signal of first) expect(signal.aborted).toBe(true);
    for (const signal of second) expect(signal.aborted).toBe(false);
    expect(client.fetchJointArrays.mock.calls.at(-1)?.[0]).toBe(2);
  });

  it("cancels a clicked cell's request when the page is left", async () => {
    client.fetchPoint.mockImplementation(never);
    const rendered = await renderLoaded();

    act(() => lastMap().onPick(2));
    expect(client.fetchPoint).toHaveBeenCalledTimes(1);
    const signal = client.fetchPoint.mock.calls[0][2] as AbortSignal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal.aborted).toBe(false);

    rendered.unmount();
    expect(signal.aborted).toBe(true);
  });

  /** A request that fails with an AbortError once cancelled, as supabase-js does. */
  const rejectOnAbort = (signal: AbortSignal, message: string) =>
    new Promise<never>((_, reject) => signal.addEventListener("abort", () => reject(new Error(message))));

  it("shows nothing from the previous map's cancelled requests on the next map", async () => {
    client.fetchJointArrays.mockImplementation((id: number, signal: AbortSignal) =>
      id === 1
        ? rejectOnAbort(signal, "Could not load the map's cells: AbortError: aborted")
        : Promise.resolve(ARRAYS),
    );
    client.fetchLabelCodes.mockImplementation((id: number, key: string, signal: AbortSignal) =>
      id === 1
        ? rejectOnAbort(signal, `Could not load ${key}: AbortError: aborted`)
        : Promise.resolve(LABELS[key]),
    );
    const rendered = render(view(1));
    rendered.rerender(view(2));

    await waitFor(() =>
      expect((screen.getByRole("button", { name: "Cell type" }) as HTMLButtonElement).disabled).toBe(false),
    );
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    expect(screen.queryByText(/AbortError/)).toBeNull();
  });

  it("shows the newly clicked cell, not the previous cell's cancelled request", async () => {
    client.fetchPoint.mockImplementation((_id: number, index: number, signal: AbortSignal) =>
      index === 2
        ? rejectOnAbort(signal, "Could not load the cell: AbortError: aborted")
        : Promise.resolve({ barcode: "AAAC-3", datasetId: 20, cellId: 6 }),
    );
    await renderLoaded();

    act(() => lastMap().onPick(2));
    act(() => lastMap().onPick(3));

    const details = screen.getByTestId("integration-point-details");
    expect(await within(details).findByText("AAAC-3")).toBeTruthy();
    expect(within(details).queryByText(/AbortError/)).toBeNull();
  });
});
