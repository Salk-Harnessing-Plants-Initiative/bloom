/** The middleware: signed out, the genome browser's data answers 401 and pages go to /login. */

import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getUser = vi.fn();
vi.mock("@supabase/ssr", () => ({ createServerClient: () => ({ auth: { getUser } }) }));

import { SIGNED_OUT_DATA_BODY } from "@/lib/signed-out-paths";
import { middleware } from "./middleware";

const request = (path: string) => middleware(new NextRequest(`http://localhost:3000${path}`));
const signedIn = (yes: boolean) =>
  getUser.mockResolvedValue({ data: { user: yes ? { email: "a@b.c" } : null }, error: null });

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, "log").mockImplementation(() => {});
});

describe("middleware", () => {
  it.each(["/app/jbrowse/density/4", "/app/jbrowse/files/4/fasta", "/app/jbrowse/expression"])(
    "answers a signed-out request for %s with 401 and a text body",
    async (path) => {
      signedIn(false);
      const response = await request(path);
      expect(response.status).toBe(401);
      expect(response.headers.get("content-type")).toBe("text/plain");
      expect(await response.text()).toBe(SIGNED_OUT_DATA_BODY);
    },
  );

  it("sends a signed-out page request to the login page", async () => {
    signedIn(false);
    const response = await request("/app/jbrowse?genome=a.v1");
    expect(response.status).toBe(307);
    expect(new URL(response.headers.get("location")!).pathname).toBe("/login");
  });

  it("lets a signed-in request through", async () => {
    signedIn(true);
    const response = await request("/app/jbrowse/density/4");
    expect(response.status).toBe(200);
    expect(response.headers.get("location")).toBeNull();
  });
});
