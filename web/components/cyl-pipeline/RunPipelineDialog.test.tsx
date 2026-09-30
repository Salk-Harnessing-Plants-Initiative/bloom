// @vitest-environment jsdom
/**
 * The shared confirm dialog (add-cyl-pipeline-ui tasks 11.1a and 11.1b): what
 * it shows before a run starts, and how it submits and reports the outcome.
 * Copy is asserted as the spec writes it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  deferred,
  queriesFor,
  resetSupabaseMock,
  supabaseMock,
  type Answer,
  type Deferred,
  type RecordedQuery,
} from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";
import { ME, OTHER, runRow, scanMeta } from "@/lib/cyl-pipeline/__fixtures__/rows";
import type { RunRow } from "@/lib/cyl-pipeline/realtime-reducer";
import type { ScanMeta } from "@/lib/cyl-pipeline/scan-meta";
import type { TriggerTarget } from "@/lib/cyl-pipeline/trigger-target";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);

import { RunPipelineButton } from "./RunPipelineButton";
import { RunPipelineDialog } from "./RunPipelineDialog";
import { resetSubmissions } from "./submissions";

// The database the dialog reads.
let scans: ScanMeta[];
let latest: { scan_id: number; max_source_id: number | null }[];
let runs: RunRow[];
/** Runs touching experiment 5. */
let members: Set<number>;
/** Runs touching other experiments, by run id. */
let elsewhere: Map<number, number[]>;
let failing: Record<string, Answer["error"]>;
let held: Record<string, Deferred<void>>;
const fetchSpy = vi.fn();

function answer(q: RecordedQuery): Answer {
  const inArgs = q.all("in");
  switch (q.table) {
    case "cyl_scans_extended": {
      let data = [...scans];
      for (const [column, value] of q.all("eq")) data = data.filter((s) => (s as unknown as Record<string, unknown>)[column as string] === value);
      for (const [column, values] of inArgs)
        data = data.filter((s) => (values as unknown[]).includes((s as unknown as Record<string, unknown>)[column as string]));
      data.sort((a, b) => a.scan_id - b.scan_id);
      const window = q.arg("range") as [number, number] | undefined;
      return { data: window ? data.slice(window[0], window[1] + 1) : data, error: null };
    }
    case "cyl_scan_latest_source": {
      const ids = inArgs[0][1] as number[];
      return { data: latest.filter((l) => ids.includes(l.scan_id)), error: null };
    }
    case "cyl_pipeline_runs": {
      const ids = inArgs.find(([c]) => c === "id")?.[1] as number[] | undefined;
      const excluded = q.arg("not") ? ["complete", "failed"] : [];
      const limit = q.arg("limit")?.[0] as number | undefined;
      const rows = runs.filter((r) => (!ids || ids.includes(r.id)) && !excluded.includes(r.status));
      return { data: limit === undefined ? rows : rows.slice(0, limit), error: null };
    }
    case "cyl_pipeline_run_experiments": {
      const experiments = inArgs.find(([c]) => c === "experiment_id")![1] as number[];
      const since = q.arg("gte")?.[1] as string | undefined;
      const touching = [
        ...[...members].map((run_id) => ({ run_id, experiment_id: 5 })),
        ...[...elsewhere].flatMap(([run_id, exps]) => exps.map((experiment_id) => ({ run_id, experiment_id }))),
      ];
      const createdAt = (id: number) => runs.find((r) => r.id === id)?.created_at ?? "";
      return {
        data: touching.filter((t) => experiments.includes(t.experiment_id) && (!since || Date.parse(createdAt(t.run_id)) >= Date.parse(since))),
        error: null,
      };
    }
    default:
      return { data: [], error: null };
  }
}

function respond(q: RecordedQuery): Answer | Promise<Answer> {
  if (failing[q.table]) return { data: null, error: failing[q.table] };
  const gate = held[q.table];
  return gate ? gate.promise.then(() => answer(q)) : answer(q);
}

/** `n` scans of experiment 5 from id `from`, pennycress at day 14 unless overridden. */
const someScans = (n: number, from = 1, overrides: Partial<ScanMeta> = {}) =>
  Array.from({ length: n }, (_, i) => scanMeta(from + i, { experiment_id: 5, ...overrides }));

