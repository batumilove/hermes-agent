import fs from 'node:fs'
import path from 'node:path'
import { execFileSync, spawnSync } from 'node:child_process'
import { discoverUnits } from './run-workspace-checks.mjs'
import { planUnits, validatePartitions, workerBudget, validateReceipts, parseUiInventory } from './ui-sharding.mjs'

const lane = process.argv[2]
const sha = execFileSync('git', ['rev-parse','HEAD'], {encoding:'utf8'}).trim()
if (lane === 'aggregate') {
  const receipts = fs.readdirSync(process.argv[3]).filter(f => f.endsWith('.json')).map(f => JSON.parse(fs.readFileSync(path.join(process.argv[3],f),'utf8')))
  validateReceipts(receipts, sha)
} else {
  if (!['other','1','2','3','4'].includes(lane)) throw Error('invalid lane')
  const plan = planUnits(discoverUnits())
  const receipt = {lane, sha, ok:false, discovered:[plan.ui, ...plan.other], units:lane === 'other' ? plan.other : [plan.ui]}
  fs.mkdirSync('/var/tmp/hermes-ui-receipts', {recursive:true})
  const out = `/var/tmp/hermes-ui-receipts/${lane}.json`
  try {
    if (lane === 'other') {
      const r = spawnSync(process.execPath, ['.github/scripts/run-workspace-checks.mjs','--without-desktop-ui','--concurrency','1'], {stdio:'inherit'})
      receipt.ok = r.status === 0
    } else {
      const cwd = path.resolve('apps/desktop')
      const vitest = path.resolve('node_modules/vitest/vitest.mjs')
      const inventory = parseUiInventory(JSON.parse(execFileSync(process.execPath,[vitest,'list','--project','ui','--filesOnly','--json'],{cwd,encoding:'utf8',maxBuffer:16*1024*1024})))
      const partitions = [[],[],[],[]]
      inventory.forEach((f,i) => partitions[i%4].push(f))
      validatePartitions(inventory, partitions)
      const selected = partitions[Number(lane)-1]
      const report = `/var/tmp/hermes-ui-receipts/report-${lane}.json`
      const r = spawnSync(process.execPath,[vitest,'run','--project','ui','--isolate','--maxWorkers',String(workerBudget()),'--reporter=json',`--outputFile=${report}`, ...selected],{cwd,stdio:'inherit'})
      const result = JSON.parse(fs.readFileSync(report,'utf8'))
      const actual = result.testResults.map(t => t.name).sort()
      // Filters are not exact filenames: reject accidental overlapping matches.
      if (JSON.stringify(actual) !== JSON.stringify([...selected].sort())) throw Error('executed file coverage differs from selected partition')
      receipt.inventory = inventory
      receipt.files = actual
      receipt.ok = r.status === 0 && result.success === true
    }
  } finally {
    fs.writeFileSync(out, JSON.stringify(receipt))
  }
  if (!receipt.ok) process.exitCode = 1
}
