import { describe, it, expect } from "vitest";
import {
  isScanVideoResult,
  parseId,
  videoErrorMessage,
  videoResultSummary,
  type ScanVideoResult,
} from "./scan-video.helpers";

function result(overrides: Partial<ScanVideoResult> = {}): ScanVideoResult {
  return {
    scan_id: 5,
    experiment_id: 1,
    frames: 72,
    frames_expected: 72,
    truncated: false,
    regenerated: true,
    path: "cyl-videos/5.mp4",
    download_url: "https://x/cyl-videos/5.mp4?token=a",
    ...overrides,
  };
}

describe("parseId", () => {
  it("accepts a positive integer", () => {
    expect(parseId("1")).toBe(1);
    expect(parseId("4207")).toBe(4207);
  });

  it("rejects path traversal, so the upstream URL can't be retargeted", () => {
    expect(parseId("1/../../health")).toBeNull();
    expect(parseId("../1")).toBeNull();
    expect(parseId("1%2F..")).toBeNull();
  });

  it("rejects zero and negatives", () => {
    expect(parseId("0")).toBeNull();
    expect(parseId("-1")).toBeNull();
  });

  it("rejects non-integers and non-digits", () => {
    expect(parseId("1.5")).toBeNull();
    expect(parseId("1e3")).toBeNull();
    expect(parseId("abc")).toBeNull();
    expect(parseId("")).toBeNull();
    expect(parseId(" 1")).toBeNull();
  });

  it("rejects missing values", () => {
    expect(parseId(undefined)).toBeNull();
    expect(parseId(null)).toBeNull();
  });

  it("rejects an integer too large to represent exactly", () => {
    expect(parseId("9".repeat(30))).toBeNull();
  });
});

describe("isScanVideoResult", () => {
  it("accepts a full result", () => {
    expect(isScanVideoResult(result())).toBe(true);
  });

  it("rejects the shapes that would render as undefined", () => {
    // "Encoded undefined frames." reached the user before this guard existed.
    expect(isScanVideoResult({ frames: 72 })).toBe(false);
    expect(isScanVideoResult({ ...result(), frames: undefined })).toBe(false);
    expect(isScanVideoResult({ ...result(), truncated: undefined })).toBe(false);
  });

  it("rejects a result with no usable download_url", () => {
    expect(isScanVideoResult({ ...result(), download_url: "" })).toBe(false);
    expect(isScanVideoResult({ ...result(), download_url: "  " })).toBe(false);
    expect(isScanVideoResult({ ...result(), download_url: null })).toBe(false);
  });

  it("rejects non-objects", () => {
    expect(isScanVideoResult(null)).toBe(false);
    expect(isScanVideoResult(undefined)).toBe(false);
    expect(isScanVideoResult("ok")).toBe(false);
    expect(isScanVideoResult([])).toBe(false);
  });

  it("rejects numeric fields sent as strings", () => {
    expect(isScanVideoResult({ ...result(), frames: "72" })).toBe(false);
  });
});

describe("videoErrorMessage", () => {
  it("prefers the upstream detail when there is one", () => {
    expect(videoErrorMessage(404, "No images found for scan 5")).toBe(
      "No images found for scan 5"
    );
  });

  it("falls back to the status when the detail is absent or blank", () => {
    expect(videoErrorMessage(429)).toBe(
      "Too many video requests. Wait a minute and try again."
    );
    expect(videoErrorMessage(429, "   ")).toBe(
      "Too many video requests. Wait a minute and try again."
    );
    expect(videoErrorMessage(429, null)).toBe(
      "Too many video requests. Wait a minute and try again."
    );
  });

  it("maps the statuses the endpoint actually returns", () => {
    expect(videoErrorMessage(401)).toBe("Sign in to generate a video.");
    expect(videoErrorMessage(403)).toBe("You do not have access to this scan.");
    expect(videoErrorMessage(404)).toBe(
      "This scan was not found in this experiment."
    );
  });

  it("treats any 5xx as a generation failure", () => {
    expect(videoErrorMessage(500)).toBe(
      "Video generation failed. Try again in a moment."
    );
    expect(videoErrorMessage(502)).toBe(
      "Video generation failed. Try again in a moment."
    );
  });

  it("names the status for anything unrecognised", () => {
    expect(videoErrorMessage(418)).toBe(
      "Could not generate the video (HTTP 418)."
    );
  });
});

describe("videoResultSummary", () => {
  it("reports a clean full encode", () => {
    expect(videoResultSummary(result())).toBe("Encoded 72 frames.");
  });

  it("reports frames that could not be read", () => {
    expect(
      videoResultSummary(result({ frames: 70, frames_expected: 72 }))
    ).toBe("Encoded 70 of 72 frames (2 could not be read).");
  });

  it("says when a long scan was capped", () => {
    const summary = videoResultSummary(result({ truncated: true }));

    expect(summary).toContain("Encoded 72 frames.");
    expect(summary).toContain("more than 72 frames");
  });

  it("says the stored video was kept, without claiming a re-encode", () => {
    const summary = videoResultSummary(
      result({ regenerated: false, frames: 72 })
    );

    expect(summary).toContain("already has a video");
    expect(summary).not.toContain("Encoded");
  });

  it("quotes no frame count when nothing was written", () => {
    // `frames` on a kept result is the stored video's count on one upstream branch and the
    // discarded encode's on the other, and the response does not say which. Quoting it
    // states a number for a video the caller did not get.
    const summary = videoResultSummary(
      result({ regenerated: false, frames: 72, frames_expected: 60 })
    );

    expect(summary).not.toContain("72");
    expect(summary).not.toContain("60");
  });
});
