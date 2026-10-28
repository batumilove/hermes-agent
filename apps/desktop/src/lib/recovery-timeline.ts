// Diagnostic-only opt-in. Never retain identifiers, content, errors, or caller objects.
type Phase =
  | 'eligibility'
  | 'timer-arm'
  | 'timer-clear'
  | 'timer-fire'
  | 'timer-skip'
  | 'backend-register'
  | 'backend-unregister'
  | 'owner-register'
  | 'owner-drop'
  | 'delegate-register'
  | 'state-reset'
  | 'silence-start'
  | 'silence-verdict'
  | 'silence-stale'
  | 'delegate'
  | 'cache'
  | 'view'
  | 'history-start'
  | 'history-return'
  | 'history-error'
  | 'hydrate-publish'
type Verdict = 'ended' | 'running' | 'unknown'
type Fields = {
  verdict?: Verdict
  owned?: boolean
  busy?: boolean
  awaitingResponse?: boolean
  turnLive?: boolean
  needsInput?: boolean
  active?: boolean
  backend?: boolean
  timer?: boolean
  reason?: 'event' | 'ineligible' | 'drop' | 'reset' | 'timeout' | 'publish'
}
type Row = Fields & {
  phase: Phase
  ms: number
  verdict?: Verdict
  owned?: boolean
  content?: 'partial' | 'full' | 'empty' | 'unknown'
}
type Capture = { expected: string; rows: Row[] }
declare global {
  interface Window {
    __recoveryTimeline?: Capture
  }
}
const phases = new Set<Phase>([
  'eligibility', 'timer-arm', 'timer-clear', 'timer-fire', 'timer-skip',
  'backend-register', 'backend-unregister', 'owner-register', 'owner-drop', 'delegate-register', 'state-reset',
  'silence-start',
  'silence-verdict',
  'silence-stale',
  'delegate',
  'cache',
  'view',
  'history-start',
  'history-return',
  'history-error',
  'hydrate-publish'
])
export function recoveryTimeline(
  phase: Phase,
  fields: Fields | (() => Fields) = {},
  text?: string | (() => string)
) {
  // All diagnostic evaluation is lazy and isolated from product recovery.
  try {
    if (typeof window === 'undefined') return
    const capture = window.__recoveryTimeline
    if (!capture || !Array.isArray(capture.rows) || capture.rows.length >= 1000 || !phases.has(phase)) return
    if (typeof fields === 'function') fields = fields()
    if (typeof text === 'function') text = text()
    const row: Row = { phase, ms: Math.max(0, Math.min(86400000, Math.floor(performance.now()))) }
    if (fields.verdict === 'ended' || fields.verdict === 'running' || fields.verdict === 'unknown')
      row.verdict = fields.verdict
    if (typeof fields.owned === 'boolean') row.owned = fields.owned
    for (const key of ['busy', 'awaitingResponse', 'turnLive', 'needsInput', 'backend', 'timer', 'active'] as const)
      if (typeof fields[key] === 'boolean') row[key] = fields[key]
    if (['event', 'ineligible', 'drop', 'reset', 'timeout', 'publish'].includes(fields.reason ?? ''))
      row.reason = fields.reason
    if (typeof text === 'string')
      row.content = !text
        ? 'empty'
        : typeof capture.expected !== 'string' || !capture.expected
          ? 'unknown'
          : text.includes(capture.expected)
            ? 'full'
            : 'partial'
    capture.rows.push(row)
  } catch {
    // Optional telemetry must never alter product publication or error handling.
  }
}
