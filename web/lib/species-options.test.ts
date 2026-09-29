import { describe, expect, it } from "vitest";

import {
  addSpecies,
  normalizeNewSpecies,
  sortedSpeciesOptions,
  speciesOption,
} from "./species-options";

const ROW = { id: 1, common_name: "Arabidopsis", genus: "arabidopsis", species: "Thaliana" };

/** A stand-in Supabase client whose add_species call answers `result`. */
function fakeClient(result: { data: unknown; error: unknown }) {
  const calls: { fn?: string; args?: unknown } = {};
  const client = {
    rpc: (fn: string, args: unknown) => {
      calls.fn = fn;
      calls.args = args;
      return { single: async () => result };
    },
  };
  return { client: client as never, calls };
}

describe("speciesOption", () => {
  it("shows the common name with the scientific name, cased properly", () => {
    expect(speciesOption(ROW)).toEqual({
      id: 1,
      label: "Arabidopsis (Arabidopsis thaliana)",
    });
  });

  it("falls back to the scientific name, and doesn't repeat it", () => {
    expect(speciesOption({ ...ROW, common_name: null }).label).toBe("Arabidopsis thaliana");
    expect(
      speciesOption({ ...ROW, common_name: "Arabidopsis thaliana" }).label
    ).toBe("Arabidopsis thaliana");
  });

  it("sorts by label", () => {
    const rows = [
      { id: 2, common_name: "Rice", genus: "Oryza", species: "sativa" },
      ROW,
    ];
    expect(sortedSpeciesOptions(rows).map((o) => o.id)).toEqual([1, 2]);
  });
});

describe("normalizeNewSpecies", () => {
  it("capitalises the genus, lowers the epithet and tidies the common name", () => {
    expect(
      normalizeNewSpecies({ genus: " thlaspi ", species: " Arvense ", commonName: "  Field   pennycress " })
    ).toEqual({
      value: { genus: "Thlaspi", species: "arvense", common_name: "Field pennycress" },
    });
  });

  it("capitalises only the first letter of the common name", () => {
    expect(
      normalizeNewSpecies({ genus: "Capsella", species: "rubella", commonName: "pink shepherd's purse" })
    ).toEqual({
      value: { genus: "Capsella", species: "rubella", common_name: "Pink shepherd's purse" },
    });
    expect(
      normalizeNewSpecies({ genus: "Oryza", species: "sativa", commonName: "rice (IR64)" })
    ).toEqual({ value: { genus: "Oryza", species: "sativa", common_name: "Rice (IR64)" } });
  });

  it("allows a hyphenated epithet", () => {
    expect(
      normalizeNewSpecies({ genus: "Capsella", species: "bursa-pastoris", commonName: "Shepherd's purse" })
    ).toEqual({
      value: { genus: "Capsella", species: "bursa-pastoris", common_name: "Shepherd's purse" },
    });
  });

  it.each([
    [{ genus: "", species: "thaliana", commonName: "A" }, "The genus is one word of letters."],
    [{ genus: "Arabidopsis thaliana", species: "x", commonName: "A" }, "The genus is one word of letters."],
    [{ genus: "Arabid0psis", species: "x", commonName: "A" }, "The genus is one word of letters."],
    [{ genus: "Arabidopsis", species: "", commonName: "A" }, "The species is one word of letters, e.g. thaliana."],
    [{ genus: "Arabidopsis", species: "sp.", commonName: "A" }, "The species is one word of letters, e.g. thaliana."],
    [{ genus: "Arabidopsis", species: "-x", commonName: "A" }, "The species is one word of letters, e.g. thaliana."],
    [{ genus: "Arabidopsis", species: "thaliana", commonName: "  " }, "Enter a common name."],
    [
      { genus: "Arabidopsis", species: "thaliana", commonName: "x".repeat(101) },
      "The common name is at most 100 characters.",
    ],
  ])("refuses %j", (input, problem) => {
    expect(normalizeNewSpecies(input)).toEqual({ problem });
  });
});

describe("addSpecies", () => {
  const VALUE = { genus: "Thlaspi", species: "arvense", common_name: "Pennycress" };
  const STORED = { id: 9, common_name: "Pennycress", genus: "Thlaspi", species: "arvense" };

  it("calls add_species with the tidied names and returns the new species", async () => {
    const { client, calls } = fakeClient({ data: { ...STORED, result: "added" }, error: null });
    expect(await addSpecies(client, VALUE)).toEqual({
      kind: "added",
      option: { id: 9, label: "Pennycress (Thlaspi arvense)" },
    });
    expect(calls).toEqual({
      fn: "add_species",
      args: { p_genus: "Thlaspi", p_species: "arvense", p_common_name: "Pennycress" },
    });
  });

  it("returns the stored species when it already exists", async () => {
    const { client } = fakeClient({ data: { ...STORED, result: "existing" }, error: null });
    expect(await addSpecies(client, VALUE)).toEqual({
      kind: "existing",
      option: { id: 9, label: "Pennycress (Thlaspi arvense)" },
    });
  });

  it.each([
    ["23505", "A removed species has that name. Ask a Bloom admin."],
    ["42501", "Sign in to add a species."],
    ["22023", "Check the genus, species and common name."],
    ["08006", "Couldn't add the species. Try again shortly."],
  ])("says what a %s means, without the database's wording", async (code, message) => {
    const { client } = fakeClient({
      data: null,
      error: { code, message: "invalid genus: x / permission denied for function" },
    });
    expect(await addSpecies(client, VALUE)).toEqual({ kind: "error", message });
  });

  it("gives the fixed message when there's no error code", async () => {
    const { client } = fakeClient({ data: null, error: null });
    expect(await addSpecies(client, VALUE)).toEqual({
      kind: "error",
      message: "Couldn't add the species. Try again shortly.",
    });
  });
});
