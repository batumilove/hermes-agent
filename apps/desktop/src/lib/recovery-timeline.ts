// Diagnostic-only opt-in. Never retain identifiers, content, errors, or caller objects.
type Phase =
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
type Row = {
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
export function recoveryTimeline(phase: Phase, fields: { verdict?: Verdict; owned?: boolean } = {}, text?: string) {
  if (typeof window === 'undefined') return
  const capture = window.__recoveryTimeline
  if (!capture || !Array.isArray(capture.rows) || capture.rows.length >= 1000 || !phases.has(phase)) return
  const row: Row = { phase, ms: Math.max(0, Math.min(86400000, Math.floor(performance.now()))) }
  if (fields.verdict === 'ended' || fields.verdict === 'running' || fields.verdict === 'unknown')
    row.verdict = fields.verdict
  if (typeof fields.owned === 'boolean') row.owned = fields.owned
  if (typeof text === 'string')
    row.content = !text
      ? 'empty'
      : typeof capture.expected !== 'string' || !capture.expected
        ? 'unknown'
        : text.includes(capture.expected)
          ? 'full'
          : 'partial'
  capture.rows.push(row)
}
