import Link from 'next/link'
import { createServerSupabaseClient, getUser } from '@salk-hpi/bloom-nextjs-auth'
import Mixpanel from 'mixpanel'
import ScientistBadge from '@/components/scientist-badge'
import ExpressionDataset from '@/components/expression-dataset'

type Plant = {
  created_at: string
  germ_day: number | null
  germ_day_color: string | null
  id: number
  qr_code: string | null
  wave_id: number | null
  accessions: {
    created_at: string
    id: number
    name: string
  } | null
}

type Wave = {
  experiment_id: number | null
  id: number
  name: string | null
  number: number | null
  cyl_plants: Plant[]
}

function getAccessions(wave: Wave) {
  // empty array of strings
  let lineNames: string[] = []
  let lineName2Id: { [key: string]: number } = {}
  wave.cyl_plants.forEach((plant: Plant) => {
    lineNames.push(plant.accessions?.name ?? '')
    lineName2Id[plant.accessions?.name ?? ''] = plant.accessions?.id ?? 0
  })
  const plantCountObj = countStrings(lineNames)
  const plantCountArray = Object.entries(plantCountObj)
  // sort by name
  const plantCountArraySorted = plantCountArray.sort((a, b) => {
    if (a[0] < b[0]) {
      return -1
    }
    if (a[0] > b[0]) {
      return 1
    }
    return 0
  })
  const plantNameCountsArray = plantCountArraySorted.map(([name, count]) => ({
    name,
    id: lineName2Id[name],
    count,
  }))
  return plantNameCountsArray
}

export default async function Experiment({
  params,
}: {
  params: { datasetId: number; speciesId: number }
}) {
  const dataset: any = await getDataset(params.datasetId)
  const datasetName = capitalizeFirstLetter(dataset?.name ?? '')
  const speciesName = dataset?.species?.common_name ?? ''

  const user = await getUser()

  const mixpanel = process.env.MIXPANEL_TOKEN ? Mixpanel.init(process.env.MIXPANEL_TOKEN) : null

  mixpanel?.track('Page view', {
    distinct_id: user?.email,
    url: `/app/expression/${params.speciesId}/${params.datasetId}`,
  })

  return (
    <div className="">
      <div className="text-xl mb-8 select-none">
        <span className="text-stone-400">
          <span className="hover:underline">
            <Link href="/app/expression">All species</Link>
          </span>
          &nbsp;▸&nbsp;
          <span className="hover:underline capitalize">
            <Link href={`/app/expression/${dataset?.species?.id}`}>{speciesName}</Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span className="">{datasetName}</span>
      </div>
      <div className="mb-8">{dataset?.people && <ScientistBadge person={dataset.people} />}</div>
      <div className="text-lg align-middle">
        {/* <span className="">Accessions</span> */}
        <ExpressionDataset name={dataset?.name || ''} />
      </div>
    </div>
  )
}

function capitalizeFirstLetter(string: String) {
  return string.charAt(0).toUpperCase() + string.slice(1)
}

async function getDataset(datasetId: number) {
  const supabase = createServerSupabaseClient()

  const { data } = await supabase
    .from('scrna_datasets')
    .select('*, people(*), species(*)')
    .eq('id', datasetId)
    .single()

  return data
}

function countStrings(strings: string[]) {
  const count: { [key: string]: number } = {}
  strings.forEach((s) => {
    count[s] = (count[s] || 0) + 1
  })
  return count
}
