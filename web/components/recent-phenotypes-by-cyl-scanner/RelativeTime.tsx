"use client";

/**
 * Shared client-only timestamp label for the recent-phenotypes cards.
 *
 * Renders empty server-side and on the client's initial paint, then fills
 * in the local-TZ formatted string after mount via useEffect. This avoids
 * the hydration mismatch that otherwise fires because the server's UTC
 * timezone and the browser's local timezone produce different strings for
 * the same instant.
 *
 * The initial empty render is a brief, almost invisible flash; the time
 * is a low-priority informational element, so a flash is the right
 * trade-off vs. SSRing a UTC timestamp that the user then sees switch to
 * their local time.
 */

import { useEffect, useState } from "react";
import { formatRelativeAndAbsolute } from "./format-times";

interface RelativeTimeProps {
  iso: string;
}

export function RelativeTime({ iso }: RelativeTimeProps) {
  const [text, setText] = useState<string>("");

  useEffect(() => {
    setText(formatRelativeAndAbsolute(iso));
  }, [iso]);

  // Always render the <p> wrapper so the surrounding flex layout doesn't
  // shift when the text appears after mount. The element takes minimal
  // height while empty.
  return <p className="text-xs text-stone-500">{text}</p>;
}
