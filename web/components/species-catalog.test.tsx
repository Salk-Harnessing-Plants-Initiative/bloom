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
import { addSpecies, type SpeciesOption } from "@/lib/species-options";

const mockedAdd = vi.mocked(addSpecies);

// The server's snapshot, already sorted.
const SPECIES: SpeciesOption[] = [
  { id: 1, label: "Arabidopsis (Arabidopsis thaliana)" },
  { id: 4, label: "Rice (Oryza sativa)" },
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

    expect(await screen.findByText("Added Pennycress (Thlaspi arvense).")).toBeTruthy();
    expect(mockedAdd.mock.calls[0][1]).toEqual({ genus: "Thlaspi", species: "arvense", common_name: "Pennycress" });
    expect(listed()).toEqual([
      "Arabidopsis (Arabidopsis thaliana)",
      "Pennycress (Thlaspi arvense)",
      "Rice (Oryza sativa)",
    ]);
    expect(screen.getByText("3 species in Bloom")).toBeTruthy();
    expect(screen.queryByLabelText("Genus")).toBeNull();
  });

  it("lists a species found already in Bloom that wasn't there when the page loaded", async () => {
    mockedAdd.mockResolvedValue({ kind: "existing", option: { id: 9, label: "Pennycress (Thlaspi arvense)" } });
    render(<SpeciesCatalog species={SPECIES} />);
    addPennycress();

    expect(await screen.findByText("Pennycress (Thlaspi arvense) is already in Bloom.")).toBeTruthy();
    expect(listed()).toEqual([
      "Arabidopsis (Arabidopsis thaliana)",
      "Pennycress (Thlaspi arvense)",
      "Rice (Oryza sativa)",
    ]);
  });

  it("reports a species already in Bloom without listing it twice", async () => {
    mockedAdd.mockResolvedValue({ kind: "existing", option: { id: 4, label: "Rice (Oryza sativa)" } });
    render(<SpeciesCatalog species={SPECIES} />);
    addPennycress();

    expect(await screen.findByText("Rice (Oryza sativa) is already in Bloom.")).toBeTruthy();
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

  it("previews how the name will be stored", () => {
    render(<SpeciesCatalog species={SPECIES} />);
    fireEvent.click(screen.getByRole("button", { name: "Add a species" }));
    type("Genus", "thlaspi");
    type("Species name", "arvense");
    type("Common name", "pennycress");
    expect(screen.getByText(/Will be added as/).textContent).toBe(
      "Will be added as Pennycress (Thlaspi arvense), for everyone to use."
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

describe("live updates", () => {
  it("subscribes to every change to species", async () => {
    render(<SpeciesCatalog species={SPECIES} />);
    await act(async () => {});
    expect(liveChannels()).toHaveLength(1);
    expect(liveChannels()[0].bindings.map((b) => b.filter)).toEqual([
      { event: "*", schema: "public", table: "species" },
    ]);
  });

  it("shows whether the list is live", async () => {
    render(<SpeciesCatalog species={SPECIES} />);
    expect(screen.getByRole("status").textContent).toBe("Connecting…");
    await act(async () => {});
    await act(async () => liveChannels()[0].status("SUBSCRIBED"));
    expect(screen.getByRole("status").textContent).toBe("Live");
  });

  it("lists a species someone else adds, in order", async () => {
    render(<SpeciesCatalog species={SPECIES} />);
    await emit("INSERT", { id: 7, common_name: "Maize", genus: "Zea", species: "mays", deleted_at: null });
    expect(listed()).toEqual([
      "Arabidopsis (Arabidopsis thaliana)",
      "Maize (Zea mays)",
      "Rice (Oryza sativa)",
    ]);
    expect(screen.getByText("3 species in Bloom")).toBeTruthy();
  });

  it("drops a species marked deleted, and relists an edited one under its new name", async () => {
    render(<SpeciesCatalog species={SPECIES} />);
    await emit("UPDATE", { id: 4, common_name: "Rice", genus: "Oryza", species: "sativa", deleted_at: "2026-10-24T10:00:00Z" });
    expect(listed()).toEqual(["Arabidopsis (Arabidopsis thaliana)"]);
    await emit("UPDATE", { id: 1, common_name: "Thale cress", genus: "Arabidopsis", species: "thaliana", deleted_at: null });
    expect(listed()).toEqual(["Thale cress (Arabidopsis thaliana)"]);
  });

  it("refreshes the list from the database when it (re)joins, and says so if that fails", async () => {
    vi.useFakeTimers();
    try {
      supabaseMock.respond = () => ({ data: null, error: { message: "connection refused" } });
      render(<SpeciesCatalog species={SPECIES} />);
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
