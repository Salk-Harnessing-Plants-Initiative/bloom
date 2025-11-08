import { getUser } from '@salk-hpi/bloom-nextjs-auth'
import Mixpanel from 'mixpanel'
import JBrowse from '@/components/jbrowse'

export default async function Genotypes() {
  const user = await getUser()

  const mixpanel = process.env.MIXPANEL_TOKEN ? Mixpanel.init(process.env.MIXPANEL_TOKEN) : null

  mixpanel?.track('Page view', {
    distinct_id: user?.email,
    url: '/app/jbrowse',
  })

  return (
    <div>
      <JBrowse />
    </div>
  )
}
