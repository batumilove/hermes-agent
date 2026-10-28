import { afterEach, expect, it, vi } from 'vitest'
import type { ClientSessionState } from '@/app/types'
import { $activeSessionId, $selectedStoredSessionId, setSessions } from '@/store/session'
import { clearAllSessionStates, publishSessionState, noteSessionEvent, setLiveTurnBackend, recordSessionEventScope, LIVE_TURN_EVENT_SILENCE_MS, startRecoveryTimeline } from '@/store/session-states'
import { $profiles } from '@/store/profile'
import { recoveryTimeline } from './recovery-timeline'
const state = { busy: true, awaitingResponse: false, turnLive: true, needsInput: false, messages: [], storedSessionId: 'private-stored' } as unknown as ClientSessionState
afterEach(() => { delete window.__recoveryTimeline; clearAllSessionStates(); setSessions([]); $activeSessionId.set(null); $selectedStoredSessionId.set(null); vi.useRealTimers() })
it('synchronously snapshots a preexisting timer and registrations without publishing or making requests', () => {
 vi.useFakeTimers(); clearAllSessionStates(); $activeSessionId.set('private-target')
 const request = vi.fn(async () => ({ sessions: [] }) as never)
 const stop = setLiveTurnBackend({ request })
 recordSessionEventScope({ session_id: 'private-target', connectionId: 'private-connection', profile: 'private-profile' })
 publishSessionState('private-target', state); noteSessionEvent('private-target')
 const timers = vi.getTimerCount()
 startRecoveryTimeline('private-answer')
 expect(window.__recoveryTimeline?.rows[0]).toMatchObject({ phase: 'snapshot', session: 1, statePresent: true, busy: true, awaitingResponse: false, turnLive: true, needsInput: false, backend: true, timer: true, eventOwner: true, delegate: expect.any(Boolean) })
 expect(request).not.toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(timers)
 expect(JSON.stringify(window.__recoveryTimeline?.rows)).not.toMatch(/private/)
 stop()
})
it('filters two interleaved runtimes across timer and transcript phases with an opaque ordinal', async () => {
 vi.useFakeTimers(); clearAllSessionStates(); $activeSessionId.set('private-target')
 const stop = setLiveTurnBackend({ request: async () => ({ sessions: [] }) as never })
 publishSessionState('private-target', state); publishSessionState('private-other', { ...state, storedSessionId: 'other-stored' })
 startRecoveryTimeline('private-answer')
 noteSessionEvent('private-other'); noteSessionEvent('private-target')
 recoveryTimeline('history-return', {}, 'private-answer', 'other-stored')
 recoveryTimeline('history-return', {}, 'private-answer', 'private-stored')
 await vi.advanceTimersByTimeAsync(LIVE_TURN_EVENT_SILENCE_MS)
 const rows = window.__recoveryTimeline!.rows
 expect(rows.filter(row => row.phase === 'timer-fire')).toHaveLength(1)
 expect(rows.filter(row => row.phase === 'history-return')).toHaveLength(1)
 expect(rows.every(row => row.session === 1)).toBe(true)
 expect(JSON.stringify(rows)).not.toMatch(/private|other-stored/)
 stop()
})
it('observes actual RPC routing separately from absent event membership and preserves exact requests', async () => {
 vi.useFakeTimers(); clearAllSessionStates(); $activeSessionId.set('private-target')
 // Legacy sole-backend permission admits the request despite absent owner metadata.
 const request = vi.fn(async () => ({ sessions: [] }) as never)
 const stop = setLiveTurnBackend({ request }); publishSessionState('private-target', state)
 startRecoveryTimeline('answer'); noteSessionEvent('private-target')
 await vi.advanceTimersByTimeAsync(LIVE_TURN_EVENT_SILENCE_MS)
 const rows = window.__recoveryTimeline!.rows
 expect(rows[0].eventOwner).toBe(false)
 expect(rows.filter(row => row.phase === 'rpc-route').map(row => row.outcome)).toEqual(['unresolved', 'resolved-dispatch', 'request-success'])
 expect(request).toHaveBeenCalledTimes(1)
 expect(request.mock.calls[0]).toEqual(['session.active_list', {}, 15000])
 stop()
})
it('marks overflow and missing target explicitly, containing throwing sinks and disabled getters', () => {
 clearAllSessionStates(); $activeSessionId.set(null); startRecoveryTimeline('answer')
 expect(window.__recoveryTimeline!.incomplete).toBe(true)
 $activeSessionId.set('private-target'); startRecoveryTimeline('answer')
 for(let i=0;i<1100;i++) recoveryTimeline('cache', {}, undefined, 'private-target')
 expect(window.__recoveryTimeline!.overflow).toBe(true)
 expect(window.__recoveryTimeline!.incomplete).toBe(true)
 expect(window.__recoveryTimeline!.rows).toHaveLength(1000)
 Object.defineProperty(window, '__recoveryTimeline', { configurable: true, get() { throw Error('private') } })
 expect(() => noteSessionEvent('private-target')).not.toThrow()
 delete window.__recoveryTimeline
 const getter = vi.fn(() => { throw Error('private') })
 recoveryTimeline('cache', getter, getter, 'private-target')
 expect(getter).not.toHaveBeenCalled()
})

