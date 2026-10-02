// @vitest-environment jsdom
/**
 * The trait explorer opens on the wave and plant age a pipeline run links to, keeps what the user
 * is looking at across trait changes when it can, and says so when it has to fall back.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

type Row = { wave_number: number; plant_age_days: number };

/** Rows `get_scan_traits` returns, per trait. */
const ROWS: Record<string, Row[]> = vi.hoisted(() => ({
  // Waves 1-3; wave 1 has ages 7 and 14, wave 2 has 14 and 21, wave 3 has 21.
  trait_a: [
    { wave_number: 1, plant_age_days: 7 },
    { wave_number: 1, plant_age_days: 14 },
    { wave_number: 2, plant_age_days: 14 },
    { wave_number: 2, plant_age_days: 21 },
    { wave_number: 3, plant_age_days: 21 },
  ],
  // Only wave 2 at day 21 and wave 3 at day 21.
  trait_b: [
    { wave_number: 2, plant_age_days: 21 },
    { wave_number: 3, plant_age_days: 21 },
  ],
  // The last wave is the youngest: the overall-oldest day (21) never occurs in wave 3.
  young_a: [
    { wave_number: 1, plant_age_days: 7 },
    { wave_number: 1, plant_age_days: 21 },
    { wave_number: 3, plant_age_days: 7 },
  ],
  young_b: [
    { wave_number: 1, plant_age_days: 7 },
    { wave_number: 1, plant_age_days: 21 },
    { wave_number: 3, plant_age_days: 7 },
  ],
  empty: [],
}));

/** When set, the next rpc call waits for this promise before answering. */
const gate = vi.hoisted(() => ({ next: null as Promise<void> | null }));
/** Trait names whose rpc call fails, the way getTraitData sees an RPC error. */
const failing = vi.hoisted(() => new Set<string>());

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    rpc: async (_fn: string, args: { trait_name_: string }) => {
      if (gate.next) {
        const wait = gate.next;
        gate.next = null;
        await wait;
      }
      if (failing.has(args.trait_name_)) {
        return { data: null, error: { message: "boom" } };
      }
      return {
      data: (ROWS[args.trait_name_] ?? []).map((r, i) => ({
        ...r,
        scan_id: i + 1,
        date_scanned: "2026-01-01",
        plant_id: i + 1,
        germ_day: 0,
        plant_qr_code: `qr-${i}`,
        accession_name: "acc",
        trait_name: args.trait_name_,
        trait_value: 1,
      })),
      error: null,
      };
    },
  }),
}));
vi.mock("@/components/scan-trait-boxplot", () => ({
  default: ({ traitData }: { traitData: unknown[] }) => (
    <div data-testid="boxplot">{traitData.length}</div>
  ),
}));

vi.mock("@/components/cyl-trait-export/TraitExportButton", () => ({
  TraitExportButton: (props: { target: unknown; disabled?: boolean }) => (
    <button type="button" data-testid="export" disabled={props.disabled} data-target={JSON.stringify(props.target)}>
      Download traits
    </button>
  ),
}));

import TraitExplorer from "./TraitExplorer";

afterEach(() => {
  cleanup();
  failing.clear();
  gate.next = null;
  vi.restoreAllMocks();
});

async function renderExplorer(
  props: {
    initialWave?: number;
    initialAge?: number;
    traitNames?: string[];
    defaultTraitName?: string;
  } = {},
) {
  const { traitNames = ["trait_a", "trait_b"], defaultTraitName = traitNames[0], ...rest } = props;
  await act(async () => {
    render(
      <TraitExplorer
        experimentId={5}
        traitNames={traitNames}
        defaultTraitName={defaultTraitName}
        {...rest}
      />,
    );
  });
}

function note(): string {
  return screen.getByRole("status").textContent ?? "";
}

function selects() {
  const [trait, wave, age] = screen.getAllByRole("combobox") as HTMLSelectElement[];
  return { trait, wave, age };
}

async function changeTrait(name: string) {
  await act(async () => {
    fireEvent.change(selects().trait, { target: { value: name } });
  });
}

