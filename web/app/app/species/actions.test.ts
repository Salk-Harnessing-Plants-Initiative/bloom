/** Adding a species on the server: checked again, added, and a new one emailed about after the response. */

import { beforeEach, describe, expect, it, vi } from "vitest";

const afterCallbacks: (() => unknown)[] = [];
vi.mock("next/server", () => ({ after: (fn: () => unknown) => afterCallbacks.push(fn) }));
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => ({ server: true }),
  getUser: async () => ({ id: "u-1", email: "scientist@salk.edu" }),
}));
vi.mock("@/lib/species-options", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/species-options")>()),
  addSpecies: vi.fn(),
}));
vi.mock("@/lib/species-added-email", () => ({ notifySpeciesAdded: vi.fn(async () => true) }));

import { addSpeciesAction } from "./actions";
import { addSpecies } from "@/lib/species-options";
import { notifySpeciesAdded } from "@/lib/species-added-email";

const mockedAdd = vi.mocked(addSpecies);
const mockedNotify = vi.mocked(notifySpeciesAdded);
const PENNYCRESS = { genus: "thlaspi", species: "Arvense", commonName: "pennycress" };

beforeEach(() => {
  afterCallbacks.length = 0;
  mockedAdd.mockReset();
  mockedNotify.mockClear();
});

describe("addSpeciesAction", () => {
  it("adds through add_species with the user's session, names normalized", async () => {
    mockedAdd.mockResolvedValue({ kind: "added", option: { id: 9, label: "Pennycress (Thlaspi arvense)" } });
    await addSpeciesAction(PENNYCRESS);
    expect(mockedAdd).toHaveBeenCalledWith(
      { server: true },
      { genus: "Thlaspi", species: "arvense", common_name: "Pennycress" }
    );
  });

  it("emails about a new species after the response, naming who added it", async () => {
    mockedAdd.mockResolvedValue({ kind: "added", option: { id: 9, label: "Pennycress (Thlaspi arvense)" } });
    const result = await addSpeciesAction(PENNYCRESS);
    expect(result).toEqual({ kind: "added", option: { id: 9, label: "Pennycress (Thlaspi arvense)" } });
    // Not yet: it runs after the response.
    expect(mockedNotify).not.toHaveBeenCalled();
    expect(afterCallbacks).toHaveLength(1);
    await afterCallbacks[0]();
    expect(mockedNotify).toHaveBeenCalledWith({
      label: "Pennycress (Thlaspi arvense)",
      addedBy: "scientist@salk.edu",
    });
  });

  it("sends nothing for a species already in Bloom, or when adding fails", async () => {
    mockedAdd.mockResolvedValueOnce({ kind: "existing", option: { id: 4, label: "Rice (Oryza sativa)" } });
    await addSpeciesAction({ genus: "oryza", species: "sativa", commonName: "rice" });
    mockedAdd.mockResolvedValueOnce({ kind: "error", message: "Sign in to add a species." });
    expect(await addSpeciesAction(PENNYCRESS)).toEqual({ kind: "error", message: "Sign in to add a species." });
    expect(afterCallbacks).toHaveLength(0);
  });

  it("checks the names again, and adds nothing when they're wrong", async () => {
    expect(await addSpeciesAction({ genus: "thl aspi", species: "arvense", commonName: "Pennycress" })).toEqual({
      kind: "error",
      message: "The genus is one word of letters.",
    });
    expect(mockedAdd).not.toHaveBeenCalled();
  });
});
