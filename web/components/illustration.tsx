export const revalidate = 60 // revalidate this page every 60 seconds

import { cookies } from 'next/headers'
import { createServerSupabaseClient } from "@salk-hpi/bloom-nextjs-auth";
import Image from 'next/image'

async function getObjectUrl(path: string) {
  const supabase = await createServerSupabaseClient();

  const { data, error } = await supabase
    .storage
    .from('species_illustrations')
    .createSignedUrl(path, 120, {
      transform: {
        width: 192
      }
    })

    console.log(error)

    const signedUrl = data?.signedUrl ?? '' 
    return signedUrl
  
}

export default async function Illustration({ path }: any) {

  const objectUrl = await getObjectUrl(path)

  return (
    <Image alt="Species illustration" src={objectUrl} width={80} height={80}/>
  )
}
