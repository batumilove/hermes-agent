import { type GatewayEvent, JsonRpcGatewayClient } from '@hermes/shared'
import { QueryClient } from '@tanstack/react-query'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/api/client'
import { hydrateStoredSessionTranscript, reconcileActiveTranscript } from '@/app/contrib/hooks/use-background-sync'
import { useSessionTileDelegate } from '@/app/contrib/hooks/use-session-tile-delegate'
import { getLatestSessionMessages } from '@/hermes'
import { chatMessageText, toChatMessages } from '@/lib/chat-messages'
import { resetInFlightTurnJournalStateForTests } from '@/lib/inflight-turn-journal'
import { setPrimaryGateway } from '@/store/gateway'
import { $activeGatewayProfile } from '@/store/profile'
import {
  $messages,
  _resetSessionOwnerHintsForTests,
  setActiveSessionId,
  setAwaitingResponse,
  setBusy,
  setConnection,
  setMessages,
  setSelectedStoredSessionId,
  setSessions
} from '@/store/session'
import {
  $sessionStates,
  clearAllSessionStates,
  LIVE_TURN_EVENT_SILENCE_MS,
  noteSessionEvent,
  setLiveTurnBackend
} from '@/store/session-states'
import type { SessionMessage, SessionResumeResult } from '@/types/hermes'

import { useMessageStream } from './use-message-stream'
import { useSessionActions } from './use-session-actions'
import { useSessionStateCache } from './use-session-state-cache'

vi.mock('@/hermes', async original => ({
  ...(await original<Record<string, unknown>>()),
  getLatestSessionMessages: vi.fn()
}))
vi.mock('@/store/profile', async original => ({
  ...(await original<Record<string, unknown>>()),
  ensureGatewayProfile: vi.fn().mockResolvedValue(undefined)
}))

const storedId = 'warm-replay-stored'
const runtimeId = 'warm-replay-runtime'
const noop = async () => undefined
const user: SessionMessage = { id: 1, role: 'user', content: 'Review example', timestamp: 1 }

class Socket extends EventTarget {
  readyState = 0
  sent: { id: string; method: string; params: Record<string, unknown> }[] = []
  send(data: string) {
    this.sent.push(JSON.parse(data))
  }
  close() {
    this.readyState = 3
    this.dispatchEvent(new CloseEvent('close'))
  }
  open() {
    this.readyState = 1
    this.dispatchEvent(new Event('open'))
  }
  frame(frame: unknown) {
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(frame) }))
  }
  event(event: GatewayEvent) {
    this.frame({ jsonrpc: '2.0', method: 'event', params: event })
  }
}

function event(seq: number, type: GatewayEvent['type'], payload: Record<string, unknown> = {}): GatewayEvent {
  return { session_id: runtimeId, seq, type, payload: { timestamp: seq, ...payload } } as GatewayEvent
}

const replay = [
  event(2, 'message.start'),
  event(3, 'message.delta', { text: 'Finished result.' }),
  event(4, 'message.complete', { text: 'Finished result.' }),
  event(5, 'session.info', { running: false })
]

const snapshot: SessionResumeResult = {
  session_id: runtimeId,
  resumed: storedId,
  messages: [],
  message_count: 0,
  running: false
}

