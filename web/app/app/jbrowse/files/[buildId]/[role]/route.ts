import { NextResponse } from "next/server";

import { REDIRECT_MAX_AGE_S, signBuildFile } from "@/lib/jbrowse/bloom-genomes-server";
import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";

/** One browser-build file: checks the login, then redirects to a URL signed with the viewer's
 * credentials, which the browser may reuse for REDIRECT_MAX_AGE_S. */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ buildId: string; role: string }> },
) {
  const { buildId, role } = await params;
  const id = Number(buildId);
  if (!/^[1-9][0-9]*$/.test(buildId) || !Number.isSafeInteger(id)) {
    return new NextResponse("No such genome file.", { status: 404 });
  }
  if (!(await getUser())) {
    return new NextResponse("Sign in to read genome files.", { status: 401 });
  }

  const file = await signBuildFile(await createServerSupabaseClient(), id, role);
  if (file.status === "not found") return new NextResponse("No such genome file.", { status: 404 });
  if (file.status === "failed") {
    console.error("[genome browser]", file.reason);
    return new NextResponse("The genome file could not be opened.", { status: 502 });
  }
  return new NextResponse(null, {
    status: 302,
    headers: { Location: file.url, "Cache-Control": `private, max-age=${REDIRECT_MAX_AGE_S}` },
  });
}
