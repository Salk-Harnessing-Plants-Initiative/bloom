import { createServerSupabaseClient } from "@/lib/supabase/server";
import { NextResponse } from "next/server";
import type { EmailOtpType } from "@supabase/supabase-js";

const ALLOWED_TYPES: EmailOtpType[] = [
  "signup",
  "recovery",
  "invite",
  "magiclink",
  "email_change",
  "email",
];

function externalOrigin(request: Request): string {
  const url = new URL(request.url);
  const host = request.headers.get("x-forwarded-host") || request.headers.get("host") || url.host;
  const proto =
    request.headers.get("x-forwarded-proto") || url.protocol.replace(":", "");
  return `${proto}://${host}`;
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const token = url.searchParams.get("token");
  const type = url.searchParams.get("type") as EmailOtpType | null;
  const redirectTo = url.searchParams.get("redirect_to");
  const origin = externalOrigin(request);

  if (!token || !type || !ALLOWED_TYPES.includes(type)) {
    return NextResponse.redirect(new URL("/login?error=invalid_link", origin));
  }

  const supabase = await createServerSupabaseClient();
  const { error } = await supabase.auth.verifyOtp({
    token_hash: token,
    type,
  });

  if (error) {
    const msg = encodeURIComponent(error.message);
    return NextResponse.redirect(new URL(`/login?error=${msg}`, origin));
  }

  if (type === "recovery") {
    return NextResponse.redirect(new URL("/reset-password", origin));
  }

  const safeRedirect =
    redirectTo && redirectTo.startsWith(origin) ? redirectTo : origin;
  return NextResponse.redirect(safeRedirect);
}
