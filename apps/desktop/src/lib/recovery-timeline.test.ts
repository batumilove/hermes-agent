import { expect, it } from 'vitest'
import { recoveryTimeline } from './recovery-timeline'
import sessionsSource from '../api/sessions.ts?raw'
import backgroundSource from '../app/contrib/hooks/use-background-sync.ts?raw'
import cacheSource from '../app/session/hooks/use-session-state-cache.ts?raw'
it('keeps actual transcript callsites inert when disabled and nonthrowing when enabled', () => {
  let evaluated = 0
  const poison = new Proxy({}, { get() { evaluated++; throw new Error('transcript sentinel') } })
  const convert = () => { evaluated++; throw new Error('conversion sentinel') }
  const calls = [sessionsSource, backgroundSource, cacheSource].flatMap(source =>
    [...source.matchAll(/recoveryTimeline\(\n[\s\S]*?\n      \)/g)].map(match => match[0])
  )
  expect(calls).toHaveLength(4)
  for (const call of calls) {
    const invoke = new Function('recoveryTimeline', 'authoritativePage', 'latest', 'nextMessages', 'next', 'toChatMessages', 'chatMessageText', call)
    delete window.__recoveryTimeline
    expect(() => invoke(recoveryTimeline, poison, { messages: [] }, poison, poison, convert, convert)).not.toThrow()
    expect(evaluated).toBe(0)
  }
  for (const call of calls) {
    window.__recoveryTimeline = { expected: 'answer', rows: [] }
    const invoke = new Function('recoveryTimeline', 'authoritativePage', 'latest', 'nextMessages', 'next', 'toChatMessages', 'chatMessageText', call)
    expect(() => invoke(recoveryTimeline, poison, { messages: [] }, poison, poison, convert, convert)).not.toThrow()
  }
  expect(evaluated).toBe(4)
  delete window.__recoveryTimeline
})
it('does not evaluate disabled capture payloads', () => {
  delete window.__recoveryTimeline
  let evaluated = false
  recoveryTimeline('cache', {}, (() => { evaluated = true; throw new Error('telemetry only') }) as never)
  expect(evaluated).toBe(false)
})
it('evaluates enabled capture lazily and contains payload failures', () => {
  window.__recoveryTimeline = { expected: 'answer', rows: [] }
  let evaluated = 0
  recoveryTimeline('history-return', {}, (() => { evaluated++; return 'answer' }) as never)
  expect(evaluated).toBe(1)
  expect(window.__recoveryTimeline.rows[0]?.content).toBe('full')
  expect(() => recoveryTimeline('history-return', {}, (() => { throw new Error('conversion failed') }) as never)).not.toThrow()
  // Telemetry failure cannot divert the surrounding product recovery try/catch.
  let published = false
  let fallback = false
  try {
    recoveryTimeline('cache', {}, (() => { throw new Error('telemetry') }) as never)
    published = true
  } catch { fallback = true }
  expect({ published, fallback }).toEqual({ published: true, fallback: false })
  delete window.__recoveryTimeline
})
it('contains enabled capture sink failures', () => {
  window.__recoveryTimeline = { expected: 'answer', rows: Object.freeze([]) as never }
  expect(() => recoveryTimeline('cache')).not.toThrow()
  delete window.__recoveryTimeline
})
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
