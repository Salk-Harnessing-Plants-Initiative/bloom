import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { getStoredPlateVideo, getStoredPlateVideoUrl } from "./plate-video";
import { createServerSupabaseClient } from "@/lib/supabase/server";

vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: vi.fn(),
}));

const mockedClient = vi.mocked(createServerSupabaseClient);

/**
 * A Supabase client that answers the signing call and the metadata row with
 * exactly what Storage and PostgREST would.
 */
function clientReturning(
  signed: { data?: unknown; error?: unknown },
  row: { data?: unknown; error?: unknown } = { data: null }
) {
  const createSignedUrl = vi.fn().mockResolvedValue(signed);
  const storageFrom = vi.fn().mockReturnValue({ createSignedUrl });

  const builder: Record<string, unknown> = {};
  const select = vi.fn().mockReturnValue(builder);
  const eq = vi.fn().mockReturnValue(builder);
  const is = vi.fn().mockReturnValue(builder);
  const maybeSingle = vi.fn().mockResolvedValue(row);
  Object.assign(builder, { select, eq, is, maybeSingle });
  const tableFrom = vi.fn().mockReturnValue(builder);

  mockedClient.mockResolvedValue({
    storage: { from: storageFrom },
    from: tableFrom,
  } as never);

  return { storageFrom, createSignedUrl, tableFrom, select, eq, is, maybeSingle };
}

const SIGNED = {
  data: {
    signedUrl: "http://kong:8000/storage/v1/object/sign/graviscan-videos/v.mp4?token=t",
  },
};
const PUBLIC_URL =
  "https://bloom.example.org/storage/v1/object/sign/graviscan-videos/v.mp4?token=t";

