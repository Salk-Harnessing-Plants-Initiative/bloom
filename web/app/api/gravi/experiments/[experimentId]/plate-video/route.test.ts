// @vitest-environment jsdom
/**
 * Unit tests for the plate time-lapse proxy and its poll.
 *
 * The validation cases are the point. `isValidPlateId` is unit-tested on its
 * own, but nothing proves the route calls it — and a plate id reaches an
 * upstream request body and a storage key. So each of those cases asserts
 * upstream was never reached, not merely that the status was 400.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as routeModule from "@/app/api/gravi/experiments/[experimentId]/plate-video/route";

vi.mock("@/lib/supabase/server", () => ({ getSession: vi.fn() }));
vi.mock("@/lib/supabase/plate-video", () => ({ getStoredPlateVideo: vi.fn() }));

import { getSession } from "@/lib/supabase/server";
import { getStoredPlateVideo } from "@/lib/supabase/plate-video";

const mockedGetSession = vi.mocked(getSession);
const mockedStored = vi.mocked(getStoredPlateVideo);

const RESULT = {
  experiment_id: 12,
  plate_id: "P7",
  wave_number: 1,
  action: "rendered",
  reason: "no video stored; encoding 86 frames",
  object_path: "12/wave-1/P7.mp4",
  frames: 86,
  coverage: null,
};

function post(body: unknown, experimentId = "12") {
  return routeModule.POST(
    new Request("http://localhost/api/gravi/experiments/12/plate-video", {
      method: "POST",
      body: typeof body === "string" ? body : JSON.stringify(body),
    }),
    { params: Promise.resolve({ experimentId }) }
  );
}

function get(query: string, experimentId = "12") {
  return routeModule.GET(
    new Request(`http://localhost/api/gravi/experiments/12/plate-video?${query}`),
    { params: Promise.resolve({ experimentId }) }
  );
}

function upstreamReturns(status: number, payload: unknown) {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(payload), { status })
  );
}

beforeEach(() => {
  // Call history survives restoreAllMocks, so a test asserting "never called"
  // would see the previous test's calls.
  vi.clearAllMocks();
  mockedGetSession.mockResolvedValue({ access_token: "token" } as never);
  mockedStored.mockResolvedValue({ status: "absent" });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("POST", () => {
  it("passes the plate through and returns what was rendered", async () => {
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(RESULT);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://workflows:5100/gravi/experiments/12/plate-video");
    expect(JSON.parse(init.body)).toEqual({ plate_id: "P7", wave_number: 1 });
  });

  it("sends the token upstream rather than exposing it to the browser", async () => {
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: 1 });
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer token");
  });

  it("does not refuse a plate that already has a video", async () => {
    // A plate keeps gaining captures, so a stored video is usually not wrong,
    // just short. Whether to re-render is the service's decision.
    mockedStored.mockResolvedValue({ status: "present", url: "https://x/y.mp4" });
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    expect((await post({ plate_id: "P7", wave_number: 1 })).status).toBe(200);
    expect(fetchMock).toHaveBeenCalled();
  });

  it.each([
    ["../secrets"],
    ["a/b"],
    [".hidden"],
    [""],
    ["P".repeat(65)],
    [null],
    [7],
  ])("refuses plate id %p without reaching upstream", async (plateId) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const res = await post({ plate_id: plateId, wave_number: 1 });

    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([[true], ["one"], [1.5], [-1]])(
    "refuses wave %p without reaching upstream",
    async (wave) => {
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);

      const res = await post({ plate_id: "P7", wave_number: wave });

      expect(res.status).toBe(400);
      expect(fetchMock).not.toHaveBeenCalled();
    }
  );

  it("accepts a plate with no wave", async () => {
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: null });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).wave_number).toBeNull();
  });

  it("treats wave zero as a wave", async () => {
    // The scanner app sends 0 when none is set, so it arrives in practice.
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: 0 });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).wave_number).toBe(0);
  });

  it("interpolates the parsed experiment id, not the raw param", async () => {
    // `parseId` accepts /^\d+$/, so "007" is valid and parses to 7. Swapping
    // the parsed integer for the raw param at the interpolation site would be
    // an SSRF that every other test in this file still passes.
    const fetchMock = upstreamReturns(200, RESULT);
    vi.stubGlobal("fetch", fetchMock);

    await post({ plate_id: "P7", wave_number: 1 }, "007");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://workflows:5100/gravi/experiments/7/plate-video"
    );
  });

  it("refuses a bad experiment id without reaching upstream", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const res = await post({ plate_id: "P7" }, "1/../../health");

    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a signed-out caller without reaching upstream", async () => {
    mockedGetSession.mockResolvedValue(null as never);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports a timeout as still encoding, not as a failure", async () => {
    // The encode carries on upstream; telling the user it failed would have
    // them click Generate again and start a second one.
    const timeout = Object.assign(new Error("timed out"), { name: "TimeoutError" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(timeout));

    const res = await post({ plate_id: "P7", wave_number: 1 });

    expect(res.status).toBe(504);
    expect((await res.json()).detail).toContain("Still encoding");
  });

  it.each([[404], [413]])(
    "passes an upstream %i detail through, because it is written for the caller",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        upstreamReturns(status, { detail: "this plate has no captures with an image" })
      );

      const res = await post({ plate_id: "P7", wave_number: 1 });

      expect(res.status).toBe(status);
      expect((await res.json()).detail).toContain("no captures");
    }
  );

  it.each([[500], [502], [503]])(
    "suppresses an upstream %i detail, which names internal things",
    async (status) => {
      // 5xx details carry the internal gateway URL, the service account's env
      // key names, and object paths.
      vi.stubGlobal(
        "fetch",
        upstreamReturns(status, { detail: "http://kong:8000 rejected WORKFLOWS_SUPABASE_EMAIL" })
      );

      const res = await post({ plate_id: "P7", wave_number: 1 });

      expect(res.status).toBe(status);
      expect((await res.json()).detail).toBeNull();
    }
  );
});

describe("GET", () => {
  it("reports the stored video's url", async () => {
    mockedStored.mockResolvedValue({ status: "present", url: "https://x/y.mp4" });

    const res = await get("plate_id=P7&wave_number=1");

    expect(res.status).toBe(200);
    expect((await res.json()).download_url).toBe("https://x/y.mp4");
  });

  it("reports no url when nothing is stored", async () => {
    mockedStored.mockResolvedValue({ status: "absent" });

    expect((await (await get("plate_id=P7&wave_number=1")).json()).download_url).toBeNull();
  });

  it("answers 503 when storage cannot say, rather than reporting no video", async () => {
    // Reported as an absence, the button would offer to render a plate that
    // already has a video, and the poll would never settle.
    mockedStored.mockResolvedValue({ status: "unknown", reason: "gateway timeout" });

    expect((await get("plate_id=P7&wave_number=1")).status).toBe(503);
  });

  it("refuses a bad plate id without touching storage", async () => {
    const res = await get("plate_id=../secrets&wave_number=1");

    expect(res.status).toBe(400);
    expect(mockedStored).not.toHaveBeenCalled();
  });

  it("reads a missing wave as no wave", async () => {
    mockedStored.mockResolvedValue({ status: "absent" });

    await get("plate_id=P7");
    expect(mockedStored).toHaveBeenCalledWith(12, "P7", null);
  });
});
