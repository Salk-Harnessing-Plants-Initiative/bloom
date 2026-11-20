import type { SupabaseClient } from "@supabase/supabase-js";

import type { Database } from "@/lib/database.types";
import { scientificName } from "@/lib/species-options";
import { toPublicStorageUrl } from "@/lib/supabase/storage-url";

import {
  BUILD_ROLES,
  assemblyName,
  mergeBrowserConfig,
  parseGenomeVersion,
  unavailableReason,
  type BloomGenome,
  type LoadedBrowserConfig,
  type UnavailableReason,
} from "./bloom-genomes";
import { browserConfig } from "./config";

/** How long a signed build-file URL works, in seconds. */
export const SIGNED_URL_TTL_S = 3600;

/** How long the browser may reuse a file's redirect, in seconds; below the TTL so a cached
 * redirect never points at an expired signature. */
export const REDIRECT_MAX_AGE_S = SIGNED_URL_TTL_S - 300;

const BUCKET = "genome-references";

/** The build read: the build, its version, the genome and its species. `!inner` with the two
 * status filters keeps only ready builds of ready versions. */
export const BROWSABLE_SELECT =
  "id, genome_reference_versions!inner(version, status, assembly, annotation, genome_references!inner(name, species(genus, species)))";

/** The file route's build read: the folder of a ready build whose version is ready. `!inner`
 * makes the version's status filter drop the build, so a withdrawn version's files are never
 * signed. */
export const SIGNABLE_BUILD_SELECT = "folder, genome_reference_versions!inner(status)";

/** The link lookup: a version by genome name and number, with its builds' statuses. */
export const LINKED_VERSION_SELECT = "status, genome_references!inner(name), genome_browser_builds(status)";

type Client = SupabaseClient<Database>;

interface BuildRow {
  id: number;
  genome_reference_versions: {
    version: number;
    assembly: string | null;
    annotation: string | null;
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
    .select(BROWSABLE_SELECT)
    .eq("status", "ready")
    .eq("genome_reference_versions.status", "ready");
  if (error) throw new Error(`reading browser builds: ${error.message}`);
  // supabase-js types a nested `!inner` embed as possibly an array; it is one row here.
  return ((data ?? []) as unknown as BuildRow[])
    .map((row) => {
      const v = row.genome_reference_versions;
      const species = v.genome_references.species;
      return {
        buildId: row.id,
        genome: v.genome_references.name,
        version: v.version,
        assembly: v.assembly,
        annotation: v.annotation,
        species: (species && scientificName(species)) || null,
      };
    })
    .sort((a, b) => a.genome.localeCompare(b.genome) || a.version - b.version);
}

/** The outside assemblies plus Bloom's browsable genomes. A failure is logged and leaves the
 * outside assemblies with a short reason. */
export async function loadBrowserConfig(supabase: Client): Promise<LoadedBrowserConfig> {
  const outside = browserConfig();
  try {
    const genomes = await fetchBrowsableGenomes(supabase);
    return {
      config: mergeBrowserConfig(outside, genomes),
      bloomGenomes: genomes.map((g) => assemblyName(g.genome, g.version)),
      error: null,
    };
  } catch (err) {
    console.error("[genome browser]", err);
    return { config: outside, bloomGenomes: [], error: "Bloom's own genomes could not be read." };
  }
}

/** Why the `?genome=<name>.v<N>` link's version is not offered; null when it is. */
export async function whyNotOffered(supabase: Client, link: string): Promise<UnavailableReason | null> {
  const parsed = parseGenomeVersion(link);
  if (!parsed) return "unknown";
  const { data, error } = await supabase
    .from("genome_reference_versions")
    .select(LINKED_VERSION_SELECT)
    .eq("genome_references.name", parsed.genome)
    .eq("version", parsed.version)
    .maybeSingle();
  if (error) throw new Error(`reading genome version ${link}: ${error.message}`);
  const row = data as unknown as { status: string; genome_browser_builds: { status: string }[] } | null;
  return unavailableReason(
    row ? { status: row.status, builds: row.genome_browser_builds.map((b) => b.status) } : null,
  );
}

export type SignedBuildFile =
  | { status: "signed"; url: string }
  | { status: "not found" }
  | { status: "failed"; reason: string };

/** One file of a ready build of a ready version, signed with the caller's credentials. */
export async function signBuildFile(
  supabase: Client,
  buildId: number,
  role: string,
): Promise<SignedBuildFile> {
  if (!(BUILD_ROLES as readonly string[]).includes(role)) return { status: "not found" };

  const { data: build, error } = await supabase
    .from("genome_browser_builds")
    .select(SIGNABLE_BUILD_SELECT)
    .eq("id", buildId)
    .eq("status", "ready")
    .eq("genome_reference_versions.status", "ready")
    .maybeSingle();
  if (error) return { status: "failed", reason: `reading build ${buildId}: ${error.message}` };
  if (!build) return { status: "not found" };

  const { data: roles, error: rolesError } = await supabase.rpc("genome_browser_build_roles");
  if (rolesError) return { status: "failed", reason: `reading file names: ${rolesError.message}` };
  const basename = roles?.find((r) => r.role === role)?.basename;
  if (!basename) return { status: "failed", reason: `no file name for role ${role}` };

  const path = `${build.folder}/${basename}`;
  const { data: signed, error: signError } = await supabase.storage
    .from(BUCKET)
    .createSignedUrl(path, SIGNED_URL_TTL_S);
  if (signError) return { status: "failed", reason: `signing ${path}: ${signError.message}` };
  const url = toPublicStorageUrl(signed?.signedUrl);
  return url ? { status: "signed", url } : { status: "failed", reason: `no signed URL for ${path}` };
}
