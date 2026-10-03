// @vitest-environment jsdom
/**
 * Selecting rendered scans on the wave × accession page and running them
 * (add-cyl-pipeline-ui task 11.3). The real confirm dialog runs against the
 * Supabase double, and the POST goes to a mocked fetch.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { resetSupabaseMock, type Answer, type RecordedQuery } from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";
import { scanMeta } from "@/lib/cyl-pipeline/__fixtures__/rows";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);
// The dialog also reads the model cards (bloom#971); keep that off `fetchSpy`,
// which counts the trigger POST.
vi.mock("@/lib/cyl-pipeline/model-cards", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/cyl-pipeline/model-cards")>()),
  fetchModelCards: async () => ({
    cards: (await import("@/lib/cyl-pipeline/__fixtures__/model-cards")).PRODUCTION_CARDS,
    skipped: 0,
  }),
}));

import { ScanCheckbox, ScanSelectionProvider, SelectAllShown, SelectionBar } from "./ScanSelection";
import { resetSubmissions } from "./submissions";

const fetchSpy = vi.fn();

function respond(q: RecordedQuery): Answer {
  if (q.table === "cyl_scans_extended") {
    const ids = q.arg("in")![1] as number[];
    return { data: ids.map((id) => scanMeta(id)), error: null };
  }
  return { data: [], error: null };
}

const tick = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms));
async function settle() {
  for (let i = 0; i < 10; i++) await tick();
}

/** A grid like the accession page's: each thumbnail is a link, the checkbox sits beside it. */
function Grid({ ids, shown = ids }: { ids: number[]; shown?: number[] }) {
  return (
    <ScanSelectionProvider shownIds={shown}>
      <SelectAllShown />
      {ids.map((id, i) => (
        <div key={i} data-testid={`cell-${i}`}>
          <a href={`/scan/${id}`}>thumbnail {id}</a>
          <ScanCheckbox scanId={id} label={`Select scan ${id}`} />
        </div>
      ))}
      <SelectionBar />
    </ScanSelectionProvider>
  );
}

const box = (id: number, nth = 0) => screen.getAllByRole("checkbox", { name: `Select scan ${id}` })[nth] as HTMLInputElement;
const bar = () => screen.queryByTestId("selection-bar");

beforeEach(() => {
  vi.useFakeTimers();
  resetSupabaseMock(respond);
  resetSubmissions();
  fetchSpy.mockReset();
  vi.stubGlobal("fetch", fetchSpy);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  cleanup();
});

describe("scan selection", () => {
  it("counts by scan id: two thumbnails of one scan are one selection", () => {
    render(<Grid ids={[7, 7, 8]} />);
    fireEvent.click(box(7, 0));
    expect(box(7, 1).checked).toBe(true);
    expect(bar()!.textContent).toContain("1 selected");
    fireEvent.click(box(8));
    expect(bar()!.textContent).toContain("2 selected");
    fireEvent.click(box(7, 1));
    expect(box(7, 0).checked).toBe(false);
    expect(bar()!.textContent).toContain("1 selected");
  });

  it("sits outside any link, and clicking it doesn't navigate", () => {
    const navigations: string[] = [];
    render(<Grid ids={[7]} />);
    document.addEventListener("click", (e) => {
      const link = (e.target as Element).closest("a");
      if (link && !e.defaultPrevented) navigations.push(link.getAttribute("href")!);
    });
    expect(box(7).closest("a")).toBeNull();
    fireEvent.click(box(7));
    expect(navigations).toEqual([]);
    expect(box(7).checked).toBe(true);
  });

  it("selects every shown scan with Select all shown", () => {
    render(<Grid ids={[1, 2, 3]} />);
    fireEvent.click(screen.getByRole("button", { name: "Select all shown" }));
    expect([1, 2, 3].every((id) => box(id).checked)).toBe(true);
    expect(bar()!.textContent).toContain("3 selected");
  });

  it("hides the bar at 0, and says the selection is per page", () => {
    render(<Grid ids={[1, 2]} />);
    expect(bar()).toBeNull();
    fireEvent.click(box(1));
    expect(bar()!.textContent).toContain("Selections are kept on this page only.");
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(bar()).toBeNull();
    expect(box(1).checked).toBe(false);
  });

  it("submits exactly the selected ids with Run selected (3)", async () => {
    fetchSpy.mockResolvedValue({ ok: true, status: 200, headers: new Headers(), json: async () => ({ pipeline_run_id: 91, scan_count: 3, reused_count: 0 }) });
    render(<Grid ids={[4, 9, 2, 6]} />);
    fireEvent.click(box(9));
    fireEvent.click(box(2));
    fireEvent.click(box(6));
    fireEvent.click(screen.getByRole("button", { name: "Run selected (3)" }));
    await settle();
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Run the pipeline on 3 selected scans · 3 scans");
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));
    await settle();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe("/api/cyl/pipeline");
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({ target_level: "scan_ids", scan_ids: [9, 2, 6] });
  });

  it("titles a one-scan selection in the singular (bloom#955)", async () => {
    render(<Grid ids={[7]} />);
    fireEvent.click(box(7));
    fireEvent.click(screen.getByRole("button", { name: "Run selected (1)" }));
    await settle();
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Run the pipeline on 1 selected scan · 1 scan");
  });

  it("disables Run selected over the limit", () => {
    const ids = Array.from({ length: 5001 }, (_, i) => i + 1);
    render(<Grid ids={[1]} shown={ids} />);
    fireEvent.click(screen.getByRole("button", { name: "Select all shown" }));
    const run = screen.getByRole("button", { name: "Run selected (5001)" }) as HTMLButtonElement;
    expect(run.disabled).toBe(true);
    expect(bar()!.textContent).toContain("at most 5000");
  });
});
