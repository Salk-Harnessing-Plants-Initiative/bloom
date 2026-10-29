// @vitest-environment jsdom
/** The Video Jobs page renders its server snapshot, highlights the job asked for, and says when it can't load. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { mockClient, queriesFor, resetSupabaseMock, supabaseMock } from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";
import { ME } from "@/lib/cyl-pipeline/__fixtures__/rows";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => mockClient,
  getUser: async () => ({ id: "4965b3af-ccfe-40f5-814b-447e1f726e1b" }),
}));

import VideoJobsPage from "./page";

const JOB = "11111111-0000-0000-0000-000000000000";
const PLATE_JOB = {
  id: JOB,
  experiment_id: 12,
  plate_id: "P7",
  wave_number: 1,
  status: "queued",
  stage: null,
  frames_done: null,
  frames_total: null,
  error_code: null,
  requested_by: ME,
  created_at: "2026-10-29T10:20:00+00:00",
  started_at: null,
  updated_at: "2026-10-29T10:20:00+00:00",
  completed_at: null,
};

beforeEach(() =>
  resetSupabaseMock((q) =>
    q.table === "gravi_plate_video_jobs"
      ? { data: [PLATE_JOB], error: null }
      : q.table === "gravi_experiments"
        ? { data: [{ id: 12, name: "Grav-12", species_id: 2 }], error: null }
        : { data: [], error: null }
  )
);
afterEach(cleanup);

describe("the Video Jobs page", () => {
  it("renders the newest jobs with their links back, attributed to the signed-in user", async () => {
    render(await VideoJobsPage({}));
    expect(screen.getByRole("heading", { name: "Video jobs" })).toBeTruthy();
    const row = screen.getByTestId(`job-${JOB}`);
    expect(row.textContent).toContain("you");
    expect(screen.getByRole("link", { name: "P7 · wave 1" }).getAttribute("href")).toBe(
      "/app/plate-phenotypes/2/12/wave/1/P7"
    );
    // Only the columns signed-in roles may read.
    const select = String(queriesFor("cyl_video_jobs")[0].arg("select")?.[0]);
    expect(select).not.toMatch(/error_message|msg_id|reads/);
  });

  it("highlights the job named in the address", async () => {
    render(await VideoJobsPage({ searchParams: Promise.resolve({ job: JOB }) }));
    expect(screen.getByTestId(`job-${JOB}`).getAttribute("aria-current")).toBe("true");
  });

  it("ignores a job reference that is not one", async () => {
    render(await VideoJobsPage({ searchParams: Promise.resolve({ job: "<script>" }) }));
    expect(screen.getByTestId(`job-${JOB}`).getAttribute("aria-current")).toBeNull();
  });

  it("says when the jobs could not be read", async () => {
    supabaseMock.respond = () => ({ data: null, error: { message: "boom" } });
    render(await VideoJobsPage({}));
    expect(screen.getByRole("alert").textContent).toContain("Could not load video jobs");
  });
});