async function mountWithPendingReplay() {
  const requestGateway = vi.fn().mockResolvedValue(snapshot)

  const hook = renderHook(() => {
    const busyRef = useRef(false)
    const creatingSessionRef = useRef(false)
    const queryClient = useRef(new QueryClient()).current

    const cache = useSessionStateCache({
      activeSessionId: null,
      selectedStoredSessionId: null,
      busyRef,
      setMessages,
      setBusy,
      setAwaitingResponse
    })

    const actions = useSessionActions({
      ...cache,
      activeSessionId: null,
      selectedStoredSessionId: null,
      busyRef,
      creatingSessionRef,
      getRouteToken: () => 'A',
      getRoutedStoredSessionId: () => null,
      navigate: vi.fn(),
      requestGateway,
      routedSessionId: null
    })

    const stream = useMessageStream({
      ...cache,
      queryClient,
      hydrateFromStoredSession: noop,
      refreshHermesConfig: noop,
      refreshSessions: noop
    })

    useSessionTileDelegate({
      ...cache,
      requestGateway,
      archiveSession: noop,
      branchLoadedSession: actions.branchLoadedSession,
      branchStoredSession: noop,
      executeSlashCommand: vi.fn(),
      removeSession: noop
    })

    return { cache, actions, stream }
  })

  const sockets: Socket[] = []

  const client = new JsonRpcGatewayClient({
    heartbeatIntervalMs: 0,
    heartbeatDeadlineMs: 0,
    socketFactory: () => {
      const socket = new Socket()
      sockets.push(socket)

      return socket as unknown as WebSocket
    }
  })

  // The store types the primary as the desktop subclass; the barrier under test lives on the shared base.
  setPrimaryGateway(client as unknown as HermesGateway)
  client.onEvent(gatewayEvent => hook.result.current.stream.handleGatewayEvent(gatewayEvent))

  const connect = async () => {
    const promise = client.connect('ws://fixture.test')
    const socket = sockets.at(-1)!
    socket.open()
    await promise

    return socket
  }

  // Warm cache: the runtime is already known and has seen seq 1.
  act(() => {
    hook.result.current.cache.updateSessionState(
      runtimeId,
      state => ({ ...state, messages: toChatMessages([user]) }),
      storedId
    )
  })
  const first = await connect()
  act(() => first.event(event(1, 'session.info', { running: false })))
  client.invalidate()
  const second = await connect()
  const request = second.sent.find(item => item.method === 'session.events.since')!
  expect(request.params).toMatchObject({ session_id: runtimeId, last_seen: 1 })

  return { ...hook, client, connect, requestGateway, second, request }
}

beforeEach(() => {
  localStorage.clear()
  clearAllSessionStates()
  resetInFlightTurnJournalStateForTests()
  _resetSessionOwnerHintsForTests()
  $activeGatewayProfile.set('default')
  setConnection(null)
  setMessages([])
  setActiveSessionId(null)
  setSelectedStoredSessionId(null)
  setBusy(false)
  setAwaitingResponse(false)
  setSessions([
    {
      id: storedId,
      title: storedId,
      source: 'desktop',
      message_count: 3,
      tool_call_count: 0,
      is_active: true,
      started_at: 1,
      last_active: 1,
      ended_at: null,
      model: null,
      preview: null,
      input_tokens: 0,
      output_tokens: 0
    }
  ])
  vi.mocked(getLatestSessionMessages).mockReset()
  vi.mocked(getLatestSessionMessages).mockResolvedValue({
    session_id: storedId,
    messages: [user, { id: 2, role: 'assistant', content: 'Finished result.', timestamp: 2 }]
  })
})

afterEach(() => {
  cleanup()
  clearAllSessionStates()
  resetInFlightTurnJournalStateForTests()
  setPrimaryGateway(null)
  localStorage.clear()
  setSessions([])
  setMessages([])
  setActiveSessionId(null)
  setSelectedStoredSessionId(null)
  setBusy(false)
  setAwaitingResponse(false)
  vi.restoreAllMocks()
})

