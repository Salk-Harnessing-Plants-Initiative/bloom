import { describe, expect, it } from "vitest";

import { passesWithoutLogin } from "./signed-out-paths";

describe("passesWithoutLogin", () => {
  it.each(["/login", "/auth/callback", "/api/config", "/app/jbrowse/files/4/fasta"])(
    "lets %s through to answer for itself",
    (path) => expect(passesWithoutLogin(path)).toBe(true),
  );

  it.each(["/app", "/app/jbrowse", "/app/jbrowse?genome=x.v1", "/app/jbrowse/filesx", "/app/genes"])(
    "sends a signed-out %s to the login page",
    (path) => expect(passesWithoutLogin(path)).toBe(false),
  );
});
