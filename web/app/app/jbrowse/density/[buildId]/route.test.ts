import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: vi.fn(async () => ({})),
  getUser: vi.fn(),
}));

vi.mock("@/lib/jbrowse/bloom-genomes-server", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/jbrowse/bloom-genomes-server")>()),
  readBuildGeneStarts: vi.fn(),
  buildIsReady: vi.fn(),
}));

import { GET, clearDensityCache } from "./route";
import { REDIRECT_MAX_AGE_S, buildIsReady, readBuildGeneStarts } from "@/lib/jbrowse/bloom-genomes-server";
import { getUser } from "@/lib/supabase/server";

const mockedUser = vi.mocked(getUser);
const mockedRead = vi.mocked(readBuildGeneStarts);
const mockedReady = vi.mocked(buildIsReady);

const call = (buildId: string) =>
  GET(new Request("http://localhost/app/jbrowse/density"), { params: Promise.resolve({ buildId }) });

beforeEach(() => {
  vi.clearAllMocks();
  clearDensityCache();
  mockedUser.mockResolvedValue({ id: "u1" } as never);
  mockedReady.mockResolvedValue(true);
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("GET /app/jbrowse/density/[buildId]", () => {
  it("answers the build's gene density as plain-text bedGraph, cacheable privately", async () => {
    mockedRead.mockResolvedValue({ status: "ok", genes: [{ contig: "Chr1", start: 5 }] });
    const response = await call("4");
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("Chr1\t0\t100000\t1\n");
    expect(response.headers.get("content-type")).toBe("text/plain; charset=utf-8");
    expect(response.headers.get("cache-control")).toBe(`private, max-age=${REDIRECT_MAX_AGE_S}`);
    expect(mockedRead).toHaveBeenCalledWith({}, 4);
  });

  it("reads a build's genes once, then answers from memory after the login check", async () => {
    mockedRead.mockResolvedValue({ status: "ok", genes: [{ contig: "Chr1", start: 5 }] });
    await call("4");
    await call("4");
    expect(mockedRead).toHaveBeenCalledTimes(1);
    mockedUser.mockResolvedValue(null);
    expect((await call("4")).status).toBe(401);
  });

  it("stops answering from memory once the build is withdrawn", async () => {
    mockedRead.mockResolvedValue({ status: "ok", genes: [{ contig: "Chr1", start: 5 }] });
    await call("4");
    mockedReady.mockResolvedValue(false);
    expect((await call("4")).status).toBe(404);
  });

  it("answers 401 without a login, reading nothing", async () => {
    mockedUser.mockResolvedValue(null);
    expect((await call("4")).status).toBe(401);
    expect(mockedRead).not.toHaveBeenCalled();
  });

  it.each(["0", "abc", "4.5", "99999999999999999999"])("answers 404 for build %j", async (buildId) => {
    expect((await call(buildId)).status).toBe(404);
    expect(mockedRead).not.toHaveBeenCalled();
  });

  it("answers 404 for a build that is not ready, or withdrawn", async () => {
    mockedRead.mockResolvedValue({ status: "not found" });
    expect((await call("4")).status).toBe(404);
  });

  it("answers 502 and logs when the genes cannot be read in full", async () => {
    mockedRead.mockResolvedValue({ status: "failed", reason: "read 10 of 12 genes" });
    expect((await call("4")).status).toBe(502);
    expect(console.error).toHaveBeenCalled();
  });
});