// This module answers one question for the plate page: is there a video to play,
// and how many frames is it. `unknown` is the reason it exists — a lookup that
// failed is not an absence, and reporting "no video" for a plate that has one
// sends a scientist to re-render something that was already fine.
describe("getStoredPlateVideo", () => {
  const ORIGINAL_PUBLIC_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
  let warn: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.clearAllMocks();
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://bloom.example.org";
    warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    warn.mockRestore();
    if (ORIGINAL_PUBLIC_URL === undefined) delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    else process.env.NEXT_PUBLIC_SUPABASE_URL = ORIGINAL_PUBLIC_URL;
  });

  it("reports a stored video, with a URL the browser can reach", async () => {
    // Storage signs against the internal gateway. Handed that back unchanged, the
    // page renders a link to a host that does not resolve outside the cluster.
    clientReturning(SIGNED, { data: { frame_count: 86 } });

    await expect(getStoredPlateVideo(7, "P1", 2)).resolves.toEqual({
      status: "present",
      url: PUBLIC_URL,
      frames: 86,
    });
  });

  it("asks for the same key the encoder writes, in the graviscan videos bucket", async () => {
    const { storageFrom, createSignedUrl } = clientReturning(SIGNED);

    await getStoredPlateVideo(7, "P1", 2);

    expect(storageFrom).toHaveBeenCalledWith("graviscan-videos");
    expect(createSignedUrl).toHaveBeenCalledWith("7/wave-2/P1.mp4", 3600);
  });

  it("signs the URL for long enough to watch the video", async () => {
    // A link that expires while the player is still buffering is a broken video
    // to the scientist looking at it.
    const { createSignedUrl } = clientReturning(SIGNED);

    await getStoredPlateVideo(7, "P1", 2);

    expect(createSignedUrl.mock.calls[0][1]).toBeGreaterThanOrEqual(600);
  });

  it("reports absent when the plate id cannot become a key", async () => {
    // No object can exist under a key that cannot be built, so this is a real
    // absence rather than a failed lookup.
    const { storageFrom } = clientReturning(SIGNED);

    await expect(getStoredPlateVideo(7, "../escape", 2)).resolves.toEqual({
      status: "absent",
    });
    expect(storageFrom).not.toHaveBeenCalled();
  });

  it("reports absent only for a genuine not-found", async () => {
    // Storage answers a missing object with HTTP 400 and a string `statusCode`.
    clientReturning({
      error: { message: "Object not found", status: 400, statusCode: "404" },
    });

    await expect(getStoredPlateVideo(7, "P1", 2)).resolves.toEqual({ status: "absent" });
  });

  it("reports unknown — never absent — when the lookup fails for any other reason", async () => {
    // The one that matters. Read as `absent`, the page offers a render button for a
    // plate that already has a video, and the scientist re-runs work that was done.
    for (const error of [
      { message: "permission denied", status: 403, statusCode: "403" },
      { message: "gateway timeout", status: 504, statusCode: "504" },
      { message: "" },
    ]) {
      clientReturning({ error });
      const stored = await getStoredPlateVideo(7, "P1", 2);
      expect(stored.status).toBe("unknown");
    }
  });

  it("carries the storage failure into the reason", async () => {
    clientReturning({ error: { message: "gateway timeout", status: 504 } });

    const stored = await getStoredPlateVideo(7, "P1", 2);

    expect(stored).toEqual({ status: "unknown", reason: "gateway timeout" });
  });

  it("reports unknown when the call succeeds but hands back no URL", async () => {
    // A success with nothing usable in it is not evidence that a video is absent.
    for (const data of [{ signedUrl: "" }, {}, null]) {
      clientReturning({ data });
      const stored = await getStoredPlateVideo(7, "P1", 2);
      expect(stored.status).toBe("unknown");
    }
  });

  it("reads the frame count from the row the service writes", async () => {
    const { tableFrom, select, eq } = clientReturning(SIGNED, {
      data: { frame_count: 42 },
    });

    const stored = await getStoredPlateVideo(7, "P1", 2);

    expect(tableFrom).toHaveBeenCalledWith("gravi_plate_videos");
    expect(select).toHaveBeenCalledWith("frame_count");
    expect(eq).toHaveBeenCalledWith("experiment_id", 7);
    expect(eq).toHaveBeenCalledWith("plate_id", "P1");
    expect(stored).toMatchObject({ frames: 42 });
  });

  it("matches the row on the wave the caller asked for", async () => {
    const { eq, is } = clientReturning(SIGNED, { data: { frame_count: 5 } });

    await getStoredPlateVideo(7, "P1", 3);

    expect(eq).toHaveBeenCalledWith("wave_number", 3);
    expect(is).not.toHaveBeenCalled();
  });

  it("matches a plate with no wave on a null column, not on equality", async () => {
    // A plate with no wave is a real case, and `wave_number = NULL` matches
    // nothing in Postgres — the row would be missed and the count lost.
    const { eq, is } = clientReturning(SIGNED, { data: { frame_count: 5 } });

    await getStoredPlateVideo(7, "P1", null);

    expect(is).toHaveBeenCalledWith("wave_number", null);
    expect(eq).not.toHaveBeenCalledWith("wave_number", null);
  });

  it("serves the video with no frame count when the row is missing", async () => {
    // A row can be lost while the object survives. That is a broken record, not an
    // empty video: the file still plays, so it is still offered.
    clientReturning(SIGNED, { data: null });

    await expect(getStoredPlateVideo(7, "P1", 2)).resolves.toEqual({
      status: "present",
      url: PUBLIC_URL,
      frames: null,
    });
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("7/wave-2/P1.mp4"));
  });

  it("keeps a recorded count of zero rather than reading it as unknown", async () => {
    clientReturning(SIGNED, { data: { frame_count: 0 } });

    await expect(getStoredPlateVideo(7, "P1", 2)).resolves.toMatchObject({ frames: 0 });
  });

  it("lets storage decide presence, not the row", async () => {
    // A row outliving its object is the dangerous direction: trusting it would
    // offer a player for a file that is gone.
    clientReturning(
      { error: { message: "Object not found", status: 400, statusCode: "404" } },
      { data: { frame_count: 86 } }
    );

    await expect(getStoredPlateVideo(7, "P1", 2)).resolves.toEqual({ status: "absent" });
  });

  it("does not ask for the row when there is no video to describe", async () => {
    const { tableFrom } = clientReturning({
      error: { message: "Object not found", statusCode: "404" },
    });

    await getStoredPlateVideo(7, "P1", 2);

    expect(tableFrom).not.toHaveBeenCalled();
  });
});

describe("getStoredPlateVideoUrl", () => {
  const ORIGINAL_PUBLIC_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;

  beforeEach(() => {
    vi.clearAllMocks();
    delete process.env.NEXT_PUBLIC_SUPABASE_URL;
  });

  afterEach(() => {
    if (ORIGINAL_PUBLIC_URL !== undefined)
      process.env.NEXT_PUBLIC_SUPABASE_URL = ORIGINAL_PUBLIC_URL;
  });

  it("gives back the URL when a video is stored", async () => {
    clientReturning({ data: { signedUrl: "https://cdn.example/v.mp4" } });

    await expect(getStoredPlateVideoUrl(7, "P1", 2)).resolves.toBe(
      "https://cdn.example/v.mp4"
    );
  });

  it("gives back null for anything short of a confirmed video", async () => {
    // Including `unknown`: this is a read-only convenience, so it must not turn an
    // undecided answer into something a caller could mistake for a stored video.
    for (const result of [
      { error: { message: "Object not found", statusCode: "404" } },
      { error: { message: "gateway timeout", status: 504 } },
    ]) {
      clientReturning(result);
      await expect(getStoredPlateVideoUrl(7, "P1", 2)).resolves.toBeNull();
    }
  });
});
