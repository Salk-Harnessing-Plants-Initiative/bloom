import type { Database } from './lib/database.types'
import { cookies } from 'next/headers'
import { cache } from 'react'
import {
  createServerActionClient,
  createServerComponentClient,
} from '@supabase/auth-helpers-nextjs'
import { createRouteHandlerClient } from '@supabase/auth-helpers-nextjs'
import { createClient } from '@supabase/supabase-js'

export const createServerSupabaseClient = cache(() => {
  const cookieStore = cookies()
  return createServerComponentClient<Database>({ cookies: () => cookieStore })
})

export const createRouteHandlerSupabaseClient = cache(() => {
  const cookieStore = cookies()
  return createRouteHandlerClient<Database>({ cookies: () => cookieStore })
})

export const createServerActionSupabaseClient = cache(() => {
  const cookieStore = cookies()
  return createServerActionClient({ cookies: () => cookieStore })
})

export async function getSession() {
  const supabase = createServerSupabaseClient()
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

export async function getUser() {
  const supabase = createServerSupabaseClient()
  try {
    const {
      data: { user },
    } = await supabase.auth.getUser()
    return user
  } catch (error) {
    console.error('Error:', error)
    return null
  }
}

export function createServiceRoleSupabaseClient(supabase_url: string, service_role_key: string) {
  const supabase = createClient<Database>(supabase_url, service_role_key, {
    auth: {
      autoRefreshToken: false,
      persistSession: false,
      detectSessionInUrl: false,
    },
  })
  return supabase
}
