import { createServerSupabaseClient } from "@/lib/supabase/server";
import { NextResponse } from "next/server";

export async function GET(request: Request) {
  // The `/auth/callback` route is required for the server-side auth flow implemented
  // by the Auth Helpers package. It exchanges an auth code for the user's session.
  // https://supabase.com/docs/guides/auth/auth-helpers/nextjs#managing-sign-in-with-code-exchange
  const requestUrl = new URL(request.url);

  console.log(`requestUrl ${requestUrl}`);

  const code = requestUrl.searchParams.get("code");

  // console.log(cookies())

  if (code) {
    console.log(`code ${code}`);
    const supabase = await createServerSupabaseClient();
    console.log("created supabase client");
    await supabase.auth.exchangeCodeForSession(code);
    console.log("exchanged code for session");
  }

  console.log(`requestUrl.origin ${requestUrl.origin}`);

  const nextParam = requestUrl.searchParams.get("next");
  const nextPath = nextParam && nextParam.startsWith("/") ? nextParam : "/";
  return NextResponse.redirect(new URL(nextPath, requestUrl.origin));
}
