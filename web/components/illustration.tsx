export const revalidate = 60 

import { createServerSupabaseClient } from '@/lib/supabase/server'
import { toPublicStorageUrl } from '@/lib/supabase/storage-url'
import { promises as fs } from 'fs'
import path from 'path'
import { DEFAULT_SPECIES_IMAGE } from '@/lib/species-image'

const ICON_DIR = path.join(process.cwd(), 'public', 'species-icons')

const DEFAULT_ICON = DEFAULT_SPECIES_IMAGE

async function getObjectUrl(storagePath: string): Promise<string | null> {
  if (!storagePath) return null
  const supabase = await createServerSupabaseClient()
  const { data, error } = await supabase
    .storage
    .from('species-illustrations')
    .createSignedUrl(storagePath, 120, { transform: { width: 192 } })
  if (error) {
    console.error('Illustration error:', error)
    return null
  }
  return toPublicStorageUrl(data?.signedUrl)
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

/**
 * The image a species shows: its uploaded illustration, else its icon in
 * public/species-icons, else the placeholder (`placeholder: true`).
 */
export async function speciesImage(
  storagePath: string | null | undefined,
  commonName: string | null | undefined,
): Promise<{ url: string; placeholder: boolean }> {
  const objectUrl = storagePath ? await getObjectUrl(storagePath) : null
  if (objectUrl) return { url: objectUrl, placeholder: false }
  const icon = await resolveFallbackIcon(commonName)
  return { url: icon, placeholder: icon === DEFAULT_ICON }
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
      // eslint-disable-next-line @next/next/no-img-element
      <img
        alt={commonName ?? 'Species illustration'}
        src={objectUrl}
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
