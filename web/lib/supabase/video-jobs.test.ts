import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getLatestPlateVideoJob, getLatestScanVideoJob } from "./video-jobs";
import { createServerSupabaseClient } from "@/lib/supabase/server";

vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: vi.fn(),
}));

const mockedClient = vi.mocked(createServerSupabaseClient);

const ROW = {
  id: "1a2b3c4d-0000-0000-0000-000000000000",
  status: "rendering",
  stage: "downloading",
  frames_done: 4,
  frames_total: 9,
  error_code: null,
  created_at: "2026-10-29T10:00:00Z",
  updated_at: "2026-10-29T10:00:05Z",
  // Not a column the browser should see; must not come through.
  error: "kong:8000 bloom_workflows",
};

/** A client answering the latest-job read with `row`, and the count with `count`. */
function clientReturning(
  row: { data?: unknown; error?: unknown },
  count: { count?: number | null; error?: unknown } = { count: 0 }
) {
  const latest: Record<string, unknown> = {};
  const select = vi.fn();
  const eq = vi.fn().mockReturnValue(latest);
  const is = vi.fn().mockReturnValue(latest);
  const order = vi.fn().mockReturnValue(latest);
  const limit = vi.fn().mockReturnValue(latest);
  const maybeSingle = vi.fn().mockResolvedValue(row);
  Object.assign(latest, { eq, is, order, limit, maybeSingle });

  const counted: Record<string, unknown> = {};
  const countEq = vi.fn().mockReturnValue(counted);
  const lt = vi.fn().mockResolvedValue(count);
  Object.assign(counted, { eq: countEq, lt });

  select.mockImplementation((_cols: string, opts?: { head?: boolean }) =>
    opts?.head ? counted : latest
  );
  const from = vi.fn().mockReturnValue({ select });
  mockedClient.mockResolvedValue({ from } as never);
  return { from, select, eq, is, order, limit, countEq, lt };
}

let warn: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  vi.clearAllMocks();
  warn = vi.spyOn(console, "warn").mockImplementation(() => {});
});
afterEach(() => warn.mockRestore());

describe("getLatestPlateVideoJob", () => {
  it("returns the job field by field, without columns the browser may not read", async () => {
    clientReturning({ data: ROW });

    const job = await getLatestPlateVideoJob(7, "P1", 2);

    expect(job).toEqual({
      id: ROW.id,
      status: "rendering",
      stage: "downloading",
      frames_done: 4,
      frames_total: 9,
      error_code: null,
      queued_ahead: null,
      created_at: ROW.created_at,
      updated_at: ROW.updated_at,
    });
    expect(job).not.toHaveProperty("error");
  });

  it("selects only the readable columns", async () => {
    const { select } = clientReturning({ data: ROW });
    await getLatestPlateVideoJob(7, "P1", 2);
    const cols = select.mock.calls[0][0] as string;
    expect(cols).not.toMatch(/\berror\b(?!_code)|msg_id|reads|\*/);
  });

  it("matches the plate and wave, newest first", async () => {
    const { from, eq, is, order, limit } = clientReturning({ data: ROW });
    await getLatestPlateVideoJob(7, "P1", 2);
    expect(from).toHaveBeenCalledWith("gravi_plate_video_jobs");
    expect(eq).toHaveBeenCalledWith("experiment_id", 7);
    expect(eq).toHaveBeenCalledWith("plate_id", "P1");
    expect(eq).toHaveBeenCalledWith("wave_number", 2);
    expect(is).not.toHaveBeenCalled();
    expect(order).toHaveBeenCalledWith("created_at", { ascending: false });
    expect(limit).toHaveBeenCalledWith(1);
  });

  it("matches a plate with no wave by IS NULL", async () => {
    const { eq, is } = clientReturning({ data: ROW });
    await getLatestPlateVideoJob(7, "P1", null);
    expect(is).toHaveBeenCalledWith("wave_number", null);
    expect(eq).not.toHaveBeenCalledWith("wave_number", expect.anything());
  });

  it("counts the queued jobs created before a queued one", async () => {
    const { countEq, lt } = clientReturning(
      { data: { ...ROW, status: "queued", stage: null } },
      { count: 2 }
    );
    const job = await getLatestPlateVideoJob(7, "P1", 2);
    expect(countEq).toHaveBeenCalledWith("status", "queued");
    expect(lt).toHaveBeenCalledWith("created_at", ROW.created_at);
    expect(job?.queued_ahead).toBe(2);
  });

  it("does not count for a job that is not queued", async () => {
    const { lt } = clientReturning({ data: ROW });
    const job = await getLatestPlateVideoJob(7, "P1", 2);
    expect(lt).not.toHaveBeenCalled();
    expect(job?.queued_ahead).toBeNull();
  });

  it("keeps the job when only the count fails", async () => {
    clientReturning(
      { data: { ...ROW, status: "queued" } },
      { count: null, error: { message: "boom" } }
    );
    const job = await getLatestPlateVideoJob(7, "P1", 2);
    expect(job?.status).toBe("queued");
    expect(job?.queued_ahead).toBeNull();
    expect(warn).toHaveBeenCalled();
  });

  it("answers null when there is no job", async () => {
    clientReturning({ data: null });
    await expect(getLatestPlateVideoJob(7, "P1", 2)).resolves.toBeNull();
  });

  it("answers null and warns when the lookup fails, rather than failing the poll", async () => {
    clientReturning({ data: null, error: { message: "permission denied" } });
    await expect(getLatestPlateVideoJob(7, "P1", 2)).resolves.toBeNull();
    expect(warn).toHaveBeenCalled();
  });

  it("answers null when the client itself throws", async () => {
    mockedClient.mockRejectedValue(new Error("no cookies"));
    await expect(getLatestPlateVideoJob(7, "P1", 2)).resolves.toBeNull();
  });

  it("refuses a row whose status is not one the service writes", async () => {
    clientReturning({ data: { ...ROW, status: "paused" } });
    await expect(getLatestPlateVideoJob(7, "P1", 2)).resolves.toBeNull();
  });
});

describe("getLatestScanVideoJob", () => {
  it("reads the scan's newest job", async () => {
    const { from, eq, order } = clientReturning({ data: ROW });
    const job = await getLatestScanVideoJob(5);
    expect(from).toHaveBeenCalledWith("cyl_video_jobs");
    expect(eq).toHaveBeenCalledWith("scan_id", 5);
    expect(order).toHaveBeenCalledWith("created_at", { ascending: false });
    expect(job?.id).toBe(ROW.id);
  });

  it("answers null and warns when the lookup fails", async () => {
    clientReturning({ data: null, error: { message: "boom" } });
    await expect(getLatestScanVideoJob(5)).resolves.toBeNull();
    expect(warn).toHaveBeenCalled();
  });
});
