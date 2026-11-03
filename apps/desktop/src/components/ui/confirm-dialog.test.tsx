import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ConfirmDialog } from './confirm-dialog'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('ConfirmDialog secondary action', () => {
  function renderWithSecondary() {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    const onSecondary = vi.fn()

    render(
      <ConfirmDialog
        onClose={onClose}
        onConfirm={onConfirm}
        open
        secondaryAction={{ label: 'Remove from sidebar', onClick: onSecondary }}
        title="Remove worktree?"
      />
    )

    return { onClose, onConfirm, onSecondary }
  }

  it('runs the secondary action and closes without confirming', async () => {
    const { onClose, onConfirm, onSecondary } = renderWithSecondary()

    fireEvent.click(await screen.findByRole('button', { name: 'Remove from sidebar' }))

    expect(onSecondary).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('still opens focused on Confirm, so Enter confirms rather than picking the secondary', async () => {
    const { onConfirm, onSecondary } = renderWithSecondary()

    const dialog = await screen.findByRole('dialog')

    // eslint-disable-next-line no-restricted-globals -- asserting real focus requires the live document
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))
    // eslint-disable-next-line no-restricted-globals -- asserting real focus requires the live document
    fireEvent.keyDown(document.activeElement!, { key: 'Enter' })

    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1))
    expect(onSecondary).not.toHaveBeenCalled()
  })
})

describe('ConfirmDialog async lifetime', () => {
  it.each([false, true])('does not close its former owner when confirmation resolves after unmount (dismissOnConfirm=%s)', async dismissOnConfirm => {
    vi.useFakeTimers()
    let finish!: () => void
    const pending = new Promise<void>(resolve => { finish = resolve })
    const onClose = vi.fn()

    const view = render(
      <ConfirmDialog confirmLabel="Confirm action" dismissOnConfirm={dismissOnConfirm} onClose={onClose}
        onConfirm={() => pending} open title="Confirm?" />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Confirm action' }))
    view.unmount()
    await act(async () => { finish(); await pending })
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    expect(onClose).not.toHaveBeenCalled()
  })
})
