import Link from 'next/link'
import { createServerSupabaseClient } from '@salk-hpi/bloom-nextjs-auth'

import PlantImage from '@/components/plant-image'

export default async function Image({
  params,
}: {
  params: { experimentId: number; accessionName: string; imageId: string }
}) {
  const experiment: any = await getExperimentWithSpecies(params.experimentId)
  const species = experiment?.species
  const experimentName = capitalizeFirstLetter(experiment?.name.replaceAll('-', ' ') ?? '')
  const speciesName = species?.common_name ?? ''

  const lineNameUnescaped = params.accessionName.replaceAll('%20', ' ')
  const image: any = await getImage(params.imageId)

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
            <Link href={`/app/phenotypes/${species?.id}/${experiment?.id}/${params.accessionName}`}>
              {lineNameUnescaped}
            </Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span className="select-all">
          Replicate <span className="font-light">{image?.cyl_scans?.cyl_plants?.qr_code}</span> (Day{' '}
          {image?.cyl_scans?.plant_age_days})
        </span>
      </div>
      <div className="table-auto select-none pr-8 pb-8">
        <PlantImage path={image?.object_path || ''} thumb={false} />
      </div>
    </div>
  )
}

function capitalizeFirstLetter(string: String) {
  return string.charAt(0).toUpperCase() + string.slice(1)
}

async function getExperimentWithSpecies(experimentId: number) {
  const supabase = createServerSupabaseClient()

  const { data } = await supabase
    .from('cyl_experiments')
    .select('*, species(*)')
    .eq('id', experimentId)
    .single()

  return data
}

async function getImage(imageId: string) {
  const supabase = createServerSupabaseClient()

  const { data } = await supabase
    .from('cyl_images')
    .select('*, cyl_scans(*, cyl_plants(*))')
    .eq('id', imageId)
    .single()

  return data
}
