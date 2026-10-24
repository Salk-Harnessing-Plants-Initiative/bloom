// @vitest-environment jsdom
/** The Species page's list, kept live through Realtime, and its Add a species form. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { liveChannels, resetSupabaseMock, supabaseMock } from "@/lib/cyl-pipeline/__fixtures__/supabase-mock";

vi.mock("@/lib/supabase/client", async () => (await import("@/lib/cyl-pipeline/__fixtures__/supabase-mock")).clientModule);
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
const SPECIES: SpeciesListItem[] = [
  { id: 1, label: "Arabidopsis (Arabidopsis thaliana)", addedBy: null },
  { id: 4, label: "Rice (Oryza sativa)", addedBy: OTHER },
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
    .map((tr) => within(tr).getAllByRole("cell").map((td) => td.textContent));
}

/** Wait for the list to join its Realtime channel, then deliver a change to `species`. */
async function emit(eventType: string, row: Record<string, unknown>) {
  await act(async () => {});
  await act(async () => liveChannels()[0].emit({ table: "species", eventType, new: row }));
}

beforeEach(() => {
  mockedAdd.mockReset();
  resetSupabaseMock();
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
});

describe("who added each species", () => {
  it("says you, another member with their id, or Bloom admin for older species", () => {
    mount([
      { id: 1, label: "Arabidopsis (Arabidopsis thaliana)", addedBy: null },
      { id: 9, label: "Pennycress (Thlaspi arvense)", addedBy: ME },
      { id: 4, label: "Rice (Oryza sativa)", addedBy: OTHER },
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

describe("adding a species", () => {
  it("adds a new species to the list, in order, and says so", async () => {
    mockedAdd.mockResolvedValue({ kind: "added", option: { id: 9, label: "Pennycress (Thlaspi arvense)" } });
    mount();
    addPennycress();

    expect(await screen.findByText("Added Pennycress (Thlaspi arvense).")).toBeTruthy();
    expect(mockedAdd.mock.calls[0][1]).toEqual({ genus: "Thlaspi", species: "arvense", common_name: "Pennycress" });
    expect(listed()).toEqual([
      "Arabidopsis (Arabidopsis thaliana)",
      "Pennycress (Thlaspi arvense)",
      "Rice (Oryza sativa)",
    ]);
    expect(screen.getByText("3 species in Bloom")).toBeTruthy();
    expect(rows()[1]).toEqual(["Pennycress (Thlaspi arvense)", "you"]);
    expect(screen.queryByLabelText("Genus")).toBeNull();
  });

  it("reloads the list when a species found already in Bloom isn't listed yet", async () => {
    mockedAdd.mockResolvedValue({ kind: "existing", option: { id: 9, label: "Pennycress (Thlaspi arvense)" } });
    supabaseMock.respond = () => ({
      data: [
        { id: 1, common_name: "Arabidopsis", genus: "Arabidopsis", species: "thaliana", created_by: null },
        { id: 9, common_name: "Pennycress", genus: "Thlaspi", species: "arvense", created_by: OTHER },
        { id: 4, common_name: "Rice", genus: "Oryza", species: "sativa", created_by: OTHER },
      ],
      error: null,
    });
    mount();
    await act(async () => {});
    addPennycress();

    expect(await screen.findByText("Pennycress (Thlaspi arvense) is already in Bloom.")).toBeTruthy();
    expect(await screen.findByText("Pennycress (Thlaspi arvense)")).toBeTruthy();
    expect(rows()).toEqual([
      ["Arabidopsis (Arabidopsis thaliana)", "Bloom admin"],
      ["Pennycress (Thlaspi arvense)", "another member · 1a2b3c4d"],
      ["Rice (Oryza sativa)", "another member · 1a2b3c4d"],
    ]);
  });


  it("reports a species already in Bloom without listing it twice", async () => {
    mockedAdd.mockResolvedValue({ kind: "existing", option: { id: 4, label: "Rice (Oryza sativa)" } });
    mount();
    addPennycress();

    expect(await screen.findByText("Rice (Oryza sativa) is already in Bloom.")).toBeTruthy();
    expect(listed()).toEqual(["Arabidopsis (Arabidopsis thaliana)", "Rice (Oryza sativa)"]);
  });

  it("keeps the form open with the typed names and the reason when adding fails", async () => {
    mockedAdd.mockResolvedValue({ kind: "error", message: "Sign in to add a species." });
    mount();
    addPennycress();

    expect((await screen.findByRole("alert")).textContent).toBe("Sign in to add a species.");
    expect((screen.getByLabelText("Genus") as HTMLInputElement).value).toBe("thlaspi");
    expect(listed()).toHaveLength(2);
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

  it("closes the form on Cancel, adding nothing", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Add a species" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Genus")).toBeNull();
    expect(mockedAdd).not.toHaveBeenCalled();
  });
});

describe("live updates", () => {
  it("subscribes to every change to species", async () => {
    mount();
    await act(async () => {});
    expect(liveChannels()).toHaveLength(1);
    expect(liveChannels()[0].bindings.map((b) => b.filter)).toEqual([
      { event: "*", schema: "public", table: "species" },
    ]);
  });

  it("shows whether the list is live", async () => {
    mount();
    expect(screen.getByRole("status").textContent).toBe("Connecting…");
    await act(async () => {});
    await act(async () => liveChannels()[0].status("SUBSCRIBED"));
    expect(screen.getByRole("status").textContent).toBe("Live");
  });

  it("lists a species someone else adds, in order", async () => {
    mount();
    await emit("INSERT", { id: 7, common_name: "Maize", genus: "Zea", species: "mays", created_by: OTHER, deleted_at: null });
    expect(listed()).toEqual([
      "Arabidopsis (Arabidopsis thaliana)",
      "Maize (Zea mays)",
      "Rice (Oryza sativa)",
    ]);
    expect(rows()[1]).toEqual(["Maize (Zea mays)", "another member · 1a2b3c4d"]);
    expect(screen.getByText("3 species in Bloom")).toBeTruthy();
  });

  it("drops a species marked deleted, and relists an edited one under its new name", async () => {
    mount();
    await emit("UPDATE", { id: 4, common_name: "Rice", genus: "Oryza", species: "sativa", deleted_at: "2026-10-24T10:00:00Z" });
    expect(listed()).toEqual(["Arabidopsis (Arabidopsis thaliana)"]);
    await emit("UPDATE", { id: 1, common_name: "Thale cress", genus: "Arabidopsis", species: "thaliana", deleted_at: null });
    expect(listed()).toEqual(["Thale cress (Arabidopsis thaliana)"]);
  });

  it("refreshes the list from the database when it (re)joins, and says so if that fails", async () => {
    vi.useFakeTimers();
    try {
      supabaseMock.respond = () => ({ data: null, error: { message: "connection refused" } });
      mount();
      await act(async () => {});
      await act(async () => liveChannels()[0].status("SUBSCRIBED"));
      await act(() => vi.advanceTimersByTimeAsync(1000));
      expect(screen.getByRole("alert").textContent).toBe("Couldn't refresh the species: connection refused");
      // The list it had stays.
      expect(listed()).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
