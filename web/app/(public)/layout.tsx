import '@/styles/globals.css'

import { redirect } from 'next/navigation'
import { getUser } from '@/lib/supabase/server'

export const metadata = {
  title: 'Bloom',
  description: 'Web app for Salk Harnessing Plants Initiative',
}

export default async function PublicLayout({ children }: { children: React.ReactNode }) {
  const user = await getUser()

  if (user) {
    redirect('/app')
  }

  return <>{children}</>
}
