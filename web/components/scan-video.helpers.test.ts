import { describe, it, expect } from "vitest";
import { videoErrorMessage, videoResultSummary } from "./scan-video.helpers";
import type { VideoJob } from "@/lib/video-job";

function job(overrides: Partial<VideoJob> = {}): VideoJob {
  return {
    id: "1a2b3c4d-0000-0000-0000-000000000000",
    status: "rendered",
    stage: null,
    frames_done: 72,
    frames_total: 72,
    error_code: null,
    queued_ahead: null,
    created_at: "2026-10-29T10:00:00Z",
    updated_at: "2026-10-29T10:00:00Z",
    ...overrides,
  };
}

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

  it("says a 503 changed nothing, rather than calling it a failure", () => {
    // Upstream answers 503 when it could not confirm what is already stored, and declines
    // rather than risk replacing it. Its own wording is suppressed by the proxy — a sibling
    // 503 carries the internal gateway URL — so this is where the distinction survives.
    const message = videoErrorMessage(503);
    expect(message).toContain("nothing was changed");
    expect(message).not.toBe("Video generation failed. Try again in a moment.");
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
    expect(videoResultSummary(job())).toBe("Encoded 72 frames.");
  });

  it("reports frames that could not be read", () => {
    expect(videoResultSummary(job({ frames_done: 70, frames_total: 72 }))).toBe(
      "Encoded 70 of 72 frames (2 could not be read)."
    );
  });

  it("says the stored video was kept, quoting no frame count", () => {
    const summary = videoResultSummary(job({ status: "kept", frames_done: 72, frames_total: 60 }));

    expect(summary).toContain("already has a video");
    expect(summary).not.toContain("72");
    expect(summary).not.toContain("60");
  });

  it("claims no count it was not given", () => {
    expect(videoResultSummary(job({ frames_done: null, frames_total: null }))).toBe(
      "The video is ready."
    );
  });
});