it.each([false, true])(
  'repairs a completion transport gap via durable history (first REST rejects: %s)',
  async rejectFirst => {
    const { result, client, requestGateway, second, request } = await mountWithPendingReplay()
    const cache = result.current.cache
    cache.selectedStoredSessionIdRef.current = storedId
    cache.activeSessionIdRef.current = runtimeId
    act(() => {
      setSelectedStoredSessionId(storedId)
      setActiveSessionId(runtimeId)
      cache.updateSessionState(
        runtimeId,
        state => ({
          ...state,
          messages: toChatMessages([user, { id: 2, role: 'assistant', content: 'Finished', timestamp: 2 }]),
          busy: true,
          awaitingResponse: true
        }),
        storedId
      )
    })
    requestGateway.mockResolvedValue({ ...snapshot, messages_omitted: true })
    if (rejectFirst) vi.mocked(getLatestSessionMessages).mockRejectedValueOnce(new Error('fixture REST unavailable'))
    try {
      let pending!: Promise<void>
      await act(async () => {
        pending = result.current.actions.resumeSession(storedId, true)
      })
      await act(async () => {
        // Completion happened before attachment; the reconnect has nothing to replay.
        second.frame({ id: request.id, jsonrpc: '2.0', result: { events: [] } })
        await pending
      })
      expect(activateCalls(requestGateway)).toHaveLength(1)
      expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
      // Snapshot running:false intentionally cannot override pre-existing live
      // busy (#70449). The terminal stream/reaper owns that transition.
      expect(cache.sessionStateByRuntimeIdRef.current.get(runtimeId)!.busy).toBe(true)
      if (rejectFirst) {
        // Failed REST is not evidence of durable completion. Preserve partial content,
        // then exercise the existing ownership-preserving background recovery seam.
        await act(async () => {
          await new Promise(resolve => setTimeout(resolve, 30))
        })
        expect($messages.get().map(chatMessageText).join('\n')).toContain('Finished')
        expect($messages.get().map(chatMessageText).join('\n')).not.toContain('Finished result.')
        await act(async () => {
          // Existing refresh must not overwrite a still-live turn even when its
          // caller supplies idle; the canonical runtime store is a second guard.
          await reconcileActiveTranscript({
            activeSessionIdRef: cache.activeSessionIdRef,
            busyRef: { current: false },
            requestSequenceRef: { current: 0 },
            resolveSession: () => ({ profile: 'default' }),
            selectedStoredSessionIdRef: cache.selectedStoredSessionIdRef,
            signatureRef: { current: new Map() },
            updateSessionState: cache.updateSessionState
          })
        })
        expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
        await act(async () => {
          second.event(event(6, 'session.info', { running: false }))
        })
        await act(async () => {
          await reconcileActiveTranscript({
            activeSessionIdRef: cache.activeSessionIdRef,
            busyRef: { current: false },
            requestSequenceRef: { current: 0 },
            resolveSession: () => ({ profile: 'default' }),
            selectedStoredSessionIdRef: cache.selectedStoredSessionIdRef,
            signatureRef: { current: new Map() },
            updateSessionState: cache.updateSessionState
          })
        })
        expect(getLatestSessionMessages).toHaveBeenCalledTimes(2)
      }
      const state = cache.sessionStateByRuntimeIdRef.current.get(runtimeId)!
      expect(
        state.messages
          .map(chatMessageText)
          .join('\n')
          .match(/Finished result\./g)
      ).toHaveLength(1)
      expect(state.busy).toBe(!rejectFirst)
      expect(state.awaitingResponse).toBe(!rejectFirst)
      // View synchronization is intentionally RAF-coalesced.
      await act(async () => {
        await new Promise(resolve => setTimeout(resolve, 30))
      })
      expect(
        $messages
          .get()
          .map(chatMessageText)
          .join('\n')
          .match(/Finished result\./g)
      ).toHaveLength(1)
    } finally {
      client.close()
    }
  }
)

