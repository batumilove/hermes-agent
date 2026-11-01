import { test } from 'node:test'
import assert from 'node:assert/strict'

const load = () => import('./ui-sharding.mjs')
test('dynamic checks cover UI once and every other discovered check once', async () => {
  const { planUnits } = await load()
  const units = [{pkg:'apps/desktop',script:'check:test:ui'}, {pkg:'future/workspace',script:'check:new'}]
  const p = planUnits(units)
  assert.equal(p.other.length, 1)
  assert.equal(p.ui.script, 'check:test:ui')
  for (const bad of [[], units.slice(1), [...units, units[0]], [...units, units[1]]]) {
    assert.throws(() => planUnits(bad))
  }
})
test('file partitions reject empty missing duplicate and foreign coverage', async () => {
  const { validatePartitions } = await load()
  assert.equal(validatePartitions(['a','b','c'], [['a'],['b'],['c']]), true)
  for (const parts of [[['a'],['b'],[]], [['a'],['b'],['b']], [['a'],['b'],['x']]]) {
    assert.throws(() => validatePartitions(['a','b','c'], parts))
  }
  assert.throws(() => validatePartitions([], [[],[],[]]))
  assert.throws(() => validatePartitions(['a','a','b'], [['a'],['a'],['b']]))
})
test('worker budget is positive integral bounded by actual CPUs', async () => {
  const { workerBudget } = await load()
  assert.equal(workerBudget(1), 1)
  assert.equal(workerBudget(4), 3)
  assert.equal(workerBudget(128), 4)
  for (const n of [0,-1,NaN,1.5]) assert.throws(() => workerBudget(n))
})
test('aggregate requires exactly all successful identity-bound lane receipts', async () => {
  const { validateReceipts } = await load()
  const discovered = [{pkg:'apps/desktop',script:'check:test:ui'},{pkg:'new',script:'check'}]
  const rs = ['other','1','2','3','4'].map(lane => ({lane, sha:'candidate', ok:true, discovered, units:lane === 'other' ? [discovered[1]] : [discovered[0]], ...(lane === 'other' ? {} : {inventory:['a','b','c','d'], files:[['a','b','c','d'][Number(lane)-1]]})}))
  assert.equal(validateReceipts(rs, 'candidate'), true)
  for (const bad of [rs.slice(1), [...rs,rs[0]], rs.map((r,i)=>i ? r : {...r,ok:false}), rs.map((r,i)=>i ? r : {...r,sha:'old'}), rs.map((r,i)=>i ? r : {...r,units:[]})]) assert.throws(() => validateReceipts(bad,'candidate'))
})
