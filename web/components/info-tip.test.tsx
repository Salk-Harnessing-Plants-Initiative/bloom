// @vitest-environment jsdom
/** Bloom's info icon: green, labelled for screen readers, with its note in a tooltip. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import InfoTip from "./info-tip";

afterEach(cleanup);

describe("InfoTip", () => {
  it("is a focusable green ⓘ that screen readers announce by its label", () => {
    render(<InfoTip label="When to add a species">Add one here first.</InfoTip>);
    const icon = screen.getByRole("img", { name: "When to add a species" });
    expect(icon.textContent).toBe("i");
    expect(icon.getAttribute("tabindex")).toBe("0");
    expect(icon.className).toContain("border-lime-700");
    expect(icon.className).toContain("bg-lime-50");
    expect(icon.className).toContain("text-lime-800");
  });

  it("holds the note in a tooltip shown on hover or focus", () => {
    render(<InfoTip label="When to add a species">Add one here first.</InfoTip>);
    const note = screen.getByRole("tooltip");
    expect(note.textContent).toBe("Add one here first.");
    expect(note.className).toContain("group-hover/info:opacity-100");
    expect(note.className).toContain("group-focus-within/info:opacity-100");
    expect(note.className).toContain("left-0");
  });

  it("can line its note up with the right edge", () => {
    render(
      <InfoTip label="About this tab" align="right">
        Note
      </InfoTip>
    );
    expect(screen.getByRole("tooltip").className).toContain("right-0");
  });
});
