'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { createClientSupabaseClient } from '@/lib/supabase/client'
import { setDefaultResultOrder } from 'dns'

export default function Login() {
  const [images, setImages] = useState<any>([])
  const supabase = createClientSupabaseClient()

  useEffect(() => {
    const getImages = async () => {
      const images = await fetch(process.env.NEXT_PUBLIC_BLOOM_URL + '/images', {
        method: 'GET',
      })
      const data = await images.json()
      console.log(data)
      setImages(data)
    }
    getImages()
  }, [])

  return (
    <div className="flex-1 flex flex-col w-full max-w-sm justify-center gap-2 text-neutral-900">
      {images.length}
    </div>
  )
}
