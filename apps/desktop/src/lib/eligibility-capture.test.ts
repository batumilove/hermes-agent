import { afterEach, expect, it, vi } from 'vitest'
import type { ClientSessionState } from '@/app/types'
import { $activeSessionId } from '@/store/session'
import { clearAllSessionStates, publishSessionState, noteSessionEvent, setLiveTurnBackend, recordSessionEventScope, dropSessionState, LIVE_TURN_EVENT_SILENCE_MS } from '@/store/session-states'
import { recoveryTimeline } from './recovery-timeline'

afterEach(() => { delete window.__recoveryTimeline; clearAllSessionStates(); vi.useRealTimers() })
it('captures actual publish eligibility, timer decisions and backend/owner lifecycle without identifiers', async () => {
  vi.useFakeTimers()
  clearAllSessionStates()
  window.__recoveryTimeline = { expected: '', rows: [] }
  $activeSessionId.set('private-runtime')
  const unregister = setLiveTurnBackend({ request: async () => ({ sessions: [] }) as never })
  recordSessionEventScope({ session_id: 'private-runtime', connectionId: 'private-connection', profile: 'private-profile' })
  const state = { busy: true, awaitingResponse: false, turnLive: true, needsInput: false, messages: [], storedSessionId: null } as unknown as ClientSessionState
  publishSessionState('private-runtime', state)
  noteSessionEvent('private-runtime')
  await vi.advanceTimersByTimeAsync(LIVE_TURN_EVENT_SILENCE_MS)
  publishSessionState('private-runtime', { ...state, busy: false, turnLive: false, needsInput: true })
  noteSessionEvent('private-runtime')
  dropSessionState('private-runtime')
  unregister()
  const rows = window.__recoveryTimeline.rows
  for (const phase of ['eligibility', 'timer-arm', 'timer-clear', 'timer-fire', 'timer-skip', 'backend-register', 'backend-unregister', 'owner-register', 'owner-drop']) expect(rows.some(row => row.phase === phase)).toBe(true)
  expect(rows).toContainEqual(expect.objectContaining({ phase: 'eligibility', busy: true, awaitingResponse: false, turnLive: true, needsInput: false }))
  expect(JSON.stringify(rows)).not.toMatch(/private-runtime|private-connection|private-profile/)
})
it('restricts new fields to booleans and closed reasons; stays lazy and bounded', () => {
  let reads = 0
  const lazy = () => { reads++; throw Error('private') }
  recoveryTimeline('eligibility' as never, lazy)
  expect(reads).toBe(0)
  window.__recoveryTimeline = { expected: '', rows: [] }
  recoveryTimeline('eligibility' as never, { busy: true, awaitingResponse: 'private', turnLive: {}, needsInput: false, reason: 'private', raw: 'private' } as never)
  expect(window.__recoveryTimeline.rows[0]).toEqual(expect.objectContaining({ phase: 'eligibility', busy: true, needsInput: false }))
  expect(JSON.stringify(window.__recoveryTimeline.rows)).not.toMatch(/private|awaitingResponse|turnLive|reason|raw/)
  expect(() => recoveryTimeline('eligibility' as never, lazy)).not.toThrow()
  for (let i = 0; i < 1100; i++) recoveryTimeline('eligibility' as never)
  expect(window.__recoveryTimeline.rows).toHaveLength(1000)
  recoveryTimeline('eligibility' as never, lazy)
  expect(reads).toBe(1)
})
