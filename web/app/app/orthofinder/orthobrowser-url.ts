export const ORTHOBROWSER_URL =
  'https://resources.michael.salk.edu/misc/hpi_orthobrowser/index.html'

/** Longest search passed on; gene ids and symbols are far shorter. */
const MAX_SEARCH_LENGTH = 200

/** The OrthoBrowser's address, opened on `search` (Bloom's ?search=) when one is given. */
export function orthobrowserUrl(search: string | string[] | undefined): string {
  const term = (Array.isArray(search) ? search[0] : search)?.trim() ?? ''
  if (!term || term.length > MAX_SEARCH_LENGTH) return ORTHOBROWSER_URL
  return `${ORTHOBROWSER_URL}?search=${encodeURIComponent(term.toLowerCase())}`
}
