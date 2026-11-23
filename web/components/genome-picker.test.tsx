// @vitest-environment jsdom
/** The genome selector beside the gene search. */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GenomePicker } from "./genome-picker";

const choices = {
  bloom: [{ name: "a.v1", label: "a v1 (Arabidopsis thaliana)" }],
  outside: [{ name: "Zmays.B73.HPIv1", label: "Zmays.B73.HPIv1" }],
};

afterEach(cleanup);

describe("GenomePicker", () => {
  it("lists Bloom's genomes and the other HPI genomes in two groups", () => {
    render(<GenomePicker choices={choices} value="" onChoose={vi.fn()} />);
    const groups = [...document.querySelectorAll("optgroup")].map((g) => [
      g.label,
      [...g.querySelectorAll("option")].map((o) => o.textContent),
    ]);
    expect(groups).toEqual([
      ["Bloom genomes", ["a v1 (Arabidopsis thaliana)"]],
      ["Other HPI genomes", ["Zmays.B73.HPIv1"]],
    ]);
  });

  it("shows the open genome, or asks for one", () => {
    const { rerender } = render(<GenomePicker choices={choices} value="a.v1" onChoose={vi.fn()} />);
    expect((screen.getByLabelText("Genome") as HTMLSelectElement).value).toBe("a.v1");
    rerender(<GenomePicker choices={choices} value="" onChoose={vi.fn()} />);
    expect((screen.getByLabelText("Genome") as HTMLSelectElement).value).toBe("");
    expect(screen.getByRole("option", { name: "Choose a genome…" })).toBeTruthy();
  });

  it("hands over the chosen genome", () => {
    const onChoose = vi.fn();
    render(<GenomePicker choices={choices} value="" onChoose={onChoose} />);
    fireEvent.change(screen.getByLabelText("Genome"), { target: { value: "Zmays.B73.HPIv1" } });
    expect(onChoose).toHaveBeenCalledWith("Zmays.B73.HPIv1");
  });

  it("leaves out the Bloom group when Bloom offers no genome, and can be turned off", () => {
    render(<GenomePicker choices={{ bloom: [], outside: choices.outside }} value="" onChoose={vi.fn()} disabled />);
    expect([...document.querySelectorAll("optgroup")].map((g) => g.label)).toEqual(["Other HPI genomes"]);
    expect((screen.getByLabelText("Genome") as HTMLSelectElement).disabled).toBe(true);
  });
});