const EXPERIMENT: TriggerTarget = { target_level: "experiment", target_id: 5 };
const onClose = vi.fn();
const onStarted = vi.fn();

function mount(target: TriggerTarget = EXPERIMENT, title = "experiment Exp five") {
  return render(<RunPipelineDialog target={target} title={title} onClose={onClose} onStarted={onStarted} />);
}
const tick = (ms = 0) => act(() => vi.advanceTimersByTimeAsync(ms));
async function settle() {
  for (let i = 0; i < 10; i++) await tick();
}
const confirmButton = () => screen.queryByRole("button", { name: "Start run" }) as HTMLButtonElement | null;
const dialogText = () => screen.getByRole("dialog").textContent ?? "";
const ack = () => screen.queryByRole("checkbox", { name: /I understand this queues/ }) as HTMLInputElement | null;

/** A fetch answer as the proxy gives it. */
const reply = (status: number, body: unknown, headers: Record<string, string> = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: new Headers(headers),
  json: async () => body,
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-29T12:00:00Z"));
  // 40 scans: 38 with pipeline results, 1 with only source-less traits, 1 with no traits.
  scans = someScans(40);
  latest = [...someScans(38).map((s) => ({ scan_id: s.scan_id, max_source_id: s.scan_id * 10 })), { scan_id: 39, max_source_id: null }];
  runs = [];
  members = new Set();
  elsewhere = new Map();
  failing = {};
  held = {};
  resetSupabaseMock(respond);
  resetSubmissions();
  supabaseMock.session = { access_token: "user-token", user: { id: ME } };
  fetchSpy.mockReset();
  vi.stubGlobal("fetch", fetchSpy);
  onClose.mockReset();
  onStarted.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  cleanup();
});

describe("before the queries settle", () => {
  it.each(["cyl_scans_extended", "cyl_scan_latest_source", "cyl_pipeline_runs"])(
    "keeps confirm disabled until the %s query settles",
    async (table) => {
      runs = [runRow(88, "2026-09-29T11:48:00+00:00", { status: "running" })];
      members = new Set([88]);
      held[table] = deferred<void>();
      mount();
      await settle();
      expect(confirmButton()!.disabled).toBe(true);
      await act(async () => held[table].resolve());
      await settle();
      expect(confirmButton()!.disabled).toBe(false);
    },
  );
});

describe("the headline", () => {
  it("names the target and N", async () => {
    mount();
    await settle();
    const heading = screen.getByRole("heading", { level: 2 });
    expect(heading.textContent).toContain("experiment Exp five");
    expect(heading.textContent).toContain("40 scans");
  });

  it("counts a 2,500-scan experiment across pages", async () => {
    scans = someScans(2500);
    latest = [];
    mount();
    await settle();
    expect(screen.getByRole("heading", { level: 2 }).textContent).toContain("2500 scans");
  });
});

describe("blocking reasons", () => {
  it("says No scans to run for an empty target, with confirm disabled", async () => {
    scans = [];
    mount({ target_level: "wave", target_id: 11 }, "wave 1");
    await settle();
    expect(dialogText()).toContain("No scans to run");
    expect(confirmButton()!.disabled).toBe(true);
  });

  it("names a selected scan that no longer enumerates, with confirm disabled", async () => {
    scans = [scanMeta(1, { experiment_id: 5 }), scanMeta(2, { experiment_id: 5 })];
    mount({ target_level: "scan_ids", scan_ids: [1, 2, 3] }, "3 selected scans");
    await settle();
    const blocker = screen.getByTestId("blockers");
    expect(blocker.textContent).toMatch(/\b3\b/);
    expect(blocker.textContent).not.toMatch(/\b1\b|\b2\b/);
    expect(confirmButton()!.disabled).toBe(true);
  });

  it("blocks a scan_ids target over MAX_TRIGGER_SCAN_IDS", async () => {
    scans = someScans(5001);
    latest = [];
    mount({ target_level: "scan_ids", scan_ids: scans.map((s) => s.scan_id) }, "5001 selected scans");
    await settle();
    expect(screen.getByTestId("blockers").textContent).toContain("5000");
    fireEvent.click(ack()!);
    expect(confirmButton()!.disabled).toBe(true);
  });

  it("allows an 8,000-scan experiment: no size blocker, and confirm is enabled once acknowledged", async () => {
    scans = someScans(8000);
    latest = [];
    mount();
    await settle();
    expect(screen.queryByTestId("blockers")).toBeNull();
    expect(confirmButton()!.disabled).toBe(true);
    fireEvent.click(ack()!);
    expect(confirmButton()!.disabled).toBe(false);
  });
});

