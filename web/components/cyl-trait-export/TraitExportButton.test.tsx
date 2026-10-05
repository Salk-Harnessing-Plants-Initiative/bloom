// @vitest-environment jsdom
/** The Download traits button (tasks.md 11.3; spec "Trait download entry points"). */

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('./TraitExportDialog', () => ({
  TraitExportDialog: (props: { target: unknown; onClose: () => void }) => (
    <div data-testid="dialog" data-target={JSON.stringify(props.target)}>
      <button type="button" onClick={props.onClose}>
        close-dialog
      </button>
    </div>
  ),
}))

import { TraitExportButton } from './TraitExportButton'

afterEach(() => {
  cleanup()
})

const TARGET = { experimentId: 5, wave: 2, age: 14, waves: [1, 2, 3], ages: [7, 14, 21] }

describe('TraitExportButton', () => {
  it('is a Download traits button that honours disabled', () => {
    render(<TraitExportButton target={TARGET} disabled />)
    const button = screen.getByRole('button', { name: 'Download traits' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    fireEvent.click(button)
    expect(screen.queryByTestId('dialog')).toBeNull()
  })

  it('mounts the dialog with its target on click, and unmounts it on close', async () => {
    render(<TraitExportButton target={TARGET} />)
    expect(screen.queryByTestId('dialog')).toBeNull()
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Download traits' })))
    expect(JSON.parse(screen.getByTestId('dialog').getAttribute('data-target') ?? '')).toEqual(
      TARGET
    )
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'close-dialog' })))
    expect(screen.queryByTestId('dialog')).toBeNull()
  })

  it('keeps the target it opened with while open', async () => {
    const { rerender } = render(<TraitExportButton target={TARGET} />)
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Download traits' })))
    rerender(<TraitExportButton target={{ ...TARGET, wave: 3 }} />)
    expect(JSON.parse(screen.getByTestId('dialog').getAttribute('data-target') ?? '').wave).toBe(2)
  })
})
