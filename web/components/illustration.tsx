export const revalidate = 60 // revalidate this page every 60 seconds

import { createServerSupabaseClient } from '@/lib/supabase/server'
import Image from 'next/image'
import { promises as fs } from 'fs'
import path from 'path'

const ICON_DIR = path.join(process.cwd(), 'public', 'species-icons')
const DEFAULT_ICON = '/species-icons/_default.svg'

async function getObjectUrl(storagePath: string): Promise<string | null> {
  if (!storagePath) return null
  const supabase = await createServerSupabaseClient()
  const { data, error } = await supabase
    .storage
    .from('species_illustrations')
    .createSignedUrl(storagePath, 120, { transform: { width: 192 } })
  if (error) {
    console.log('Illustration error:', error)
    return null
  }
  return data?.signedUrl ?? null
}

function slugify(value: string | null | undefined): string {
  if (!value) return ''
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

async function resolveFallbackIcon(
  commonName: string | null | undefined,
): Promise<string> {
  const slug = slugify(commonName)
  if (!slug) return DEFAULT_ICON
  // Prefer PNG (watercolor) over SVG (line-art) so the richer illustration
  // wins when both exist for the same species.
  for (const ext of ['png', 'svg']) {
    const iconFile = `${slug}.${ext}`
    try {
      await fs.access(path.join(ICON_DIR, iconFile))
      return `/species-icons/${iconFile}`
    } catch {
      // try next extension
    }
  }
  return DEFAULT_ICON
}

export default async function Illustration({
  path: storagePath,
  commonName,
}: {
  path?: string | null
  commonName?: string | null
}) {
  const objectUrl = storagePath ? await getObjectUrl(storagePath) : null

  if (objectUrl) {
    return (
      <Image
        alt={commonName ?? 'Species illustration'}
        src={objectUrl}
        width={192}
        height={192}
        className="w-full h-full object-contain"
      />
    )
  }

  const fallback = await resolveFallbackIcon(commonName)
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={fallback}
      alt={commonName ?? 'plant'}
      className="w-full h-full object-contain"
    />
  )
}
