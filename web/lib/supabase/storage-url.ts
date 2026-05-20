
const INTERNAL_HOST = /^https?:\/\/kong:\d+/

export function toPublicStorageUrl(url: string | null | undefined): string | null {
  if (!url) return null
  const publicBase = process.env.NEXT_PUBLIC_SUPABASE_URL
  if (!publicBase) return url
  return url.replace(INTERNAL_HOST, publicBase)
}
