// @vitest-environment jsdom
/**
 * The Video Jobs list: both kinds in one table, live progress from both job
 * tables, the links back to where each video plays, and its filters.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  deferred,
  liveChannels,
  queriesFor,
  resetSupabaseMock,
  supabaseMock,
  type Answer,
  type RecordedQuery,
} from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";
import { ME, OTHER } from "@/lib/cyl-pipeline/__fixtures__/rows";
import type { ActiveJob, ListedJob, PlateJob, ScanJob } from "@/lib/video-jobs/list";

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
/** Answers in place of the normal responder for one table, e.g. to fail or hold a read. */
let override: Partial<Record<string, () => Answer | Promise<Answer>>> = {};

const strip = ({ kind: _kind, ...row }: ListedJob) => row;

function respond(q: RecordedQuery): Answer | Promise<Answer> {
  const special = override[q.table];
  if (special) return special();
  const filtered = (rows: ListedJob[]) => {
    const mine = q.all("eq").find(([col]) => col === "requested_by");
    const statuses = q.all("in").find(([col]) => col === "status");
    const before = q.arg("lte")?.[1] as string | undefined;
    const limit = (q.arg("limit")?.[0] as number | undefined) ?? rows.length;
    return [...rows]
      .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
      .filter((r) => !mine || r.requested_by === mine[1])
      .filter((r) => !statuses || (statuses[1] as string[]).includes(r.status))
      .filter((r) => !before || r.created_at <= before)
      .slice(0, limit)
      .map(strip);
  };
  if (q.table === "gravi_plate_video_jobs") return { data: filtered(plates), error: null };
  if (q.table === "cyl_video_jobs") return { data: filtered(scans), error: null };
  if (q.table === "gravi_experiments") return { data: [{ id: 12, name: "Grav-12", species_id: 2 }], error: null };
  if (q.table === "cyl_scans_extended") return { data: PLACES.scans, error: null };
  if (q.table === "cyl_experiments") return { data: [{ id: 3, name: "Cyl-3", species_id: 1 }], error: null };
  return { data: [], error: null };
}

const activeOf = (rows: ListedJob[]): ActiveJob[] =>
  rows
    .filter((r) => r.status === "queued" || r.status === "rendering")
    .map(({ kind, id, status, created_at, updated_at }) => ({ kind, id, status, created_at, updated_at }));

