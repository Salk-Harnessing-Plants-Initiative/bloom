'use client'

/**
 * The scan page's Download traits (spec "Trait download entry points"). The page is a
 * server component, so this client boundary takes only the scan id.
 */

import { TraitExportButton } from '@/components/cyl-trait-export/TraitExportButton'

export default function ScanTraitExportButton({ scanId }: { scanId: number }) {
  return <TraitExportButton target={{ scanId }} />
}
