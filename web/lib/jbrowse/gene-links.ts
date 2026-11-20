import type { SupabaseClient } from "@supabase/supabase-js";

import type { Database } from "@/lib/database.types";

import { assemblyName } from "./bloom-genomes";

type Client = SupabaseClient<Database>;

/** Where a gene lies in one browsable Bloom genome version (1-based, both ends included). */
export interface GenePlace {
  gene: string;
  name: string | null;
  assembly: string;
  label: string;
  contig: string;
  start: number;
  end: number;
}

/** A genome version's ready browser build, as the browser names it. */
export interface ReadyBuild {
  buildId: number;
  assembly: string;
  label: string;
}

export type NoPlaceReason = "no genome version" | "no browser files" | "unavailable";

/** What a gene without a browser link says on hover. */
export const NO_PLACE_TEXT: Record<NoPlaceReason, string> = {
  "no genome version": "This dataset does not record its genome version.",
  "no browser files": "This dataset's genome version has no browser files yet.",
  "unavailable": "The dataset's genome could not be looked up.",
};

/** Most genes asked about in one request, to keep the URL short. */
export const GENE_BATCH = 200;

const GENE_COLUMNS = "gene_id, gene_name, contig, gene_start, gene_end";
const WHERE_COLUMNS =
  "genome_browser_builds!inner(status, genome_reference_versions!inner(version, status, genome_references!inner(name)))";

