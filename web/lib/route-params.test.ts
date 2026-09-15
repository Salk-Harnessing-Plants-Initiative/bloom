import { describe, it, expect } from "vitest";
import { parseId } from "./route-params";

describe("parseId", () => {
  it("accepts a positive integer", () => {
    expect(parseId("1")).toBe(1);
    expect(parseId("4207")).toBe(4207);
  });

  it("rejects path traversal, so the upstream URL can't be retargeted", () => {
    expect(parseId("1/../../health")).toBeNull();
    expect(parseId("../1")).toBeNull();
    expect(parseId("1%2F..")).toBeNull();
  });

  it("rejects zero and negatives", () => {
    expect(parseId("0")).toBeNull();
    expect(parseId("-1")).toBeNull();
  });

  it("rejects non-integers and non-digits", () => {
    expect(parseId("1.5")).toBeNull();
    expect(parseId("1e3")).toBeNull();
    expect(parseId("abc")).toBeNull();
    expect(parseId("")).toBeNull();
    expect(parseId(" 1")).toBeNull();
  });

  it("rejects missing values", () => {
    expect(parseId(undefined)).toBeNull();
    expect(parseId(null)).toBeNull();
  });

  it("rejects an integer too large to represent exactly", () => {
    expect(parseId("9".repeat(30))).toBeNull();
  });
});
