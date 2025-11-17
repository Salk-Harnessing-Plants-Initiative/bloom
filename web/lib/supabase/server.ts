import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'
import { createClient } from '@supabase/supabase-js'
import { cache } from 'react'
import type { Database } from '@/lib/database.types'

/**
 * Creates a Supabase client for use in Server Components, Server Actions, and Route Handlers.
 * Automatically handles cookie management for authentication.
 */
export const createServerSupabaseClient = cache(async () => {
  const cookieStore = await cookies()

  // Use SUPABASE_URL for server-side (internal container network)
  // Falls back to NEXT_PUBLIC_SUPABASE_URL for local development
  const supabaseUrl = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL!
  const cookieName = process.env.SUPABASE_COOKIE_NAME || 'sb-localhost-auth-token'

  return createServerClient<Database>(
    supabaseUrl,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll()
        },
        setAll(cookiesToSet) {
          try {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options)
            )
          } catch {
            // The `setAll` method was called from a Server Component.
            // This can be ignored if you have middleware refreshing
            // user sessions.
          }
        },
      },
      cookieOptions: {
        name: cookieName,
      },
    }
  )
})

/**
 * Alias for createServerSupabaseClient to match old API naming
 */
export const createRouteHandlerSupabaseClient = createServerSupabaseClient

/**
 * Alias for createServerSupabaseClient to match old API naming
 */
export const createServerActionSupabaseClient = createServerSupabaseClient

/**
 * Gets the current session from Supabase
 */
export async function getSession() {
  const supabase = await createServerSupabaseClient()
  try {
    const {
      data: { session },
    } = await supabase.auth.getSession()
    return session
  } catch (error) {
    console.error('Error:', error)
    return null
  }
}

/**
 * Gets the current user from Supabase
 */
export async function getUser() {
  const supabase = await createServerSupabaseClient()
  try {
    const {
      data: { user },
      error,
    } = await supabase.auth.getUser()
    
    console.error('[getUser] User:', user?.email || 'null', 'Error:', error?.message || 'none')
    
    return user
  } catch (error) {
    console.error('Error:', error)
    return null
  }
}

/**
 * Creates a Supabase client with service role privileges.
 * Use with caution - bypasses Row Level Security (RLS).
 */
export function createServiceRoleSupabaseClient(
  supabase_url: string,
  service_role_key: string
) {
  const supabase = createClient<Database>(supabase_url, service_role_key, {
    auth: {
      autoRefreshToken: false,
      persistSession: false,
      detectSessionInUrl: false,
    },
  })
  return supabase
}
