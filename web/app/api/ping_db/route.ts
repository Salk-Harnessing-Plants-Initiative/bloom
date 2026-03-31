import { createClient } from '@supabase/supabase-js'
export async function GET() {
  const supabase = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!)
  const { data, error } = await supabase.from('users').select('*').limit(1) // your table
  return Response.json({ ok: !error, error })
}