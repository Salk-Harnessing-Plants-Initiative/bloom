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

export async function GET(request: Request) {
  const url = new URL(request.url);
  const token = url.searchParams.get("token");
  const type = url.searchParams.get("type") as EmailOtpType | null;
  const redirectTo = url.searchParams.get("redirect_to");

  if (!token || !type || !ALLOWED_TYPES.includes(type)) {
    return NextResponse.redirect(new URL("/login?error=invalid_link", url.origin));
  }

  const supabase = await createServerSupabaseClient();
  const { error } = await supabase.auth.verifyOtp({
    token_hash: token,
    type,
  });

  if (error) {
    const msg = encodeURIComponent(error.message);
    return NextResponse.redirect(new URL(`/login?error=${msg}`, url.origin));
  }

  if (type === "recovery") {
    return NextResponse.redirect(new URL("/reset-password", url.origin));
  }

  const safeRedirect =
    redirectTo && redirectTo.startsWith(url.origin) ? redirectTo : url.origin;
  return NextResponse.redirect(safeRedirect);
}
