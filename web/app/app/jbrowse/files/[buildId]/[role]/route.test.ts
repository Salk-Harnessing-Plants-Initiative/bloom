import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: vi.fn(async () => ({})),
  getUser: vi.fn(),
}));

vi.mock("@/lib/jbrowse/bloom-genomes-server", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/jbrowse/bloom-genomes-server")>()),
  signBuildFile: vi.fn(),
}));

import { GET } from "./route";
import { REDIRECT_MAX_AGE_S, signBuildFile } from "@/lib/jbrowse/bloom-genomes-server";
import { getUser } from "@/lib/supabase/server";

const mockedUser = vi.mocked(getUser);
const mockedSign = vi.mocked(signBuildFile);

const call = (buildId: string, role: string) =>
  GET(new Request("http://localhost/app/jbrowse/files"), { params: Promise.resolve({ buildId, role }) });

beforeEach(() => {
  vi.clearAllMocks();
  mockedUser.mockResolvedValue({ id: "u1" } as never);
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("GET /app/jbrowse/files/[buildId]/[role]", () => {
  it("redirects to the signed URL, reusable by this browser for less than the TTL", async () => {
    mockedSign.mockResolvedValue({ status: "signed", url: "https://bloom.example/api/sign/x?token=t" });
    const response = await call("4", "fasta");
    expect(response.status).toBe(302);
    expect(response.headers.get("location")).toBe("https://bloom.example/api/sign/x?token=t");
    expect(response.headers.get("cache-control")).toBe(`private, max-age=${REDIRECT_MAX_AGE_S}`);
    expect(mockedSign).toHaveBeenCalledWith({}, 4, "fasta");
  });

  it("hands out nothing without a login", async () => {
    mockedUser.mockResolvedValue(null);
    const response = await call("4", "fasta");
    expect(response.status).toBe(401);
    expect(mockedSign).not.toHaveBeenCalled();
  });

  it.each(["0", "-1", "4.5", "abc", "99999999999999999999"])("is not found for build id %j", async (id) => {
    expect((await call(id, "fasta")).status).toBe(404);
    expect(mockedSign).not.toHaveBeenCalled();
  });

  it("is not found when the build or role is not offered", async () => {
    mockedSign.mockResolvedValue({ status: "not found" });
    expect((await call("4", "fasta")).status).toBe(404);
  });

  it("logs the detail and answers a short message when signing fails", async () => {
    mockedSign.mockResolvedValue({ status: "failed", reason: "signing a/b: Object not found" });
    const response = await call("4", "fasta");
    expect(response.status).toBe(502);
    expect(await response.text()).toBe("The genome file could not be opened.");
    expect(console.error).toHaveBeenCalledWith("[genome browser]", "signing a/b: Object not found");
  });
});
