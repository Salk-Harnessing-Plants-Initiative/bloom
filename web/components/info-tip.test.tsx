// @vitest-environment jsdom
/** Bloom's info icon: green, labelled for screen readers, with its note on hover or focus. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import InfoTip from "./info-tip";

afterEach(cleanup);

const icon = () => screen.getByRole("img", { name: "When to add a species" });
const note = () => screen.queryByRole("tooltip");

function mount(align?: "left" | "right") {
  render(
    <InfoTip label="When to add a species" align={align}>
      Add one here first.
    </InfoTip>
  );
}

describe("InfoTip", () => {
  it("is a focusable green ⓘ that screen readers announce by its label", () => {
    mount();
    expect(icon().textContent).toBe("i");
    expect(icon().getAttribute("tabindex")).toBe("0");
    expect(icon().className).toContain("text-lime-800");
  });

  it("is closed, and hidden from screen readers, until hovered", () => {
    mount();
    expect(note()).toBeNull();
    fireEvent.mouseEnter(icon());
    expect(note()?.textContent).toBe("Add one here first.");
    fireEvent.mouseLeave(icon());
    expect(note()).toBeNull();
  });

  it("opens on keyboard focus, closes on Esc and on blur", () => {
    mount();
    fireEvent.focus(icon());
    expect(note()).not.toBeNull();
    fireEvent.keyDown(icon(), { key: "Escape" });
    expect(note()).toBeNull();
    fireEvent.focus(icon());
    fireEvent.blur(icon());
    expect(note()).toBeNull();
  });

  it("stays open while the pointer is on the note", () => {
    mount();
    fireEvent.mouseEnter(icon());
    fireEvent.mouseEnter(screen.getByRole("tooltip"));
    expect(note()).not.toBeNull();
  });

  it("gives the icon its note as its description", () => {
    mount();
    const tooltip = screen.getByRole("tooltip", { hidden: true });
    expect(icon().getAttribute("aria-describedby")).toBe(tooltip.id);
  });

  it("lines the note up with the left edge, or the right", () => {
    mount("right");
    expect(screen.getByRole("tooltip", { hidden: true }).className).toContain("right-0");
    cleanup();
    mount();
    expect(screen.getByRole("tooltip", { hidden: true }).className).toContain("left-0");
  });
});
