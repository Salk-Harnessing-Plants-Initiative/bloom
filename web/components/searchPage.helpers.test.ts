/**
 * Unit tests for the pure plant-search helpers. The React component is exercised
 * elsewhere; these lock the parsing / href / escaping logic that drives batch
 * detection and the correct-page navigation.
 */

import { describe, it, expect } from "vitest";
import {
  parseQuery,
  fieldHrefs,
  escapeLike,
  quoteOrValue,
  ilikeAnyFilter,
  navigateHref,
  batchNotice,
  MAX_BATCH,
} from "./searchPage.helpers";

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

  it("does not treat a single term as a list", () => {
    expect(parseQuery("A1").list).toBeNull();
  });

  it("splits a space-separated batch into a list", () => {
    // The case that silently did nothing before: pasting barcodes separated by
    // spaces fell through to a free-text search for the whole string.
    expect(parseQuery("SOY-W1-001 SOY-W1-002").list).toEqual([
      "SOY-W1-001",
      "SOY-W1-002",
    ]);
  });

  it("splits a tab-separated batch into a list", () => {
    expect(parseQuery("A1\tB2\tC3").list).toEqual(["A1", "B2", "C3"]);
  });

  it("splits on a mix of separators", () => {
    expect(parseQuery("A1, B2\tC3\nD4").list).toEqual(["A1", "B2", "C3", "D4"]);
  });

  it("treats a multi-word term as a list, not one term", () => {
    // Accepted trade-off of splitting on spaces: a multi-word species name
    // becomes a barcode list and finds nothing — the UI says "No matches found."
    expect(parseQuery("Red Oak").list).toEqual(["Red", "Oak"]);
  });

  it("still lists a single entry when a comma or newline is present", () => {
    expect(parseQuery("A1,").list).toEqual(["A1"]);
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

describe("quoteOrValue", () => {
  it("wraps the value in double quotes", () => {
    expect(quoteOrValue("%oak%")).toBe('"%oak%"');
  });

  it("escapes backslashes so an escaped wildcard survives the .or() grammar", () => {
    expect(quoteOrValue("%a\\_b%")).toBe('"%a\\\\_b%"');
  });

  it("escapes embedded double quotes so the value can't close its own quoting", () => {
    expect(quoteOrValue('a"b')).toBe('"a\\"b"');
  });

  it("carries the grammar's reserved characters literally", () => {
    expect(quoteOrValue("%Oak (red), sp.%")).toBe('"%Oak (red), sp.%"');
  });
});

describe("ilikeAnyFilter", () => {
  it("builds one ilike clause per column, comma-joined", () => {
    expect(ilikeAnyFilter(["qr_code", "accession_name"], "oak")).toBe(
      'qr_code.ilike."%oak%",accession_name.ilike."%oak%"',
    );
  });

  it("escapes ILIKE wildcards in the term so they match literally", () => {
    // Verified against a live PostgREST: the doubled backslash reaches SQL as
    // \_ and matches a literal underscore, not any-single-character.
    expect(ilikeAnyFilter(["qr_code"], "SOY_1")).toBe(
      'qr_code.ilike."%SOY\\\\_1%"',
    );
  });

  it("keeps parentheses in the term from breaking the .or() grammar", () => {
    expect(ilikeAnyFilter(["qr_code"], "Oak (red)")).toBe(
      'qr_code.ilike."%Oak (red)%"',
    );
  });
});

describe("navigateHref", () => {
  it("maps a species answer to the species page", () => {
    expect(navigateHref({ target: "species", species_id: 7 })).toBe("/app/phenotypes/7");
  });

  it("maps a plant answer to the accession-in-wave page", () => {
    expect(
      navigateHref({
        target: "plant",
        species_id: 1,
        experiment_id: 2,
        wave_id: 3,
        accession_id: 4,
      }),
    ).toBe("/app/phenotypes/1/2/3/4");
  });

  it("returns null for an explicit 'none' answer (show the dropdown)", () => {
    expect(navigateHref({ target: "none" })).toBeNull();
  });

  it("returns null when the RPC returned nothing at all", () => {
    expect(navigateHref(null)).toBeNull();
    expect(navigateHref(undefined)).toBeNull();
  });

  it("returns null for a plant answer missing an id in the href", () => {
    // Shouldn't happen — the RPC filters these out — but a malformed answer
    // must not produce a broken route.
    expect(
      navigateHref({ target: "plant", species_id: 1, experiment_id: 2 } as any),
    ).toBeNull();
  });
});

describe("batchNotice", () => {
  it("is silent when neither the list nor the rows hit a cap", () => {
    expect(batchNotice(10, 10)).toBe("");
  });

  it("warns when the pasted list was trimmed before querying", () => {
    expect(batchNotice(MAX_BATCH + 1, 5)).toContain("Too many barcodes");
  });

  it("warns when the rows came back at the cap even though the list did not", () => {
    // Barcodes are unique per wave only, so <=MAX_BATCH barcodes can match more
    // than MAX_BATCH rows — the case a list-length-only check missed.
    expect(batchNotice(10, MAX_BATCH)).toContain(`first ${MAX_BATCH}`);
  });

  it("prefers the list-trimmed message when both caps are hit", () => {
    expect(batchNotice(MAX_BATCH + 1, MAX_BATCH)).toContain("Too many barcodes");
  });

  it("is silent one row below the cap", () => {
    expect(batchNotice(10, MAX_BATCH - 1)).toBe("");
  });
});
