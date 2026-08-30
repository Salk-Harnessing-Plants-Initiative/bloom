import { describe, expect, it } from "vitest";
import { withShortTimeZoneName } from "./format-times";

describe("withShortTimeZoneName", () => {
  it("preserves requested fields and adds a short time-zone name", () => {
    const options = {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    } satisfies Intl.DateTimeFormatOptions;

    expect(withShortTimeZoneName(options)).toEqual({
      ...options,
      timeZoneName: "short",
    });
    expect(options).not.toHaveProperty("timeZoneName");
  });

  it("does not force a timezone and exposes a timezone-name part", () => {
    const options = withShortTimeZoneName({
      year: "numeric",
      month: "short",
      day: "numeric",
    });

    expect(options).not.toHaveProperty("timeZone");

    const parts = new Intl.DateTimeFormat("en-US", options).formatToParts(
      new Date("2026-05-28T12:00:00Z"),
    );
    expect(parts.some((part) => part.type === "timeZoneName")).toBe(true);
  });
});
