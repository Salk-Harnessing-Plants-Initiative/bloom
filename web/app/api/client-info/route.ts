import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({
    api_url: process.env.NEXT_PUBLIC_SUPABASE_URL,
    anon_key: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  });
}
