import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

test('locked Vitest executes each selected prefix sibling exactly once', async () => {
  const { exactUiFilters, parseUiInventory, validatePartitions } = await load()
  const vitest = fileURLToPath(new URL('../../node_modules/vitest/vitest.mjs', import.meta.url))
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'ui-sharding-'))
  try {
    fs.writeFileSync(path.join(cwd, 'vitest.config.mjs'), 'export default {server:{host:"127.0.0.1"},test:{projects:[{server:{host:"127.0.0.1"},test:{name:"ui",include:["**/*.test.{ts,tsx}"]}}]}}')
    // No imports: globals keep fixture dependency resolution inside Vitest.
    for (const name of ['menu.test.ts', 'menu.test.tsx', '[id]/menu.test.tsx', 'other.test.ts']) {
      const file = path.join(cwd, name)
      fs.mkdirSync(path.dirname(file), {recursive:true})
      fs.writeFileSync(file, 'test("runs",()=>expect(1).toBe(1))')
    }
    const run = args => execFileSync(process.execPath, [vitest, ...args, '--project', 'ui', '--globals'], {cwd, encoding:'utf8'})
    const inventory = parseUiInventory(JSON.parse(run(['list', '--filesOnly', '--json'])))
    const executed = inventory.map((file, i) => {
      const report = path.join(cwd, `report-${i}.json`)
      run(['run', '--isolate', '--maxWorkers', '1', '--reporter=json', `--outputFile=${report}`, ...exactUiFilters(inventory, [file])])
      const result = JSON.parse(fs.readFileSync(report, 'utf8'))
      assert.equal(result.success, true)
      const actual = result.testResults.map(t => t.name)
      assert.deepEqual(actual, [file])
      return actual
    })
    assert.equal(validatePartitions(inventory, executed), true)
  } finally {
    fs.rmSync(cwd, {recursive:true, force:true})
  }
})

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

test('exact UI filters exclude unselected prefix siblings without dropping selected files', async () => {
  const { exactUiFilters } = await load()
  const files = ['/repo/src/menu.test.ts', '/repo/src/menu.test.tsx', '/repo/src/[id]/menu.test.tsx']
  assert.deepEqual(exactUiFilters(files, [files[0]]), [files[0], '--exclude', files[1], '--exclude', '/repo/src/\\[id\\]/menu.test.tsx'])
  assert.deepEqual(exactUiFilters(files, [files[1], files[2]]), [files[1], files[2], '--exclude', files[0]])
  for (const selected of [[], [files[0], files[0]], ['/foreign']]) assert.throws(() => exactUiFilters(files, selected))
})