describe("the stage-in warning", () => {
  it("counts scans that will fail at stage-in", async () => {
    scans = [...someScans(30, 1, { species_name: " Pennycress " }), ...someScans(8, 31, { plant_age_days: 21 }), ...someScans(2, 39, { plant_age_days: null })];
    mount();
    await settle();
    expect(dialogText()).toContain("2 scans will fail at stage-in — ask a Bloom admin to fix the plant metadata");
  });

  it("is absent when every scan has its metadata", async () => {
    mount();
    await settle();
    expect(confirmButton()!.disabled).toBe(false);
    expect(dialogText()).not.toContain("will fail at stage-in");
  });
});

describe("concurrent runs", () => {
  it("lists a run touching this experiment with its display state, requester, age and link", async () => {
    runs = [runRow(88, "2026-09-29T11:48:00+00:00", { status: "running", scan_count: 40, done_count: 10, requested_by: OTHER })];
    members = new Set([88]);
    mount();
    await settle();
    const entry = screen.getByTestId("concurrent-run-88");
    expect(entry.textContent).toContain("Running · 10 / 40 succeeded");
    expect(entry.textContent).toContain("started 12 min ago");
    expect(entry.textContent).toContain("another member · 0b7e2c91");
    expect(within(entry).getByRole("link", { name: /Run 88/ }).getAttribute("href")).toBe("/app/cyl-pipeline-runs/88");
    // Membership comes from the view, for the enumerated scans' experiments, within 7 days.
    const [view] = queriesFor("cyl_pipeline_run_experiments");
    expect(view.all("in")).toEqual([["experiment_id", [5]]]);
    expect(view.arg("gte")).toEqual(["created_at", "2026-09-22T12:00:00.000Z"]);
  });

  it("calls your own run yours", async () => {
    runs = [runRow(88, "2026-09-29T11:48:00+00:00", { status: "queued", requested_by: ME })];
    members = new Set([88]);
    mount();
    await settle();
    expect(screen.getByTestId("concurrent-run-88").textContent).toContain("you");
  });

  it("shows 10 and then and M more", async () => {
    runs = Array.from({ length: 12 }, (_, i) => runRow(100 - i, `2026-09-29T11:${String(40 - i).padStart(2, "0")}:00+00:00`));
    members = new Set(runs.map((r) => r.id));
    mount();
    await settle();
    expect(screen.queryAllByTestId(/^concurrent-run-/)).toHaveLength(10);
    expect(dialogText()).toContain("and 2 more");
  });

  it("lists nothing for a run that doesn't touch the experiment", async () => {
    runs = [runRow(88, "2026-09-29T11:48:00+00:00")];
    elsewhere = new Map([[88, [6]]]);
    mount();
    await settle();
    expect(confirmButton()!.disabled).toBe(false);
    expect(screen.queryByTestId("concurrent-run-88")).toBeNull();
  });
});

describe("the pre-check", () => {
  it("reads K of N, with the full text in its details", async () => {
    mount();
    await settle();
    expect(dialogText()).toContain("38 of 40 already have pipeline results.");
    const details = screen.getByTestId("precheck-details");
    expect(details.tagName).toBe("DETAILS");
    expect(details.textContent).toContain(
      "1 more scans have only traits without a recorded source (typically older, pre-pipeline data), which a successful run replaces in trait views. All 40 will be sent; the cluster skips scans it has already processed with the same models and code.",
    );
  });

  it("omits the L clause when L = 0", async () => {
    latest = latest.filter((l) => l.max_source_id !== null);
    mount();
    await settle();
    const details = screen.getByTestId("precheck-details").textContent ?? "";
    expect(details).not.toContain("more scans have only traits");
    expect(details).toContain("All 40 will be sent; the cluster skips scans it has already processed with the same models and code.");
  });

  it("shows the all-results notice in place of the line when K = N", async () => {
    scans = someScans(12);
    latest = scans.map((s) => ({ scan_id: s.scan_id, max_source_id: 1 }));
    mount();
    await settle();
    expect(dialogText()).toContain(
      "All 12 scans already have pipeline results. The run will still be created and sent to the cluster, which skips scans it has already processed with the same models and code.",
    );
    expect(dialogText()).not.toContain("of 12 already have pipeline results");
  });
});

