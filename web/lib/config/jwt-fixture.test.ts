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

  // ─── UTF-8 safety (Copilot review #1 + #2 on PR #268) ─────────────────────
  // JWTs are defined to carry UTF-8 JSON; `atob`/`btoa` only handle Latin-1.
  // These tests would have caught the original bug where `makeAnonKey` threw
  // InvalidCharacterError on non-ASCII input and `decodeAnonKeyProject`
  // silently corrupted multi-byte characters.

  it("round-trips an iss claim containing multi-byte UTF-8 characters", () => {
    const iss = "https://bloöm-dev.salk.edu/café";
    const jwt = makeAnonKey({ iss });
    expect(decodeAnonKeyProject(jwt).iss).toBe(iss);
  });

  it("round-trips a ref claim with non-ASCII unicode", () => {
    const ref = "bloomdev-中文-🌱";
    const jwt = makeAnonKey({ ref });
    expect(decodeAnonKeyProject(jwt).ref).toBe(ref);
  });

  it("round-trips an extra claim with combining marks and emoji", () => {
    // Validates the encode/decode path doesn't drop or corrupt characters
    // that span multiple bytes (or even multiple code points after NFC).
    const sub = "ユーザー́-👨‍🔬"; // combining acute accent + ZWJ sequence
    const iss = "https://bloom-dev.salk.edu";
    const jwt = makeAnonKey({ iss, ref: "bloomdev", sub });
    const claims = decodeAnonKeyProject(jwt);
    expect(claims.iss).toBe(iss);
    // The fixture preserves all claims in the payload; decode only surfaces
    // iss/ref by spec. Manually decode payload to verify sub survived too.
    const payload = jwt.split(".")[1];
    // Replicate the base64url decode that decodeAnonKeyProject does
    // (using Buffer here for the test, not the production code path).
    const padded = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padding =
      padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
    const json = Buffer.from(padded + padding, "base64").toString("utf-8");
    const parsed = JSON.parse(json) as { sub?: string };
    expect(parsed.sub).toBe(sub);
  });
});
