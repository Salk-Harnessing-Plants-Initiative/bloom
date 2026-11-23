import { describe, expect, it } from "vitest";

import { SIGNED_OUT_DATA_BODY, signedOutAnswer } from "./signed-out-paths";

describe("signedOutAnswer", () => {
  it.each(["/login", "/forgot-password", "/reset-password", "/verify", "/auth/callback", "/error", "/api/config"])(
    "lets the public path %s through",
    (path) => expect(signedOutAnswer(path)).toBe("pass"),
  );

  it.each([
    "/app/jbrowse/files/4/fasta",
    "/app/jbrowse/density/4",
    "/app/jbrowse/expression",
    "/app/jbrowse/expression/4",
  ])("answers the genome browser's data path %s with 401", (path) => expect(signedOutAnswer(path)).toBe("401"));

  it.each(["/app", "/app/jbrowse", "/app/genes", "/app/jbrowse/filesx", "/app/jbrowse/densityx", "/app/jbrowse/expressions"])(
    "sends the signed-out page %s to the login page",
    (path) => expect(signedOutAnswer(path)).toBe("login"),
  );

  it("tells the caller to sign in", () => {
    expect(SIGNED_OUT_DATA_BODY).toBe("Sign in to read genome data.");
  });
});
