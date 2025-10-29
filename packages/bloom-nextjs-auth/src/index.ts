import type { Database } from "./lib/database.types";
import { cookies } from "next/headers";
import { cache } from "react";
import { createServerClient } from "@supabase/ssr";
import { createClient } from "@supabase/supabase-js";
import { createBrowserClient } from "@supabase/ssr";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

function createCookieAdapter(cookieStore: Awaited<ReturnType<typeof cookies>>) {
  return {
    get: (name: string) => cookieStore.get(name)?.value,
    set: async () => {},
    remove: async () => {},
  };
}

export const createServerSupabaseClient = cache(async () => {
  const cookieStore = await cookies();
  return createServerClient<Database>(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: createCookieAdapter(cookieStore),
  });
});


export const createRouteHandlerSupabaseClient = cache(async () => {
  const cookieStore = await cookies();
  const cookieMethods = {
    get: (name: string) => cookieStore.get(name)?.value,
    set: (name: string, value: string, options: any) =>
      cookieStore.set?.(name, value, options),
    remove: (name: string, options?: any) =>
      cookieStore.delete?.(options ? { name, ...options } : name),
  } as any;

  return createServerClient<Database>(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: cookieMethods,
  });
});

export const createServerActionSupabaseClient = createServerSupabaseClient;


export async function getSession() {
  const supabase = await createServerSupabaseClient();
  try {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    return session;
  } catch (error) {
    console.error("Error getting session:", error);
    return null;
  }
}

export async function getUser() {
  const supabase = await createServerSupabaseClient();
  try {
    const {
      data: { user },
    } = await supabase.auth.getUser();
    return user;
  } catch (error) {
    console.error("Error getting user:", error);
    return null;
  }
}

export function createServiceRoleSupabaseClient(
  supabase_url: string,
  service_role_key: string
) {
  return createClient<Database>(supabase_url, service_role_key, {
    auth: {
      autoRefreshToken: false,
      persistSession: false,
      detectSessionInUrl: false,
    },
  });
}

export function createBrowserSupabaseClient() {
  return createBrowserClient<Database>(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}
