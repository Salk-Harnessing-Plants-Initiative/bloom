import { describe, it, expect } from "vitest";
import { isNotFound, scanVideoPath } from "./scan-video";

// This module decides whether it is safe to overwrite a scan's video. Upstream
// writes in place and the bucket has no versioning, so a lookup misread as
// "absent" destroys a complete rotation. Everything below is about that one call.

describe("isNotFound", () => {
  it("recognises the shape Storage actually returns for a missing object", () => {
    // HTTP 400 with the service code in `statusCode`, as a string. Checking
    // `status === 404` alone never matched, which left the wording regex as the
    // only guard on an irreversible write.
    expect(isNotFound({ message: "Object not found", status: 400, statusCode: "404" })).toBe(
      true
    );
  });

  it("recognises it from the code alone, without help from the wording", () => {
    // The message deliberately does not match the regex. If this is the only
    // assertion standing, the guard rests on Storage never rephrasing itself.
    expect(isNotFound({ message: "Object was removed", status: 400, statusCode: "404" })).toBe(
      true
    );
  });

  it("still accepts a literal 404 status, for anything that reports one", () => {
    expect(isNotFound({ message: "whatever", status: 404 })).toBe(true);
  });

  it("falls back to the message when neither code is present", () => {
    expect(isNotFound({ message: "Object not found" })).toBe(true);
    expect(isNotFound({ message: "The resource does not exist" })).toBe(true);
    expect(isNotFound({ message: "no such key" })).toBe(true);
  });

  it("treats a permission failure as unknown, not absent", () => {
    // The dangerous direction: 403 means we cannot see the object, not that it
    // is missing. Reading it as absent overwrites whatever is actually there.
    expect(isNotFound({ message: "new row violates row-level security policy", status: 403 })).toBe(
      false
    );
    expect(isNotFound({ message: "Unauthorized", status: 401, statusCode: "401" })).toBe(false);
  });

  it("treats a gateway or timeout failure as unknown", () => {
    expect(isNotFound({ message: "upstream connect error", status: 502 })).toBe(false);
    expect(isNotFound({ message: "timeout of 5000ms exceeded" })).toBe(false);
    expect(isNotFound({ message: "Internal Server Error", status: 500, statusCode: "500" })).toBe(
      false
    );
  });

  it("treats an error with nothing to go on as unknown", () => {
    expect(isNotFound({})).toBe(false);
    expect(isNotFound({ message: "" })).toBe(false);
  });

  it("does not read a 404 mentioned mid-message as a service code", () => {
    // `statusCode` is compared, not searched for — a message that happens to
    // contain the digits must not decide this.
    expect(isNotFound({ message: "bucket policy 404 rule denied", status: 403 })).toBe(false);
  });
});

describe("scanVideoPath", () => {
  it("matches the key services/workflows/video.py writes", () => {
    expect(scanVideoPath(4207)).toBe("cyl-videos/4207.mp4");
  });
});