it.each(['missing-backend', 'refused', 'request-failure'] as const)('records actual %s without duplicate requests', async outcome => {
 vi.useFakeTimers(); clearAllSessionStates(); $activeSessionId.set('private-target')
 const prior = $profiles.get()
 const request = vi.fn(async () => { throw Error('private-error') })
 const stop = setLiveTurnBackend({ request })
 if (outcome === 'missing-backend') stop()
 if (outcome === 'refused') $profiles.set([{ name: 'one' }, { name: 'two' }] as never)
 try {
  publishSessionState('private-target', state); startRecoveryTimeline('answer'); noteSessionEvent('private-target')
  await vi.advanceTimersByTimeAsync(LIVE_TURN_EVENT_SILENCE_MS)
  expect(window.__recoveryTimeline!.rows).toContainEqual(expect.objectContaining({ phase: 'rpc-route', outcome, session: 1 }))
  expect(request).toHaveBeenCalledTimes(outcome === 'request-failure' ? 1 : 0)
  expect(JSON.stringify(window.__recoveryTimeline!.rows)).not.toMatch(/private/)
 } finally { stop(); $profiles.set(prior) }
})
it('contains throwing capture getters without changing the original timer and RPC oracle', async () => {
 vi.useFakeTimers(); clearAllSessionStates(); $activeSessionId.set('private-target')
 const request = vi.fn(async () => ({ sessions: [] }) as never)
 const stop = setLiveTurnBackend({ request }); publishSessionState('private-target', state)
 Object.defineProperty(window, '__recoveryTimeline', { configurable: true, get() { throw Error('private') } })
 noteSessionEvent('private-target')
 await vi.advanceTimersByTimeAsync(LIVE_TURN_EVENT_SILENCE_MS)
 expect(request).toHaveBeenCalledTimes(1)
 expect(request.mock.calls[0]).toEqual(['session.active_list', {}, 15000])
 delete window.__recoveryTimeline; stop()
})

it('rejects injected ordinal, open route outcomes and nonboolean snapshot fields', () => {
 window.__recoveryTimeline = { expected: '', rows: [] }
 recoveryTimeline('rpc-route', { outcome: 'private-route', session: 'private-id', statePresent: 'private', eventOwner: {}, delegate: 'private' } as never)
 expect(Object.keys(window.__recoveryTimeline.rows[0]).sort()).toEqual(['ms', 'phase'])
 expect(JSON.stringify(window.__recoveryTimeline.rows)).not.toMatch(/private/)
})
