/** Genes named in a link to a dataset's expression page (`?gene=`), matched to the dataset's own names. */

import { MAX_GENES } from "@/components/expression-lib/gene-stats";

/** Gene search results read per linked name; the exact name sorts ahead of longer ones. */
const SEARCH_LIMIT = 10;

/** The names in `gene`, repeated or comma-separated, without blanks or repeats, up to MAX_GENES. */
export function parseGeneParam(value: string | string[] | undefined): string[] {
  const parts = (Array.isArray(value) ? value : [value ?? ""]).flatMap((v) => v.split(","));
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of parts) {
    const name = raw.trim();
    const key = name.toLowerCase();
    if (!name || seen.has(key)) continue;
    seen.add(key);
    out.push(name);
  }
  return out.slice(0, MAX_GENES);
}

/** Each name as the dataset spells it, matched as a whole name ignoring case, as the page's gene
 *  search finds it; names the dataset doesn't have (or whose search failed) are `notFound`. */
export async function resolveLinkedGenes(
  names: readonly string[],
  search: (q: string, limit: number) => Promise<string[]>,
): Promise<{ genes: string[]; notFound: string[] }> {
  const found = await Promise.all(
    names.map(async (name) => {
      try {
        const hits = await search(name, SEARCH_LIMIT);
        return hits.find((g) => g.toLowerCase() === name.toLowerCase()) ?? null;
      } catch {
        return null;
      }
    }),
  );
  const genes: string[] = [];
  const notFound: string[] = [];
  found.forEach((gene, i) => {
    if (gene === null) notFound.push(names[i]);
    else if (!genes.includes(gene)) genes.push(gene);
  });
  return { genes, notFound };
}
