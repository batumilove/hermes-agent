import { expect, it } from 'vitest'
import { recoveryTimeline } from './recovery-timeline'
it('keeps bounded scalar metadata only, rejects unknown discriminators and adversarial fields', () => {
  window.__recoveryTimeline = { expected: 'private answer', rows: [] }
  recoveryTimeline(
    'history-return',
    { verdict: { secret: 'credential' } as never, owned: 'secret' as never, raw: 'credential' } as never,
    'private answer'
  )
  recoveryTimeline('not-a-phase' as never, {}, 'credential')
  expect(window.__recoveryTimeline.rows).toHaveLength(1)
  expect(window.__recoveryTimeline.rows[0].content).toBe('full')
  expect(Object.keys(window.__recoveryTimeline.rows[0]).sort()).toEqual(['content', 'ms', 'phase'])
  expect(JSON.stringify(window.__recoveryTimeline.rows)).not.toMatch(/private|credential|secret/)
  for (let i = 0; i < 1100; i++) recoveryTimeline('cache')
  expect(window.__recoveryTimeline.rows).toHaveLength(1000)
  delete window.__recoveryTimeline
  recoveryTimeline('cache')
})
