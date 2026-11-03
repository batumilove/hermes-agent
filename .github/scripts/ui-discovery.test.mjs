import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as sharding from './ui-sharding.mjs'

// Vitest v4.1.12 cli-api.ts formatFilesAsJSON / test/cli/test/list.test.ts:
// --filesOnly --json emits objects; the named project is included.
const lockedFiles = [
  { file: '/repo/apps/desktop/src/z.test.tsx', projectName: 'ui' },
  { file: '/repo/apps/desktop/src/a.test.tsx', projectName: 'ui' },
]
test('locked Vitest filesOnly JSON preserves exact file identity', () => {
  assert.deepEqual(sharding.parseUiInventory(lockedFiles), lockedFiles.map(x => x.file).sort())
})
test('discovery fails closed on malformed duplicate and foreign identities', () => {
  assert.equal(typeof sharding.parseUiInventory, 'function')
  for (const bad of [null, {}, [], ['a'], [null], [{file:'a'}], [{file:'',projectName:'ui'}], [{file:'  ',projectName:'ui'}], [{file:1,projectName:'ui'}], [{file:'a',projectName:'electron'}], [...lockedFiles, lockedFiles[0]]]) {
    assert.throws(() => sharding.parseUiInventory(bad))
  }
})
test('aggregate finishes with mandatory default clean checkout action', () => {
  const workflow = readFileSync(new URL('../workflows/js-tests.yml', import.meta.url), 'utf8')
  const aggregate = workflow.split('\n  aggregate:')[1]
  assert.match(aggregate.trimEnd(), /uses: \.\/\.github\/actions\/check-clean-worktree$/)
})
