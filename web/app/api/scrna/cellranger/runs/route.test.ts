// @vitest-environment jsdom
/**
 * Unit tests for the Cell Ranger run proxy: what reaches the workflows service, and
 * which upstream details the browser is allowed to see.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as routeModule from "@/app/api/scrna/cellranger/runs/route";

vi.mock("@/lib/supabase/server", () => ({
  getSession: vi.fn(),
}));

import { getSession } from "@/lib/supabase/server";

const mockedGetSession = vi.mocked(getSession);

const STARTED = {
  run_id: 12,
  sample: "tinygex",
  reference: "tiny_ref",
  run_key: "scrna-cellranger-12",
};

let fetchSpy: ReturnType<typeof vi.fn>;

// bloom-web listens on 0.0.0.0, so the request URL's host is never the public one.
const SAME_ORIGIN = {
  "content-type": "application/json",
  host: "bloom.salk.edu",
  origin: "https://bloom.salk.edu",
};

function callRoute(body: unknown, headers: Record<string, string> = SAME_ORIGIN) {
  return routeModule.POST(
    new Request("http://0.0.0.0:3000/api/scrna/cellranger/runs", {
      method: "POST",
      headers,
      body: typeof body === "string" ? body : JSON.stringify(body),
    })
  );
}

function upstream(body: unknown, status = 201) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedGetSession.mockResolvedValue({ access_token: "tok" } as never);
  fetchSpy = vi.fn().mockResolvedValue(upstream(STARTED));
  vi.stubGlobal("fetch", fetchSpy);
  vi.stubEnv("WORKFLOWS_URL", "http://workflows.test:5100");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

describe("module contract", () => {
  it("is a dynamic node route", () => {
    expect(routeModule.dynamic).toBe("force-dynamic");
    expect(routeModule.runtime).toBe("nodejs");
  });
});

describe("request checks", () => {
  it("refuses a body that isn't declared as JSON with 415, before anything else", async () => {
    const { "content-type": _omit, ...headers } = SAME_ORIGIN;
    for (const h of [headers, { ...SAME_ORIGIN, "content-type": "text/plain" }]) {
      const res = await callRoute({ sample: "tinygex", reference: "tiny_ref" }, h);
      expect(res.status).toBe(415);
    }
    expect(mockedGetSession).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([
    ["no Origin", (({ origin: _o, ...h }) => h)(SAME_ORIGIN)],
    ["a null Origin", { ...SAME_ORIGIN, origin: "null" }],
    ["another site", { ...SAME_ORIGIN, origin: "https://evil.example" }],
    ["another port", { ...SAME_ORIGIN, origin: "https://bloom.salk.edu:8443" }],
  ])("refuses %s with 403 before any work", async (_label, headers) => {
    const res = await callRoute({ sample: "tinygex", reference: "tiny_ref" }, headers);
    expect(res.status).toBe(403);
    expect(mockedGetSession).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("accepts the forwarded host Caddy sets", async () => {
    const res = await callRoute(
      { sample: "tinygex", reference: "tiny_ref" },
      { ...SAME_ORIGIN, host: "bloom-web:3000", "x-forwarded-host": "bloom.salk.edu" }
    );
    expect(res.status).toBe(201);
  });

  it("refuses a body that isn't JSON without calling upstream", async () => {
    const res = await callRoute("not json");
    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([
    [{}],
    [{ sample: "tinygex" }],
    [{ reference: "tiny_ref" }],
    [{ sample: 1, reference: "tiny_ref" }],
    [null],
  ])("refuses %j without calling upstream", async (body) => {
    const res = await callRoute(body);
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ detail: "Choose a sample and a reference." });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("returns 401 when signed out, without calling upstream", async () => {
    mockedGetSession.mockResolvedValue(null as never);
    const res = await callRoute({ sample: "tinygex", reference: "tiny_ref" });
    expect(res.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("forwarding", () => {
  it("posts only sample and reference with the user's token", async () => {
    const res = await callRoute({
      sample: "tinygex",
      reference: "tiny_ref",
      extra: "dropped",
    });
    expect(res.status).toBe(201);
    expect(await res.json()).toEqual(STARTED);

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("http://workflows.test:5100/scrna/cellranger/runs");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ sample: "tinygex", reference: "tiny_ref" });
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("forwards the dataset details as metadata", async () => {
    const metadata = { species_id: 2, dataset_name: "Root atlas" };
    await callRoute({ sample: "tinygex", reference: "tiny_ref", metadata });
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({
      sample: "tinygex",
      reference: "tiny_ref",
      metadata,
    });
  });

  it.each([[null], [[1]], ["root"], [5]])(
    "refuses metadata %j without calling upstream",
    async (metadata) => {
      const res = await callRoute({ sample: "tinygex", reference: "tiny_ref", metadata });
      expect(res.status).toBe(400);
      expect(fetchSpy).not.toHaveBeenCalled();
    }
  );

  it("defaults to the in-cluster workflows host", async () => {
    vi.unstubAllEnvs();
    delete process.env.WORKFLOWS_URL;
    await callRoute({ sample: "tinygex", reference: "tiny_ref" });
    expect(fetchSpy.mock.calls[0][0]).toBe("http://workflows:5100/scrna/cellranger/runs");
  });
});

describe("upstream answers", () => {
  it("returns 502 without the internal host when upstream is unreachable", async () => {
    fetchSpy.mockRejectedValue(new TypeError("connect ECONNREFUSED workflows.test"));
    const res = await callRoute({ sample: "tinygex", reference: "tiny_ref" });
    expect(res.status).toBe(502);
    expect(JSON.stringify(await res.json())).not.toContain("workflows.test");
  });

  it.each([422, 429])("passes the %i detail through", async (status) => {
    fetchSpy.mockResolvedValue(upstream({ detail: "Sample names can't contain '__'" }, status));
    const res = await callRoute({ sample: "a__b", reference: "tiny_ref" });
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ detail: "Sample names can't contain '__'" });
  });

  it.each([401, 500, 503])("hides the %i detail", async (status) => {
    fetchSpy.mockResolvedValue(upstream({ detail: "db host bloom-db:5432 refused" }, status));
    const res = await callRoute({ sample: "tinygex", reference: "tiny_ref" });
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ detail: null });
  });

  it("drops a 422 detail that isn't a string", async () => {
    fetchSpy.mockResolvedValue(upstream({ detail: [{ loc: ["body", "sample"] }] }, 422));
    const res = await callRoute({ sample: "x", reference: "tiny_ref" });
    expect(await res.json()).toEqual({ detail: null });
  });

  it("keeps a non-JSON error's status with no detail", async () => {
    fetchSpy.mockResolvedValue(upstream("<html>Bad Gateway</html>", 503));
    const res = await callRoute({ sample: "tinygex", reference: "tiny_ref" });
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ detail: null });
  });

  it.each([
    ["non-JSON", "ok"],
    ["a malformed", { run_id: "12", sample: "tinygex" }],
  ])("returns 502 for %s success body", async (_label, body) => {
    fetchSpy.mockResolvedValue(upstream(body, 201));
    const res = await callRoute({ sample: "tinygex", reference: "tiny_ref" });
    expect(res.status).toBe(502);
  });
});
