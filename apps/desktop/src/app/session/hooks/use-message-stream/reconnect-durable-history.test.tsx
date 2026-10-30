import type { GatewayEventName } from '@hermes/shared'
import { QueryClient } from '@tanstack/react-query'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { hydrateStoredSessionTranscript } from '@/app/contrib/hooks/use-background-sync'
import { useSessionTileDelegate } from '@/app/contrib/hooks/use-session-tile-delegate'
import { chatMessageText } from '@/lib/chat-messages'
import { clearInFlightTurnJournal } from '@/lib/inflight-turn-journal'
import { $messages, setActiveSessionId, setMessages } from '@/store/session'
import { $sessionStates, reconcileBusyStatesOnReconnect, sessionTileDelegate } from '@/store/session-states'

import { usePromptActions } from '../use-prompt-actions'
import { useSessionStateCache } from '../use-session-state-cache'

import { useMessageStream } from './index'

const SID = 'reconnect-r2-runtime'
const STORED = 'reconnect-r2-stored'
const noop = async () => undefined

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  $sessionStates.set({})
  setMessages([])
  setActiveSessionId(SID)
})
afterEach(() => {
  cleanup()
  clearInFlightTurnJournal(STORED)
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

// Replays the byte-for-byte response from real SessionDB append/close/reopen
// through the actual ASGI messages route. Electron IPC alone is an adapter.
it.each(['late-complete', 'newer-turn', 'submit-before-idle', 'start-before-idle'] as const)('recovers durable history with %s fencing', async mode => {
  const response = await import('./reconnect-durable-history.fixture.json')
  let release: (() => void) | undefined
  const pending = new Promise<void>(resolve => { release = resolve })
  const api = vi.fn(async ({ path }: { path: string }) => {
    expect(path).toContain(`/api/sessions/${STORED}/messages`)
    if (mode === 'newer-turn') {
      await pending
    }
    return response.default
  })
  vi.stubGlobal('hermesDesktop', { api, notify: noop })
  const hydrate = vi.fn(hydrateStoredSessionTranscript)
  const queryClient = new QueryClient()
  const requestGateway = async <T,>(method: string, params?: Record<string, unknown>): Promise<T> =>
    (method === 'prompt.submit'
      ? { admission_id: params?.submission_id, session_id: SID, status: 'started' }
      : { status: 'redirected' }) as T
  const hook = renderHook(() => {
    const busyRef = useRef(false)
    const cache = useSessionStateCache({
      activeSessionId: SID, selectedStoredSessionId: STORED, busyRef,
      setAwaitingResponse: () => undefined,
      setBusy: value => { busyRef.current = value }, setMessages
    })
    useSessionTileDelegate({
      archiveSession: noop, branchLoadedSession: async () => false,
      branchStoredSession: noop, executeSlashCommand: async () => false,
      removeSession: noop, requestGateway,
      runtimeIdByStoredSessionIdRef: cache.runtimeIdByStoredSessionIdRef,
      sessionStateByRuntimeIdRef: cache.sessionStateByRuntimeIdRef,
      updateSessionState: cache.updateSessionState
    })
    const stream = useMessageStream({
      activeSessionIdRef: cache.activeSessionIdRef,
      sessionStateByRuntimeIdRef: cache.sessionStateByRuntimeIdRef,
      updateSessionState: cache.updateSessionState, queryClient,
      hydrateFromStoredSession: (attempts = 1, storedSessionId = STORED, runtimeSessionId = SID) => {
        if (storedSessionId === null || runtimeSessionId === null) {
          throw new Error('Reconnect hydration requires stored and runtime session IDs')
        }

        return hydrate({ attempts, storedSessionId, runtimeSessionId, storedProfile: 'default', updateSessionState: cache.updateSessionState })
      },
      refreshHermesConfig: noop, refreshSessions: noop
    })
    const actions = usePromptActions({
      activeSessionId: SID, activeSessionIdRef: cache.activeSessionIdRef,
      branchCurrentSession: async () => true, busyRef,
      createBackendSessionForSend: async () => SID,
      getRoutedStoredSessionId: () => STORED, getRuntimeIdForStoredSession: () => SID,
      getRouteToken: () => 'r2-route', handleSkinCommand: () => '', openMemoryGraph: () => undefined,
      refreshSessions: noop, requestGateway, resumeStoredSession: noop,
      runtimeIdByStoredSessionIdRef: cache.runtimeIdByStoredSessionIdRef,
      selectedStoredSessionIdRef: cache.selectedStoredSessionIdRef,
      startFreshSessionDraft: () => undefined, sttEnabled: false, updateSessionState: cache.updateSessionState
    })
    return { cache, stream, actions }
  })
  const state = () => hook.result.current.cache.sessionStateByRuntimeIdRef.current.get(SID)!
  const send = (type: GatewayEventName, payload: Record<string, unknown> = {}) =>
    act(() => hook.result.current.stream.handleGatewayEvent({ type, payload, session_id: SID }))
  act(() => hook.result.current.cache.ensureSessionState(SID, STORED))
  await act(async () => { expect(await hook.result.current.actions.submitText('Reply after drop', { attachments: [] })).toBe(true) })
  await send('message.start')
  await send('message.delta', { text: 'A8-9wmhbw' })
  await act(async () => { await vi.advanceTimersByTimeAsync(50) })
  expect(state().busy).toBe(true)
  expect(state().turnLive).toBe(true)
  const streamId = state().streamId
  expect(streamId).toBeTruthy()
  const retire = vi.spyOn(sessionTileDelegate()!, 'retireBusyClaim')
  act(() => reconcileBusyStatesOnReconnect())
  expect(retire).toHaveBeenCalledWith(SID)
  expect(retire).toHaveReturnedWith(true)
  expect(state().busy).toBe(false)
  expect(state().awaitingResponse).toBe(false)
  expect(state().turnLive).toBe(false)
  expect(state().streamId).toBe(streamId)
  expect(state().reconnectHistoryPending).toBe(true)
  if (mode === 'submit-before-idle' || mode === 'start-before-idle') {
    if (mode === 'submit-before-idle') {
      await act(async () => { expect(await hook.result.current.actions.submitText('Newer request', { attachments: [] })).toBe(true) })
    }
    if (mode === 'start-before-idle') {
      await send('message.start')
      await send('message.delta', { text: 'NEWER LIVE ANSWER' })
      await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    }
    expect(state().reconnectHistoryPending).toBe(false)
    const before = state().messages.map(chatMessageText)
    await send('session.info', { running: false, stored_session_id: STORED })
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    expect(api).not.toHaveBeenCalled()
    expect(state().messages.map(chatMessageText)).toEqual(before)
    if (mode === 'submit-before-idle') {
      expect(state().busy).toBe(true)
    }
    hook.unmount()
    queryClient.clear()
    return
  }
  await send('session.info', { running: false, stored_session_id: STORED })
  expect(state().reconnectHistoryPending).toBe(false)
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  if (mode === 'newer-turn') {
    expect(api).toHaveBeenCalledTimes(1)
    await act(async () => { expect(await hook.result.current.actions.submitText('Newer request', { attachments: [] })).toBe(true) })
    await send('message.start')
    await send('message.delta', { text: 'NEWER LIVE ANSWER' })
    await act(async () => { await vi.advanceTimersByTimeAsync(50) })
    const before = $messages.get()
    await act(async () => { release!(); await Promise.resolve() })
    expect($messages.get()).toBe(before)
    expect(state().busy).toBe(true)
    expect(state().turnLive).toBe(true)
    hook.unmount()
    queryClient.clear()
    return
  }
  expect($messages.get().filter(m => m.role === 'assistant').map(chatMessageText)).toEqual(['A8-9wmhbw survives the drop'])
  expect(api).toHaveBeenCalledTimes(1)
  await send('session.info', { running: false, stored_session_id: STORED })
  await send('message.complete', { text: 'A8-9wmhbw survives the drop' })
  expect($messages.get().filter(m => m.role === 'assistant').map(chatMessageText)).toEqual(['A8-9wmhbw survives the drop'])
  await send('message.complete', { text: 'A8-9wmhbw survives the drop' })
  expect($messages.get().filter(m => m.role === 'assistant').map(chatMessageText)).toEqual(['A8-9wmhbw survives the drop'])
  expect(api).toHaveBeenCalledTimes(1)
  hook.unmount()
  queryClient.clear()
})
