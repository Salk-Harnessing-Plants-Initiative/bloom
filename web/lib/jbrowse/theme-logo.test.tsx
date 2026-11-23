// @vitest-environment jsdom
/** The browser's header shows no logo: Bloom's page already carries one. Checked against
 * JBrowse's own logo and theme, so a JBrowse upgrade that changes either is caught here. */

import AppLogo from "@jbrowse/core/ui/AppLogo";
import { createJBrowseTheme } from "@jbrowse/core/ui/theme";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { BLOOM_LOGO, BLOOM_THEME, LOGO_SELECTOR } from "./theme";

afterEach(cleanup);

describe("the header logo", () => {
  it("is set, so JBrowse draws the image the theme hides instead of its own logo", () => {
    const session = { configuration: { logoPath: BLOOM_LOGO } };
    const { container } = render(<AppLogo session={session as never} />);
    const logo = container.querySelector("img");
    expect(logo?.getAttribute("src")).toBe(BLOOM_LOGO.uri);
    expect(logo?.matches(LOGO_SELECTOR)).toBe(true);
  });

  it("stays hidden once JBrowse builds the theme", () => {
    const theme = createJBrowseTheme(BLOOM_THEME) as unknown as {
      components: { MuiAppBar: { styleOverrides: { root: Record<string, unknown> } } };
    };
    expect(theme.components.MuiAppBar.styleOverrides.root[`& ${LOGO_SELECTOR}`]).toEqual({ display: "none" });
  });
});
