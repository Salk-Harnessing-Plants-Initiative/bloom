// @vitest-environment jsdom
/**
 * Unit tests for the S3 folder check proxy: the request checks, what reaches the workflows
 * service, and which upstream details the browser sees.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as routeModule from "@/app/api/scrna/cellranger/folder-check/route";

vi.mock("@/lib/supabase/server", () => ({
  getSession: vi.fn(),
}));

import { getSession } from "@/lib/supabase/server";

const mockedGetSession = vi.mocked(getSession);

const CHECK = {
  fastq_url: "s3://lab-data/run42/",
  sample: "col0",
  lanes: [1],
  files: [
    { name: "col0_S1_L001_R1_001.fastq.gz", size: 10, etag: '"a"' },
    { name: "col0_S1_L001_R2_001.fastq.gz", size: 32, etag: '"b"' },
  ],
  file_count: 2,
  total_bytes: 42,
};

const SAME_ORIGIN = {
  "content-type": "application/json",
  host: "bloom.salk.edu",
  origin: "https://bloom.salk.edu",
};

let fetchSpy: ReturnType<typeof vi.fn>;

function callRoute(body: unknown, headers: Record<string, string> = SAME_ORIGIN) {
  return routeModule.POST(
    new Request("http://0.0.0.0:3000/api/scrna/cellranger/folder-check", {
      method: "POST",
      headers,
      body: typeof body === "string" ? body : JSON.stringify(body),
    })
  );
}

function upstream(body: unknown, status = 200) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedGetSession.mockResolvedValue({ access_token: "tok" } as never);
  fetchSpy = vi.fn().mockResolvedValue(upstream(CHECK));
  vi.stubGlobal("fetch", fetchSpy);
  vi.stubEnv("WORKFLOWS_URL", "http://workflows.test:5100");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
});

describe("request checks", () => {
  it("is a dynamic node route", () => {
    expect(routeModule.dynamic).toBe("force-dynamic");
    expect(routeModule.runtime).toBe("nodejs");
  });

  it("refuses a body not declared as JSON, and another site's request", async () => {
    expect((await callRoute(CHECK, { ...SAME_ORIGIN, "content-type": "text/plain" })).status).toBe(415);
    expect((await callRoute(CHECK, { ...SAME_ORIGIN, origin: "https://evil.example" })).status).toBe(403);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([["not json"], [{}], [{ fastq_url: "  " }], [{ fastq_url: 7 }], [null]])(
    "refuses %j without calling upstream",
    async (body) => {
      expect((await callRoute(body)).status).toBe(400);
      expect(fetchSpy).not.toHaveBeenCalled();
    }
  );

  it("returns 401 when signed out", async () => {
    mockedGetSession.mockResolvedValue(null as never);
    expect((await callRoute({ fastq_url: CHECK.fastq_url })).status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("forwarding", () => {
  it("posts only the folder with the user's token and returns the check", async () => {
    const res = await callRoute({ fastq_url: "s3://lab-data/run42", extra: "dropped" });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(CHECK);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("http://workflows.test:5100/scrna/cellranger/folder-check");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(JSON.parse(init.body)).toEqual({ fastq_url: "s3://lab-data/run42" });
  });

  it.each([422, 429])("passes a %i detail through", async (status) => {
    fetchSpy.mockResolvedValue(upstream({ detail: "No FASTQs directly in s3://lab-data/run42/" }, status));
    const res = await callRoute({ fastq_url: CHECK.fastq_url });
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ detail: "No FASTQs directly in s3://lab-data/run42/" });
  });

  it("hides other upstream details", async () => {
    fetchSpy.mockResolvedValue(upstream({ detail: "S3 answered 500 when listing" }, 502));
    const res = await callRoute({ fastq_url: CHECK.fastq_url });
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ detail: null });
  });

  it("says the service is unavailable when it can't be reached", async () => {
    fetchSpy.mockRejectedValue(new TypeError("fetch failed"));
    const res = await callRoute({ fastq_url: CHECK.fastq_url });
    expect(res.status).toBe(502);
    expect((await res.json()).detail).toBe("The job service isn't available right now.");
  });

  it("defaults to the in-cluster workflows host", async () => {
    vi.unstubAllEnvs();
    delete process.env.WORKFLOWS_URL;
    await callRoute({ fastq_url: "s3://lab-data/run42/" });
    expect(fetchSpy.mock.calls[0][0]).toBe("http://workflows:5100/scrna/cellranger/folder-check");
    expect(fetchSpy.mock.calls[0][1].method).toBe("POST");
  });

  it("returns 401 for a session without a token", async () => {
    mockedGetSession.mockResolvedValue({ access_token: "" } as never);
    const res = await callRoute({ fastq_url: "s3://lab-data/run42/" });
    expect(res.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([401, 500, 503])("hides a %i detail", async (status) => {
    fetchSpy.mockResolvedValue(upstream({ detail: "operator text" }, status));
    const res = await callRoute({ fastq_url: "s3://lab-data/run42/" });
    expect(res.status).toBe(status);
    expect(await res.json()).toEqual({ detail: null });
  });

  it("drops a blank 422 detail", async () => {
    fetchSpy.mockResolvedValue(upstream({ detail: "   " }, 422));
    const res = await callRoute({ fastq_url: "s3://lab-data/run42/" });
    expect(await res.json()).toEqual({ detail: null });
  });

  it("answers a non-JSON 200 with 502 and says so", async () => {
    fetchSpy.mockResolvedValue(upstream("<html>", 200));
    const res = await callRoute({ fastq_url: "s3://lab-data/run42/" });
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ detail: "Unexpected response from the job service." });
  });

  it("keeps a non-JSON error's status with no detail", async () => {
    fetchSpy.mockResolvedValue(upstream("<html>", 503));
    const res = await callRoute({ fastq_url: "s3://lab-data/run42/" });
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ detail: null });
  });

  it("refuses a body over 256 KB with 413", async () => {
    const res = await callRoute({ fastq_url: "s3://lab-data/run42/", pad: "x".repeat(300_000) });
    expect(res.status).toBe(413);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("doesn't follow a redirect, and stops when the caller goes away or time runs out", async () => {
    await callRoute({ fastq_url: "s3://lab-data/run42/" });
    const init = fetchSpy.mock.calls[0][1];
    expect(init.redirect).toBe("manual");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("refuses a success body it doesn't recognise", async () => {
    fetchSpy.mockResolvedValue(upstream({ sample: "col0" }));
    expect((await callRoute({ fastq_url: CHECK.fastq_url })).status).toBe(502);
  });
});
