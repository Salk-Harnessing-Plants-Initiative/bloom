// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { TransgeneSummary } from "./transgene-summary";

afterEach(cleanup);

describe("the transgene summary", () => {
  it("says how many cells carry the transgene, out of how many, and where most are", () => {
    render(
      <TransgeneSummary
        positive={232}
        total={8683}
        totalNoun="cells"
        top={[{ name: "Phellem", positive: 82 }, { name: "Columella", positive: 40 }]}
      />,
    );
    const text = screen.getByTestId("transgene-summary").textContent ?? "";
    expect(text).toContain("232 transgene+");
    expect(text).toContain("of 8,683 cells (2.7%)");
    expect(text).toContain("most in Phellem (82), Columella (40)");
  });

  it("leaves out where they are when no group is named", () => {
    render(<TransgeneSummary positive={5} total={10} totalNoun="cells" top={[]} />);
    expect(screen.getByTestId("transgene-summary").textContent).not.toContain("most in");
  });
});
