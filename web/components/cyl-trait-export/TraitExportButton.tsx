'use client'

/**
 * Download traits (spec "Trait download entry points"; design D8). It opens the dialog
 * with the target as it was on click; the dialog is mounted only while open, so
 * closing it unmounts it and cancels any job it holds.
 */

import { useState } from 'react'
import { TraitExportDialog, type ExportTarget } from './TraitExportDialog'

export function TraitExportButton({
  target,
  disabled = false,
}: {
  target: ExportTarget
  disabled?: boolean
}) {
  const [opened, setOpened] = useState<ExportTarget | null>(null)
  return (
    <>
      <button
        type="button"
        onClick={() => setOpened(target)}
        disabled={disabled}
        className="rounded-md border border-lime-700 px-2.5 py-1 text-sm text-lime-700 hover:bg-lime-50 disabled:cursor-not-allowed disabled:border-stone-300 disabled:text-stone-400"
      >
        Download traits
      </button>
      {opened && <TraitExportDialog target={opened} onClose={() => setOpened(null)} />}
    </>
  )
}