describe("TraitExplorer's initial wave and age", () => {
  it("opens on the last wave and oldest age with no URL parameters", async () => {
    await renderExplorer();
    expect(selects().wave.value).toBe("3");
    expect(selects().age.value).toBe("21");
    expect(note()).toBe("");
  });

  it("opens on the wave and age a run links to", async () => {
    await renderExplorer({ initialWave: 1, initialAge: 14 });
    expect(selects().wave.value).toBe("1");
    expect(selects().age.value).toBe("14");
    expect(screen.getByTestId("boxplot").textContent).toBe("1");
  });

  it("falls back, and says so, when the linked pair has no data", async () => {
    await renderExplorer({ initialWave: 9, initialAge: 14 });
    expect(selects().wave.value).toBe("3");
    expect(note()).toBe(
      "Wave 9 · day 14 has no data for this trait; showing wave 3 · day 21.",
    );
  });

  it("keeps the current wave and age on a trait change when the new trait has them", async () => {
    await renderExplorer({ initialWave: 2, initialAge: 21 });
    await changeTrait("trait_b");
    expect(selects().wave.value).toBe("2");
    expect(selects().age.value).toBe("21");
    expect(note()).toBe("");
  });

  it("falls back with a note on a trait change when the new trait lacks them", async () => {
    await renderExplorer({ initialWave: 1, initialAge: 14 });
    await changeTrait("trait_b");
    expect(selects().wave.value).toBe("3");
    expect(selects().age.value).toBe("21");
    expect(note()).toBe(
      "Wave 1 · day 14 has no data for this trait; showing wave 3 · day 21.",
    );
  });

  it("keeps a wave and age the user picked by hand rather than returning to the link's", async () => {
    await renderExplorer({ initialWave: 1, initialAge: 14 });
    // Wave 2 · day 21 exists for both traits; the link's wave 1 · day 14 exists only for trait_a.
    await act(async () => {
      fireEvent.change(selects().wave, { target: { value: "2" } });
    });
    await act(async () => {
      fireEvent.change(selects().age, { target: { value: "21" } });
    });
    await changeTrait("trait_b");
    await changeTrait("trait_a");
    expect(selects().wave.value).toBe("2");
    expect(selects().age.value).toBe("21");
  });

  it("keeps the old behaviour on a visit without parameters: no note on a trait change", async () => {
    await renderExplorer({ traitNames: ["young_a", "young_b"] });
    expect(selects().wave.value).toBe("3");
    expect(selects().age.value).toBe("7");
    await changeTrait("young_b");
    expect(selects().wave.value).toBe("3");
    expect(note()).toBe("");
  });

  it("returns to the run's wave and age after a detour through a trait that lacks them", async () => {
    await renderExplorer({ initialWave: 1, initialAge: 14 });
    await changeTrait("trait_b");
    expect(selects().wave.value).toBe("3");
    await changeTrait("trait_a");
    expect(selects().wave.value).toBe("1");
    expect(selects().age.value).toBe("14");
    expect(note()).toBe("");
  });

  it("says so when the linked trait has no data, and still honours the link afterwards", async () => {
    await renderExplorer({
      traitNames: ["empty", "trait_a"],
      initialWave: 1,
      initialAge: 14,
    });
    expect(note()).toBe("Wave 1 · day 14 has no data for this trait.");
    await changeTrait("trait_a");
    expect(selects().wave.value).toBe("1");
    expect(selects().age.value).toBe("14");
    expect(note()).toBe("");
  });

  it("clears the note when the user picks a wave or age", async () => {
    await renderExplorer({ initialWave: 9, initialAge: 14 });
    expect(note()).not.toBe("");
    await act(async () => {
      fireEvent.change(selects().wave, { target: { value: "1" } });
    });
    expect(note()).toBe("");
  });

  it("keeps the note's status region mounted so screen readers announce it", async () => {
    await renderExplorer();
    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
  });

  it("disables the wave and age pickers while a trait is loading", async () => {
    await renderExplorer();
    let release!: () => void;
    gate.next = new Promise<void>((resolve) => {
      release = resolve;
    });
    await changeTrait("trait_b");
    expect(selects().wave.disabled).toBe(true);
    expect(selects().age.disabled).toBe(true);
    await act(async () => {
      release();
    });
    expect(selects().wave.disabled).toBe(false);
  });

  it("explains a wave-only link whose wave does not exist", async () => {
    await renderExplorer({ initialWave: 9 });
    expect(note()).toBe("Wave 9 has no data for this trait; showing wave 3 · day 21.");
  });

  it("treats a failed load as no data and keeps the link for the next trait", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    failing.add("trait_b");
    await renderExplorer({
      traitNames: ["trait_b", "trait_a"],
      initialWave: 1,
      initialAge: 14,
    });
    expect(note()).toBe("Wave 1 · day 14 has no data for this trait.");
    await changeTrait("trait_a");
    expect(selects().wave.value).toBe("1");
    expect(selects().age.value).toBe("14");
    expect(note()).toBe("");
  });

  it("hides the note while the next trait loads", async () => {
    await renderExplorer({ initialWave: 9, initialAge: 14 });
    expect(note()).not.toBe("");
    let release!: () => void;
    gate.next = new Promise<void>((resolve) => {
      release = resolve;
    });
    await changeTrait("trait_b");
    expect(note()).toBe("");
    await act(async () => {
      release();
    });
  });
});

describe("TraitExplorer's Download traits button", () => {
  const exportButton = () => screen.getByTestId("export") as HTMLButtonElement;
  const exportTarget = () => JSON.parse(exportButton().dataset.target!);

  it("is disabled until the first trait's waves and ages have loaded", async () => {
    let release!: () => void;
    gate.next = new Promise<void>((resolve) => {
      release = resolve;
    });
    await renderExplorer();
    expect(exportButton().disabled).toBe(true);
    await act(async () => {
      release();
    });
    expect(exportButton().disabled).toBe(false);
  });

  it("passes the experiment, the current wave and age, and the loaded lists", async () => {
    await renderExplorer();
    expect(exportTarget()).toEqual({ experimentId: 5, wave: 3, age: 21, waves: [1, 2, 3], ages: [7, 14, 21] });
    await act(async () => {
      fireEvent.change(selects().wave, { target: { value: "1" } });
    });
    expect(exportTarget()).toMatchObject({ wave: 1 });
  });

  it("is disabled again while a trait change reloads", async () => {
    await renderExplorer();
    let release!: () => void;
    gate.next = new Promise<void>((resolve) => {
      release = resolve;
    });
    await changeTrait("trait_b");
    expect(exportButton().disabled).toBe(true);
    await act(async () => {
      release();
    });
    expect(exportButton().disabled).toBe(false);
    expect(exportTarget()).toMatchObject({ waves: [2, 3], ages: [21] });
  });

  it("is enabled with empty lists for a trait with no data", async () => {
    await renderExplorer({ traitNames: ["empty"] });
    expect(exportButton().disabled).toBe(false);
    expect(exportTarget()).toEqual({ experimentId: 5, wave: 0, age: 0, waves: [], ages: [] });
  });
});
