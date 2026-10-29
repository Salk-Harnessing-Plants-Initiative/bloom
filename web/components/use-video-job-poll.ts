"use client";

import { useEffect, useRef } from "react";
import {
  VIDEO_JOB_POLL_INTERVAL_MS,
  VIDEO_JOB_POLL_TIMEOUT_MS,
} from "@/lib/video-job";

/** Ask `url` every interval while `active`, handing each readable answer on.
 *  A failed or unreadable ask says nothing about the job, so it is skipped. */
export function useVideoJobPoll(
  url: string,
  active: boolean,
  onAnswer: (body: unknown) => void
) {
  const latest = useRef(onAnswer);
  useEffect(() => {
    latest.current = onAnswer;
  });

  useEffect(() => {
    if (!active) return;
    let live = true;
    let inFlight = false;

    const timer = setInterval(async () => {
      // A slow answer must not stack up behind the next tick.
      if (inFlight) return;
      inFlight = true;
      try {
        const res = await fetch(url, {
          signal: AbortSignal.timeout(VIDEO_JOB_POLL_TIMEOUT_MS),
        });
        if (!res.ok) return;
        const body = await res.json();
        if (live) latest.current(body);
      } catch {
        // A blip mid-render is not an answer — keep asking.
      } finally {
        inFlight = false;
      }
    }, VIDEO_JOB_POLL_INTERVAL_MS);

    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [url, active]);
}
