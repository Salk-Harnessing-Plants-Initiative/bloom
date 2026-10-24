// @vitest-environment jsdom
/** The ⓘ on the Species page opens a note on when to add a species. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import SpeciesInfoTip from "./species-info-tip";

afterEach(cleanup);

const tip = () => screen.queryByRole("tooltip");

describe("SpeciesInfoTip", () => {
  it("is closed until hovered, and says when to add a species", () => {
    render(<SpeciesInfoTip />);
    expect(tip()).toBeNull();
    fireEvent.mouseEnter(screen.getByRole("button", { name: "When to add a species" }));
    expect(tip()?.textContent).toBe(
      "Add a species here before you use it in:• Bloom Desktop, for cylinder or plate scans• an Expression analysis"
    );
    fireEvent.mouseLeave(screen.getByRole("button", { name: "When to add a species" }));
    expect(tip()).toBeNull();
  });

  it("opens and closes on click too", () => {
    render(<SpeciesInfoTip />);
    const button = screen.getByRole("button", { name: "When to add a species" });
    fireEvent.click(button);
    expect(tip()).not.toBeNull();
    expect(button.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(button);
    expect(tip()).toBeNull();
  });
});
