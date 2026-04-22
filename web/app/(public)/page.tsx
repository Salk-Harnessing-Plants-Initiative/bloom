import Link from 'next/link'
import { redirect } from 'next/navigation'
import { getUser } from '@/lib/supabase/server'

export default async function Index() {
  const user = await getUser()

  if (user) {
    redirect('/app')
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center bg-stone-100 gap-6">
      <img src="/logo.png" className="h-16" alt="Bloom" />
      <h1 className="text-3xl">Welcome to Bloom.</h1>
      <Link href="/login" className="underline text-green-700">
        Sign in
      </Link>
    </main>
  )
}
