/**
 * Unit tests for the pure plant-search helpers. The React component is exercised
 * elsewhere; these lock the parsing / href / escaping logic that drives batch
 * detection and the correct-page navigation.
 */

import { describe, it, expect, vi } from "vitest";
import {
  parseQuery,
  fieldHrefs,
  escapeLike,
  quoteOrValue,
  ilikeAnyFilter,
  singleDestination,
  resolveJumpTarget,
  batchNotice,
  MAX_BATCH,
  MAX_JUMP_MATCHES,
} from "./searchPage.helpers";

// A jump-candidate row: only the id fields fieldHrefs reads.
const row = (species: number, experiment: number, wave: number, accession: number) => ({
  species_id: species,
  experiment_id: experiment,
  wave_id: wave,
  accession_id: accession,
});

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

describe("singleDestination", () => {
  it("returns the shared destination when every row agrees", () => {
    expect(singleDestination([row(1, 2, 3, 4), row(1, 2, 3, 4)])).toBe(
      "/app/phenotypes/1/2/3/4",
    );
  });

  it("returns null when rows span more than one destination", () => {
    expect(singleDestination([row(1, 2, 3, 4), row(1, 2, 9, 4)])).toBeNull();
  });

  it("returns null for an empty match set", () => {
    expect(singleDestination([])).toBeNull();
  });

  it("returns null for a null match set (a failed query)", () => {
    expect(singleDestination(null)).toBeNull();
  });

  it("returns null when the set is truncated, even if the rows seen agree", () => {
    const rows = Array.from({ length: 4 }, () => row(1, 2, 3, 4));
    expect(singleDestination(rows, 3)).toBeNull();
  });

  it("still resolves at exactly the cap (truncation is > max, not >=)", () => {
    const rows = Array.from({ length: 3 }, () => row(1, 2, 3, 4));
    expect(singleDestination(rows, 3)).toBe("/app/phenotypes/1/2/3/4");
  });

  it("ignores rows with no derivable destination", () => {
    expect(singleDestination([row(1, 2, 3, 4), { species_id: null }])).toBe(
      "/app/phenotypes/1/2/3/4",
    );
  });

  it("defaults its cap to MAX_JUMP_MATCHES", () => {
    const rows = Array.from({ length: MAX_JUMP_MATCHES + 1 }, () => row(1, 2, 3, 4));
    expect(singleDestination(rows)).toBeNull();
  });
});

describe("resolveJumpTarget", () => {
  const fetchers = (species: any[], barcode: any[], accession: any[]) => ({
    species: vi.fn(async () => species),
    barcode: vi.fn(async () => barcode),
    accession: vi.fn(async () => accession),
  });

  it("prefers an exact species match over barcode and accession", async () => {
    const f = fetchers([{ id: 7 }], [row(1, 2, 3, 4)], [row(1, 2, 3, 4)]);
    expect(await resolveJumpTarget(f)).toBe("/app/phenotypes/7");
  });

  it("does not query barcode or accession once species resolves", async () => {
    const f = fetchers([{ id: 7 }], [], []);
    await resolveJumpTarget(f);
    expect(f.barcode).not.toHaveBeenCalled();
    expect(f.accession).not.toHaveBeenCalled();
  });

  it("falls through to barcode when species is ambiguous", async () => {
    const f = fetchers([{ id: 7 }, { id: 8 }], [row(1, 2, 3, 4)], []);
    expect(await resolveJumpTarget(f)).toBe("/app/phenotypes/1/2/3/4");
  });

  it("does not query accession once barcode resolves", async () => {
    const f = fetchers([], [row(1, 2, 3, 4)], []);
    await resolveJumpTarget(f);
    expect(f.accession).not.toHaveBeenCalled();
  });

  it("falls through to accession when the barcode spans several destinations", async () => {
    const f = fetchers([], [row(1, 2, 3, 4), row(1, 2, 9, 4)], [row(5, 6, 7, 8)]);
    expect(await resolveJumpTarget(f)).toBe("/app/phenotypes/5/6/7/8");
  });

  it("falls through to accession when the barcode set is truncated", async () => {
    const barcode = Array.from({ length: 4 }, () => row(1, 2, 3, 4));
    const f = fetchers([], barcode, [row(5, 6, 7, 8)]);
    expect(await resolveJumpTarget(f, 3)).toBe("/app/phenotypes/5/6/7/8");
  });

  it("returns null when nothing resolves to a single destination", async () => {
    const f = fetchers([], [], []);
    expect(await resolveJumpTarget(f)).toBeNull();
  });

  it("returns null when every step's query failed", async () => {
    const f = {
      species: vi.fn(async () => null),
      barcode: vi.fn(async () => null),
      accession: vi.fn(async () => null),
    };
    expect(await resolveJumpTarget(f)).toBeNull();
  });

  it("consults every step in priority order when none resolve", async () => {
    const f = fetchers([], [], []);
    await resolveJumpTarget(f);
    expect(f.species).toHaveBeenCalled();
    expect(f.barcode).toHaveBeenCalled();
    expect(f.accession).toHaveBeenCalled();
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
