import Link from 'next/link'
import { createServerSupabaseClient, getUser } from '@salk-hpi/bloom-nextjs-auth'

import PlantImage from '@/components/plant-image'
import PlantScan from '@/components/plant-scan'
import Mixpanel from 'mixpanel'
import ScientistBadge from '@/components/scientist-badge'

export default async function Image({
  params,
}: {
  params: {
    experimentId: number
    waveId: string
    accessionId: number
    speciesId: number
    scanId: number
  }
}) {
  const experiment: any = await getExperimentWithPlants(params.experimentId)
  const species = experiment?.species
  const experimentName = capitalizeFirstLetter(experiment?.name.replaceAll('-', ' ') ?? '')
  const speciesName: any = species?.common_name ?? ''
  const scan: any = await getScan(params.scanId)
  const wave = scan?.cyl_plants?.cyl_waves

  const accession: any = await getAccession(params.accessionId)
  const user = await getUser()

  const mixpanel = process.env.MIXPANEL_TOKEN ? Mixpanel.init(process.env.MIXPANEL_TOKEN) : null

  mixpanel?.track('Page view', {
    distinct_id: user?.email,
    url: `/app/phenotypes/${params.speciesId}/${params.experimentId}/${params.waveId}/${params.accessionId}/${params.scanId}`,
  })

  return (
    <div className="">
      <div className="text-xl mb-8 select-none">
        <span className="text-stone-400">
          <span className="hover:underline">
            <Link href="/app/phenotypes">All species</Link>
          </span>
          &nbsp;▸&nbsp;
          <span className="hover:underline capitalize">
            <Link href={`/app/phenotypes/${species?.id}`}>{speciesName}</Link>
          </span>
          &nbsp;▸&nbsp;
          <span className="hover:underline">
            <Link href={`/app/phenotypes/${species?.id}/${experiment?.id}`}>{experimentName}</Link>
          </span>
          &nbsp;▸&nbsp;
          <span className="hover:underline">
            <Link
              href={`/app/phenotypes/${species?.id}/${experiment?.id}/${params.waveId}/${params.accessionId}`}
            >
              {accession?.name} (Wave {wave?.number})
            </Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span className="select-auto">
          Replicate <span className="font-light">{scan?.cyl_plants?.qr_code}</span> (Day{' '}
          {scan?.plant_age_days})
        </span>
      </div>
      <div className="mb-4">
        {experiment?.people && <ScientistBadge person={experiment.people} />}
      </div>
      <div className="table-auto select-none pr-8 pb-8">
        {scan && <PlantScan scan={scan} thumb={false} />}
      </div>
    </div>
  )
}

function capitalizeFirstLetter(string: String) {
  return string.charAt(0).toUpperCase() + string.slice(1)
}

async function getExperimentWithPlants(experimentId: number) {
  const supabase = createServerSupabaseClient()

  const { data } = await supabase
    .from('cyl_experiments')
    .select('*, cyl_waves(*, cyl_plants(*, accessions!inner(*))), species(*), people(*)')
    .eq('id', experimentId)
    .single()

  return data
}

async function getAccession(id: number) {
  const supabase = createServerSupabaseClient()

  const { data } = await supabase.from('accessions').select('*').eq('id', id).single()

  return data
}

async function getImage(imageId: string) {
  const supabase = createServerSupabaseClient()

  const { data } = await supabase
    .from('cyl_images')
    .select('*, cyl_scans(*, cyl_plants(*, cyl_waves(*)))')
    .eq('id', imageId)
    .single()

  return data
}

async function getScan(scanId: number) {
  const supabase = createServerSupabaseClient()

  const { data } = await supabase
    .from('cyl_scans')
    .select('*, cyl_images(*), cyl_plants(*, cyl_waves(*))')
    .eq('id', scanId)
    .single()

  return data
}
