import { describe, expect, it } from "vitest";

import { parseMarkers } from "./cluster-markers";

describe("parseMarkers", () => {
  it("keeps a marker's symbol and the atlas its label came from", () => {
    const parsed = parseMarkers({
      top: [{ gene: "AT5G09530", symbol: "PELPK1", source: "shahan", log2fc: 2.4, q: 2e-8, pct_1: 0.9, pct_2: 0.1 }],
      n_significant: 26,
    });
    expect(parsed?.top[0]).toEqual({
      gene: "AT5G09530", symbol: "PELPK1", source: "shahan", log2fc: 2.4, q: 2e-8, pct_1: 0.9, pct_2: 0.1,
    });
    expect(parsed?.n_significant).toBe(26);
  });

  it("reads a blank or missing symbol and atlas as none", () => {
    const parsed = parseMarkers({
      top: [
        { gene: "A", symbol: "", log2fc: 1, q: 0.01, pct_1: 0.5, pct_2: 0.2 },
        { gene: "B", log2fc: 1, q: 0.01, pct_1: 0.5, pct_2: 0.2 },
      ],
      n_significant: 2,
    });
    expect(parsed?.top.map((m) => [m.symbol, m.source])).toEqual([[null, null], [null, null]]);
  });

  it("returns null for anything not shaped like markers", () => {
    expect(parseMarkers(null)).toBeNull();
    expect(parseMarkers([])).toBeNull();
    expect(parseMarkers({ top: "x" })).toBeNull();
  });
});
