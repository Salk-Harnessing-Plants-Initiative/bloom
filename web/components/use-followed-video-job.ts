"use client";

import { useEffect, useRef, useState } from "react";
import { toVideoJob, type VideoJob } from "@/lib/video-job";
import { useVideoJobPoll, type PollLost } from "./use-video-job-poll";

/**
 * Follow one video job to its end, for either button.
 *
 * The poll names the job by id, so a newer job on the same item can neither end
 * the wait nor hide this one's outcome. Following stops when the item changes.
 */
export function useFollowedVideoJob({
  pollUrl,
  waiting,
  onSettled,
  onFailed,
  onLost,
}: {
  /** The item's poll route, with or without a query of its own. */
  pollUrl: string;
  /** Whether the button is waiting; the poll runs only while it is and a job is followed. */
  waiting: boolean;
  /** The job ended rendered or kept; `body` is the poll answer it came in. */
  onSettled: (job: VideoJob, body: unknown) => void;
  /** The job failed; `body` may still carry a stored video's link. */
  onFailed: (job: VideoJob, body: unknown) => void;
  onLost: (why: PollLost) => void;
}) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<VideoJob | null>(null);

  // A different plate or scan: whatever was followed belongs to the old one.
  const shown = useRef(pollUrl);
  useEffect(() => {
    if (shown.current === pollUrl) return;
    shown.current = pollUrl;
    setJobId(null);
    setJob(null);
  }, [pollUrl]);

  const url = jobId
    ? `${pollUrl}${pollUrl.includes("?") ? "&" : "?"}${new URLSearchParams({ job_id: jobId })}`
    : pollUrl;

  useVideoJobPoll(
    url,
    waiting && jobId !== null,
    (body) => {
      const current = toVideoJob((body as { job?: unknown } | null)?.job);
      // Job rows are never deleted: no job here means it could not be read.
      if (!current || current.id !== jobId) return false;
      setJob(current);
      if (current.status === "failed") onFailed(current, body);
      else if (current.status === "rendered" || current.status === "kept") onSettled(current, body);
      return true;
    },
    onLost
  );

  return {
    job,
    /** Start following a job: one just queued (by id), or one found running (the job itself). */
    follow(next: string | VideoJob) {
      if (typeof next === "string") {
        setJobId(next);
        setJob(null);
      } else {
        setJobId(next.id);
        setJob(next);
      }
    },
    /** Forget the job, as when a new request starts. */
    clear() {
      setJob(null);
    },
  };
}
