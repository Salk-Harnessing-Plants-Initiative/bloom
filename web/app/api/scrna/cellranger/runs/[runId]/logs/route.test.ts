// @vitest-environment jsdom
/** The step log proxy: what reaches the workflows service, and which details the browser sees. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as routeModule from "@/app/api/scrna/cellranger/runs/[runId]/logs/route";

vi.mock("@/lib/supabase/server", () => ({ getSession: vi.fn() }));

import { getSession } from "@/lib/supabase/server";

const mockedGetSession = vi.mocked(getSession);
let fetchSpy: ReturnType<typeof vi.fn>;

function callRoute(runId: string, step: string | null = "count") {
  const url = `http://0.0.0.0:3000/api/scrna/cellranger/runs/${runId}/logs${step === null ? "" : `?step=${step}`}`;
  return routeModule.GET(new Request(url), { params: Promise.resolve({ runId }) });
}

function upstream(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  mockedGetSession.mockResolvedValue({ access_token: "tok" } as never);
  fetchSpy = vi.fn().mockResolvedValue(upstream({ run_id: 1, step: "count", pod: "p", log: "done\n", truncated: false }));
  vi.stubGlobal("fetch", fetchSpy);
  vi.stubEnv("WORKFLOWS_URL", "http://workflows.test:5100");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

describe("checks before upstream", () => {
  it.each(["0", "-1", "abc", "1.5", "1e3", "12345678901234567890"])("refuses run id %j", async (runId) => {
    expect((await callRoute(runId)).status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([null, "", "COUNT", "../x", "align"])("refuses step %j", async (step) => {
    expect((await callRoute("1", step)).status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("returns 401 when signed out", async () => {
    mockedGetSession.mockResolvedValue(null as never);
    expect((await callRoute("1")).status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("forwarding", () => {
  it("asks for the step's log with the user's token and returns only the log", async () => {
    const res = await callRoute("7", "stage-reference");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ step: "count", log: "done\n", truncated: false });
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("http://workflows.test:5100/scrna/cellranger/runs/7/logs?step=stage-reference");
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it.each([404, 409, 410])("passes the %i detail through", async (status) => {
    fetchSpy.mockResolvedValue(upstream({ detail: "Step count of run 1 hasn't started running yet" }, status));
    const res = await callRoute("1");
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ detail: "Step count of run 1 hasn't started running yet" });
  });

  it.each([500, 502, 503])("hides the %i detail", async (status) => {
    fetchSpy.mockResolvedValue(upstream({ detail: "Cluster access is not configured" }, status));
    const res = await callRoute("1");
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ detail: null });
  });

  it("returns 502 when the service can't be reached or answers oddly", async () => {
    fetchSpy.mockRejectedValue(new TypeError("connect ECONNREFUSED"));
    expect((await callRoute("1")).status).toBe(502);
    fetchSpy.mockResolvedValue(upstream({ log: 5 }));
    expect((await callRoute("1")).status).toBe(502);
  });
});
