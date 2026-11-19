import { describe, expect, it, vi } from "vitest";

import { MAX_GENES } from "./gene-stats";
import { parseGeneParam, resolveLinkedGenes } from "./linked-genes";

describe("parseGeneParam", () => {
  it("is empty without a gene", () => {
    expect(parseGeneParam(undefined)).toEqual([]);
    expect(parseGeneParam("")).toEqual([]);
  });

  it("reads one name, repeated names and comma-separated names", () => {
    expect(parseGeneParam("AT4G28110")).toEqual(["AT4G28110"]);
    expect(parseGeneParam(["AT4G28110", "AT1G01010"])).toEqual(["AT4G28110", "AT1G01010"]);
    expect(parseGeneParam(["AT4G28110, AT1G01010", "AT2G02020"])).toEqual([
      "AT4G28110",
      "AT1G01010",
      "AT2G02020",
    ]);
  });

  it("drops blanks and repeats, keeping the first spelling", () => {
    expect(parseGeneParam(" AT4G28110 ,, at4g28110 ,AT1G01010")).toEqual(["AT4G28110", "AT1G01010"]);
  });

  it("keeps at most the view's gene limit", () => {
    const many = Array.from({ length: MAX_GENES + 3 }, (_, i) => `G${i}`);
    expect(parseGeneParam(many.join(","))).toEqual(many.slice(0, MAX_GENES));
  });
});

describe("resolveLinkedGenes", () => {
  const dataset = ["AT1G01010", "AT4G28110", "AT4G28110.2", "MYB41"];
  const search = vi.fn(async (q: string) =>
    dataset.filter((g) => g.toLowerCase().startsWith(q.toLowerCase())),
  );

  it("takes the dataset's own spelling of each name, ignoring case", async () => {
    expect(await resolveLinkedGenes(["at4g28110", "myb41"], search)).toEqual({
      genes: ["AT4G28110", "MYB41"],
      notFound: [],
      failed: [],
    });
  });

  it("matches whole names only, not a longer name the search also finds", async () => {
    expect(await resolveLinkedGenes(["AT4G2811"], search)).toEqual({
      genes: [],
      notFound: ["AT4G2811"],
      failed: [],
    });
  });

  it("lists names the dataset doesn't have and keeps the others", async () => {
    expect(await resolveLinkedGenes(["NOPE1", "AT1G01010"], search)).toEqual({
      genes: ["AT1G01010"],
      notFound: ["NOPE1"],
      failed: [],
    });
  });

  it("lists a name whose search failed apart from the names the dataset lacks", async () => {
    const flaky = vi.fn(async (q: string) => {
      if (q === "AT1G01010") throw new Error("network");
      return search(q);
    });
    expect(await resolveLinkedGenes(["AT1G01010", "NOPE1", "MYB41"], flaky)).toEqual({
      genes: ["MYB41"],
      notFound: ["NOPE1"],
      failed: ["AT1G01010"],
    });
  });

  it("drops two names that resolve to the same gene", async () => {
    expect(await resolveLinkedGenes(["MYB41", "myb41"], search)).toEqual({
      genes: ["MYB41"],
      notFound: [],
      failed: [],
    });
  });
});
