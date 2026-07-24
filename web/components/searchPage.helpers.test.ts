/**
 * Unit tests for the pure plant-search helpers. The React component is exercised
 * elsewhere; these lock the parsing / href / escaping logic that drives batch
 * detection and the correct-page navigation.
 */

import { describe, it, expect } from "vitest";
import { parseQuery, fieldHrefs, escapeLike } from "./searchPage.helpers";

describe("parseQuery", () => {
  it("treats a plain term as free text (no list)", () => {
    expect(parseQuery("oak")).toEqual({ list: null, text: "oak" });
  });

  it("trims surrounding whitespace", () => {
    expect(parseQuery("  oak  ")).toEqual({ list: null, text: "oak" });
  });

  it("treats an empty / whitespace-only input as empty free text", () => {
    expect(parseQuery("   ")).toEqual({ list: null, text: "" });
  });

  it("splits a comma-separated batch into a list", () => {
    expect(parseQuery("A1, B2, C3").list).toEqual(["A1", "B2", "C3"]);
  });

  it("splits a newline-separated batch into a list", () => {
    expect(parseQuery("A1\nB2\nC3").list).toEqual(["A1", "B2", "C3"]);
  });

  it("drops empty entries from a ragged batch (trailing commas, extra spaces)", () => {
    expect(parseQuery("A1, ,B2,\n,C3,").list).toEqual(["A1", "B2", "C3"]);
  });

  it("does not treat a single term (no comma/newline) as a list", () => {
    expect(parseQuery("A1").list).toBeNull();
  });
});

describe("fieldHrefs", () => {
  it("builds the full nested hrefs when every id is present", () => {
    const h = fieldHrefs({
      species_id: 1,
      experiment_id: 2,
      wave_id: 3,
      accession_id: 4,
    });
    expect(h).toEqual({
      species: "/app/phenotypes/1",
      experiment: "/app/phenotypes/1/2",
      accession: "/app/phenotypes/1/2/3/4",
    });
  });

  it("returns null for deeper links when an ancestor id is missing", () => {
    const h = fieldHrefs({ species_id: 1, experiment_id: null, wave_id: 3, accession_id: 4 });
    expect(h).toEqual({
      species: "/app/phenotypes/1",
      experiment: null,
      accession: null,
    });
  });

  it("returns all null when there is no species id", () => {
    expect(fieldHrefs({ species_id: null })).toEqual({
      species: null,
      experiment: null,
      accession: null,
    });
  });

  it("keeps a 0 id (only null/undefined are missing)", () => {
    expect(fieldHrefs({ species_id: 0 }).species).toBe("/app/phenotypes/0");
  });
});

describe("escapeLike", () => {
  it("escapes ILIKE wildcards so they match literally", () => {
    expect(escapeLike("a_b%c")).toBe("a\\_b\\%c");
  });

  it("escapes a literal backslash", () => {
    expect(escapeLike("a\\b")).toBe("a\\\\b");
  });

  it("leaves ordinary text untouched", () => {
    expect(escapeLike("Red Oak")).toBe("Red Oak");
  });
});
