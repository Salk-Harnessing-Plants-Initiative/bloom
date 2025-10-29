import Link from 'next/link'
import { createServerSupabaseClient } from '@salk-hpi/bloom-nextjs-auth'

import PlantImage from '@/components/plant-image';
// import type { Database } from "@/lib/database.types";
import { Key } from 'react';


export default async function Accession({ params }: { params: { experimentId: number, accessionName: string } }) {

  const experiment : any = await getExperimentWithSpecies(params.experimentId)
  const species = experiment?.species
  const experimentName = capitalizeFirstLetter(experiment?.name.replaceAll('-', ' ') ?? '')
  const speciesName = species?.common_name ?? ''

  const lineNameUnescaped = params.accessionName.replaceAll('%20', ' ')
  const plants: any  = await getPlants(lineNameUnescaped, params.experimentId)  

  return (
    <div className=''>
      <div className='text-xl mb-8 select-none'>
        <span className='text-stone-400'>
          <span className='hover:underline'><Link href='/app/phenotypes'>All species</Link></span>
          &nbsp;▸&nbsp;
          <span className='hover:underline capitalize'>
            <Link href={`/app/phenotypes/${species?.id}`}>
                {speciesName}
            </Link>
          </span>
          &nbsp;▸&nbsp;
          <span className='hover:underline'>
            <Link href={`/app/phenotypes/${species?.id}/${experiment?.id}`}>
                {experimentName}
            </Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span className='select-all'>
          {lineNameUnescaped}
        </span>
      </div>
      <div className='table-auto select-none'>
        {plants?.map((plant: { id: Key | null | undefined; qr_code: any; cyl_scans: any[]; }, index: number) => (
          <div className='table-row' key={plant.id}>
            <div className='table-cell text-lg align-middle pb-4 pr-4'>
              <div className='align-middle'>
                <div className='text-sm text-neutral-700'>
                  <span className='text-neutral-500'>Replicate {index + 1}</span>
                  <br/>
                  <span className='text-neutral-500'>ID:</span>&nbsp;
                  <span className='text-neutral-400 select-all'>{plant?.qr_code ?? ''}</span>
                </div>
              </div>
            </div>
            <div className='table-cell text-lg align-middle pb-4'>
              <div className='text-sm mt-2 text-neutral-400 flex flex-row'>
                {plant.cyl_scans.sort((a, b) => ((a.plant_age_days ?? 0) - (b.plant_age_days ?? 0))).map((scan) => (
                  <div key={scan.id} className='mr-4'>
                    {scan.cyl_images.map((image: { id: Key | null | undefined; object_path: string | null; }) => (
                      <div key={image.id} className='text-center'>
                        <div className='pb-1'>Day {scan.plant_age_days}</div>
                        <Link href={`/app/phenotypes/${species?.id}/${experiment?.id}/${params.accessionName}/${image.id}`}>
                          <PlantImage path={image.object_path} thumb={true} />
                        </Link>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function capitalizeFirstLetter(string: String) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}


async function getExperimentWithSpecies(experimentId: number) {

  const supabase = await createServerSupabaseClient()

  const { data } = await supabase
    .from('cyl_experiments')
    .select('*, species(*)')
    .eq('id', experimentId)
    .single()
  
  return data;
  
}

async function getPlants(lineName: string, experimentId: number) {

  const supabase = await createServerSupabaseClient()

  const { data } = await supabase
    .from('cyl_plants')
    .select('*, cyl_scans(*, cyl_images(*)), cyl_waves(*, cyl_experiments(*, species(*))), accessions!inner(*)')
    .eq('cyl_scans.cyl_images.frame_number', 1)
    .eq('accessions.name', lineName)
    
  return data;
  
}
