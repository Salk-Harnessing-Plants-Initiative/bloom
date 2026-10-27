// @vitest-environment jsdom
/** The Species page's list, as the server sends it, and its Add a species form. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);
const router = { refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/species-options", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/species-options")>()),
  addSpecies: vi.fn(),
}));

import SpeciesCatalog from "./species-catalog";
import { addSpecies, type SpeciesListItem } from "@/lib/species-options";

const mockedAdd = vi.mocked(addSpecies);

const ME = "4965b3af-ccfe-40f5-814b-447e1f726e1b";
const OTHER = "1a2b3c4d-0000-4000-8000-000000000002";

// The server's snapshot, already sorted. Arabidopsis was added before created_by was recorded.
const ICON = { url: "/species-icons/arabidopsis.png", placeholder: false };
const PLACEHOLDER = { url: "/species-icons/_default.svg", placeholder: true };

const SPECIES: SpeciesListItem[] = [
  { id: 1, label: "Arabidopsis (Arabidopsis thaliana)", addedBy: null, image: ICON },
  { id: 4, label: "Rice (Oryza sativa)", addedBy: OTHER, image: PLACEHOLDER },
];

function mount(species: SpeciesListItem[] = SPECIES) {
  return render(<SpeciesCatalog species={species} currentUserId={ME} />);
}

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

/** The species column, top to bottom. */
function listed() {
  return rows().map((r) => r[0]);
}

/** Each body row's cells: [species, added by]. */
function rows() {
  return within(screen.getByRole("table", { name: "Species" }))
    .getAllByRole("row")
    .slice(1)
    .map((tr) => within(tr).getAllByRole("cell").slice(0, 2).map((td) => td.textContent));
}

beforeEach(() => {
  mockedAdd.mockReset();
  router.refresh.mockReset();
});
afterEach(cleanup);

describe("the species list", () => {
  it("lists the species, with a count", () => {
    mount();
    expect(listed()).toEqual(["Arabidopsis (Arabidopsis thaliana)", "Rice (Oryza sativa)"]);
    expect(screen.getByText("2 species in Bloom")).toBeTruthy();
  });

  it("says so when there are none", () => {
    mount([]);
    expect(screen.getByText("No species yet.")).toBeTruthy();
  });

  it("offers no way to rename or remove a species", () => {
    mount();
    const buttons = screen.getAllByRole("button").map((b) => b.textContent);
    expect(buttons).toEqual(["Add a species"]);
  });

  it("isn't live: no Live indicator", () => {
    mount();
    expect(screen.queryByText("Live")).toBeNull();
    expect(screen.queryByText("Connecting…")).toBeNull();
  });
});

describe("who added each species", () => {
  it("says you, another member with their id, or Bloom admin for older species", () => {
    mount([
      { id: 1, label: "Arabidopsis (Arabidopsis thaliana)", addedBy: null, image: ICON },
      { id: 9, label: "Pennycress (Thlaspi arvense)", addedBy: ME, image: PLACEHOLDER },
      { id: 4, label: "Rice (Oryza sativa)", addedBy: OTHER, image: PLACEHOLDER },
    ]);
    expect(rows()).toEqual([
      ["Arabidopsis (Arabidopsis thaliana)", "Bloom admin"],
      ["Pennycress (Thlaspi arvense)", "you"],
      ["Rice (Oryza sativa)", "another member · 1a2b3c4d"],
    ]);
  });

  it("shows the full user id on hover, to trace it", () => {
    mount();
    expect(screen.getByText("another member · 1a2b3c4d").getAttribute("title")).toBe(OTHER);
    expect(screen.getByText("Bloom admin").getAttribute("title")).toBeNull();
  });
});

describe("each species' image", () => {
  const imageCell = (name: string) =>
    within(screen.getByRole("row", { name: new RegExp(name) })).getAllByRole("cell")[2];

  it("shows the server's image, or the placeholder marked No image yet", () => {
    mount();
    expect(imageCell("Arabidopsis").querySelector("img")?.getAttribute("src")).toBe("/species-icons/arabidopsis.png");
    expect(imageCell("Arabidopsis").textContent).toBe("");
    expect(imageCell("Rice").querySelector("img")?.getAttribute("src")).toBe("/species-icons/_default.svg");
    expect(imageCell("Rice").textContent).toBe("No image yet");
  });
});

describe("adding a species", () => {
  it("reloads the list from the server after adding a species, and says so", async () => {
    mockedAdd.mockResolvedValue({ kind: "added", option: { id: 9, label: "Pennycress (Thlaspi arvense)" } });
    mount();
    addPennycress();

    expect(await screen.findByText("Added Pennycress (Thlaspi arvense).")).toBeTruthy();
    expect(mockedAdd.mock.calls[0][1]).toEqual({ genus: "Thlaspi", species: "arvense", common_name: "Pennycress" });
    expect(router.refresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("Genus")).toBeNull();
  });

  it("reloads the list when a species found already in Bloom isn't listed yet", async () => {
    mockedAdd.mockResolvedValue({ kind: "existing", option: { id: 9, label: "Pennycress (Thlaspi arvense)" } });
    mount();
    addPennycress();

    expect(await screen.findByText("Pennycress (Thlaspi arvense) is already in Bloom.")).toBeTruthy();
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("reports a species already listed without reloading", async () => {
    mockedAdd.mockResolvedValue({ kind: "existing", option: { id: 4, label: "Rice (Oryza sativa)" } });
    mount();
    addPennycress();

    expect(await screen.findByText("Rice (Oryza sativa) is already in Bloom.")).toBeTruthy();
    expect(listed()).toEqual(["Arabidopsis (Arabidopsis thaliana)", "Rice (Oryza sativa)"]);
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("keeps the form open with the typed names and the reason when adding fails", async () => {
    mockedAdd.mockResolvedValue({ kind: "error", message: "Sign in to add a species." });
    mount();
    addPennycress();

    expect((await screen.findByRole("alert")).textContent).toBe("Sign in to add a species.");
    expect((screen.getByLabelText("Genus") as HTMLInputElement).value).toBe("thlaspi");
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("previews how the name will be stored", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Add a species" }));
    type("Genus", "thlaspi");
    type("Species name", "arvense");
    type("Common name", "pennycress");
    expect(screen.getByText(/Will be added as/).textContent).toBe(
      "Will be added as Pennycress (Thlaspi arvense), for everyone to use."
    );
  });

  it("puts the cursor in Genus when the form opens, and back on the button when it closes", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Add a species" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Genus"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Add a species" }));
  });

  it("closes the form on Cancel, adding nothing", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Add a species" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Genus")).toBeNull();
    expect(mockedAdd).not.toHaveBeenCalled();
  });
});
