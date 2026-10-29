// @vitest-environment jsdom
/**
 * The Video Jobs list: both kinds in one table, live progress from both job
 * tables, the links back to where each video plays, and its filters.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  liveChannels,
  queriesFor,
  resetSupabaseMock,
  supabaseMock,
  type Answer,
  type RecordedQuery,
} from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";
import { ME, OTHER } from "@/lib/cyl-pipeline/__fixtures__/rows";
import type { ListedJob, PlateJob, ScanJob } from "@/lib/video-jobs/list";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);

import { VideoJobsLive, type Places } from "./VideoJobsLive";

const at = (minute: number) => `2026-10-29T10:${String(minute).padStart(2, "0")}:00+00:00`;
const PLATE_ID = "11111111-0000-0000-0000-000000000000";
const SCAN_ID = "22222222-0000-0000-0000-000000000000";

function plate(over: Partial<PlateJob> = {}): PlateJob {
  return {
    kind: "plate",
    id: PLATE_ID,
    experiment_id: 12,
    plate_id: "P7",
    wave_number: 1,
    status: "queued",
    stage: null,
    frames_done: null,
    frames_total: null,
    error_code: null,
    requested_by: ME,
    created_at: at(20),
    started_at: null,
    updated_at: at(20),
    completed_at: null,
    ...over,
  };
}

function scan(over: Partial<ScanJob> = {}): ScanJob {
  return {
    kind: "scan",
    id: SCAN_ID,
    experiment_id: 3,
    scan_id: 44,
    status: "rendering",
    stage: "downloading",
    frames_done: 10,
    frames_total: 72,
    error_code: null,
    requested_by: OTHER,
    created_at: at(19),
    started_at: at(19),
    updated_at: at(29),
    completed_at: null,
    ...over,
  };
}

const PLACES: Places = {
  plates: [{ experiment_id: 12, name: "Grav-12", species_id: 2 }],
  scans: [{ scan_id: 44, experiment_name: "Cyl-3", species_id: 1, wave_id: 7, accession_id: 9, plant_age_days: 5 }],
};

let plates: PlateJob[] = [];
let scans: ScanJob[] = [];

const strip = ({ kind: _kind, ...row }: ListedJob) => row;

function respond(q: RecordedQuery): Answer {
  const filtered = (rows: ListedJob[]) => {
    const mine = q.all("eq").find(([col]) => col === "requested_by");
    const statuses = q.all("in").find(([col]) => col === "status");
    return rows
      .filter((r) => !mine || r.requested_by === mine[1])
      .filter((r) => !statuses || (statuses[1] as string[]).includes(r.status))
      .map(strip);
  };
  if (q.table === "gravi_plate_video_jobs") return { data: filtered(plates), error: null };
  if (q.table === "cyl_video_jobs") return { data: filtered(scans), error: null };
  if (q.table === "gravi_experiments") return { data: [{ id: 12, name: "Grav-12", species_id: 2 }], error: null };
  if (q.table === "cyl_scans_extended") return { data: PLACES.scans, error: null };
  return { data: [], error: null };
}

function mount(props: Partial<Parameters<typeof VideoJobsLive>[0]> = {}) {
  return render(
    <VideoJobsLive
      initialJobs={props.initialJobs ?? [...plates, ...scans]}
      initialHasOlder={false}
      initialPlaces={props.initialPlaces ?? PLACES}
      currentUserId={ME}
      initialError={props.initialError ?? null}
      highlightJobId={props.highlightJobId ?? null}
    />
  );
}

const channel = () => liveChannels()[0];
const tick = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms));
async function subscribe() {
  await act(async () => channel().status("SUBSCRIBED"));
  await tick();
}
const emit = (table: string, eventType: "INSERT" | "UPDATE", row: Record<string, unknown>) =>
  act(async () => channel().emit({ table, eventType, new: row }));
const row = (id: string) => screen.getByTestId(`job-${id}`);

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-29T10:30:00Z"));
  plates = [plate()];
  scans = [scan()];
  resetSupabaseMock(respond);
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("the list", () => {
  it("shows plate and cylinder scan jobs together, newest first", () => {
    mount();
    const ids = screen.queryAllByTestId(/^job-/).map((el) => el.dataset.testid);
    expect(ids).toEqual([`job-${PLATE_ID}`, `job-${SCAN_ID}`]);
    expect(row(PLATE_ID).textContent).toContain("Plate time-lapse");
    expect(row(SCAN_ID).textContent).toContain("Cylinder scan");
  });

  it("links each job back to the page its video plays on", () => {
    mount();
    expect(within(row(PLATE_ID)).getByRole("link", { name: "P7 · wave 1" }).getAttribute("href")).toBe(
      "/app/plate-phenotypes/2/12/wave/1/P7"
    );
    expect(within(row(SCAN_ID)).getByRole("link", { name: "Scan 44 · day 5" }).getAttribute("href")).toBe(
      "/app/phenotypes/1/3/7/9/44"
    );
  });

  it("says who asked, and where each job is", () => {
    mount();
    expect(row(PLATE_ID).textContent).toContain("you");
    expect(row(PLATE_ID).textContent).toContain("Queued — next up");
    expect(row(SCAN_ID).textContent).toContain("another member");
    expect(row(SCAN_ID).textContent).toContain("Downloading frame 10 of 72");
    expect(within(row(SCAN_ID)).getByRole("progressbar").getAttribute("aria-valuenow")).toBe("10");
  });

  it("gives a failed job's reason with its reference, and a finished one a link to watch it", () => {
    plates = [plate({ status: "failed", error_code: "no_frames" })];
    scans = [scan({ status: "rendered", stage: null })];
    mount();
    expect(row(PLATE_ID).textContent).toContain("no captures with an image");
    expect(row(PLATE_ID).textContent).toContain("(job 11111111)");
    expect(within(row(SCAN_ID)).getByRole("link", { name: "watch it" })).toBeTruthy();
  });

  it("highlights the job the visitor came to follow", () => {
    mount({ highlightJobId: SCAN_ID });
    expect(row(SCAN_ID).getAttribute("aria-current")).toBe("true");
    expect(row(PLATE_ID).getAttribute("aria-current")).toBeNull();
  });

  it("warns when a rendering job has gone quiet", () => {
    scans = [scan({ updated_at: at(20) })];
    mount();
    expect(row(SCAN_ID).textContent).toContain("may be stuck");
  });
});

describe("live updates", () => {
  it("listens to both job tables", () => {
    mount();
    expect(supabaseMock.channels[0].bindings.map((b) => b.filter.table)).toEqual([
      "gravi_plate_video_jobs",
      "gravi_plate_video_jobs",
      "cyl_video_jobs",
      "cyl_video_jobs",
    ]);
  });

  it("moves a job's progress as the worker reports it", async () => {
    mount();
    await subscribe();
    await emit("cyl_video_jobs", "UPDATE", { id: SCAN_ID, frames_done: 50 });
    expect(row(SCAN_ID).textContent).toContain("Downloading frame 50 of 72");
  });

  it("adds a job someone just asked for", async () => {
    mount();
    await subscribe();
    const fresh = "33333333-0000-0000-0000-000000000000";
    await emit("gravi_plate_video_jobs", "INSERT", { ...strip(plate({ id: fresh, created_at: at(29) })) });
    expect(row(fresh).textContent).toContain("Plate time-lapse");
  });
});

describe("filters", () => {
  it("shows only the visitor's own jobs, and asks the database for only those", async () => {
    mount();
    await subscribe();
    fireEvent.click(screen.getByRole("checkbox", { name: "Only mine" }));
    await tick();
    expect(screen.queryByTestId(`job-${SCAN_ID}`)).toBeNull();
    const asked = queriesFor("cyl_video_jobs").at(-1)!;
    expect(asked.all("eq")).toContainEqual(["requested_by", ME]);
  });

  it("shows one kind of video", async () => {
    mount();
    await subscribe();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "scan" } });
    await tick();
    expect(screen.queryByTestId(`job-${PLATE_ID}`)).toBeNull();
    expect(row(SCAN_ID)).toBeTruthy();
  });

  it("shows only jobs still queued or rendering", async () => {
    plates = [plate({ status: "rendered" })];
    mount();
    await subscribe();
    fireEvent.click(screen.getByRole("checkbox", { name: "Only queued or rendering" }));
    await tick();
    expect(screen.queryByTestId(`job-${PLATE_ID}`)).toBeNull();
    expect(row(SCAN_ID)).toBeTruthy();
  });
});
