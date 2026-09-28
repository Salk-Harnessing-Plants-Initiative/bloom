/**
 * When a live view refetches its snapshot (design D2).
 *
 * Every `SUBSCRIBED`, including the first, is a resync: the first closes the
 * gap between the server-rendered snapshot and the subscription, and later
 * ones cover a reconnect. It is leading plus trailing: a refetch opens a
 * window, and any further `SUBSCRIBED` inside it produces exactly one more
 * refetch when the window ends. A leading-only throttle could drop a real
 * reconnect. Nothing here runs on a timer except that trailing refetch, so a
 * quiet view never fetches.
 */

export const RESYNC_WINDOW_MS = 2000;

export type ConnectionState = "connecting" | "live" | "offline";

const OFFLINE = new Set(["CHANNEL_ERROR", "TIMED_OUT", "CLOSED"]);

/** Connecting until the first SUBSCRIBED, then live; offline after an error, timeout or close. */
export function nextConnectionState(prev: ConnectionState, status: string): ConnectionState {
  if (status === "SUBSCRIBED") return "live";
  if (OFFLINE.has(status)) return "offline";
  return prev;
}

export interface ResyncScheduler {
  onStatus(status: string): void;
  dispose(): void;
}

export function createResyncScheduler(refetch: () => void, windowMs = RESYNC_WINDOW_MS): ResyncScheduler {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending = false;
  let disposed = false;

  const run = () => {
    pending = false;
    refetch();
    timer = setTimeout(() => {
      timer = null;
      if (pending && !disposed) run();
    }, windowMs);
  };

  return {
    onStatus(status) {
      if (disposed || status !== "SUBSCRIBED") return;
      if (timer === null) run();
      else pending = true;
    },
    dispose() {
      disposed = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}
