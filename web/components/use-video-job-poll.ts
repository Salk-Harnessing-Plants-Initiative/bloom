"use client";

import { useEffect, useRef } from "react";
import {
  VIDEO_JOB_MAX_FAILED_POLLS,
  VIDEO_JOB_POLL_INTERVAL_MS,
  VIDEO_JOB_POLL_TIMEOUT_MS,
} from "@/lib/video-job";

/** Why the poll gave up: the session ended, or the answers stopped coming. */
export type PollLost = "signed_out" | "unreachable";

/** Ask `url` every interval while `active`, handing each readable answer on.
 *  One failed ask says nothing about the job, so it is retried; a run of them,
 *  or an expired session, ends the poll through `onLost`. */
export function useVideoJobPoll(
  url: string,
  active: boolean,
  onAnswer: (body: unknown) => void,
  onLost: (why: PollLost) => void
) {
  const latest = useRef({ onAnswer, onLost });
  useEffect(() => {
    latest.current = { onAnswer, onLost };
  });

  useEffect(() => {
    if (!active) return;
    let live = true;
    let inFlight = false;
    let failures = 0;

    const stop = (why: PollLost) => {
      clearInterval(timer);
      if (live) latest.current.onLost(why);
    };

    const timer = setInterval(async () => {
      // A slow answer must not stack up behind the next tick.
      if (inFlight) return;
      inFlight = true;
      try {
        const res = await fetch(url, {
          signal: AbortSignal.timeout(VIDEO_JOB_POLL_TIMEOUT_MS),
        });
        if (res.status === 401) return stop("signed_out");
        if (!res.ok) throw new Error(`poll answered ${res.status}`);
        const body = await res.json();
        failures = 0;
        if (live) latest.current.onAnswer(body);
      } catch {
        // A blip mid-render is not an answer; a long run of them is.
        failures += 1;
        if (failures >= VIDEO_JOB_MAX_FAILED_POLLS) stop("unreachable");
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
