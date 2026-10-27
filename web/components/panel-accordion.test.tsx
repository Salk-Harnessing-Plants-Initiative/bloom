// @vitest-environment jsdom
/** The Timeline page's stacked panels: one open at most, closing it, and only the open panel rendered. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import PanelAccordion from "./panel-accordion";

afterEach(cleanup);

const PANELS = [
  { id: "a", label: "Cylinder scanners" },
  { id: "b", label: "Plate scanners" },
  { id: "c", label: "RNA-seq runs" },
];

function renderAccordion(openId: string | null = "a") {
  const onOpen = vi.fn<(id: string) => void>();
  const onClose = vi.fn<(id: string) => void>();
  render(
    <PanelAccordion panels={PANELS} openId={openId} onOpen={onOpen} onClose={onClose}>
      <p>Open panel content</p>
    </PanelAccordion>
  );
  return { onOpen, onClose };
}

function header(label: string) {
  return screen.getByRole("button", { name: label });
}

describe("PanelAccordion", () => {
  it("shows a header for every panel, in order, with only the open one expanded", () => {
    renderAccordion("b");
    expect(PANELS.map((p) => header(p.label).getAttribute("aria-expanded"))).toEqual([
      "false",
      "true",
      "false",
    ]);
  });

  it("renders the content only in the open panel", () => {
    renderAccordion("b");
    expect(screen.getAllByText("Open panel content")).toHaveLength(1);
    const content = screen.getByText("Open panel content");
    expect(content.closest("#panel-b")).toBeTruthy();
  });

  it("asks to open a closed panel when its header is clicked", () => {
    const { onOpen } = renderAccordion("a");
    fireEvent.click(header("RNA-seq runs"));
    expect(onOpen).toHaveBeenCalledWith("c");
  });

  it("asks to close the open panel when its own header is clicked", () => {
    const { onOpen, onClose } = renderAccordion("a");
    fireEvent.click(header("Cylinder scanners"));
    expect(onClose).toHaveBeenCalledWith("a");
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("shows every panel closed, with no content, when none is open", () => {
    renderAccordion(null);
    expect(PANELS.map((p) => header(p.label).getAttribute("aria-expanded"))).toEqual([
      "false",
      "false",
      "false",
    ]);
    expect(screen.queryByText("Open panel content")).toBeNull();
  });

  it("links each header to its panel for screen readers", () => {
    renderAccordion("a");
    expect(header("Plate scanners").getAttribute("aria-controls")).toBe("panel-b");
  });
});
