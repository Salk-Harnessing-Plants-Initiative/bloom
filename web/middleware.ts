import { createServerClient } from '@supabase/ssr'
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
export const runtime = 'nodejs'

export async function middleware(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request })

  const supabaseUrl = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL!
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  const jwtSecret =
    process.env.SUPABASE_JWT_SECRET ||
    'super-secret-jwt-token-with-at-least-32-characters'
  const cookieName = process.env.SUPABASE_COOKIE_NAME || 'sb-localhost-auth-token'

  // initialize Supabase client for SSR auth cookies
  const supabase = createServerClient(supabaseUrl, anonKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (cookiesToSet) => {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value))
        supabaseResponse = NextResponse.next({ request })
        cookiesToSet.forEach(({ name, value, options }) =>
          supabaseResponse.cookies.set(name, value, options)
        )
      },
    },
    cookieOptions: {
      name: cookieName,
    },
  })

  // const authCookie = request.cookies.get(cookieName)
  // TESTING MIDDLEWARE USING MANUAL FETCH
  // let accessToken: string | null = null
  // if (authCookie?.value) {
  //   try {
  //     const decoded = JSON.parse(
  //       Buffer.from(authCookie.value.replace('base64-', ''), 'base64').toString('utf8')
  //     )
  //     accessToken = decoded.access_token
  //   } catch {
  //     accessToken = authCookie.value
  //   }
  // }

  // console.log('[Middleware Debug] Supabase URL:', supabaseUrl)
  // console.log('[Middleware Debug] ANON KEY prefix:', anonKey.slice(0, 20))
  // console.log('[Middleware Debug] Access token prefix:', accessToken?.slice(0, 40))

  // Optional: test direct /auth/v1/user fetch (just for debugging)
  // if (accessToken) {
  //   try {
  //     const resp = await fetch(`${supabaseUrl}/auth/v1/user`, {
  //       headers: {
  //         apikey: anonKey,
  //         Authorization: `Bearer ${accessToken}`,
  //       },
  //     })
  //     console.log('[Middleware Debug] Manual fetch →', resp.status)
  //     if (!resp.ok) {
  //       console.warn('[Middleware Debug] Manual fetch text:', await resp.text())
  //     }
  //   } catch (err) {
  //     console.error('[Middleware Debug] Manual fetch failed:', err)
  //   }
  // }

  const {
    data: { user },
    error,
  } = await supabase.auth.getUser()

  if (error) console.error('[Middleware] getUser() error:', error.message)
  console.log('[Middleware] User →', user?.email || 'NO USER')

  // only redirect unauthenticated users on protected routes
  const path = request.nextUrl.pathname
  const isPublic =
    path.startsWith('/login') ||
    path.startsWith('/auth') ||
    path.startsWith('/error') ||
    path.startsWith('/api') // allow internal routes

  if (!user && !isPublic) {
    console.log('[Middleware] Redirecting to /login')
    const url = request.nextUrl.clone()
    url.pathname = '/login'
    return NextResponse.redirect(url)
  }

  return supabaseResponse
}

export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)',
  ],
}