it.each(['rejected', 'partial'] as const)(
  'characterizes permanently lost terminal through actual silence timer after %s REST',
  async firstRead => {
    const { result, client, second, request } = await mountWithPendingReplay()
    const cache = result.current.cache
    const latest = vi.mocked(getLatestSessionMessages).getMockImplementation()!
    const status = vi.fn().mockResolvedValue({ sessions: [] })
    const refresh = vi.fn((runtimeSessionId: string, storedSessionId: string) =>
      hydrateStoredSessionTranscript({
        attempts: 3,
        runtimeSessionId,
        storedSessionId,
        storedProfile: 'default',
        updateSessionState: cache.updateSessionState
      })
    )
    const unregister = setLiveTurnBackend({ request: status, refreshTranscript: refresh })
    try {
      await act(async () => {
        second.frame({ id: request.id, jsonrpc: '2.0', result: { events: [] } })
        setSelectedStoredSessionId(storedId)
        setActiveSessionId(runtimeId)
        cache.selectedStoredSessionIdRef.current = storedId
        cache.activeSessionIdRef.current = runtimeId
      })
      ;(window as any).__recoveryTimeline = { expected: 'Finished result.', rows: [] }
      vi.useFakeTimers()
      act(() => {
        second.event(event(2, 'message.start'))
        second.event(event(3, 'message.delta', { text: 'Finished' }))
        noteSessionEvent(runtimeId)
      })
      expect($sessionStates.get()[runtimeId].busy).toBe(true)
      if (firstRead === 'rejected')
        vi.mocked(getLatestSessionMessages).mockRejectedValueOnce(new Error('REST temporarily unavailable'))
      else
        vi.mocked(getLatestSessionMessages).mockResolvedValueOnce({
          session_id: storedId,
          messages: [user, { id: 2, role: 'assistant', content: 'Finished', timestamp: 2 }]
        })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(LIVE_TURN_EVENT_SILENCE_MS + 1000)
      })
      const timeline = (window as any).__recoveryTimeline.rows
      expect(timeline.some((r: any) => r.phase === 'silence-verdict' && r.verdict === 'ended')).toBe(true)
      expect(timeline.some((r: any) => r.phase === 'delegate' && r.owned === true)).toBe(true)
      expect(timeline.some((r: any) => r.phase === 'cache')).toBe(true)
      expect(timeline.some((r: any) => r.phase === 'view')).toBe(true)
      expect(
        timeline.some((r: any) => r.phase === (firstRead === 'partial' ? 'history-return' : 'history-error'))
      ).toBe(true)
      if (firstRead === 'partial')
        expect(timeline.some((r: any) => r.phase === 'history-return' && r.content === 'partial')).toBe(true)
      else expect(timeline.some((r: any) => r.phase === 'history-return' && r.content === 'full')).toBe(true)
      expect(JSON.stringify(timeline)).not.toMatch(/warm-replay|Finished|unavailable/)
      expect(status).toHaveBeenCalledWith('session.active_list', {}, 15000)
      expect(refresh).toHaveBeenCalledTimes(1)
      expect(second.readyState).toBe(1)
      expect($sessionStates.get()[runtimeId].busy).toBe(false)
      // No terminal event, reconnection, reselect, or manual busy clearing supplied.
      const text = cache.sessionStateByRuntimeIdRef.current.get(runtimeId)!.messages.map(chatMessageText).join('\n')
      if (firstRead === 'partial') {
        // Best-effort hydration accepts a nonempty page; it cannot know that this
        // otherwise valid persisted answer is an earlier prefix. No retry promised.
        expect(text).toContain('Finished')
        expect(text).not.toContain('Finished result.')
        expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
        await act(async () => {
          await reconcileActiveTranscript({
            activeSessionIdRef: cache.activeSessionIdRef,
            busyRef: { current: false },
            requestSequenceRef: { current: 0 },
            resolveSession: () => ({ profile: 'default' }),
            selectedStoredSessionIdRef: cache.selectedStoredSessionIdRef,
            signatureRef: { current: new Map() },
            updateSessionState: cache.updateSessionState
          })
          await vi.advanceTimersByTimeAsync(30)
        })
      } else {
        expect(text.match(/Finished result\./g)).toHaveLength(1)
        expect(getLatestSessionMessages).toHaveBeenCalledTimes(2)
      }
      expect(
        $messages
          .get()
          .map(chatMessageText)
          .join('\n')
          .match(/Finished result\./g)
      ).toHaveLength(1)
      expect(latest).toBeDefined()
    } finally {
      unregister()
      client.close()
      vi.useRealTimers()
    }
  }
)

const activateCalls = (requestGateway: ReturnType<typeof vi.fn>) =>
  requestGateway.mock.calls.filter(([method]) => method === 'session.activate')

it('waits for reconnect replay before activating and reading warm history', async () => {
  const { result, client, requestGateway, second, request } = await mountWithPendingReplay()

  try {
    let pending!: Promise<void>
    await act(async () => {
      pending = result.current.actions.resumeSession(storedId, true)
    })
    // The completed turn is already durable; neither the activation snapshot
    // nor REST history may publish it ahead of the replay that carries it.
    expect(activateCalls(requestGateway)).toHaveLength(0)
    expect(getLatestSessionMessages).not.toHaveBeenCalled()

    await act(async () => {
      second.frame({ id: request.id, jsonrpc: '2.0', result: { events: replay } })
      await pending
    })

    expect(activateCalls(requestGateway)).toHaveLength(1)
    const rows = result.current.cache.sessionStateByRuntimeIdRef.current.get(runtimeId)!.messages
    const text = rows.map(chatMessageText).join('\n')
    expect(text).toContain('Review example')
    expect(text.match(/Finished result\./g)).toHaveLength(1)
  } finally {
    client.close()
  }
})

it('still settles a warm resume through session.activate when its replay socket is lost', async () => {
  const { result, client, requestGateway } = await mountWithPendingReplay()

  requestGateway.mockImplementation(async (method: string) => {
    if (method === 'session.activate') {
      throw new Error('session not found')
    }

    return snapshot
  })

  try {
    let pending!: Promise<void>
    await act(async () => {
      pending = result.current.actions.resumeSession(storedId, true)
    })
    expect(activateCalls(requestGateway)).toHaveLength(0)

    await act(async () => {
      client.invalidate()
      await pending
    })

    // A lost socket is not proof the runtime is alive or gone: activation
    // decides, and a gone runtime falls back to a cold resume instead of
    // leaving the view on an unrebound warm cache.
    expect(activateCalls(requestGateway)).toHaveLength(1)
    expect(requestGateway.mock.calls.some(([method]) => method === 'session.resume')).toBe(true)
  } finally {
    client.close()
  }
})

