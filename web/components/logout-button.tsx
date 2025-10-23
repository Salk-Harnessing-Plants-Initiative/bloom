'use client'

import { createClientComponentClient } from '@supabase/auth-helpers-nextjs'

export default function LogoutButton() {
  // Create a Supabase client configured to use cookies
  const supabase = createClientComponentClient()

  const signOut = async () => {
    await supabase.auth.signOut()
  }

  return (
    <button className="hover:underline" onClick={signOut}>
      Logout
    </button>
  )
}
