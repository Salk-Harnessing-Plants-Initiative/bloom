import { NextResponse } from "next/server";
import { createServerSupabaseClient } from "@salk-hpi/bloom-nextjs-auth";

export async function GET() {
  const supabase = await createServerSupabaseClient();
  const { data: species } = await supabase.from("species").select();
  return NextResponse.json(species);
}
