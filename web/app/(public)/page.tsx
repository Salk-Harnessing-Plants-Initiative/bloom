import { redirect } from 'next/navigation'
import { getUser } from '@salk-hpi/bloom-nextjs-auth'

export default async function Index() {
  const user = await getUser()

  if (user) {
    redirect('/app')
  } else {
    return <div className="text-3xl mx-auto text-center mt-8">Welcome to Bloom.</div>
  }
}
