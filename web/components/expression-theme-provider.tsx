"use client";

import { createTheme, ThemeProvider } from "@mui/material/styles";
import type { ReactNode } from "react";

/** Tailwind palette colors mirrored into MUI so the two systems stay in sync. */
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

/** Scoped MUI theme provider — wrap only the expression subtree. */
export function ExpressionThemeProvider({ children }: { children: ReactNode }) {
  return <ThemeProvider theme={expressionTheme}>{children}</ThemeProvider>;
}

export default ExpressionThemeProvider;
