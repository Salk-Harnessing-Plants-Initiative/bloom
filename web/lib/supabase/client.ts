'use client'

import { createBrowserClient } from '@supabase/ssr'
import type { Database } from '@/lib/database.types'

let supabaseBrowserClient: ReturnType<typeof createBrowserClient<Database>> | null = null

export function createClientSupabaseClient() {
  if (supabaseBrowserClient) return supabaseBrowserClient

  const cookieName = process.env.NEXT_PUBLIC_SUPABASE_COOKIE_NAME || 'sb-localhost-auth-token'

  supabaseBrowserClient = createBrowserClient<Database>(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookieOptions: {
        name: cookieName,
      },
    }
  )

  // supabaseBrowserClient.auth.onAuthStateChange((event, session) => {
  //   // console.log('[SupabaseClient] Auth event:', event)
  //   // console.log('[SupabaseClient] Session snapshot:', session)
  //   try {
  //     const stored = window.localStorage.getItem(cookieName)
  //     console.log('[SupabaseClient] LocalStorage current value:', stored)
  //   } catch (err) {
  //     console.warn('[SupabaseClient] Could not read localStorage:', err)
  //   }
  // })

  return supabaseBrowserClient
}
