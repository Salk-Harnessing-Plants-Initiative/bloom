
import '@/styles/globals.css'

import Link from 'next/link'
// import Badge from '@/components/badge'
import { redirect } from 'next/navigation'
import { getUser } from '@/lib/supabase/server'

export const metadata = {
  title: 'Bloom',
  description: 'Web app for Salk Harnessing Plants Initiative',
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {

  const user = await getUser()

  if (user) {
    redirect('/app')
  }

  return (
    <main className="min-h-screen flex flex-col bg-stone-100">
      <div className='absolute left-12 top-8 align-middle'>
        <Link href='/'><img src="/logo.png" className="h-12 inline" /></Link>
      </div>
      <div className='absolute right-12 top-8'>
        <div className="flex text-sm">
          <span className="ml-auto">
            <Link href="/login" className="hover:underline">
              Login
            </Link>
          </span>
        </div>
      </div>
      <div className='mt-20 flex flex-col'>
        {children}
      </div>
    </main>
  )
}
