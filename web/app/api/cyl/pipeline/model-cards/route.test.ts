/**
 * Unit tests for the model-card proxy, GET /api/cyl/pipeline/model-cards
 * (bloom#971): one test per scenario of its spec requirement, plus the module
 * contract. Refusals must happen before any upstream call.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as routeModule from "@/app/api/cyl/pipeline/model-cards/route";

vi.mock("@/lib/supabase/server", () => ({
  getSession: vi.fn(),
}));

import { getSession } from "@/lib/supabase/server";
import { PRODUCTION_CARDS } from "@/lib/cyl-pipeline/__fixtures__/model-cards";
import {
  MODEL_CARDS_TIMED_OUT,
  MODEL_CARDS_UNAVAILABLE,
  MODEL_CARDS_UPSTREAM_TIMEOUT_MS,
} from "@/lib/cyl-pipeline/model-cards-proxy";

const mockedGetSession = vi.mocked(getSession);
const TOKEN = "sekret-access-token";
const URL_ = "http://0.0.0.0:3000/api/cyl/pipeline/model-cards";
const LIST = { cards: PRODUCTION_CARDS, fetched_at: "2026-10-02T12:00:00+00:00", skipped: 0 };

let fetchSpy: ReturnType<typeof vi.fn>;

const get = (headers: Record<string, string> = {}) => routeModule.GET(new Request(URL_, { headers }));
const upstreamJson = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  mockedGetSession.mockResolvedValue({ access_token: TOKEN } as never);
  fetchSpy = vi.fn().mockImplementation(async () => upstreamJson(LIST));
  vi.stubGlobal("fetch", fetchSpy);
  delete process.env.WORKFLOWS_URL;
  vi.stubEnv("CYL_PIPELINE_TRIGGER_ENABLED", "true");
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("module contract", () => {
  it("is a dynamic node route that exports GET and no other method", () => {
    expect(routeModule.dynamic).toBe("force-dynamic");
    expect(routeModule.runtime).toBe("nodejs");
    expect(typeof routeModule.GET).toBe("function");
    for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
      expect(method in routeModule).toBe(false);
    }
  });

  it("times out upstream after 8 seconds", () => {
    expect(MODEL_CARDS_UPSTREAM_TIMEOUT_MS).toBe(8_000);
  });
});

describe("refusals", () => {
  it("answers 503 when switched off, without reading the session or calling upstream", async () => {
    vi.stubEnv("CYL_PIPELINE_TRIGGER_ENABLED", "false");
    const res = await get();
    expect(res.status).toBe(503);
    expect((await res.json()).detail).toMatch(/not enabled/i);
    expect(mockedGetSession).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("answers 401 without a session, without calling upstream", async () => {
    mockedGetSession.mockResolvedValue(null as never);
    const res = await get();
    expect(res.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("pass-through", () => {
  it("returns a well-formed list for a request with no Origin, sending the bearer token", async () => {
    const res = await get();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(LIST);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("http://workflows:5100/model-cards");
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    expect(init.redirect).toBe("manual");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("uses WORKFLOWS_URL when set", async () => {
    vi.stubEnv("WORKFLOWS_URL", "http://elsewhere:1234");
    await get();
    expect(fetchSpy.mock.calls[0][0]).toBe("http://elsewhere:1234/model-cards");
  });
});

describe("upstream failures", () => {
  it.each([
    ["a 503 with detail", () => upstreamJson({ detail: "upstream-text" }, 503)],
    ["a redirect", () => new Response(null, { status: 302, headers: { Location: "/elsewhere" } })],
    ["a non-JSON 200", () => new Response("upstream-text", { status: 200 })],
    ["a bad shape", () => upstreamJson({ cards: [{ ...PRODUCTION_CARDS[0], selectors: "upstream-text" }], fetched_at: "t", skipped: 0 })],
    ["a missing skipped", () => upstreamJson({ cards: PRODUCTION_CARDS, fetched_at: "upstream-text" })],
  ])("maps %s to a fixed 502", async (_label, make) => {
    fetchSpy.mockImplementation(async () => make());
    const res = await get();
    expect(res.status).toBe(502);
    const text = await res.text();
    expect(JSON.parse(text).detail).toBe(MODEL_CARDS_UNAVAILABLE);
    expect(text).not.toContain("upstream-text");
  });

  it("maps a network error to 502", async () => {
    fetchSpy.mockRejectedValue(new TypeError("fetch failed", { cause: { code: "ECONNREFUSED" } }));
    const res = await get();
    expect(res.status).toBe(502);
    expect((await res.json()).detail).toBe(MODEL_CARDS_UNAVAILABLE);
  });

  it("maps its own timeout to 504", async () => {
    fetchSpy.mockRejectedValue(new DOMException("t", "TimeoutError"));
    const res = await get();
    expect(res.status).toBe(504);
    expect((await res.json()).detail).toBe(MODEL_CARDS_TIMED_OUT);
  });
});

describe("timeout wiring and logging", () => {
  it("bounds upstream with AbortSignal.timeout(8000)", async () => {
    const spy = vi.spyOn(AbortSignal, "timeout");
    await get();
    expect(spy).toHaveBeenCalledWith(8_000);
  });

  it("answers 401 for a session object without an access token", async () => {
    mockedGetSession.mockResolvedValue({} as never);
    const res = await get();
    expect(res.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("never logs the token or upstream text", async () => {
    fetchSpy.mockImplementation(async () => upstreamJson({ detail: "upstream-text" }, 503));
    await get();
    fetchSpy.mockRejectedValue(new TypeError("fetch failed"));
    await get();
    const logged = vi.mocked(console.error).mock.calls.flat().join(" ");
    expect(logged).not.toContain(TOKEN);
    expect(logged).not.toContain("upstream-text");
  });
});
