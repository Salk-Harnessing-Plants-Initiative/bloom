/**
 * Unit tests for the pipeline trigger proxy, POST /api/cyl/pipeline.
 *
 * One test per scenario in the spec's three "Trigger proxy" requirements, plus
 * the ordering, Origin, limit, upstream-failure and logging cases of tasks §9.3.
 *
 * The checks that refuse a request are only worth anything if they refuse it
 * before work happens, so those cases assert that upstream was never reached
 * and, for the three pre-body checks, that the body stream was never pulled.
 *
 * Requests are built against `http://0.0.0.0:3000` on purpose: bloom-web runs
 * `next start -H 0.0.0.0`, so the request URL's host is never the public one
 * and the Origin check must not read it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as routeModule from "@/app/api/cyl/pipeline/route";

vi.mock("@/lib/supabase/server", () => ({
  getSession: vi.fn(),
}));

import { getSession } from "@/lib/supabase/server";

const mockedGetSession = vi.mocked(getSession);

const TOKEN = "sekret-access-token";
const URL_ = "http://0.0.0.0:3000/api/cyl/pipeline";
const SAME_ORIGIN = {
  "content-type": "application/json",
  host: "bloom.salk.edu",
  origin: "https://bloom.salk.edu",
};
const VALID = { target_level: "scan", target_id: 42 };
const RESULT = { pipeline_run_id: 91, scan_count: 40, reused_count: 0 };

let fetchSpy: ReturnType<typeof vi.fn>;

function post(body: unknown = VALID, headers: Record<string, string> = SAME_ORIGIN) {
  return routeModule.POST(
    new Request(URL_, {
      method: "POST",
      headers,
      body: typeof body === "string" ? body : JSON.stringify(body),
    })
  );
}

// A request whose body is a stream that records whether anything read it.
function streamed(headers: Record<string, string>) {
  const pullSpy = vi.fn((controller: ReadableStreamDefaultController<Uint8Array>) => {
    controller.enqueue(new TextEncoder().encode(JSON.stringify(VALID)));
    controller.close();
  });
  const req = new Request(URL_, {
    method: "POST",
    headers,
    body: new ReadableStream({ pull: pullSpy }, { highWaterMark: 0 }),
    duplex: "half",
  } as RequestInit);
  return { req, pullSpy };
}

function upstreamJson(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function forwarded() {
  const [url, init] = fetchSpy.mock.calls[0];
  return { url, init, body: JSON.parse(init.body as string) };
}

beforeEach(() => {
  mockedGetSession.mockResolvedValue({ access_token: TOKEN } as never);
  fetchSpy = vi.fn().mockResolvedValue(upstreamJson(RESULT));
  vi.stubGlobal("fetch", fetchSpy);
  delete process.env.WORKFLOWS_URL;
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "info").mockImplementation(() => {});
  vi.spyOn(console, "log").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("module contract", () => {
  it("is a dynamic node route that only accepts POST", () => {
    expect(routeModule.dynamic).toBe("force-dynamic");
    expect(routeModule.runtime).toBe("nodejs");
    expect("GET" in routeModule).toBe(false);
  });
});

// Requirement: rejects cross-origin and unauthenticated calls before any other work
describe("media type", () => {
  it("refuses a non-JSON post without reading the body or calling upstream", async () => {
    for (const contentType of ["text/plain", "text/plain; application/json"]) {
      const { req, pullSpy } = streamed({ ...SAME_ORIGIN, "content-type": contentType });
      const res = await routeModule.POST(req);
      expect(res.status).toBe(415);
      expect(pullSpy).not.toHaveBeenCalled();
      expect(req.bodyUsed).toBe(false);
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses a missing Content-Type", async () => {
    const { "content-type": _omit, ...headers } = SAME_ORIGIN;
    expect((await post(VALID, headers)).status).toBe(415);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("accepts JSON with parameters as JSON", async () => {
    const res = await post(VALID, { ...SAME_ORIGIN, "content-type": "application/json; charset=utf-8" });
    expect(res.status).toBe(200);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("trims and lowercases the media type", async () => {
    const res = await post(VALID, { ...SAME_ORIGIN, "content-type": " Application/JSON ;charset=utf-8" });
    expect(res.status).toBe(200);
  });
});

describe("Origin", () => {
  it("refuses a missing, null or foreign Origin without calling upstream", async () => {
    const { origin: _omit, ...noOrigin } = SAME_ORIGIN;
    for (const headers of [
      noOrigin,
      { ...SAME_ORIGIN, origin: "null" },
      { ...SAME_ORIGIN, origin: "https://evil.salk.edu" },
      { ...SAME_ORIGIN, origin: "not a url" },
    ]) {
      expect((await post(VALID, headers)).status).toBe(403);
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("includes the port in the comparison", async () => {
    const res = await post(VALID, {
      ...SAME_ORIGIN,
      host: "staging.bloom.salk.edu:8443",
      origin: "https://staging.bloom.salk.edu:8443",
    });
    expect(res.status).toBe(200);
  });

  it("refuses a different port on the same host", async () => {
    const res = await post(VALID, {
      ...SAME_ORIGIN,
      host: "localhost:3001",
      origin: "http://localhost:3000",
    });
    expect(res.status).toBe(403);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("compares case-insensitively", async () => {
    const res = await post(VALID, {
      ...SAME_ORIGIN,
      host: "BLOOM.salk.edu",
      origin: "https://Bloom.Salk.Edu",
    });
    expect(res.status).toBe(200);
  });

  it("compares with the first x-forwarded-host value, ahead of Host", async () => {
    expect(
      (await post(VALID, { ...SAME_ORIGIN, host: "bloom-web:3000", "x-forwarded-host": "bloom.salk.edu, evil.salk.edu" }))
        .status
    ).toBe(200);

    // Host matches Origin here, but a present x-forwarded-host is what counts.
    expect(
      (await post(VALID, { ...SAME_ORIGIN, "x-forwarded-host": "evil.salk.edu, bloom.salk.edu" })).status
    ).toBe(403);
  });

  it("never compares with the request URL's host", async () => {
    // The URL is http://0.0.0.0:3000/…; Host is the public name.
    expect((await post(VALID, SAME_ORIGIN)).status).toBe(200);
    expect((await post(VALID, { ...SAME_ORIGIN, origin: "http://0.0.0.0:3000" })).status).toBe(403);
  });
});

describe("session", () => {
  it("refuses an unauthenticated same-origin call without calling upstream", async () => {
    mockedGetSession.mockResolvedValue(null as never);
    expect((await post()).status).toBe(401);

    mockedGetSession.mockResolvedValue({} as never);
    expect((await post()).status).toBe(401);

    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("check order: 415, then 403, then 401, all before the body", () => {
  it("415 wins over a foreign Origin and no session", async () => {
    mockedGetSession.mockResolvedValue(null as never);
    const { req, pullSpy } = streamed({ "content-type": "text/plain", host: "bloom.salk.edu", origin: "https://evil.salk.edu" });

    expect((await routeModule.POST(req)).status).toBe(415);
    expect(pullSpy).not.toHaveBeenCalled();
    expect(req.bodyUsed).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("403 wins over no session, and the session is never looked up", async () => {
    mockedGetSession.mockResolvedValue(null as never);
    const { req, pullSpy } = streamed({ ...SAME_ORIGIN, origin: "https://evil.salk.edu" });

    expect((await routeModule.POST(req)).status).toBe(403);
    expect(pullSpy).not.toHaveBeenCalled();
    expect(req.bodyUsed).toBe(false);
    expect(mockedGetSession).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("401 comes before the body is read", async () => {
    mockedGetSession.mockResolvedValue(null as never);
    const { req, pullSpy } = streamed(SAME_ORIGIN);

    expect((await routeModule.POST(req)).status).toBe(401);
    expect(pullSpy).not.toHaveBeenCalled();
    expect(req.bodyUsed).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

// Requirement: validates the body locally and forwards a rebuilt body
describe("forwarding", () => {
  it("forwards a valid request rebuilt, with empty params and the user's token", async () => {
    const res = await post({ target_level: "scan", target_id: 42, params: { age: 7 }, extra: 1 });

    expect(res.status).toBe(200);
    const { url, init, body } = forwarded();
    expect(url).toBe("http://workflows:5100/pipeline");
    expect(init.method).toBe("POST");
    expect(body).toEqual({ target_level: "scan", target_id: 42, params: {} });
    const headers = new Headers(init.headers);
    expect(headers.get("authorization")).toBe(`Bearer ${TOKEN}`);
    expect(headers.get("content-type")).toBe("application/json");
  });

  it("forwards scan_ids at the upper boundary with a null target_id", async () => {
    const scanIds = Array.from({ length: 5000 }, (_, i) => i + 1);
    const res = await post({ target_level: "scan_ids", target_id: null, scan_ids: scanIds });

    expect(res.status).toBe(200);
    expect(forwarded().body).toEqual({ target_level: "scan_ids", scan_ids: scanIds, params: {} });
  });

  it("reads WORKFLOWS_URL per request", async () => {
    process.env.WORKFLOWS_URL = "http://workflows.test:9999";
    await post();
    expect(forwarded().url).toBe("http://workflows.test:9999/pipeline");
  });
});

describe("local validation", () => {
  it("rejects out-of-range scan_ids without calling upstream", async () => {
    const tooMany = Array.from({ length: 5001 }, (_, i) => i + 1);
    for (const scan_ids of [tooMany, []]) {
      expect((await post({ target_level: "scan_ids", target_id: null, scan_ids })).status).toBe(422);
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects cross-field and type violations without calling upstream", async () => {
    const bodies: unknown[] = [
      { target_level: "experiment", target_id: 5, scan_ids: [1] },
      { target_level: "scan_ids", target_id: 3, scan_ids: [1] },
    ];
    for (const target_id of ["42", true, 0, -1, 1.5]) bodies.push({ target_level: "scan", target_id });
    for (const body of bodies) {
      expect((await post(body)).status).toBe(422);
    }
    // Written out, because JSON.stringify would print 9007199254740992.
    expect((await post('{"target_level": "scan", "target_id": 9007199254740993}')).status).toBe(422);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("rejects malformed JSON and a non-object body", async () => {
    for (const raw of ["{not json", "", "[1]", "null", '"scan"']) {
      expect((await post(raw)).status).toBe(422);
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("body cap", () => {
  const CAP = 256 * 1024;

  it("refuses a 257 KB streamed body with no Content-Length", async () => {
    const chunk = new TextEncoder().encode(" ".repeat(1024));
    let sent = 0;
    const req = new Request(URL_, {
      method: "POST",
      headers: SAME_ORIGIN,
      body: new ReadableStream({
        pull(controller) {
          if (sent >= 257) return controller.close();
          controller.enqueue(chunk);
          sent += 1;
        },
      }),
      duplex: "half",
    } as RequestInit);
    expect(req.headers.get("content-length")).toBeNull();

    expect((await routeModule.POST(req)).status).toBe(413);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses a Content-Length over the cap without reading the body", async () => {
    const { req, pullSpy } = streamed({ ...SAME_ORIGIN, "content-length": String(CAP + 1) });

    expect((await routeModule.POST(req)).status).toBe(413);
    expect(pullSpy).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("counts bytes, not characters", async () => {
    // 90,000 three-byte characters: under the cap in UTF-16 units, over it in bytes.
    const body = JSON.stringify({ ...VALID, pad: "€".repeat(90_000) });
    expect(body.length).toBeLessThan(CAP);

    expect((await post(body)).status).toBe(413);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("accepts a body of exactly 256 KB", async () => {
    const json = JSON.stringify(VALID);
    const res = await post(json + " ".repeat(CAP - json.length));
    expect(res.status).toBe(200);
  });
});

// Requirement: maps upstream outcomes to caller-safe responses
describe("upstream outcomes", () => {
  it("returns a success unchanged", async () => {
    const res = await post();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(RESULT);
  });

  it("does not trust a malformed success", async () => {
    for (const body of [{ ok: true }, { pipeline_run_id: "91", scan_count: 40 }, { pipeline_run_id: 91, scan_count: 1.5 }, null]) {
      fetchSpy.mockResolvedValueOnce(upstreamJson(body));
      expect((await post()).status).toBe(502);
    }
  });

  it("treats a non-JSON success body as 502", async () => {
    fetchSpy.mockResolvedValue(new Response("<html>ok</html>", { status: 200 }));
    expect((await post()).status).toBe(502);
  });

  it("keeps an integer Retry-After on a rate limit", async () => {
    fetchSpy.mockResolvedValue(upstreamJson({ detail: "Rate limit exceeded" }, 429, { "Retry-After": "60" }));

    const res = await post();
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("60");
  });

  it("drops a non-integer Retry-After", async () => {
    for (const value of ["Wed, 21 Oct 2026 07:28:00 GMT", "1.5", "-1", ""]) {
      fetchSpy.mockResolvedValueOnce(upstreamJson({ detail: "slow down" }, 429, { "Retry-After": value }));
      const res = await post();
      expect(res.status).toBe(429);
      expect(res.headers.get("retry-after")).toBeNull();
    }
  });

  it("does not leak an upstream 5xx", async () => {
    fetchSpy.mockResolvedValue(upstreamJson({ detail: "auth check failed: http://kong:8000/auth/v1/user" }, 503));

    const res = await post();
    const text = await res.text();
    expect(res.status).toBe(502);
    expect(text).not.toContain("kong");
    expect(text).not.toContain("auth check failed");
    expect(JSON.parse(text).detail).toEqual(expect.any(String));
  });

  it("maps any unlisted status to 502", async () => {
    for (const status of [400, 403, 409, 500]) {
      fetchSpy.mockResolvedValueOnce(upstreamJson({ detail: "internal words" }, status));
      const res = await post();
      expect(res.status).toBe(502);
      expect(await res.text()).not.toContain("internal words");
    }
  });

  it("maps 401 to a fixed session-expired detail", async () => {
    fetchSpy.mockResolvedValue(upstreamJson({ detail: "invalid JWT: kid mismatch" }, 401));

    const res = await post();
    expect(res.status).toBe(401);
    const detail = (await res.json()).detail;
    expect(detail).toMatch(/session/i);
    expect(detail).not.toContain("JWT");
  });

  it("truncates a long 404 string detail to 300 characters", async () => {
    fetchSpy.mockResolvedValue(upstreamJson({ detail: "x".repeat(20_000) }, 404));

    const res = await post();
    expect(res.status).toBe(404);
    expect((await res.json()).detail.length).toBeLessThanOrEqual(300);
  });

  it("passes a short 404 or 422 string detail through", async () => {
    fetchSpy.mockResolvedValueOnce(upstreamJson({ detail: "scan 42 not found" }, 404));
    expect((await (await post()).json()).detail).toBe("scan 42 not found");

    fetchSpy.mockResolvedValueOnce(upstreamJson({ detail: "params must be an object" }, 422));
    const res = await post();
    expect(res.status).toBe(422);
    expect((await res.json()).detail).toBe("params must be an object");
  });

  it("replaces a non-string 404 or 422 detail with a fixed one", async () => {
    // FastAPI's own validation errors are a list of objects.
    fetchSpy.mockResolvedValueOnce(upstreamJson({ detail: [{ loc: ["body"], msg: "internal" }] }, 422));
    const res = await post();
    expect(res.status).toBe(422);
    const detail = (await res.json()).detail;
    expect(detail).toEqual(expect.any(String));
    expect(detail).not.toContain("internal");

    fetchSpy.mockResolvedValueOnce(new Response("<html>nope</html>", { status: 404 }));
    const res404 = await post();
    expect(res404.status).toBe(404);
    expect((await res404.json()).detail).toEqual(expect.any(String));
  });
});

describe("transport failures", () => {
  it("gives the upstream fetch an abort signal", async () => {
    await post();
    expect(forwarded().init.signal).toBeInstanceOf(AbortSignal);
  });

  it("reports a timeout as 504", async () => {
    fetchSpy.mockRejectedValue(new DOMException("t", "TimeoutError"));
    expect((await post()).status).toBe(504);
    expect(fetchSpy.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
  });

  it("reports an unreachable upstream as 502 without naming it", async () => {
    fetchSpy.mockRejectedValue(new TypeError("fetch failed", { cause: { code: "ECONNREFUSED" } }));

    const res = await post();
    const text = await res.text();
    expect(res.status).toBe(502);
    expect(text).not.toContain("ECONNREFUSED");
    expect(text).not.toContain("workflows");
  });
});

describe("logging", () => {
  function logged(): string {
    return [console.error, console.warn, console.info, console.log]
      .flatMap((fn) => vi.mocked(fn).mock.calls)
      .map((args) => args.map((a) => (typeof a === "string" ? a : JSON.stringify(a) ?? String(a))).join(" "))
      .join("\n");
  }

  it("logs the upstream status and a truncated detail", async () => {
    fetchSpy.mockResolvedValue(upstreamJson({ detail: "y".repeat(20_000) }, 500));
    await post();

    const text = logged();
    expect(text).toContain("500");
    expect(text).toContain("y".repeat(300));
    expect(text).not.toContain("y".repeat(301));
  });

  it("never logs the Authorization header or the token", async () => {
    fetchSpy.mockResolvedValueOnce(upstreamJson({ detail: "boom" }, 503));
    await post();
    fetchSpy.mockRejectedValueOnce(new TypeError("fetch failed", { cause: { code: "ECONNREFUSED" } }));
    await post();
    fetchSpy.mockRejectedValueOnce(new DOMException("t", "TimeoutError"));
    await post();

    const text = logged();
    expect(text).not.toBe("");
    expect(text).not.toContain(TOKEN);
    expect(text.toLowerCase()).not.toContain("authorization");
  });
});
