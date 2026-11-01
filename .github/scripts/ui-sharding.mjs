import { availableParallelism } from 'node:os'

// Vitest 4.1.12 --filesOnly --json returns {file, projectName} records.
export function parseUiInventory(records) {
  if (!Array.isArray(records) || !records.length || records.some(r => !r || typeof r !== 'object' || Array.isArray(r) || r.projectName !== 'ui' || typeof r.file !== 'string' || !r.file.trim())) throw Error('invalid UI file inventory')
  const files = records.map(r => r.file)
  if (new Set(files).size !== files.length) throw Error('duplicate UI file inventory')
  return files.sort()
}

const key = u => `${u.pkg} :: ${u.script}`
export function planUnits(units) {
  if (!units.length || new Set(units.map(key)).size !== units.length) throw Error('empty or duplicate workspace checks')
  const ui = units.filter(u => u.pkg === 'apps/desktop' && u.script === 'check:test:ui')
  if (ui.length !== 1) throw Error('missing or duplicate Desktop UI check')
  const other = units.filter(u => u !== ui[0])
  if (!other.length) throw Error('empty non-UI check lane')
  return { ui: ui[0], other }
}
export function validatePartitions(files, partitions) {
  const all = partitions.flat()
  if (!files.length || partitions.some(p => !p.length) || new Set(files).size !== files.length || new Set(all).size !== all.length || all.length !== files.length || all.some(f => !files.includes(f))) throw Error('empty, missing, duplicate or foreign file coverage')
  return true
}
export function workerBudget(cpus = availableParallelism()) {
  if (!Number.isInteger(cpus) || cpus < 1) throw Error('invalid CPU budget')
  return Math.min(4, Math.max(1, cpus - 1))
}
export function validateReceipts(receipts, sha) {
  const expected = ['other', '1', '2', '3', '4']
  if (receipts.length !== expected.length || new Set(receipts.map(r => r.lane)).size !== expected.length || receipts.some(r => !expected.includes(r.lane) || r.sha !== sha || r.ok !== true || !r.units?.length)) throw Error('missing, duplicate or failed lane receipt')
  const plans = receipts.map(r => planUnits(r.discovered))
  const discovered = JSON.stringify(receipts[0].discovered)
  if (receipts.some((r,i) => JSON.stringify(r.discovered) !== discovered || JSON.stringify(r.units) !== JSON.stringify(r.lane === 'other' ? plans[i].other : [plans[i].ui]))) throw Error('workspace check coverage drift')
  const ui = receipts.filter(r => r.lane !== 'other')
  if (ui.some(r => !Array.isArray(r.inventory) || !Array.isArray(r.files))) throw Error('missing UI coverage receipt')
  {
    const inventory = JSON.stringify(ui[0].inventory)
    if (ui.some(r => JSON.stringify(r.inventory) !== inventory)) throw Error('inventory drift')
    validatePartitions(ui[0].inventory, ui.map(r => r.files))
  }
  return true
}