describe("resolved params", () => {
  it("groups by species, mode and age, collapsed beyond 3, with the #897 caption and no param input", async () => {
    scans = [
      ...someScans(30, 1, { species_name: " Pennycress ", plant_age_days: 14 }),
      ...someScans(8, 31, { species_name: "Pennycress", plant_age_days: 21 }),
      ...someScans(3, 39, { plant_age_days: 7 }),
      ...someScans(2, 42, { plant_age_days: 3 }),
      ...someScans(1, 44, { plant_age_days: 1 }),
    ];
    mount();
    await settle();
    const params = screen.getByTestId("params");
    const group = (text: string) => within(params).getByText(text);
    for (const shown of ["pennycress · cylinder · 14 — 30", "pennycress · cylinder · 21 — 8", "pennycress · cylinder · 7 — 3"]) {
      expect(group(shown).closest("details")).toBeNull();
    }
    for (const collapsed of ["pennycress · cylinder · 3 — 2", "pennycress · cylinder · 1 — 1"]) {
      const details = group(collapsed).closest("details") as HTMLDetailsElement;
      expect(details).not.toBeNull();
      expect(details.open).toBe(false);
    }
    expect(params.textContent).toContain("Parameters come from each scan's metadata; overrides aren't supported yet");
    expect(within(params).getByRole("link", { name: /bloom#897/ }).getAttribute("href")).toBe(
      "https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/897",
    );
    expect(screen.getByRole("dialog").querySelectorAll("input:not([type=checkbox]), textarea, select")).toHaveLength(0);
  });
});

describe("the large-run acknowledgement", () => {
  it("keeps confirm disabled at N = 800 until it is ticked", async () => {
    scans = someScans(800);
    latest = [];
    mount();
    await settle();
    expect(ack()!.parentElement!.textContent).toContain(
      "I understand this queues 800 scans on the shared GPU cluster; runs can't be cancelled from Bloom.",
    );
    expect(confirmButton()!.disabled).toBe(true);
    fireEvent.click(ack()!);
    expect(confirmButton()!.disabled).toBe(false);
  });

  it("is not asked below 500", async () => {
    scans = someScans(499);
    latest = [];
    mount();
    await settle();
    expect(ack()).toBeNull();
    expect(confirmButton()!.disabled).toBe(false);
  });
});

describe("banned phrases", () => {
  it("never says will run, will be skipped or reused, before or after starting", async () => {
    runs = [runRow(88, "2026-09-29T11:48:00+00:00", { status: "running", reused_count: 7 })];
    members = new Set([88]);
    scans = [...someScans(600), ...someScans(2, 601, { plant_age_days: null })];
    mount();
    await settle();
    const banned = /will run|will be skipped|reused/i;
    expect(dialogText()).not.toMatch(banned);
    fetchSpy.mockResolvedValue(reply(200, { pipeline_run_id: 91, scan_count: 602, reused_count: 5 }));
    fireEvent.click(ack()!);
    fireEvent.click(confirmButton()!);
    await settle();
    expect(dialogText()).toContain("Run 91 started");
    expect(dialogText()).not.toMatch(banned);
  });
});

describe("submitting", () => {
  it("sends exactly one POST, to exactly /api/cyl/pipeline, for two synchronous clicks", async () => {
    const pending = deferred<ReturnType<typeof reply>>();
    fetchSpy.mockReturnValue(pending.promise);
    mount();
    await settle();
    const button = confirmButton()!;
    // One act(), so React doesn't re-render (and disable the button) between
    // the clicks: only the synchronous in-flight flag can stop the second.
    act(() => {
      button.click();
      button.click();
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/api/cyl/pipeline");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ target_level: "experiment", target_id: 5 });
    await act(async () => pending.resolve(reply(200, { pipeline_run_id: 91, scan_count: 40, reused_count: 0 })));
    await settle();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("sends a selection's ids exactly", async () => {
    scans = [scanMeta(4, { experiment_id: 5 }), scanMeta(9, { experiment_id: 5 }), scanMeta(2, { experiment_id: 5 })];
    fetchSpy.mockResolvedValue(reply(200, { pipeline_run_id: 91, scan_count: 3, reused_count: 0 }));
    mount({ target_level: "scan_ids", scan_ids: [4, 9, 2] }, "3 selected scans");
    await settle();
    fireEvent.click(confirmButton()!);
    await settle();
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({ target_level: "scan_ids", scan_ids: [4, 9, 2] });
  });

  it("shows the started run, its link and the timing note, and offers no confirm", async () => {
    fetchSpy.mockResolvedValue(reply(200, { pipeline_run_id: 91, scan_count: 40, reused_count: 0 }));
    mount();
    await settle();
    fireEvent.click(confirmButton()!);
    await settle();
    expect(dialogText()).toContain("Run 91 started with 40 scans");
    expect(screen.getByRole("link", { name: /Open run 91/ }).getAttribute("href")).toBe("/app/cyl-pipeline-runs/91");
    expect(dialogText()).toContain(
      "Results arrive when each batch of up to 25 scans finishes; counts often stay at 0 for most of the run. Reload the traits page to see new results.",
    );
    expect(confirmButton()).toBeNull();
    expect(dialogText()).not.toMatch(/counted \d+ scans/);
    expect(onStarted).toHaveBeenCalledWith({
      pipeline_run_id: 91,
      scan_count: 40,
      target: EXPERIMENT,
      requested_by: ME,
      started_at: "2026-09-29T12:00:00.000Z",
    });
  });

  it("notes when the trigger's scan_count differs from N", async () => {
    fetchSpy.mockResolvedValue(reply(200, { pipeline_run_id: 91, scan_count: 41, reused_count: 0 }));
    mount();
    await settle();
    fireEvent.click(confirmButton()!);
    await settle();
    expect(dialogText()).toContain("Run 91 started with 41 scans");
    expect(dialogText()).toContain("The pipeline service counted 41 scans; this dialog counted 40.");
  });

  it("explains a 429 and re-enables confirm", async () => {
    fetchSpy.mockResolvedValue(reply(429, { detail: "Too many requests to the pipeline service. Try again shortly." }, { "Retry-After": "60" }));
    mount();
    await settle();
    fireEvent.click(confirmButton()!);
    await settle();
    expect(dialogText()).toContain("Too many requests — this limit is shared with other workflow actions, such as video generation and Cell Ranger runs. Try again in about a minute.");
    expect(confirmButton()!.disabled).toBe(false);
    fireEvent.click(confirmButton()!);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["a 502", () => fetchSpy.mockResolvedValue(reply(502, { detail: "upstream text" }))],
    ["a 504", () => fetchSpy.mockResolvedValue(reply(504, { detail: "upstream text" }))],
    ["a 500", () => fetchSpy.mockResolvedValue(reply(500, { detail: "upstream text" }))],
    ["a 503", () => fetchSpy.mockResolvedValue(reply(503, { detail: "upstream text" }))],
    ["a network failure", () => fetchSpy.mockRejectedValue(new TypeError("Failed to fetch"))],
    ["a malformed success", () => fetchSpy.mockResolvedValue(reply(200, { ok: true }))],
  ])("after %s, says the run may have started, links the runs list, and keeps confirm disabled", async (_what, arrange) => {
    arrange();
    mount();
    await settle();
    fireEvent.click(confirmButton()!);
    await settle();
    expect(dialogText()).toContain("The run may have started");
    expect(screen.getByRole("link", { name: /Cylinder pipeline runs/ }).getAttribute("href")).toBe("/app/cyl-pipeline-runs");
    expect(confirmButton()?.disabled ?? true).toBe(true);
    expect(dialogText()).not.toContain("upstream text");
    expect(onStarted).not.toHaveBeenCalled();
  });

  it("says the session expired on a 401", async () => {
    fetchSpy.mockResolvedValue(reply(401, { detail: "Your session has expired. Sign in again to start a run." }));
    mount();
    await settle();
    fireEvent.click(confirmButton()!);
    await settle();
    expect(dialogText()).toContain("session expired — sign in again");
  });

  it.each([403, 413, 415, 404, 422])("shows a %s's detail and allows another try", async (status) => {
    fetchSpy.mockResolvedValue(reply(status, { detail: "scan_ids not found: [3]" }));
    mount();
    await settle();
    fireEvent.click(confirmButton()!);
    await settle();
    expect(dialogText()).toContain("scan_ids not found: [3]");
    expect(confirmButton()!.disabled).toBe(false);
  });
});

describe("query failures", () => {
  it.each(["cyl_scans_extended", "cyl_scan_latest_source", "cyl_pipeline_run_experiments", "cyl_pipeline_runs"])(
    "shows an error and keeps confirm disabled when %s fails",
    async (table) => {
      runs = [runRow(88, "2026-09-29T11:48:00+00:00")];
      members = new Set([88]);
      failing[table] = { message: "statement timeout", code: "57014" };
      mount();
      await settle();
      expect(screen.getByRole("alert").textContent).toContain("statement timeout");
      expect(confirmButton()!.disabled).toBe(true);
    },
  );
});

describe("concurrent runs, per spec", () => {
  it("looks them up for the enumerated scans' experiments, not the target's id", async () => {
    scans = [...someScans(3, 1, { wave_id: 11, experiment_id: 5 }), ...someScans(2, 4, { wave_id: 11, experiment_id: 6 })];
    runs = [runRow(90, "2026-09-29T11:50:00+00:00", { status: "running" })];
    elsewhere = new Map([[90, [6]]]);
    mount({ target_level: "wave", target_id: 11 }, "wave 1");
    await settle();
    expect(screen.getByTestId("concurrent-run-90")).toBeTruthy();
    expect(queriesFor("cyl_pipeline_run_experiments")[0].all("in")).toEqual([["experiment_id", [5, 6]]]);
  });

  it("isn't crowded out by unfinished runs elsewhere", async () => {
    const others = Array.from({ length: 25 }, (_, i) => runRow(200 + i, `2026-09-29T11:${String(30 + i).padStart(2, "0")}:00+00:00`));
    runs = [...others, runRow(88, "2026-09-29T11:00:00+00:00", { status: "running" })];
    elsewhere = new Map(others.map((r) => [r.id, [6]]));
    members = new Set([88]);
    mount();
    await settle();
    expect(screen.getByTestId("concurrent-run-88")).toBeTruthy();
    expect(screen.queryAllByTestId(/^concurrent-run-/)).toHaveLength(1);
  });

  it("says how many more there really are", async () => {
    runs = Array.from({ length: 25 }, (_, i) => runRow(100 - i, `2026-09-29T11:${String(40 - i).padStart(2, "0")}:00+00:00`));
    members = new Set(runs.map((r) => r.id));
    mount();
    await settle();
    expect(screen.queryAllByTestId(/^concurrent-run-/)).toHaveLength(10);
    expect(dialogText()).toContain("and 15 more");
  });

  it("leaves out runs that are complete, failed, or settled by their counts", async () => {
    runs = [
      runRow(88, "2026-09-29T11:48:00+00:00", { status: "complete", scan_count: 40, done_count: 30 }),
      runRow(87, "2026-09-29T11:47:00+00:00", { status: "failed" }),
      runRow(86, "2026-09-29T11:46:00+00:00", { status: "partial", scan_count: 2, done_count: 1, failed_count: 1 }),
      runRow(85, "2026-09-29T11:45:00+00:00", { status: "queued" }),
    ];
    members = new Set([88, 87, 86, 85]);
    mount();
    await settle();
    expect(screen.queryAllByTestId(/^concurrent-run-/).map((e) => e.dataset.testid)).toEqual(["concurrent-run-85"]);
  });
});

describe("boundaries", () => {
  it("asks for the acknowledgement at exactly 500 scans", async () => {
    scans = someScans(500);
    latest = [];
    mount();
    await settle();
    expect(ack()).not.toBeNull();
    expect(confirmButton()!.disabled).toBe(true);
  });

  it("allows exactly MAX_TRIGGER_SCAN_IDS selected scans", async () => {
    scans = someScans(5000);
    latest = [];
    mount({ target_level: "scan_ids", scan_ids: scans.map((s) => s.scan_id) }, "5000 selected scans");
    await settle();
    expect(screen.queryByTestId("blockers")).toBeNull();
    fireEvent.click(ack()!);
    expect(confirmButton()!.disabled).toBe(false);
  });

  it("shows no pre-check and no params for an empty target", async () => {
    scans = [];
    latest = [];
    mount({ target_level: "wave", target_id: 11 }, "wave 1");
    await settle();
    expect(dialogText()).toContain("No scans to run");
    expect(dialogText()).not.toContain("already have pipeline results");
    expect(screen.queryByTestId("params")).toBeNull();
  });

  it("lists at most 20 missing ids, then how many more", async () => {
    scans = [];
    mount({ target_level: "scan_ids", scan_ids: Array.from({ length: 25 }, (_, i) => 101 + i) }, "25 selected scans");
    await settle();
    const blocker = screen.getByTestId("blockers").textContent ?? "";
    expect(blocker).toContain("120");
    expect(blocker).not.toContain("121");
    expect(blocker).toContain("and 5 more");
  });

  it("shows exactly 3 parameter sets without a disclosure", async () => {
    scans = [...someScans(3, 1, { plant_age_days: 7 }), ...someScans(2, 4, { plant_age_days: 3 }), ...someScans(1, 6, { plant_age_days: 1 })];
    mount();
    await settle();
    expect(screen.getByTestId("params").querySelector("details")).toBeNull();
  });
});

describe("one submission per target, whatever happens to the dialog", () => {
  const openButton = () => fireEvent.click(screen.getByRole("button", { name: "Run experiment" }));
  const mountButton = () => render(<RunPipelineButton target={EXPERIMENT} label="Run experiment" title="experiment Exp five" />);

  it("keeps a send going when the dialog is closed, and shows it again on reopening instead of a fresh confirm", async () => {
    const pending = deferred<ReturnType<typeof reply>>();
    fetchSpy.mockReturnValue(pending.promise);
    mountButton();
    openButton();
    await settle();
    fireEvent.click(confirmButton()!);
    expect(dialogText()).toContain("You can close this");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await settle();
    expect(screen.queryByRole("dialog")).toBeNull();

    openButton();
    await settle();
    expect(confirmButton()?.disabled ?? true).toBe(true);
    expect(dialogText()).toContain("Starting the run");
    fireEvent.click(screen.getByRole("button", { name: "Starting…" }));
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    await act(async () => pending.resolve(reply(200, { pipeline_run_id: 91, scan_count: 40, reused_count: 0 })));
    await settle();
    expect(dialogText()).toContain("Run 91 started with 40 scans");
  });

  it("still shows the may-have-started warning after a 504, closing and reopening", async () => {
    fetchSpy.mockResolvedValue(reply(504, { detail: "timeout" }));
    mountButton();
    openButton();
    await settle();
    fireEvent.click(confirmButton()!);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await settle();
    openButton();
    await settle();
    expect(dialogText()).toContain("The run may have started");
    expect(confirmButton()?.disabled ?? true).toBe(true);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("allows another try after a refusal, on reopening", async () => {
    fetchSpy.mockResolvedValue(reply(429, { detail: "slow down" }));
    mountButton();
    openButton();
    await settle();
    fireEvent.click(confirmButton()!);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await settle();
    openButton();
    await settle();
    expect(confirmButton()!.disabled).toBe(false);
  });

  it("sends the target it checked, even if its caller's target changes while it is open", async () => {
    scans = [scanMeta(1, { experiment_id: 5 }), scanMeta(2, { experiment_id: 5 }), scanMeta(3, { experiment_id: 5 })];
    fetchSpy.mockResolvedValue(reply(200, { pipeline_run_id: 91, scan_count: 3, reused_count: 0 }));
    const view = render(<RunPipelineDialog target={{ target_level: "scan_ids", scan_ids: [1, 2, 3] }} title="3 scans" onClose={onClose} />);
    await settle();
    const reads = queriesFor("cyl_scans_extended").length;
    view.rerender(<RunPipelineDialog target={{ target_level: "scan_ids", scan_ids: [1, 2] }} title="2 scans" onClose={onClose} />);
    await settle();
    expect(queriesFor("cyl_scans_extended")).toHaveLength(reads);
    expect(screen.getByRole("heading", { level: 2 }).textContent).toContain("3 scans");
    fireEvent.click(confirmButton()!);
    await settle();
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({ target_level: "scan_ids", scan_ids: [1, 2, 3] });
  });

  it("takes focus when it opens, and closes on Escape", async () => {
    mount();
    await settle();
    expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
