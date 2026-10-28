// Diagnostic-only opt-in. Never retain identifiers, content, errors, or caller objects.
type Phase =
  | 'snapshot'
  | 'rpc-route'
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
export type RouteOutcome = 'missing-backend' | 'unresolved' | 'refused' | 'resolved-dispatch' | 'request-success' | 'request-failure'
type Fields = {
  statePresent?: boolean
  eventOwner?: boolean
  delegate?: boolean
  outcome?: RouteOutcome
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
  session?: number
  phase: Phase
  ms: number
  verdict?: Verdict
  owned?: boolean
  content?: 'partial' | 'full' | 'empty' | 'unknown'
}
type Capture = { expected: string; rows: Row[]; overflow?: boolean; incomplete?: boolean; targeted?: boolean }
declare global {
  interface Window {
    __recoveryTimeline?: Capture
  }
}
// Binding is private and never serialized; all target rows use the bounded ordinal 1.
let binding: { capture: Capture; ids: Set<string> } | undefined
export function bindRecoveryTimeline(expected: string, ids: string[]) {
  try {
    const capture: Capture = { expected, rows: [], targeted: true, overflow: false, incomplete: ids.length === 0 }
    window.__recoveryTimeline = capture
    binding = { capture, ids: new Set(ids.slice(0, 2)) }
  } catch { /* Diagnostic-only. */ }
}
const phases = new Set<Phase>([
  'snapshot', 'rpc-route',
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
  text?: string | (() => string),
  sessionId?: string | null
) {
  // All diagnostic evaluation is lazy and isolated from product recovery.
  let current: Capture | undefined
  try {
    if (typeof window === 'undefined') return
    const capture = window.__recoveryTimeline
    current = capture
    if (!capture || !Array.isArray(capture.rows) || !phases.has(phase)) return
    if (capture.targeted && (binding?.capture !== capture || (sessionId != null && !binding.ids.has(sessionId)))) return
    if (capture.rows.length >= 1000) { capture.overflow = true; capture.incomplete = true; return }
    if (typeof fields === 'function') fields = fields()
    if (typeof text === 'function') text = text()
    const row: Row = { phase, ms: Math.max(0, Math.min(86400000, Math.floor(performance.now()))) }
    if (capture.targeted) row.session = 1
    if (['missing-backend', 'unresolved', 'refused', 'resolved-dispatch', 'request-success', 'request-failure'].includes(fields.outcome ?? '')) row.outcome = fields.outcome
    if (fields.verdict === 'ended' || fields.verdict === 'running' || fields.verdict === 'unknown')
      row.verdict = fields.verdict
    if (typeof fields.owned === 'boolean') row.owned = fields.owned
    for (const key of ['busy', 'awaitingResponse', 'turnLive', 'needsInput', 'backend', 'timer', 'active', 'statePresent', 'eventOwner', 'delegate'] as const)
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
    try { if (current) current.incomplete = true } catch { /* Frozen sink. */ }
    // Optional telemetry must never alter product publication or error handling.
  }
}
