"use server";

import { loadBrowserConfig, type LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes-server";
import { createServerSupabaseClient } from "@/lib/supabase/server";

/** The browser's configuration with freshly signed file URLs, for a page whose URLs are expiring. */
export async function freshBrowserConfig(): Promise<LoadedBrowserConfig> {
  return loadBrowserConfig(await createServerSupabaseClient());
}