function mount(props: Partial<Parameters<typeof VideoJobsLive>[0]> = {}) {
  return render(
    <VideoJobsLive
      initialJobs={props.initialJobs ?? [...plates, ...scans]}
      initialHasOlder={props.initialHasOlder ?? false}
      initialPlaces={props.initialPlaces ?? PLACES}
      initialActive={props.initialActive ?? activeOf([...plates, ...scans])}
      currentUserId={ME}
      initialError={props.initialError ?? null}
      highlightJobId={props.highlightJobId ?? null}
      highlightedJob={props.highlightedJob ?? null}
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
  override = {};
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
    expect(row(SCAN_ID).textContent).toContain("Downloaded 10 of 72 frames");
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
    expect(row(SCAN_ID).textContent).toContain("Downloaded 50 of 72 frames");
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
    const filtered = queriesFor("cyl_video_jobs").filter((q) =>
      q.all("eq").some(([col, value]) => col === "requested_by" && value === ME)
    );
    expect(filtered).not.toHaveLength(0);
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

describe("queue position", () => {
  it("counts another member's earlier job even with Only mine ticked", async () => {
    const theirs = plate({ id: "44444444-0000-0000-0000-000000000000", requested_by: OTHER, created_at: at(10) });
    plates = [plate(), theirs];
    mount();
    expect(row(PLATE_ID).textContent).toContain("Queued — 1 ahead");
    await subscribe();
    fireEvent.click(screen.getByRole("checkbox", { name: "Only mine" }));
    await tick();
    expect(screen.queryByTestId(`job-${theirs.id}`)).toBeNull();
    expect(row(PLATE_ID).textContent).toContain("Queued — 1 ahead");
  });

  it("moves up as the job ahead finishes, whether or not it is listed", async () => {
    const theirs = plate({ id: "44444444-0000-0000-0000-000000000000", requested_by: OTHER, created_at: at(10) });
    plates = [plate(), theirs];
    mount({ initialJobs: [plate()] });
    await subscribe();
    // Rendering, it is still ahead; finished, the line moves.
    await emit("gravi_plate_video_jobs", "UPDATE", { id: theirs.id, status: "rendering" });
    expect(row(PLATE_ID).textContent).toContain("Queued — 1 ahead");
    await emit("gravi_plate_video_jobs", "UPDATE", { id: theirs.id, status: "rendered" });
    expect(row(PLATE_ID).textContent).toContain("Queued — next up");
  });

  it("says a worker may be stopped only when nothing of its kind is rendering", () => {
    plates = [plate({ created_at: at(20) })];
    scans = [];
    vi.setSystemTime(new Date("2026-10-29T10:40:00Z"));
    mount();
    expect(row(PLATE_ID).textContent).toContain("may be stopped");
    cleanup();
    plates = [plate({ created_at: at(20) }), plate({ id: "55555555-0000-0000-0000-000000000000", status: "rendering", created_at: at(19), updated_at: at(39) })];
    mount();
    expect(row(PLATE_ID).textContent).not.toContain("may be stopped");
  });
});

describe("the job the visitor came for", () => {
  it("says it carries on, with a way back to its page", () => {
    mount({ highlightJobId: PLATE_ID, highlightedJob: plate() });
    const banner = screen.getByRole("status", { name: "Your video job" });
    expect(banner.textContent).toContain("This is the video you asked for");
    expect(banner.textContent).toContain("carries on if you leave");
    expect(within(banner).getByRole("link", { name: "Back to its page" }).getAttribute("href")).toBe(
      "/app/plate-phenotypes/2/12/wave/1/P7"
    );
  });

  it("says when the request joined someone else's", () => {
    mount({ highlightJobId: SCAN_ID, highlightedJob: scan() });
    expect(screen.getByRole("status", { name: "Your video job" }).textContent).toContain("joined one already in the queue");
  });

  it("shows the job in the banner when it is not on the first page, and follows it live", async () => {
    const old = plate({ id: "66666666-0000-0000-0000-000000000000", created_at: at(1) });
    mount({ highlightJobId: old.id, highlightedJob: old, initialPlaces: PLACES });
    const banner = screen.getByRole("status", { name: "Your video job" });
    expect(within(banner).getByTestId(`job-${old.id}`)).toBeTruthy();
    await subscribe();
    await emit("gravi_plate_video_jobs", "UPDATE", { id: old.id, status: "rendering", stage: "encoding" });
    expect(within(banner).getByTestId(`job-${old.id}`).textContent).toContain("Encoding");
  });

  it("says when the job could not be found", () => {
    mount({ highlightJobId: "77777777-0000-0000-0000-000000000000", highlightedJob: null });
    expect(screen.getByRole("status", { name: "Your video job" }).textContent).toContain("could not be found");
  });
});

describe("loading older jobs", () => {
  it("asks for jobs at or before the oldest held, and adds them below", async () => {
    const older = scan({ id: "88888888-0000-0000-0000-000000000000", status: "rendered", stage: null, created_at: at(2) });
    scans = [scan(), older];
    mount({ initialJobs: [plate(), scan()], initialHasOlder: true });
    await subscribe();
    // The resync read both rows already; start again from the first page alone.
    cleanup();
    resetSupabaseMock(respond);
    mount({ initialJobs: [plate(), scan()], initialHasOlder: true });
    fireEvent.click(screen.getByRole("button", { name: "Load older jobs" }));
    await tick();
    const asked = queriesFor("cyl_video_jobs").at(-1)!;
    expect(asked.arg("lte")).toEqual(["created_at", at(19)]);
    expect(row(older.id)).toBeTruthy();
  });
});

describe("links back", () => {
  it("looks a job's place up when the server could not, and keeps asking after a failure", async () => {
    override = { gravi_experiments: () => ({ data: null, error: { message: "blip" } }) };
    mount({ initialPlaces: { plates: [], scans: PLACES.scans } });
    expect(within(row(PLATE_ID)).queryByRole("link")).toBeNull();
    await subscribe();
    expect(within(row(PLATE_ID)).queryByRole("link")).toBeNull();

    // The job's next event asks again.
    override = {};
    await emit("gravi_plate_video_jobs", "UPDATE", { id: PLATE_ID, status: "rendering" });
    await tick();
    expect(within(row(PLATE_ID)).getByRole("link", { name: "P7 · wave 1" })).toBeTruthy();
  });

  it("reads an experiment's place once, however many of its jobs arrive", async () => {
    mount({ initialPlaces: { plates: [], scans: PLACES.scans } });
    await subscribe();
    const reads = queriesFor("gravi_experiments").length;
    for (const id of ["99999999-0000-0000-0000-000000000001", "99999999-0000-0000-0000-000000000002"]) {
      await emit("gravi_plate_video_jobs", "INSERT", { ...strip(plate({ id, created_at: at(29) })) });
    }
    await tick();
    expect(queriesFor("gravi_experiments").length).toBe(reads);
  });
});

describe("resync", () => {
  it("closes the gap before the subscription on the first SUBSCRIBED", async () => {
    mount({ initialJobs: [plate()] });
    expect(screen.queryByTestId(`job-${SCAN_ID}`)).toBeNull();
    await subscribe();
    expect(row(SCAN_ID)).toBeTruthy();
  });

  it("hides what a filter rules out at once, before its snapshot lands", async () => {
    mount();
    await subscribe();
    const pending = deferred<Answer>();
    override = { cyl_video_jobs: () => pending.promise };
    fireEvent.click(screen.getByRole("checkbox", { name: "Only mine" }));
    expect(screen.queryByTestId(`job-${SCAN_ID}`)).toBeNull();
    await act(async () => pending.resolve({ data: [], error: null }));
  });
});
