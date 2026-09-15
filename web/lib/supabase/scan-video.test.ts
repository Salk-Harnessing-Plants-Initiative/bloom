import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { getStoredScanVideo, getStoredScanVideoUrl } from "./scan-video";
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
