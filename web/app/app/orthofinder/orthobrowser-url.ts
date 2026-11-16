export const ORTHOBROWSER_URL =
  'https://resources.michael.salk.edu/misc/hpi_orthobrowser/index.html'

/** Longest search passed on; gene ids and symbols are far shorter. */
const MAX_SEARCH_LENGTH = 200

/** The searched gene as given (Bloom's ?search=), or null when there is none or it is too long. */
export function searchedGene(search: string | string[] | undefined): string | null {
  const term = (Array.isArray(search) ? search[0] : search)?.trim() ?? ''
  return term && term.length <= MAX_SEARCH_LENGTH ? term : null
}

/** The OrthoBrowser's address, opened on `search` (Bloom's ?search=) when one is given. */
export function orthobrowserUrl(search: string | string[] | undefined): string {
  const term = searchedGene(search)
  if (!term) return ORTHOBROWSER_URL
  return `${ORTHOBROWSER_URL}?search=${encodeURIComponent(term.toLowerCase())}`
}
