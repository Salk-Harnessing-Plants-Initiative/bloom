import { NextResponse } from "next/server";
import {
  getUser,
  getSession,
  createServiceRoleSupabaseClient,
} from "@/lib/supabase/server";

export async function GET() {
  return NextResponse.json({ app_url: process.env.NEXT_PUBLIC_APP_URL });
}
