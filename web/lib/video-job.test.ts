import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
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
    expect(renderingNote(j)).toBe("Downloaded 4 of 9 frames");
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

  it("covers every code the service can write, read from the service itself", () => {
    const repo = (path: string) => fileURLToPath(new URL(`../../${path}`, import.meta.url));
    const worker = (file: string) => readFileSync(repo(`services/workflows/${file}`), "utf8");
    const migration = readdirSync(repo("supabase/migrations")).find((f) =>
      f.endsWith("_create_video_generation_queues.sql")
    )!;
    const sql = readFileSync(repo(`supabase/migrations/${migration}`), "utf8");
    const literals = (text: string) => [...text.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    const between = (text: string, from: string, to: string) => {
      const start = text.indexOf(from);
      expect(start, `${from} not found`).toBeGreaterThanOrEqual(0);
      return text.slice(start, text.indexOf(to, start + from.length));
    };

    // The queue's own codes: what the loop and the claim wrapper write.
    const shared = [
      /^UNEXPECTED = "([a-z_]+)"/m.exec(worker("video_queue.py"))![1],
      /^STALLED = "([a-z_]+)"/m.exec(worker("video_worker_loop.py"))![1],
      /error_code = '([a-z_]+)'/.exec(sql)![1],
    ];
    const plate = [
      ...literals(between(worker("plate_video_worker.py"), "FAILURES = (", "\n)\n")),
      ...literals(between(worker("plate_request.py"), "REFUSAL_STATUS = {", "}")),
      ...shared,
    ];
    const scan = [...literals(between(worker("cyl_video_worker.py"), "def code_for_refusal", "\ndef ")), ...shared];

    expect(plate).toEqual(expect.arrayContaining(["frame_too_large", "no_frames", "redelivered", "stalled"]));
    expect(scan).toEqual(expect.arrayContaining(["database_unavailable", "no_images", "unexpected"]));
    for (const code of plate) expect(PLATE_FAILURES[code], code).toBeTruthy();
    for (const code of scan) expect(SCAN_FAILURES[code], code).toBeTruthy();
  });
});
