import { createClient } from '@supabase/supabase-js'

export async function GET() {
  const supabaseUrl = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL
  const supabaseServiceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY // ⚠️ never expose to client!

  if (!supabaseUrl || !supabaseServiceRoleKey) {
    return Response.json({ error: 'Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY' }, { status: 500 })
  }

  const supabase = createClient(supabaseUrl, supabaseServiceRoleKey)

  // Use the admin API to list users
  const { data, error } = await supabase.auth.admin.listUsers()

  if (error) {
    return Response.json({ error: error.message }, { status: 500 })
  }

  return Response.json({ count: data.users.length, users: data.users.map(u => u.email) })
}
