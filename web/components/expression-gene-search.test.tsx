// @vitest-environment jsdom
/** A failed gene search says so, rather than showing an empty list. */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const search = vi.hoisted(() => ({ fail: null as string | null }));

vi.mock("@/components/expression-lib/scrna-client", () => ({
  searchGenes: vi.fn(async () => {
    if (search.fail) throw new Error(search.fail);
    return ["AT1G01010"];
  }),
}));

import { ExpressionGeneSearch } from "./expression-gene-search";

beforeEach(() => {
  search.fail = null;
});
afterEach(cleanup);

/** Types into the box as a person would: focused first, since MUI resets an unfocused box. */
const type = (text: string) => {
  const box = screen.getByLabelText("Gene") as HTMLInputElement;
  act(() => box.focus());
  fireEvent.change(box, { target: { value: text } });
};

describe("ExpressionGeneSearch", () => {
  it("says the search failed, and why", async () => {
    search.fail = "statement timeout";
    render(<ExpressionGeneSearch datasetId={1} value={null} onChange={() => {}} />);
    type("AT1");
    expect(await screen.findByText("Could not search genes: statement timeout")).toBeTruthy();
  });

  it("drops the message once a search works again", async () => {
    search.fail = "statement timeout";
    render(<ExpressionGeneSearch datasetId={1} value={null} onChange={() => {}} />);
    type("AT1");
    await screen.findByText(/Could not search genes/);

    search.fail = null;
    type("AT1G");
    await waitFor(() => expect(screen.queryByText(/Could not search genes/)).toBeNull());
  });
});
