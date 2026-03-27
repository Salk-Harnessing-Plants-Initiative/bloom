import { type TreeNode, getLeaves, getSpeciesFromLeaf, findMRCA, countTerminals } from "./newick";
import { SPECIES_GROUPS } from "../constants";

export interface MonophylyResult {
  family: string;
  status: "confirmed" | "not_confirmed" | "insufficient";
  message: string;
}

export function checkFamilyMonophyly(tree: TreeNode): MonophylyResult[] {
  const leaves = getLeaves(tree);
  const results: MonophylyResult[] = [];

  // Group species by family
  const familySpecies: Record<string, string[]> = {};
  for (const [species, group] of Object.entries(SPECIES_GROUPS)) {
    if (!familySpecies[group.family]) familySpecies[group.family] = [];
    familySpecies[group.family].push(species);
  }

  for (const [family, species] of Object.entries(familySpecies)) {
    const familyLeaves = leaves.filter((l) => species.includes(getSpeciesFromLeaf(l.name)));
    const speciesPresent = new Set(familyLeaves.map((l) => getSpeciesFromLeaf(l.name)));

    if (speciesPresent.size < 2) continue;

    const familyLeafNames = new Set(familyLeaves.map((l) => l.name));
    const mrca = findMRCA(tree, familyLeafNames);

    if (!mrca) {
      results.push({ family, status: "not_confirmed", message: `${family}: could not find MRCA` });
      continue;
    }

    const terminalCount = countTerminals(mrca);
    const speciesNames = [...speciesPresent].join(", ");

    if (terminalCount === familyLeafNames.size) {
      results.push({
        family,
        status: "confirmed",
        message: `${family} monophyly confirmed: all ${terminalCount} genes (${speciesNames}) cluster together`,
      });
    } else {
      const nonFamily = terminalCount - familyLeafNames.size;
      results.push({
        family,
        status: "not_confirmed",
        message: `${family} monophyly not confirmed: ${nonFamily} non-${family} gene(s) interleaved among ${familyLeafNames.size} genes (${speciesNames})`,
      });
    }
  }

  return results;
}
