import { afterEach, expect, it } from 'vitest'
import { installRecoveryTimelineBootstrap } from '@/store/session-states'
import { $activeSessionId } from '@/store/session'
afterEach(() => { delete window.__enableRecoveryTimeline; delete window.__startRecoveryTimeline; delete window.__recoveryTimeline; $activeSessionId.set(null) })
it('exposes only explicitly enabled synchronous bounded initialization', () => {
 installRecoveryTimelineBootstrap()
 expect(window.__startRecoveryTimeline).toBeUndefined()
 window.__enableRecoveryTimeline = true
 installRecoveryTimelineBootstrap()
 $activeSessionId.set('private-target')
 expect(window.__startRecoveryTimeline!('answer')).toBeUndefined()
 expect(window.__recoveryTimeline?.rows[0]).toMatchObject({ phase: 'snapshot', session: 1, statePresent: false })
 expect(window.__recoveryTimeline?.incomplete).toBe(false)
 expect(JSON.stringify(window.__recoveryTimeline?.rows)).not.toContain('private-target')
 delete window.__enableRecoveryTimeline
 window.__startRecoveryTimeline!('different')
 expect(window.__recoveryTimeline?.expected).toBe('answer')
})
it('refuses oversized or nonstring input without starting capture', () => {
 window.__enableRecoveryTimeline = true; installRecoveryTimelineBootstrap()
 window.__startRecoveryTimeline!('x'.repeat(4097))
 window.__startRecoveryTimeline!(null as never)
 expect(window.__recoveryTimeline).toBeUndefined()
})