it('holds a reconnect re-resume of the selected session behind its replay (cold path)', async () => {
  const { result, client, requestGateway, second, request } = await mountWithPendingReplay()
  const { activeSessionIdRef, runtimeIdByStoredSessionIdRef, selectedStoredSessionIdRef } = result.current.cache

  // The view is streaming this runtime, but the warm mapping is gone, so the
  // route's reconnect re-resume takes the cold REST + session.resume path.
  selectedStoredSessionIdRef.current = storedId
  activeSessionIdRef.current = runtimeId
  runtimeIdByStoredSessionIdRef.current.delete(storedId)

  try {
    let pending!: Promise<void>
    await act(async () => {
      pending = result.current.actions.resumeSession(storedId, true)
    })
    expect(getLatestSessionMessages).not.toHaveBeenCalled()
    expect(requestGateway).not.toHaveBeenCalled()

    await act(async () => {
      second.frame({ id: request.id, jsonrpc: '2.0', result: { events: replay } })
      await pending
    })

    expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
    expect(requestGateway.mock.calls.some(([method]) => method === 'session.resume')).toBe(true)
    expect(
      $messages
        .get()
        .map(chatMessageText)
        .join('\n')
        .match(/Finished result\./g)
    ).toHaveLength(1)
  } finally {
    client.close()
  }
})

it('does not paint a cold re-resume read ahead of a replay redialed while REST was in flight', async () => {
  const { result, client, connect } = await mountWithPendingReplay()
  const { activeSessionIdRef, runtimeIdByStoredSessionIdRef, selectedStoredSessionIdRef } = result.current.cache
  let releaseRest!: () => void
  const restGate = new Promise<void>(resolve => (releaseRest = resolve))
  const latest = vi.mocked(getLatestSessionMessages).getMockImplementation()!
  vi.mocked(getLatestSessionMessages).mockImplementation(async (...args) => restGate.then(() => latest(...args)))
  selectedStoredSessionIdRef.current = storedId
  activeSessionIdRef.current = runtimeId
  runtimeIdByStoredSessionIdRef.current.delete(storedId)

  try {
    let pending!: Promise<void>
    await act(async () => {
      pending = result.current.actions.resumeSession(storedId, true)
    })
    // The replay socket drops (barrier false: cold resume starts its REST read)
    // and redials before that read returns, owning a fresh replay of the turn.
    let third!: Awaited<ReturnType<typeof connect>>
    await act(async () => {
      client.invalidate()
      third = await connect()
    })
    expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
    const request = third.sent.find(item => item.method === 'session.events.since')!

    await act(async () => {
      releaseRest()
      await new Promise(resolve => setTimeout(resolve, 0))
      third.frame({ id: request.id, jsonrpc: '2.0', result: { events: replay } })
      await pending
    })

    expect(
      $messages
        .get()
        .map(chatMessageText)
        .join('\n')
        .match(/Finished result\./g)
    ).toHaveLength(1)
  } finally {
    client.close()
  }
})

it('holds a background active-transcript refresh behind the reconnect replay', async () => {
  const { result, client, second, request } = await mountWithPendingReplay()

  const { activeSessionIdRef, selectedStoredSessionIdRef, sessionStateByRuntimeIdRef, updateSessionState } =
    result.current.cache

  selectedStoredSessionIdRef.current = storedId
  activeSessionIdRef.current = runtimeId

  try {
    const refresh = reconcileActiveTranscript({
      activeSessionIdRef,
      busyRef: { current: false },
      requestSequenceRef: { current: 0 },
      resolveSession: () => ({ profile: 'default' }),
      selectedStoredSessionIdRef,
      signatureRef: { current: new Map() },
      updateSessionState
    })

    await new Promise(resolve => setTimeout(resolve, 0))
    expect(getLatestSessionMessages).not.toHaveBeenCalled()

    await act(async () => {
      second.frame({ id: request.id, jsonrpc: '2.0', result: { events: replay } })
      await refresh
    })

    expect(getLatestSessionMessages).toHaveBeenCalledTimes(1)
    const text = sessionStateByRuntimeIdRef.current.get(runtimeId)!.messages.map(chatMessageText).join('\n')
    expect(text.match(/Finished result\./g)).toHaveLength(1)
  } finally {
    client.close()
  }
})
