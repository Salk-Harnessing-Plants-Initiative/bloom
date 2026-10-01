"use client";

/**
 * Small pulsing green dot + "Live" label, shown next to a widget heading.
 *
 * With no `state` it is purely decorative, as the recent-phenotype widgets use
 * it: they don't read their channel's status, so if the channel drops the
 * indicator still pulses, and a missed INSERT surfaces on the next
 * server render.
 *
 * The pipeline-run views pass the channel's real state (add-cyl-pipeline-ui):
 * `connecting` until the first SUBSCRIBED, `live`, or `offline` after an
 * error, timeout or close, with a manual refresh.
 */

export type LiveState = "connecting" | "live" | "offline";

export function LiveIndicator({ state, onRefresh }: { state?: LiveState; onRefresh?: () => void } = {}) {
  const dot = (
    <span className="relative inline-flex h-2 w-2">
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-green-600" />
    </span>
  );

  if (state === undefined) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-stone-600">
        {dot}
        Live
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-stone-600">
      <span role="status" aria-live="polite" className="inline-flex items-center gap-1.5">
        {state === "live" ? (
          dot
        ) : (
          <span
            className={`inline-flex h-2 w-2 rounded-full ${state === "offline" ? "bg-red-500" : "bg-stone-400"}`}
            aria-hidden="true"
          />
        )}
        {state === "live" ? "Live" : state === "connecting" ? "Connecting…" : "Offline: live updates stopped"}
      </span>
      {state === "offline" && onRefresh && (
        <button type="button" onClick={onRefresh} className="text-lime-700 underline hover:no-underline">
          Refresh
        </button>
      )}
    </span>
  );
}
