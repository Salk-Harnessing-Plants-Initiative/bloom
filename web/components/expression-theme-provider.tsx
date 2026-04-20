"use client";

import { createTheme, ThemeProvider } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import type { ReactNode } from "react";

/**
 * Tailwind palette colors we need MUI to match. Kept as a single source
 * of truth so the two systems stay in sync.
 *   lime-700  #4d7c0f  -> primary accent (buttons, toggles, sliders, checkboxes)
 *   stone-500 #78716c  -> secondary / muted text
 *   stone-50  #fafaf9  -> page background
 *   stone-900 #1c1917  -> primary text
 */
const tailwindLime700 = "#4d7c0f";
const tailwindLime800 = "#3f6212";
const tailwindStone500 = "#78716c";
const tailwindStone200 = "#e7e5e4";
const tailwindStone900 = "#1c1917";

const expressionTheme = createTheme({
  palette: {
    primary: {
      main: tailwindLime700,
      dark: tailwindLime800,
      contrastText: "#ffffff",
    },
    secondary: {
      main: tailwindStone500,
      contrastText: "#ffffff",
    },
    text: {
      primary: tailwindStone900,
      secondary: tailwindStone500,
    },
    divider: tailwindStone200,
  },
  shape: {
    borderRadius: 6,
  },
  typography: {
    fontFamily: [
      "ui-sans-serif",
      "system-ui",
      "-apple-system",
      "BlinkMacSystemFont",
      '"Segoe UI"',
      "Roboto",
      '"Helvetica Neue"',
      "Arial",
      "sans-serif",
    ].join(","),
  },
  components: {
    MuiButtonBase: {
      defaultProps: { disableRipple: false },
    },
    MuiToggleButton: {
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 500,
        },
      },
    },
  },
});

/**
 * Scoped MUI theme provider. Wrap only the expression subtree so we don't
 * disturb MUI defaults used by the rest of the app.
 */
export function ExpressionThemeProvider({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider theme={expressionTheme}>
      {/* Not calling CssBaseline at scope level to avoid fighting the app's
          global body styling; rely on palette + component overrides only. */}
      {children}
    </ThemeProvider>
  );
}

export default ExpressionThemeProvider;
