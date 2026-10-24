// @vitest-environment jsdom
/** The Species page's list and its Add a species form. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("@/lib/supabase/client", () => ({ createClientSupabaseClient: () => ({}) }));
vi.mock("@/lib/species-options", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/species-options")>()),
  addSpecies: vi.fn(),
}));

import SpeciesCatalog from "./species-catalog";
import { addSpecies, type SpeciesOption } from "@/lib/species-options";

const mockedAdd = vi.mocked(addSpecies);

const SPECIES: SpeciesOption[] = [
  { id: 4, label: "Rice (Oryza sativa)" },
  { id: 1, label: "Arabidopsis (Arabidopsis thaliana)" },
];

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function addPennycress() {
  fireEvent.click(screen.getByRole("button", { name: "Add a species" }));
  type("Genus", "thlaspi");
  type("Species name", "arvense");
  type("Common name", "Pennycress");
  fireEvent.click(screen.getByRole("button", { name: "Add species" }));
}

function listed() {
  return within(screen.getByRole("list", { name: "Species" }))
    .getAllByRole("listitem")
    .map((li) => li.textContent);
}

beforeEach(() => mockedAdd.mockReset());
afterEach(cleanup);

describe("the species list", () => {
  it("lists every species, sorted, with a count", () => {
    render(<SpeciesCatalog species={SPECIES} />);
    expect(listed()).toEqual(["Arabidopsis (Arabidopsis thaliana)", "Rice (Oryza sativa)"]);
    expect(screen.getByText("2 species in Bloom")).toBeTruthy();
  });

  it("says so when there are none", () => {
    render(<SpeciesCatalog species={[]} />);
    expect(screen.getByText("No species yet.")).toBeTruthy();
  });

  it("offers no way to rename or remove a species", () => {
    render(<SpeciesCatalog species={SPECIES} />);
    const buttons = screen.getAllByRole("button").map((b) => b.textContent);
    expect(buttons).toEqual(["Add a species"]);
  });
});

describe("adding a species", () => {
  it("adds a new species to the list, in order, and says so", async () => {
    mockedAdd.mockResolvedValue({ kind: "added", option: { id: 9, label: "Pennycress (Thlaspi arvense)" } });
    render(<SpeciesCatalog species={SPECIES} />);
    addPennycress();

    expect((await screen.findByRole("status")).textContent).toBe("Added Pennycress (Thlaspi arvense).");
    expect(mockedAdd.mock.calls[0][1]).toEqual({ genus: "Thlaspi", species: "arvense", common_name: "Pennycress" });
    expect(listed()).toEqual([
      "Arabidopsis (Arabidopsis thaliana)",
      "Pennycress (Thlaspi arvense)",
      "Rice (Oryza sativa)",
    ]);
    expect(screen.getByText("3 species in Bloom")).toBeTruthy();
    expect(screen.queryByLabelText("Genus")).toBeNull();
  });

  it("reports a species already in Bloom without listing it twice", async () => {
    mockedAdd.mockResolvedValue({ kind: "existing", option: { id: 4, label: "Rice (Oryza sativa)" } });
    render(<SpeciesCatalog species={SPECIES} />);
    addPennycress();

    expect((await screen.findByRole("status")).textContent).toBe("Rice (Oryza sativa) is already in Bloom.");
    expect(listed()).toEqual(["Arabidopsis (Arabidopsis thaliana)", "Rice (Oryza sativa)"]);
  });

  it("keeps the form open with the typed names and the reason when adding fails", async () => {
    mockedAdd.mockResolvedValue({ kind: "error", message: "Sign in to add a species." });
    render(<SpeciesCatalog species={SPECIES} />);
    addPennycress();

    expect((await screen.findByRole("alert")).textContent).toBe("Sign in to add a species.");
    expect((screen.getByLabelText("Genus") as HTMLInputElement).value).toBe("thlaspi");
    expect(listed()).toHaveLength(2);
  });

  it("previews the name without mentioning the Expression page", () => {
    render(<SpeciesCatalog species={SPECIES} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a species" }));
    type("Genus", "thlaspi");
    type("Species name", "arvense");
    type("Common name", "pennycress");
    expect(screen.getByText(/Will be added as/).textContent).toBe(
      "Will be added as Pennycress (Thlaspi arvense), for everyone."
    );
  });

  it("closes the form on Cancel, adding nothing", () => {
    render(<SpeciesCatalog species={SPECIES} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a species" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Genus")).toBeNull();
    expect(mockedAdd).not.toHaveBeenCalled();
  });
});
