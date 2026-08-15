import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  getStoredScanVideo,
  getStoredScanVideoUrl,
  isNotFound,
} from "./scan-video";
import { createServerSupabaseClient } from "@/lib/supabase/server";

vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: vi.fn(),
}));

const mockedClient = vi.mocked(createServerSupabaseClient);

/** A Supabase client whose signing call returns exactly what Storage would. */
function clientReturning(result: { data?: unknown; error?: unknown }) {
  const createSignedUrl = vi.fn().mockResolvedValue(result);
  const from = vi.fn().mockReturnValue({ createSignedUrl });
  mockedClient.mockResolvedValue({ storage: { from } } as never);
  return { from, createSignedUrl };
}

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

// The three-state answer is the whole point of this function: `absent` is the only one
// that permits generating over the top of whatever is there.
describe("getStoredScanVideo", () => {
  const ORIGINAL_PUBLIC_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;

  beforeEach(() => {
    vi.clearAllMocks();
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://bloom.example.org";
  });

  afterEach(() => {
    if (ORIGINAL_PUBLIC_URL === undefined) delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    else process.env.NEXT_PUBLIC_SUPABASE_URL = ORIGINAL_PUBLIC_URL;
  });

  it("reports a stored video, with a URL the browser can reach", async () => {
    // Storage signs against the internal gateway; a caller who got that back would
    // render a link to a host that does not resolve outside the cluster.
    clientReturning({
      data: { signedUrl: "http://kong:8000/storage/v1/object/sign/videos/x.mp4?token=t" },
    });

    await expect(getStoredScanVideo(11)).resolves.toEqual({
      status: "present",
      url: "https://bloom.example.org/storage/v1/object/sign/videos/x.mp4?token=t",
    });
  });

  it("asks for the same key the encoder writes, in the videos bucket", async () => {
    const { from, createSignedUrl } = clientReturning({
      data: { signedUrl: "https://cdn.example/x.mp4" },
    });

    await getStoredScanVideo(4207);

    expect(from).toHaveBeenCalledWith("videos");
    expect(createSignedUrl).toHaveBeenCalledWith("cyl-videos/4207.mp4", expect.any(Number));
  });

  it("reports absent only for a genuine not-found", async () => {
    clientReturning({ error: { message: "Object not found", status: 400, statusCode: "404" } });

    await expect(getStoredScanVideo(11)).resolves.toEqual({ status: "absent" });
  });

  it("reports unknown — never absent — when the lookup fails for any other reason", async () => {
    // This is the one that matters. Read as `absent`, a permissions or gateway failure
    // lets generation proceed and overwrite a complete rotation, in place, unrecoverably.
    for (const error of [
      { message: "permission denied", status: 403, statusCode: "403" },
      { message: "gateway timeout", status: 504, statusCode: "504" },
      { message: "" },
    ]) {
      clientReturning({ error });
      const stored = await getStoredScanVideo(11);
      expect(stored.status).toBe("unknown");
    }
  });

  it("reports unknown when the call succeeds but hands back no URL", async () => {
    // A success with nothing usable in it is not evidence that a video is absent.
    clientReturning({ data: { signedUrl: "" } });

    const stored = await getStoredScanVideo(11);

    expect(stored.status).toBe("unknown");
  });
});

describe("getStoredScanVideoUrl", () => {
  beforeEach(() => vi.clearAllMocks());

  it("gives back the URL when a video is stored", async () => {
    clientReturning({ data: { signedUrl: "https://cdn.example/v.mp4" } });

    await expect(getStoredScanVideoUrl(11)).resolves.toBe("https://cdn.example/v.mp4");
  });

  it("gives back null for anything short of a confirmed video", async () => {
    // Including `unknown`: this is a read-only convenience, so it must not turn an
    // undecided answer into something a caller could mistake for a stored video.
    for (const result of [
      { error: { message: "Object not found", statusCode: "404" } },
      { error: { message: "gateway timeout", status: 504 } },
    ]) {
      clientReturning(result);
      await expect(getStoredScanVideoUrl(11)).resolves.toBeNull();
    }
  });
});
