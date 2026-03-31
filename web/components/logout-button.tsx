'use client'

import { createClientSupabaseClient } from '@/lib/supabase/client'

export default function LogoutButton() {
  // Create a Supabase client configured to use cookies
  const supabase = createClientSupabaseClient()

  const signOut = async () => {
    await supabase.auth.signOut()
  }

  return (
    <button className="hover:underline" onClick={signOut}>
      Logout
    </button>
  )
}