/** Characters that would change a PostgREST `or` filter; a search holding one is not run. */
const FILTER_SYNTAX = /[,()*\\"]/;

/** The browser opened on the gene's genome version at its span. */
export function browserLink(place: Pick<GenePlace, "assembly" | "contig" | "start" | "end">): string {
  const loc = `${place.contig}:${place.start}..${place.end}`;
  return `/app/jbrowse?genome=${encodeURIComponent(place.assembly)}&loc=${encodeURIComponent(loc)}`;
}

/** The browser opened on a genome version at a gene, which the page places from its ID or name. */
export function geneInGenomeLink(assembly: string, gene: string): string {
  return `/app/jbrowse?genome=${encodeURIComponent(assembly)}&loc=${encodeURIComponent(gene)}`;
}

/** A location the browser reads as is (`contig:start..end`), not a gene to look up. */
export function isRegion(loc: string): boolean {
  return loc.includes(":");
}

/** Bloom's OrthoFinder page searching the gene. */
export function orthobrowserLink(gene: string): string {
  return `/app/orthofinder?search=${encodeURIComponent(gene)}`;
}

/** The genome version a single-cell dataset was mapped to; null when it records none. */
export async function datasetGenome(supabase: Client, datasetId: number): Promise<number | null> {
  const { data, error } = await supabase
    .from("scrna_datasets")
    .select("genome_version_id")
    .eq("id", datasetId)
    .maybeSingle();
  if (error) throw new Error(`reading dataset ${datasetId}: ${error.message}`);
  return data?.genome_version_id ?? null;
}

/** A genome version's ready build, when the version itself is ready; null otherwise. */
export async function readyBuild(supabase: Client, versionId: number): Promise<ReadyBuild | null> {
  const { data, error } = await supabase
    .from("genome_browser_builds")
    .select("id, genome_reference_versions!inner(version, status, genome_references!inner(name))")
    .eq("version_id", versionId)
    .eq("status", "ready")
    .eq("genome_reference_versions.status", "ready")
    .maybeSingle();
  if (error) throw new Error(`reading the browser build of version ${versionId}: ${error.message}`);
  if (!data) return null;
  const v = (data as unknown as { genome_reference_versions: { version: number; genome_references: { name: string } } })
    .genome_reference_versions;
  const name = v.genome_references.name;
  return { buildId: data.id, assembly: assemblyName(name, v.version), label: `${name} v${v.version}` };
}

interface GeneRow {
  gene_id: string;
  gene_name: string | null;
  contig: string;
  gene_start: number;
  gene_end: number;
}

const toPlace = (row: GeneRow, build: Omit<ReadyBuild, "buildId">): GenePlace => ({
  gene: row.gene_id,
  name: row.gene_name,
  assembly: build.assembly,
  label: build.label,
  contig: row.contig,
  start: row.gene_start,
  end: row.gene_end,
});

const batches = <T,>(items: T[]) =>
  Array.from({ length: Math.ceil(items.length / GENE_BATCH) }, (_, i) =>
    items.slice(i * GENE_BATCH, (i + 1) * GENE_BATCH),
  );

/** Where each of `genes` lies in one build: by gene ID, else by a gene name only one gene has. */
export async function placeGenes(
  supabase: Client,
  build: ReadyBuild,
  genes: string[],
): Promise<Map<string, GenePlace>> {
  const wanted = [...new Set(genes.filter(Boolean))];
  const places = new Map<string, GenePlace>();
  const read = async (column: "gene_id" | "gene_name", values: string[]) => {
    const rows: GeneRow[] = [];
    for (const batch of batches(values)) {
      const { data, error } = await supabase
        .from("genome_browser_genes")
        .select(GENE_COLUMNS)
        .eq("browser_build_id", build.buildId)
        .in(column, batch);
      if (error) throw new Error(`reading gene positions: ${error.message}`);
      rows.push(...((data ?? []) as GeneRow[]));
    }
    return rows;
  };

  for (const row of await read("gene_id", wanted)) places.set(row.gene_id, toPlace(row, build));
  const byName = new Map<string, GeneRow[]>();
  for (const row of await read("gene_name", wanted.filter((g) => !places.has(g)))) {
    if (row.gene_name) byName.set(row.gene_name, [...(byName.get(row.gene_name) ?? []), row]);
  }
  for (const [name, rows] of byName) if (rows.length === 1) places.set(name, toPlace(rows[0], build));
  return places;
}

type WhereRow = GeneRow & {
  genome_browser_builds: {
    genome_reference_versions: { version: number; genome_references: { name: string } };
  };
};

const whereRows = (data: unknown): GenePlace[] =>
  ((data ?? []) as WhereRow[])
    .map((row) => {
      const v = row.genome_browser_builds.genome_reference_versions;
      const name = v.genome_references.name;
      return toPlace(row, { assembly: assemblyName(name, v.version), label: `${name} v${v.version}` });
    })
    .sort((a, b) => a.assembly.localeCompare(b.assembly, undefined, { numeric: true }) || a.gene.localeCompare(b.gene));

/** Every browsable Bloom genome version that holds the gene ID. */
export async function placeGeneEverywhere(supabase: Client, gene: string): Promise<GenePlace[]> {
  const { data, error } = await supabase
    .from("genome_browser_genes")
    .select(`${GENE_COLUMNS}, ${WHERE_COLUMNS}`)
    .eq("gene_id", gene)
    .eq("genome_browser_builds.status", "ready")
    .eq("genome_browser_builds.genome_reference_versions.status", "ready");
  if (error) throw new Error(`reading where ${gene} lies: ${error.message}`);
  return whereRows(data);
}

/** Every browsable Bloom genome version holding each gene ID, in batches. */
export async function placeGenesEverywhere(
  supabase: Client,
  genes: string[],
): Promise<Map<string, GenePlace[]>> {
  const found = new Map<string, GenePlace[]>();
  for (const batch of batches([...new Set(genes.filter(Boolean))])) {
    const { data, error } = await supabase
      .from("genome_browser_genes")
      .select(`${GENE_COLUMNS}, ${WHERE_COLUMNS}`)
      .in("gene_id", batch)
      .eq("genome_browser_builds.status", "ready")
      .eq("genome_browser_builds.genome_reference_versions.status", "ready");
    if (error) throw new Error(`reading gene positions: ${error.message}`);
    for (const place of whereRows(data)) found.set(place.gene, [...(found.get(place.gene) ?? []), place]);
  }
  return found;
}

/** Genes in browsable Bloom genome versions whose ID or name contains `text`; only in `builds`
 * when given. */
export async function searchGenes(
  supabase: Client,
  text: string,
  limit: number,
  builds: number[] | null = null,
): Promise<GenePlace[]> {
  const term = text.trim();
  if (term.length < 2 || FILTER_SYNTAX.test(term)) return [];
  let query = supabase
    .from("genome_browser_genes")
    .select(`${GENE_COLUMNS}, ${WHERE_COLUMNS}`)
    .or(`gene_id.ilike.*${term}*,gene_name.ilike.*${term}*`)
    .eq("genome_browser_builds.status", "ready")
    .eq("genome_browser_builds.genome_reference_versions.status", "ready");
  if (builds) query = query.in("browser_build_id", builds);
  const { data, error } = await query.limit(limit);
  if (error) throw new Error(`searching genes: ${error.message}`);
  return whereRows(data);
}
