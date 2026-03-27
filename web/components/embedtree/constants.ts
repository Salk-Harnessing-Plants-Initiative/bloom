export const SPECIES_COLORS: Record<string, string> = {
  arabidopsis: "#4FC3F7",
  pennycress: "#FFD54F",
  soybean: "#81C784",
  sorghum: "#EF5350",
};

export const SPECIES_GROUPS: Record<
  string,
  { family: string; type: string }
> = {
  arabidopsis: { family: "Brassicaceae", type: "Dicot" },
  pennycress: { family: "Brassicaceae", type: "Dicot" },
  soybean: { family: "Fabaceae", type: "Dicot" },
  sorghum: { family: "Poaceae", type: "Monocot" },
};

export function getSpeciesColor(species: string): string {
  return SPECIES_COLORS[species.toLowerCase()] ?? "#9E9E9E";
}

export function getSpeciesGroup(species: string) {
  return SPECIES_GROUPS[species.toLowerCase()] ?? { family: "Unknown", type: "Unknown" };
}

export const ALL_SPECIES = Object.keys(SPECIES_COLORS);

export const DISTANCE_METRICS = ["cosine", "euclidean", "correlation", "cityblock"] as const;
export type DistanceMetric = (typeof DISTANCE_METRICS)[number];
