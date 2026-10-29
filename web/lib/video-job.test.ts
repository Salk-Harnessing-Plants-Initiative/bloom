import { describe, expect, it } from "vitest";
import {
  PLATE_FAILURES,
  SCAN_FAILURES,
  VIDEO_JOB_STAGES,
  countedFrames,
  isActiveJob,
  jobFailureMessage,
  queuedNote,
  renderingNote,
  toVideoJob,
  type VideoJob,
} from "./video-job";

function job(overrides: Partial<VideoJob> = {}): VideoJob {
  return {
    id: "1a2b3c4d-0000-0000-0000-000000000000",
    status: "rendering",
    stage: null,
    frames_done: null,
    frames_total: null,
    error_code: null,
    queued_ahead: null,
    created_at: "2026-10-29T10:00:00Z",
    updated_at: "2026-10-29T10:00:00Z",
    ...overrides,
  };
}

describe("VIDEO_JOB_STAGES", () => {
  it("names the stages exactly as the service writes them", () => {
    // A rename on either side must fail here, not fall through to the wrong sentence.
    expect(VIDEO_JOB_STAGES).toEqual({
      downloading: "downloading",
      encoding: "encoding",
      uploading: "uploading",
    });
  });
});

describe("toVideoJob", () => {
  it("picks the known fields only", () => {
    const picked = toVideoJob({ ...job(), error: "kong:8000", extra: 1 });
    expect(picked).toEqual(job());
  });

  it("refuses something that is not a job", () => {
    expect(toVideoJob(null)).toBeNull();
    expect(toVideoJob({ id: 1, status: "queued" })).toBeNull();
    expect(toVideoJob({ id: "x", status: "done" })).toBeNull();
  });
});

describe("isActiveJob", () => {
  it("is true while queued or rendering only", () => {
    expect(isActiveJob(job({ status: "queued" }))).toBe(true);
    expect(isActiveJob(job({ status: "rendering" }))).toBe(true);
    expect(isActiveJob(job({ status: "rendered" }))).toBe(false);
    expect(isActiveJob(job({ status: "kept" }))).toBe(false);
    expect(isActiveJob(job({ status: "failed" }))).toBe(false);
    expect(isActiveJob(null)).toBe(false);
  });
});

describe("queuedNote", () => {
  it("says how many are ahead, or that it is next", () => {
    expect(queuedNote(job({ status: "queued", queued_ahead: 2 }))).toBe("Queued — 2 ahead");
    expect(queuedNote(job({ status: "queued", queued_ahead: 0 }))).toBe("Queued — next up");
    expect(queuedNote(job({ status: "queued", queued_ahead: null }))).toBe("Queued — next up");
  });
});

describe("renderingNote and countedFrames", () => {
  it("counts frames while downloading", () => {
    const j = job({ stage: "downloading", frames_done: 4, frames_total: 9 });
    expect(countedFrames(j)).toEqual({ done: 4, total: 9 });
    expect(renderingNote(j)).toBe("Downloading frame 4 of 9");
  });

  it("never counts past the total", () => {
    expect(countedFrames(job({ stage: "downloading", frames_done: 12, frames_total: 9 }))).toEqual({
      done: 9,
      total: 9,
    });
  });

  it("does not count with no total, or outside downloading", () => {
    expect(countedFrames(job({ stage: "downloading", frames_total: 0 }))).toBeNull();
    expect(countedFrames(job({ stage: "encoding", frames_done: 9, frames_total: 9 }))).toBeNull();
    expect(renderingNote(job({ stage: "encoding" }))).toBe("Encoding the video…");
    expect(renderingNote(job({ stage: "uploading" }))).toBe("Saving the video…");
    expect(renderingNote(job())).toBe("Starting…");
  });
});

describe("jobFailureMessage", () => {
  it("names the failure and the job", () => {
    const message = jobFailureMessage(
      job({ status: "failed", error_code: "no_frames" }),
      PLATE_FAILURES
    );
    expect(message).toBe(`${PLATE_FAILURES.no_frames} (job 1a2b3c4d)`);
  });

  it("falls back to a generic sentence for a code it does not know", () => {
    const message = jobFailureMessage(job({ status: "failed", error_code: "novel" }), SCAN_FAILURES);
    expect(message).toBe(`${SCAN_FAILURES.unexpected} (job 1a2b3c4d)`);
  });

  it("does not read inherited properties as sentences", () => {
    const message = jobFailureMessage(
      job({ status: "failed", error_code: "constructor" }),
      SCAN_FAILURES
    );
    expect(message).toBe(`${SCAN_FAILURES.unexpected} (job 1a2b3c4d)`);
  });

  it("covers every code the service can write", () => {
    const plate = [
      "no_frames", "too_large", "unusable_plate", "storage_unavailable",
      "database_unavailable", "frame_too_large", "frame_depth_unsupported",
      "frame_size_mismatch", "frame_unreadable", "not_stored", "not_recorded",
      "encode_failed", "plate_mismatch", "unexpected", "redelivered", "stalled",
    ];
    const scan = [
      "no_images", "scan_not_in_experiment", "storage_unavailable",
      "no_frames_encoded", "encode_failed", "not_signed", "unexpected", "redelivered", "stalled",
    ];
    for (const code of plate) expect(PLATE_FAILURES[code], code).toBeTruthy();
    for (const code of scan) expect(SCAN_FAILURES[code], code).toBeTruthy();
  });
});
