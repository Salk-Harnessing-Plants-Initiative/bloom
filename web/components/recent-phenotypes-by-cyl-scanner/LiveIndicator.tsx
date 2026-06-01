"use client";

/**
 * Small pulsing green dot + "Live" label, shown next to the widget heading
 * to signal that the underlying Realtime subscription is wired up. Purely
 * decorative — there's no current "connected/disconnected" hook reading
 * the actual channel state because Supabase's @supabase/ssr browser client
 * doesn't expose one in a stable API. If the channel ever silently drops,
 * the indicator will still pulse, but a missed INSERT will surface on the
 * next user-triggered navigation when the server re-renders.
 */

export function LiveIndicator() {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-stone-600">
      <span className="relative inline-flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-green-600" />
      </span>
      Live
    </span>
  );
}
