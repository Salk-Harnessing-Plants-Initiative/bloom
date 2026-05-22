import { NextResponse } from "next/server";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const host =
    request.headers.get("x-forwarded-host") ||
    request.headers.get("host") ||
    url.host;
  const proto =
    request.headers.get("x-forwarded-proto") || url.protocol.replace(":", "");
  const goTrueVerify = `${proto}://${host}/api/auth/v1/verify${url.search}`;
  return NextResponse.redirect(goTrueVerify);
}
