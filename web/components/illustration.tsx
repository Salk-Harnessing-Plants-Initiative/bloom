export const revalidate = 60 // revalidate this page every 60 seconds

import { createServerSupabaseClient } from '@/lib/supabase/server'
import Image from 'next/image'

async function getObjectUrl(path: string) {
  if (!path) {
    return null
  }

  const supabase = await createServerSupabaseClient()

  const { data, error } = await supabase
    .storage
    .from('species_illustrations')
    .createSignedUrl(path, 120, {
      transform: {
        width: 192
      }
    })

  if (error) {
    console.log('Illustration error:', error)
    return null
  }

  const signedUrl = data?.signedUrl ?? null
  return signedUrl
}

export default async function Illustration({ path }: any) {
  const objectUrl = await getObjectUrl(path)

  if (!objectUrl) {
    // Return a placeholder or simple icon instead of null to avoid hydration issues
    return (
      <div className="w-20 h-20 bg-gray-200 rounded-full flex items-center justify-center">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
          stroke="currentColor"
          className="w-10 h-10 text-gray-400"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm10.5-11.25h.008v.008h-.008V8.25Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"
          />
        </svg>
      </div>
    )
  }

  return (
    <Image alt="Species illustration" src={objectUrl} width={80} height={80} className="rounded-full"/>
  )
}
