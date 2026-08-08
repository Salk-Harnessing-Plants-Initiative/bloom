// @vitest-environment jsdom
/**
 * Unit tests for the on-demand cyl scan video proxy.
 *
 * The jsdom directive overrides the workspace default of `environment: 'node'`
 * (vitest.config.ts), matching the existing route-handler test in
 * app/api/config/route.test.ts.
 *
 * The id-validation cases are the point of this file: `parseId` is unit-tested
 * on its own, but nothing proved the route actually calls it. Swapping the
 * parsed integers for the raw params at the interpolation site would be an
 * SSRF with a fully green suite, so each of those cases asserts upstream was
 * never reached — not merely that the status was 400.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as routeModule from "@/app/api/cyl/experiments/[experimentId]/scans/[scanId]/video/route";

vi.mock("@/lib/supabase/server", () => ({
  getSession: vi.fn(),
}));

import { getSession } from "@/lib/supabase/server";

const mockedGetSession = vi.mocked(getSession);

const RESULT = {
  scan_id: 5,
  experiment_id: 1,
  frames: 72,
  frames_expected: 72,
  truncated: false,
  regenerated: true,
  path: "cyl-videos/5.mp4",
  download_url: "https://storage.test/cyl-videos/5.mp4?token=abc",
};

let fetchSpy: ReturnType<typeof vi.fn>;

function callRoute(experimentId: string, scanId: string) {
  return routeModule.POST(new Request("http://localhost/api", { method: "POST" }), {
    params: Promise.resolve({ experimentId, scanId }),
  });
}

function upstreamJson(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedGetSession.mockResolvedValue({ access_token: "tok" } as never);
  fetchSpy = vi.fn().mockResolvedValue(upstreamJson(RESULT));
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
});

describe("id validation", () => {
  it("rejects a traversal experimentId without calling upstream", async () => {
    const res = await callRoute("1/../../health", "5");

    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects a traversal scanId without calling upstream", async () => {
    const res = await callRoute("1", "5/../../health");

    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects zero, negative and non-numeric ids", async () => {
    for (const [e, s] of [
      ["0", "5"],
      ["-1", "5"],
      ["abc", "5"],
      ["1", ""],
    ]) {
      fetchSpy.mockClear();
      const res = await callRoute(e, s);
      expect(res.status).toBe(400);
      expect(fetchSpy).not.toHaveBeenCalled();
    }
  });

  it("interpolates the parsed integers, not the raw params", async () => {
    await callRoute("007", "5");

    expect(fetchSpy.mock.calls[0][0]).toBe(
      "http://workflows:5100/cyl/experiments/7/scans/5/video"
    );
  });
});

describe("auth", () => {
  it("returns 401 without calling upstream when there is no session", async () => {
    mockedGetSession.mockResolvedValue(null as never);

    const res = await callRoute("1", "5");

    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ detail: "Sign in to generate a video." });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("returns 401 when the session carries no access token", async () => {
    mockedGetSession.mockResolvedValue({} as never);

    const res = await callRoute("1", "5");

    expect(res.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("forwards the access token as a bearer header", async () => {
    await callRoute("1", "5");

    const init = fetchSpy.mock.calls[0][1];
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
  });
});

describe("upstream passthrough", () => {
  it("returns a valid result verbatim", async () => {
    const res = await callRoute("1", "5");

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(RESULT);
  });

  it("passes an upstream 404 and its detail through", async () => {
    fetchSpy.mockResolvedValue(
      upstreamJson({ detail: "No images found for scan 5" }, 404)
    );

    const res = await callRoute("1", "5");

    expect(res.status).toBe(404);
    expect((await res.json()).detail).toBe("No images found for scan 5");
  });

  it("passes a rate limit through so the client can tell it apart", async () => {
    fetchSpy.mockResolvedValue(upstreamJson({ detail: "rate limited" }, 429));

    expect((await callRoute("1", "5")).status).toBe(429);
  });
});

describe("bad upstream responses", () => {
  it("turns a non-JSON success body into a 502", async () => {
    fetchSpy.mockResolvedValue(new Response("<html>ok</html>", { status: 200 }));

    const res = await callRoute("1", "5");

    expect(res.status).toBe(502);
    expect((await res.json()).detail).toBe(
      "Unexpected response from the video service."
    );
  });

  it("treats an empty success body as a 502", async () => {
    fetchSpy.mockResolvedValue(new Response("", { status: 200 }));

    expect((await callRoute("1", "5")).status).toBe(502);
  });

  it("keeps the upstream status when a failure body is not JSON", async () => {
    fetchSpy.mockResolvedValue(new Response("<html>bad gateway</html>", { status: 502 }));

    const res = await callRoute("1", "5");

    expect(res.status).toBe(502);
    expect((await res.json()).detail).toBeNull();
  });

  it("rejects a 200 whose shape would render as undefined", async () => {
    fetchSpy.mockResolvedValue(upstreamJson({ frames: 72 }, 200));

    const res = await callRoute("1", "5");

    expect(res.status).toBe(502);
  });

  it("rejects a 200 carrying JSON null", async () => {
    fetchSpy.mockResolvedValue(upstreamJson(null, 200));

    expect((await callRoute("1", "5")).status).toBe(502);
  });

  it("rejects a 200 with no usable download_url", async () => {
    fetchSpy.mockResolvedValue(
      upstreamJson({ ...RESULT, download_url: "" }, 200)
    );

    expect((await callRoute("1", "5")).status).toBe(502);
  });
});

describe("transport failures", () => {
  it("reports an unreachable service without naming the internal host", async () => {
    fetchSpy.mockRejectedValue(new Error("connect ECONNREFUSED workflows:5100"));

    const res = await callRoute("1", "5");
    const body = await res.text();

    expect(res.status).toBe(502);
    expect(body).not.toContain("workflows");
  });

  it("reports a timeout as still-encoding, not as a failure", async () => {
    const timeout = new Error("timed out");
    timeout.name = "TimeoutError";
    fetchSpy.mockRejectedValue(timeout);

    const res = await callRoute("1", "5");

    expect(res.status).toBe(504);
    expect((await res.json()).detail).toContain("Still encoding");
  });
});

describe("WORKFLOWS_URL", () => {
  it("is read per request, so it is not frozen at module load", async () => {
    process.env.WORKFLOWS_URL = "http://workflows.test:9999";

    await callRoute("1", "5");

    expect(fetchSpy.mock.calls[0][0]).toBe(
      "http://workflows.test:9999/cyl/experiments/1/scans/5/video"
    );
  });
});
