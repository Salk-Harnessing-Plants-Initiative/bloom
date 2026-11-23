/** The genome browser in Bloom's look: lime accents on warm stone, in the app's own font. */

/** Tailwind's sans-serif stack, which the rest of Bloom is set in. */
export const BLOOM_FONT =
  'ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji"';

/** JBrowse's custom-logo image (`@jbrowse/core/ui/AppLogo`), drawn at its own size. */
export const LOGO_SELECTOR = 'img[alt="Custom logo"]';

/** JBrowse's `configuration.theme`: Tailwind lime-800, lime-700 and stone-600, with a pale
 * stone-100 header bar and lime-800 menu text. */
export const BLOOM_THEME = {
  palette: {
    primary: { main: "#3f6212" },
    secondary: { main: "#4d7c0f" },
    tertiary: { main: "#57534e" },
  },
  typography: { fontFamily: BLOOM_FONT },
  components: {
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: "#f5f5f4",
          color: "#3f6212",
          boxShadow: "none",
          borderBottom: "1px solid #e7e5e4",
          // No logo in the browser's header; Bloom's page already shows one.
          [`& ${LOGO_SELECTOR}`]: { display: "none" },
        },
      },
    },
  },
};

/** A custom logo, which the theme hides, so JBrowse doesn't draw its own white one instead. */
export const BLOOM_LOGO = { uri: "/logo-mark.png", locationType: "UriLocation" };

/** How genes are drawn: lime-700 exons, stone-500 introns, lime-300 UTRs. */
export const GENE_RENDERER = {
  type: "CanvasFeatureRenderer",
  color1: "#4d7c0f",
  color2: "#78716c",
  color3: "#bef264",
};

/** Chromosome name colours on the ruler, taken in turn: lime-700, stone-600. */
export const CONTIG_COLOURS = ["#4d7c0f", "#57534e"];

/** A gene track's display, drawing its genes in Bloom's colours. */
export function geneDisplays(trackId: string) {
  return [{ type: "LinearBasicDisplay", displayId: `${trackId}-LinearBasicDisplay`, renderer: GENE_RENDERER }];
}

interface Track {
  type?: string;
  trackId?: string;
  displays?: unknown;
  [key: string]: unknown;
}

/** The configuration with Bloom's theme, and Bloom's colours on assemblies and feature tracks that set none. */
export function withBloomLook<T extends { assemblies: unknown[]; tracks: unknown[]; configuration?: unknown }>(
  config: T,
): T {
  const configuration = (config.configuration ?? {}) as Record<string, unknown>;
  return {
    ...config,
    configuration: { ...configuration, theme: BLOOM_THEME, logoPath: BLOOM_LOGO },
    assemblies: (config.assemblies as Record<string, unknown>[]).map((assembly) =>
      assembly.refNameColors ? assembly : { ...assembly, refNameColors: CONTIG_COLOURS },
    ),
    tracks: (config.tracks as Track[]).map((track) =>
      track.type === "FeatureTrack" && track.trackId && !track.displays
        ? { ...track, displays: geneDisplays(track.trackId) }
        : track,
    ),
  };
}
