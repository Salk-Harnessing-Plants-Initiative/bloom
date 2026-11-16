import type { SupabaseClient } from "@supabase/supabase-js";

import type { Database } from "@/lib/database.types";
import { toPublicStorageUrl } from "@/lib/supabase/storage-url";

import {
  BUILD_ROLES,
  assemblyName,
  bloomGenomeConfig,
  mergeBrowserConfig,
  parseGenomeVersion,
  unavailableReason,
  type BloomGenome,
  type BuildFileUrls,
  type UnavailableReason,
} from "./bloom-genomes";
import { browserConfig, type BrowserConfig } from "./config";

/** How long a browser file URL stays valid; the page fetches fresh ones before then. */
export const BROWSER_URL_TTL = 3600;

const BUCKET = "genome-references";

type Client = SupabaseClient<Database>;

/** The browser's configuration and what it holds of Bloom's own genomes. */
export interface LoadedBrowserConfig {
  config: BrowserConfig;
  /** Assembly names of Bloom's genome versions in `config`. */
  bloomGenomes: string[];
  /** How long the signed URLs in `config` work from now, in ms; null when it has none.
   * A duration, so the browser times it on its own clock. */
  validForMs: number | null;
  /** Why Bloom's genomes could not be added; the outside assemblies are still in `config`. */
  error: string | null;
}

interface BuildRow {
  folder: string;
  genome_reference_versions: {
    version: number;
    genome_references: {
      name: string;
      species: { genus: string | null; species: string | null } | null;
    };
  };
}

/** Ready genome versions with a ready browser build, by genome name then version. */
export async function fetchBrowsableGenomes(supabase: Client): Promise<BloomGenome[]> {
  const { data, error } = await supabase
    .from("genome_browser_builds")
    .select(
      "folder, genome_reference_versions!inner(version, status, genome_references!inner(name, species(genus, species)))",
    )
    .eq("status", "ready")
    .eq("genome_reference_versions.status", "ready");
  if (error) throw new Error(`reading browser builds: ${error.message}`);
  return ((data ?? []) as unknown as BuildRow[])
    .map((row) => {
      const v = row.genome_reference_versions;
      const s = v.genome_references.species;
      return {
        genome: v.genome_references.name,
        version: v.version,
        species: [s?.genus, s?.species].filter(Boolean).join(" ") || null,
        folder: row.folder,
      };
    })
    .sort((a, b) => a.genome.localeCompare(b.genome) || a.version - b.version);
}

/** Each genome's five files signed with the caller's credentials, in one Storage call. */
export async function signBuildFiles(
  supabase: Client,
  genomes: BloomGenome[],
): Promise<BuildFileUrls[]> {
  if (genomes.length === 0) return [];
  const { data: roles, error: rolesError } = await supabase.rpc("genome_browser_build_roles");
  if (rolesError) throw new Error(`reading browser file names: ${rolesError.message}`);
  const basename = new Map((roles ?? []).map((r) => [r.role, r.basename]));
  const missing = BUILD_ROLES.filter((role) => !basename.has(role));
  if (missing.length) throw new Error(`no file name for browser roles: ${missing.join(", ")}`);

  const paths = genomes.flatMap((g) => BUILD_ROLES.map((role) => `${g.folder}/${basename.get(role)}`));
  const { data, error } = await supabase.storage.from(BUCKET).createSignedUrls(paths, BROWSER_URL_TTL);
  if (error) throw new Error(`signing browser files: ${error.message}`);
  const signed = new Map((data ?? []).map((entry) => [entry.path, entry]));

  return genomes.map((g, i) => {
    const urls = {} as BuildFileUrls;
    BUILD_ROLES.forEach((role, j) => {
      const path = paths[i * BUILD_ROLES.length + j];
      const url = toPublicStorageUrl(signed.get(path)?.signedUrl);
      if (!url) {
        const reason = signed.get(path)?.error ?? "no signed URL returned";
        throw new Error(`signing ${path}: ${reason}`);
      }
      urls[role] = url;
    });
    return urls;
  });
}

/** The outside assemblies plus Bloom's browsable genomes, with freshly signed file URLs. */
export async function loadBrowserConfig(supabase: Client): Promise<LoadedBrowserConfig> {
  const outside = browserConfig();
  try {
    const genomes = await fetchBrowsableGenomes(supabase);
    const urls = await signBuildFiles(supabase, genomes);
    return {
      config: mergeBrowserConfig(
        outside,
        genomes.map((g, i) => bloomGenomeConfig(g, urls[i])),
      ),
      bloomGenomes: genomes.map((g) => assemblyName(g.genome, g.version)),
      validForMs: genomes.length ? BROWSER_URL_TTL * 1000 : null,
      error: null,
    };
  } catch (err) {
    return {
      config: outside,
      bloomGenomes: [],
      validForMs: null,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

/** Why the `?genome=<name>.v<N>` link's version is not offered; null when it is. */
export async function whyNotOffered(supabase: Client, link: string): Promise<UnavailableReason | null> {
  const parsed = parseGenomeVersion(link);
  if (!parsed) return "unknown";
  const { data, error } = await supabase
    .from("genome_reference_versions")
    .select("status, genome_references!inner(name), genome_browser_builds(status)")
    .eq("genome_references.name", parsed.genome)
    .eq("version", parsed.version)
    .maybeSingle();
  if (error) throw new Error(`reading genome version ${link}: ${error.message}`);
  const row = data as unknown as { status: string; genome_browser_builds: { status: string }[] } | null;
  return unavailableReason(
    row ? { status: row.status, builds: row.genome_browser_builds.map((b) => b.status) } : null,
  );
}
