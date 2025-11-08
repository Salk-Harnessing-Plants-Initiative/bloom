import { getUser } from '@salk-hpi/bloom-nextjs-auth'
import Mixpanel from 'mixpanel'

export default async function Genotypes() {
  const user = await getUser()

  const mixpanel = process.env.MIXPANEL_TOKEN ? Mixpanel.init(process.env.MIXPANEL_TOKEN) : null

  mixpanel?.track('Page view', {
    distinct_id: user?.email,
    url: '/app/genotypes',
  })

  return (
    <div>
      <div className="italic text-xl mb-8 select-none">Genotypes</div>
      <div className="mb-6 select-none">Information about different plant genotypes.</div>
    </div>
  )
}
