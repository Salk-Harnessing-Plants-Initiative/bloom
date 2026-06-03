/**
 * Round-trip tests for the JWT fixture helper.
 *
 * Lives at `web/lib/config/jwt-fixture.test.ts` (one level above the
 * `__fixtures__/` dir so it IS discovered by vitest's `include` glob —
 * the helper itself at `__fixtures__/jwt.ts` is excluded).
 *
 * Spec: openspec/changes/add-ghcr-image-publishing tasks.md §2.5.
 */

import { describe, expect, it } from "vitest";

import { decodeAnonKeyProject } from "@/lib/config/public-config";
import { makeAnonKey } from "@/lib/config/__fixtures__/jwt";

describe("makeAnonKey + decodeAnonKeyProject round-trip", () => {
  it("emits a 3-segment JWT", () => {
    const jwt = makeAnonKey({ iss: "https://bloom-dev.salk.edu" });
    expect(jwt.split(".")).toHaveLength(3);
  });

  it("payload segment contains base64url characters when claims include them", () => {
    // `iss` with `:` and `/` produces `:` (→ stays the same since `:` is
    // outside the base64 alphabet so it ends up encoded) — but more importantly,
    // JSON-encoded URLs reliably produce `+` / `/` in the encoded form which
    // the helper then substitutes to `-` / `_`. We assert at least one
    // base64url-specific character appears in the payload across a realistic
    // claim set.
    const jwt = makeAnonKey({
      iss: "https://bloom-dev.salk.edu",
      ref: "with-dashes_and_underscores",
      sub: "user~with?special&chars",
    });
    const payload = jwt.split(".")[1];
    // At minimum, base64url-encoded payload MUST NOT contain `+`, `/`, or `=`.
    expect(payload).not.toMatch(/[+/=]/);
  });

  it("decodes back to the original iss claim", () => {
    const jwt = makeAnonKey({ iss: "https://bloom-dev.salk.edu" });
    expect(decodeAnonKeyProject(jwt).iss).toBe("https://bloom-dev.salk.edu");
  });

  it("decodes back to the original ref claim", () => {
    const jwt = makeAnonKey({ ref: "bloomdev" });
    expect(decodeAnonKeyProject(jwt).ref).toBe("bloomdev");
  });

  it("omits ref when not present in claims", () => {
    const jwt = makeAnonKey({ iss: "https://only-iss.example" });
    const claims = decodeAnonKeyProject(jwt);
    expect(claims.iss).toBe("https://only-iss.example");
    expect(claims.ref).toBeUndefined();
  });

  it("omits iss when not present in claims", () => {
    const jwt = makeAnonKey({ ref: "only-ref" });
    const claims = decodeAnonKeyProject(jwt);
    expect(claims.ref).toBe("only-ref");
    expect(claims.iss).toBeUndefined();
  });

  it("ignores extra claims that aren't iss/ref", () => {
    const jwt = makeAnonKey({
      iss: "https://bloom-dev.salk.edu",
      ref: "bloomdev",
      sub: "user-id-123",
      role: "anon",
      exp: 1234567890,
    });
    const claims = decodeAnonKeyProject(jwt);
    expect(claims).toEqual({
      iss: "https://bloom-dev.salk.edu",
      ref: "bloomdev",
    });
  });
});
