// @vitest-environment jsdom
/**
 * The species list in the job form: choosing a species, and adding one that isn't there
 * yet, including when it turns out to exist already.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/supabase/client", () => ({ createClientSupabaseClient: () => ({}) }));
vi.mock("@/lib/species-options", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/species-options")>()),
  addSpecies: vi.fn(),
}));

import ScrnaSpeciesPicker from "./scrna-species-picker";
import { addSpecies, type SpeciesOption } from "@/lib/species-options";

const mockedAdd = vi.mocked(addSpecies);

const SPECIES: SpeciesOption[] = [
  { id: 1, label: "Arabidopsis (Arabidopsis thaliana)" },
  { id: 4, label: "Rice (Oryza sativa)" },
];

let onChange: ReturnType<typeof vi.fn<(id: number | null) => void>>;
let onAdded: ReturnType<typeof vi.fn<(option: SpeciesOption) => void>>;

function renderPicker(value: number | null = null) {
  onChange = vi.fn<(id: number | null) => void>();
  onAdded = vi.fn<(option: SpeciesOption) => void>();
  return render(
    <ScrnaSpeciesPicker
      species={SPECIES}
      value={value}
      onChange={onChange}
      onAdded={onAdded}
      fieldClass=""
      labelClass=""
    />
  );
}

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function openAdd() {
  type("Species", "add-new");
}

function fillNew() {
  type("Genus", "thlaspi");
  type("Species name", "Arvense");
  type("Common name", "Pennycress");
}

beforeEach(() => {
  mockedAdd.mockReset();
});
afterEach(cleanup);

describe("choosing", () => {
  it("lists the species and an option to add one", () => {
    renderPicker();
    expect(screen.getByRole("option", { name: "Rice (Oryza sativa)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "+ Add a species…" })).toBeTruthy();
    expect(screen.queryByLabelText("Genus")).toBeNull();
  });

  it("reports the chosen species id, and null for none", () => {
    renderPicker();
    type("Species", "4");
    expect(onChange).toHaveBeenLastCalledWith(4);
    type("Species", "");
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});

describe("adding a species", () => {
  it("opens the fields and clears the choice until one is added", () => {
    renderPicker(1);
    openAdd();
    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(screen.getByLabelText("Genus")).toBeTruthy();
    expect(screen.getByLabelText("Common name")).toBeTruthy();
  });

  it("previews the species in its stored casing before adding", () => {
    renderPicker();
    openAdd();
    type("Genus", " thlaspi ");
    type("Species name", "ARVENSE");
    type("Common name", "field   pennycress");
    expect(screen.getByText(/Will be added as/).textContent).toBe(
      "Will be added as Field pennycress (Thlaspi arvense), for everyone, and shown on the Expression page."
    );
    expect(
      (screen.getByRole("button", { name: "Add species" }) as HTMLButtonElement).disabled
    ).toBe(false);
  });

  it("shows what's wrong instead of a preview, and can't be added", () => {
    renderPicker();
    openAdd();
    type("Genus", "Arabidopsis thaliana");
    expect(screen.getByText("The genus is one word of letters.")).toBeTruthy();
    expect(screen.queryByText(/Will be added as/)).toBeNull();
    const add = screen.getByRole("button", { name: "Add species" }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    fireEvent.click(add);
    expect(mockedAdd).not.toHaveBeenCalled();
  });

  it("explains where the species goes before anything is typed", () => {
    renderPicker();
    openAdd();
    expect(screen.getByText(/Added to Bloom's species list for everyone/)).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Add species" }) as HTMLButtonElement).disabled
    ).toBe(true);
  });

  it("adds the species, hands it to the form, selects it and says so", async () => {
    mockedAdd.mockResolvedValue({
      kind: "added",
      option: { id: 9, label: "Pennycress (Thlaspi arvense)" },
    });
    renderPicker();
    openAdd();
    fillNew();
    fireEvent.click(screen.getByRole("button", { name: "Add species" }));

    expect((await screen.findByRole("status")).textContent).toBe(
      "Added Pennycress (Thlaspi arvense)."
    );
    expect(mockedAdd.mock.calls[0][1]).toEqual({
      genus: "Thlaspi",
      species: "arvense",
      common_name: "Pennycress",
    });
    expect(onChange).toHaveBeenLastCalledWith(9);
    expect(onAdded).toHaveBeenCalledWith({ id: 9, label: "Pennycress (Thlaspi arvense)" });
    expect(screen.queryByLabelText("Genus")).toBeNull();
  });

  it("selects the stored species when it already exists", async () => {
    mockedAdd.mockResolvedValue({
      kind: "existing",
      option: { id: 4, label: "Rice (Oryza sativa)" },
    });
    renderPicker();
    openAdd();
    type("Genus", "Oryza");
    type("Species name", "sativa");
    type("Common name", "Rice");
    fireEvent.click(screen.getByRole("button", { name: "Add species" }));

    expect((await screen.findByRole("status")).textContent).toBe(
      "Rice (Oryza sativa) is already in Bloom, so it's selected."
    );
    expect(onChange).toHaveBeenLastCalledWith(4);
    expect(onAdded).toHaveBeenCalledWith({ id: 4, label: "Rice (Oryza sativa)" });
  });

  it("keeps the fields open with the reason when adding fails", async () => {
    mockedAdd.mockResolvedValue({
      kind: "error",
      message: "Couldn't add the species. Try again shortly.",
    });
    renderPicker();
    openAdd();
    fillNew();
    fireEvent.click(screen.getByRole("button", { name: "Add species" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Couldn't add the species. Try again shortly."
    );
    expect((screen.getByLabelText("Genus") as HTMLInputElement).value).toBe("thlaspi");
    expect(onChange).not.toHaveBeenCalledWith(expect.any(Number));

    type("Genus", "Thlaspi");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("disables the fields while saving", () => {
    mockedAdd.mockReturnValue(new Promise(() => {}));
    renderPicker();
    openAdd();
    fillNew();
    fireEvent.click(screen.getByRole("button", { name: "Add species" }));
    expect(
      (screen.getByRole("button", { name: "Adding…" }) as HTMLButtonElement).disabled
    ).toBe(true);
    expect((screen.getByLabelText("Genus") as HTMLInputElement).disabled).toBe(true);
  });

  it("closes the fields on Cancel and restores the species chosen before", () => {
    renderPicker(4);
    openAdd();
    expect(onChange).toHaveBeenLastCalledWith(null);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Genus")).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith(4);
  });

  it("lists the species it is given, including one the form added", () => {
    onChange = vi.fn<(id: number | null) => void>();
    const { rerender } = render(
      <ScrnaSpeciesPicker
        species={SPECIES}
        value={null}
        onChange={onChange}
        onAdded={() => {}}
        fieldClass=""
        labelClass=""
      />
    );
    rerender(
      <ScrnaSpeciesPicker
        species={[...SPECIES, { id: 9, label: "Pennycress (Thlaspi arvense)" }]}
        value={9}
        onChange={onChange}
        onAdded={() => {}}
        fieldClass=""
        labelClass=""
      />
    );
    expect((screen.getByLabelText("Species") as HTMLSelectElement).value).toBe("9");
  });
});
