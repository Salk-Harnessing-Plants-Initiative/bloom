/**
 * Microsecond-exact timestamp ordering. `Date.parse` on microsecond strings is
 * implementation-defined (design D2), and the list's keyset cursor compares
 * the raw `created_at` values Realtime and PostgREST send.
 */

import { describe, expect, it } from "vitest";
import postgrest from "./__fixtures__/postgrest-timestamps.json";
import runs from "./__fixtures__/realtime-runs.json";
import { compareTimestamps, parseTimestampMicros } from "./timestamps";

describe("compareTimestamps", () => {
  it("orders by microseconds", () => {
    expect(compareTimestamps("2026-09-28T10:00:10.123456+00:00", "2026-09-28T10:00:10.1234+00:00")).toBeGreaterThan(0);
    expect(compareTimestamps("2026-09-28T10:00:10.1234+00:00", "2026-09-28T10:00:10.123456+00:00")).toBeLessThan(0);
  });

  it("puts a value with no fraction before .5 of the same second", () => {
    expect(compareTimestamps("2026-09-28T10:00:10+00:00", "2026-09-28T10:00:10.5+00:00")).toBeLessThan(0);
  });

  it("treats +00:00 and Z as the same instant", () => {
    expect(compareTimestamps("2026-09-28T10:00:10.1234+00:00", "2026-09-28T10:00:10.123400Z")).toBe(0);
  });

  it("applies non-UTC offsets", () => {
    expect(compareTimestamps("2026-09-28T12:00:10.5+02:00", "2026-09-28T10:00:10.5Z")).toBe(0);
    expect(compareTimestamps("2026-09-28T05:30:00-04:30", "2026-09-28T10:00:00+00:00")).toBe(0);
  });

  it("sorts an unparsable value as newest", () => {
    expect(compareTimestamps("not a time", "2099-01-01T00:00:00+00:00")).toBeGreaterThan(0);
    expect(compareTimestamps("2099-01-01T00:00:00+00:00", null)).toBeLessThan(0);
    expect(compareTimestamps(undefined, "garbage")).toBe(0);
  });

  it("rejects out-of-range fields and more than six fraction digits", () => {
    expect(parseTimestampMicros("2026-13-01T00:00:00+00:00")).toBeNull();
    expect(parseTimestampMicros("2026-02-30T00:00:00+00:00")).toBeNull();
    expect(parseTimestampMicros("2026-09-28T24:00:00+00:00")).toBeNull();
    expect(parseTimestampMicros("2026-09-28T10:00:10.1234567+00:00")).toBeNull();
    expect(parseTimestampMicros("2026-09-28T10:00:10")).toBeNull();
  });

  it("orders the captured PostgREST renderings (.1234 is .123400; no fraction is :10)", () => {
    const [p1234, whole, half] = postgrest.rows.map((r) => r.created_at);
    expect(p1234).toBe("2026-09-28T10:00:10.1234+00:00");
    expect(whole).toBe("2026-09-28T10:00:10+00:00");
    expect(half).toBe("2026-09-28T10:00:10.5+00:00");
    expect(parseTimestampMicros(p1234)! - parseTimestampMicros(whole)!).toBe(BigInt(123_400));
    expect(parseTimestampMicros(half)! - parseTimestampMicros(whole)!).toBe(BigInt(500_000));
    expect([half, p1234, whole].sort(compareTimestamps)).toEqual([whole, p1234, half]);
  });

  it("parses every timestamp in the captured Realtime payloads", () => {
    const values = runs.events.flatMap((e) => {
      const row = e.payload.new as Record<string, unknown>;
      return ["created_at", "submitted_at"].map((k) => row[k]).filter((v): v is string => typeof v === "string");
    });
    expect(values.length).toBeGreaterThan(0);
    for (const v of values) expect(parseTimestampMicros(v)).not.toBeNull();
  });
});
