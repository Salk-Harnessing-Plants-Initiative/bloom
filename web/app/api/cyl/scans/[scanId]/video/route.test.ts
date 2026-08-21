// @vitest-environment jsdom
/**
 * Unit tests for the stored-video lookup.
 *
 * The route must never reach the video service: it answers "has it landed yet"
 * from storage, and a poll that could start an encode is the failure this file
 * exists to catch.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as routeModule from "@/app/api/cyl/scans/[scanId]/video/route";

vi.mock("@/lib/supabase/server", () => ({
  getSession: vi.fn(),
}));

vi.mock("@/lib/supabase/scan-video", () => ({
  getStoredScanVideo: vi.fn(),
}));

import { getSession } from "@/lib/supabase/server";
import { getStoredScanVideo } from "@/lib/supabase/scan-video";

const mockedGetSession = vi.mocked(getSession);
const mockedStoredVideo = vi.mocked(getStoredScanVideo);

const STORED_URL = "https://storage.test/cyl-videos/5.mp4?token=a";

let fetchSpy: ReturnType<typeof vi.fn>;

function callGet(scanId: string) {
  return routeModule.GET(new Request("http://localhost/api"), {
    params: Promise.resolve({ scanId }),
  });
}

beforeEach(() => {
  mockedGetSession.mockResolvedValue({ access_token: "tok" } as never);
  mockedStoredVideo.mockResolvedValue({ status: "absent" } as never);
  fetchSpy = vi.fn();
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("module contract", () => {
  it("is a dynamic node route", () => {
    expect(routeModule.dynamic).toBe("force-dynamic");
    expect(routeModule.runtime).toBe("nodejs");
  });

  it("exposes no generate verb — an encode needs the experiment-scoped route", () => {
    expect("POST" in routeModule).toBe(false);
  });
});

describe("GET — has the video landed yet?", () => {
  it("returns the stored video's url", async () => {
    mockedStoredVideo.mockResolvedValue({ status: "present", url: STORED_URL } as never);

    const res = await callGet("5");

    expect(res.status).toBe(200);
    expect((await res.json()).download_url).toBe(STORED_URL);
  });

  it("404s while no video is stored, so a poll keeps waiting", async () => {
    expect((await callGet("5")).status).toBe(404);
  });

  it("503s when storage could not say, rather than claiming there is none", async () => {
    mockedStoredVideo.mockResolvedValue({
      status: "unknown",
      reason: "connect ECONNREFUSED kong:8000",
    } as never);

    const res = await callGet("5");

    expect(res.status).toBe(503);
    // The reason names the internal gateway — it is for operators, not callers.
    expect(await res.text()).not.toContain("kong");
  });

  it("never calls the video service", async () => {
    mockedStoredVideo.mockResolvedValue({ status: "present", url: STORED_URL } as never);

    await callGet("5");

    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("requires a session", async () => {
    mockedGetSession.mockResolvedValue(null as never);

    expect((await callGet("5")).status).toBe(401);
    expect(mockedStoredVideo).not.toHaveBeenCalled();
  });

  it("requires an access token, not merely a session object", async () => {
    mockedGetSession.mockResolvedValue({} as never);

    expect((await callGet("5")).status).toBe(401);
    expect(mockedStoredVideo).not.toHaveBeenCalled();
  });

  it("rejects zero, negative and non-numeric ids", async () => {
    for (const id of ["0", "-1", "abc", "1.5", "", " 5"]) {
      expect((await callGet(id)).status).toBe(400);
    }
    expect(mockedStoredVideo).not.toHaveBeenCalled();
  });

  it("rejects a traversal id", async () => {
    expect((await callGet("5/../../health")).status).toBe(400);
    expect(mockedStoredVideo).not.toHaveBeenCalled();
  });

  it("looks up the scan actually requested, as an integer", async () => {
    await callGet("42");

    expect(mockedStoredVideo).toHaveBeenCalledWith(42);
  });
});
