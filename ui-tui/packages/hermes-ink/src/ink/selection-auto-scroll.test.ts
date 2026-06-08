import { describe, expect, it } from 'vitest'

import Ink from './ink.js'
import { createSelectionState, startSelection } from './selection.js'

describe('selection auto-scroll', () => {
  it('stops the auto-scroll timer when dragging at a scroll boundary', () => {
    const selection = createSelectionState()

    startSelection(selection, 0, 0)

    const timer = setInterval(() => undefined, 1000)
    const app = {
      altScreenActive: true,
      findPrimaryScrollBox: () => ({
        scrollHeight: 20,
        scrollTop: 10,
        scrollViewportHeight: 10
      }),
      selection,
      selectionAutoScrollDir: 1,
      selectionAutoScrollTimer: timer,
      selectionDragCell: { col: 0, row: 9 },
      stopSelectionAutoScroll: (Ink.prototype as any).stopSelectionAutoScroll
    }

    ;(Ink.prototype as any).stepSelectionAutoScroll.call(app)

    expect(app.selectionAutoScrollTimer).toBeNull()
    expect(app.selectionAutoScrollDir).toBe(0)
    expect(app.selectionDragCell).toBeNull()
  })
})
