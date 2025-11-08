import Link from 'next/link'
import Illustration from '@/components/illustration'
import { createServerSupabaseClient, getUser } from '@salk-hpi/bloom-nextjs-auth'
import Mixpanel from 'mixpanel'

import type { SpeciesWithExperiments } from '@/lib/custom.types'

export default async function AllSpecies() {
  const user = await getUser()

  const mixpanel = process.env.MIXPANEL_TOKEN ? Mixpanel.init(process.env.MIXPANEL_TOKEN) : null

  mixpanel?.track('Page view', {
    distinct_id: user?.email,
    url: '/app/traits',
  })

  const speciesList = await getSpeciesList()

  const getSpeciesInfo = (species: SpeciesWithExperiments) => {
    const numExps = species.cyl_experiments.length
    const suffix = numExps == 1 ? '' : 's'
    const text = numExps + ' experiment' + suffix + ''
    return <span>{text}</span>
  }

  return (
    <div>
      <div className="text-xl mb-4 select-none">All species</div>
      <div className="table-auto select-none">
        {speciesList?.map((species: SpeciesWithExperiments) => (
          <div className="table-row" key={species.id}>
            <div className="table-cell py-4">
              <Illustration path={species.illustration_path} />
            </div>
            <div className="table-cell text-lg align-middle p-4">
              <div className="align-middle">
                <Link href={`/app/traits/${species.id}`}>
                  <span className="capitalize text-lime-700 hover:underline">
                    {species.common_name}
                  </span>
                </Link>
                <div className="text-sm italic text-neutral-600">
                  <span className="capitalize">{species.genus}</span>
                  &nbsp;
                  <span className="lowercase">{species.species}</span>
                </div>
              </div>
              <div className="text-sm mt-2 text-neutral-400">{getSpeciesInfo(species)}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

async function getSpeciesList() {
  const supabase = createServerSupabaseClient()

  const { data } = await supabase
    .from('species')
    .select('*, cyl_experiments!inner(*)')
    .neq('cyl_experiments.deleted', true)

  // sort by length of cyl_experiments
  data?.sort((a: { cyl_experiments: string | any[] }, b: { cyl_experiments: string | any[] }) => {
    return b.cyl_experiments.length - a.cyl_experiments.length
  })

  return data
}
