import { NextResponse } from "next/server";

import { REDIRECT_MAX_AGE_S, buildIsReady, readBuildGeneStarts } from "@/lib/jbrowse/bloom-genomes-server";
import { densityBedGraph } from "@/lib/jbrowse/gene-density";
import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";

/** Builds whose density is kept in memory; a ready build's genes never change. */
const CACHED_BUILDS = 50;
const cache = new Map<number, string>();

/** Empties the in-memory density cache (for tests). */
export function clearDensityCache(): void {
  cache.clear();
}

const bedGraph = (text: string) =>
  new NextResponse(text, {
    status: 200,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": `private, max-age=${REDIRECT_MAX_AGE_S}` },
  });

/** A ready browser build's gene density as bedGraph, for the browser's density track. */
export async function GET(_request: Request, { params }: { params: Promise<{ buildId: string }> }) {
  const { buildId } = await params;
  const id = Number(buildId);
  if (!/^[1-9][0-9]*$/.test(buildId) || !Number.isSafeInteger(id)) {
    return new NextResponse("No such genome build.", { status: 404 });
  }
  if (!(await getUser())) return new NextResponse("Sign in to read genome data.", { status: 401 });

  const supabase = await createServerSupabaseClient();
  const kept = cache.get(id);
  if (kept !== undefined) {
    try {
      if (await buildIsReady(supabase, id)) return bedGraph(kept);
    } catch (err) {
      console.error("[genome browser] gene density", err);
      return new NextResponse("The gene density could not be read.", { status: 502 });
    }
    cache.delete(id);
    return new NextResponse("No such genome build.", { status: 404 });
  }

  const read = await readBuildGeneStarts(supabase, id);
  if (read.status === "not found") return new NextResponse("No such genome build.", { status: 404 });
  if (read.status === "failed") {
    console.error("[genome browser] gene density", read.reason);
    return new NextResponse("The gene density could not be read.", { status: 502 });
  }
  const text = densityBedGraph(read.genes);
  if (cache.size >= CACHED_BUILDS) cache.delete(cache.keys().next().value!);
  cache.set(id, text);
  return bedGraph(text);
}
