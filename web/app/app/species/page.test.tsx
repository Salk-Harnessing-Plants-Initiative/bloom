// @vitest-environment jsdom
/** The Species page lists the non-deleted species, and shows the error when it can't. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

type Result = { data: unknown; error: { message: string } | null };
let result: Result;
const calls: { method: string; args: unknown[] }[] = [];

// A Supabase query chain that records its calls and resolves to `result`.
const query = {
  from: (...args: unknown[]) => (calls.push({ method: "from", args }), query),
  select: (...args: unknown[]) => (calls.push({ method: "select", args }), query),
  is: (...args: unknown[]) => (calls.push({ method: "is", args }), Promise.resolve(result)),
};

vi.mock("@/lib/supabase/client", () => ({ createClientSupabaseClient: () => ({}) }));
vi.mock("@/lib/supabase/server", () => ({
  createServerSupabaseClient: async () => query,
  getUser: async () => ({ id: "u-1", email: "scientist@example.org" }),
}));

import SpeciesPage from "./page";

beforeEach(() => {
  calls.length = 0;
});
afterEach(cleanup);

describe("the Species page", () => {
  it("lists the species that haven't been removed, sorted", async () => {
    result = {
      data: [
        { id: 4, common_name: "Rice", genus: "Oryza", species: "sativa" },
        { id: 1, common_name: "Arabidopsis", genus: "arabidopsis", species: "Thaliana" },
      ],
      error: null,
    };
    render(await SpeciesPage());

    expect(screen.getByRole("heading", { name: "Species" })).toBeTruthy();
    const items = within(screen.getByRole("list", { name: "Species" }))
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(items).toEqual(["Arabidopsis (Arabidopsis thaliana)", "Rice (Oryza sativa)"]);
    expect(calls).toEqual([
      { method: "from", args: ["species"] },
      { method: "select", args: ["id, common_name, genus, species"] },
      { method: "is", args: ["deleted_at", null] },
    ]);
  });

  it("shows the error, not an empty list, when the species can't be loaded", async () => {
    result = { data: null, error: { message: "connection refused" } };
    render(await SpeciesPage());

    expect(screen.getByRole("alert").textContent).toBe("Couldn't load the species: connection refused");
    expect(screen.queryByText("No species yet.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a species" })).toBeNull();
  });
});
