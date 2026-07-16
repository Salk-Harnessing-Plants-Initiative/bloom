import { describe, it, expect } from "vitest";
import { toCsv, downloadCsv } from "./csv";

describe("toCsv", () => {
  it("joins header and rows with CRLF and cells with commas", () => {
    expect(toCsv(["a", "b"], [["1", "2"], ["3", "4"]])).toBe("a,b\r\n1,2\r\n3,4");
  });

  it("quotes a field containing a comma", () => {
    expect(toCsv(["x"], [["a,b"]])).toBe('x\r\n"a,b"');
  });

  it("doubles embedded quotes and wraps the field in quotes", () => {
    expect(toCsv(["x"], [['he said "hi"']])).toBe('x\r\n"he said ""hi"""');
  });

  it("quotes fields containing newlines or carriage returns", () => {
    expect(toCsv(["x"], [["line1\nline2"]])).toBe('x\r\n"line1\nline2"');
    expect(toCsv(["x"], [["a\rb"]])).toBe('x\r\n"a\rb"');
  });

  it("renders null and undefined as empty strings", () => {
    expect(toCsv(["x", "y"], [[null, undefined]])).toBe("x,y\r\n,");
  });

  it("stringifies numbers and booleans", () => {
    expect(toCsv(["n", "b"], [[42, true]])).toBe("n,b\r\n42,true");
  });

  it("leaves plain values unquoted", () => {
    expect(toCsv(["gene"], [["AT1G01010"]])).toBe("gene\r\nAT1G01010");
  });

  it("handles an empty row set (headers only)", () => {
    expect(toCsv(["a", "b"], [])).toBe("a,b");
  });
});

describe("downloadCsv", () => {
  it("is a no-op on the server (no document) and does not throw", () => {
    expect(() => downloadCsv("f.csv", "a,b")).not.toThrow();
  });
});
