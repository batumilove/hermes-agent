import { registerHooks } from 'node:module'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'
import { test } from 'node:test'
registerHooks({ resolve(specifier, context, next) {
  if (specifier.endsWith('.js') && specifier.startsWith('.')) {
    const url = new URL(specifier.replace(/\.js$/, '.ts'), context.parentURL)
    if (existsSync(fileURLToPath(url))) return next(url.href, context)
  }
  return next(specifier, context)
} })
const { JsonRpcGatewayClient } = await import('./json-rpc-gateway.ts')
class Socket extends EventTarget {
  readyState = 0
  constructor(owner) { super(); this.owner = owner }
  open() { this.readyState = 1; this.dispatchEvent(new Event('open')) }
  close() { this.readyState = 3; this.owner.attached = null; this.dispatchEvent(new Event('close')) }
  frame(frame) { this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(frame) })) }
  send(text) { this.owner.receive(this, JSON.parse(text)) }
}
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
for (const phase of ['before-replay', 'snapshot-to-attach', 'after-attach']) {
  test(`deferred owner: ${phase}`, async () => {
    const snapshot = deferred(), attach = deferred(), attached = deferred()
    const ring = [], delivered = [], requests = []
    let persisted = null, pendingSnapshot, pendingAttach
    const owner = {
      attached: null,
      receive(socket, frame) {
        requests.push(frame.method)
        if (frame.method === 'session.events.since') {
          pendingSnapshot = () => socket.frame({ id: frame.id, result: { events: [...ring], replay_epoch: 'epoch', latest_seq: ring.at(-1)?.seq ?? 1 } })
          snapshot.resolve()
        } else if (frame.method === 'session.activate') {
          pendingAttach = () => {
            owner.attached = socket
            socket.frame({ id: frame.id, result: { running: !persisted, messages: [], messages_omitted: true } })
            attached.resolve()
          }
          attach.resolve()
        }
      }
    }
    const complete = () => {
      persisted = 'A8 survives the drop'
      const event = { type: 'message.complete', session_id: 's', seq: 2, replay_epoch: 'epoch', payload: { content: persisted } }
      ring.push(event)
      owner.attached?.frame({ method: 'event', params: event })
    }
    const sockets = []
    const client = new JsonRpcGatewayClient({ heartbeatIntervalMs: 0, connectTimeoutMs: 0, socketFactory: () => { const s = new Socket(owner); sockets.push(s); return s } })
    client.onAny(event => { if (event.type === 'message.complete') delivered.push(event.payload.content) })
    let connected = client.connect('ws://fixture.invalid'); sockets[0].open(); await connected
    sockets[0].frame({ method: 'event', params: { type: 'message.start', session_id: 's', seq: 1, replay_epoch: 'epoch', payload: {} } })
    client.invalidate()
    if (phase === 'before-replay') complete()
    connected = client.connect('ws://fixture.invalid'); sockets[1].open(); await connected
    await snapshot.promise
    // Actual client replay request snapshots without subscribing the replacement.
    const replay = client.sessionReplayBarrier('s')
    pendingSnapshot()
    if (phase === 'snapshot-to-attach') complete()
    await replay
    // Exact warm-resume ordering, not the React hook: replay, activate, REST.
    const activated = client.request('session.activate', { session_id: 's', omit_messages: true })
    await attach.promise; pendingAttach(); await attached.promise; await activated
    if (phase === 'after-attach') complete()
    const transportExpected = phase === 'snapshot-to-attach' && !process.env.EXPECT_LOSSLESS ? [] : ['A8 survives the drop']
    assert.deepEqual(delivered, transportExpected)
    assert.deepEqual(requests, ['session.events.since', 'session.activate'])
    // The hook also reads authoritative persisted history AFTER attachment.
    // This fixture-fed read is a control, not execution of Desktop hydration.
    assert.equal(persisted, 'A8 survives the drop')
    console.log(JSON.stringify({ phase, delivered, persisted, requests }))
    client.close()
  })
}
