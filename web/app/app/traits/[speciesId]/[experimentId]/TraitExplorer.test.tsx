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
}));

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    rpc: async (_fn: string, args: { trait_name_: string }) => ({
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
    }),
  }),
}));
vi.mock("@/components/scan-trait-boxplot", () => ({
  default: ({ traitData }: { traitData: unknown[] }) => (
    <div data-testid="boxplot">{traitData.length}</div>
  ),
}));

import TraitExplorer from "./TraitExplorer";

afterEach(cleanup);

async function renderExplorer(props: { initialWave?: number; initialAge?: number } = {}) {
  await act(async () => {
    render(
      <TraitExplorer
        experimentId={5}
        traitNames={["trait_a", "trait_b"]}
        defaultTraitName="trait_a"
        {...props}
      />,
    );
  });
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
    expect(screen.queryByRole("status")).toBeNull();
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
    expect(screen.getByRole("status").textContent).toBe(
      "Wave 9 · day 14 has no data for this trait; showing wave 3 · day 21.",
    );
  });

  it("keeps the current wave and age on a trait change when the new trait has them", async () => {
    await renderExplorer({ initialWave: 2, initialAge: 21 });
    await changeTrait("trait_b");
    expect(selects().wave.value).toBe("2");
    expect(selects().age.value).toBe("21");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("falls back with a note on a trait change when the new trait lacks them", async () => {
    await renderExplorer({ initialWave: 1, initialAge: 14 });
    await changeTrait("trait_b");
    expect(selects().wave.value).toBe("3");
    expect(selects().age.value).toBe("21");
    expect(screen.getByRole("status").textContent).toBe(
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
});
