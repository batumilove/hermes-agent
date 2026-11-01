// After npm ci --ignore-scripts in the root and website, run:
// node scripts/ci/npm-security-compat.mjs
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdtempSync, mkdirSync, writeFileSync, rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join, resolve} from 'node:path'
import {createServer} from 'node:http'
import test from 'node:test'

const root = resolve(import.meta.dirname, '../..')
const requireRoot = createRequire(join(root, 'package.json'))
const requireWebsite = createRequire(join(root, 'website/package.json'))

function fixture(t) {
  const folder = mkdtempSync(join(tmpdir(), 'hermes-glob-compat-'))
  t.after(() => rmSync(folder, {recursive: true, force: true}))
  mkdirSync(join(folder, 'nested'))
  for (const name of ['a.txt', 'b.js', '.hidden.txt', 'nested/c.txt']) writeFileSync(join(folder, name), name)
  return folder
}

test('legacy glob callers retain callbacks, synchronous matching, dot policy and exclusions', async t => {
  const cwd = fixture(t)
  const glob = requireRoot('glob-legacy')
  assert.deepEqual(glob.sync('**/*.txt', {cwd}).sort(), ['a.txt', 'nested/c.txt'])
  assert.deepEqual(glob.sync('*.txt', {cwd, dot: true}).sort(), ['.hidden.txt', 'a.txt'])
  assert.deepEqual(glob.sync('**/*.txt', {cwd, ignore: 'nested/**'}), ['a.txt'])
  assert.deepEqual(glob.sync('@(a|b).*', {cwd}).sort(), ['a.txt', 'b.js'])
  const result = await new Promise((resolveResult, reject) => glob('*.txt', {cwd}, (error, files) => error ? reject(error) : resolveResult(files)))
  assert.deepEqual(result, ['a.txt'])
  // Exercise the real nested legacy consumer, rather than just the direct alias.
  const requireRimraf = createRequire(requireRoot.resolve('rimraf/package.json'))
  assert.deepEqual(requireRimraf('glob').sync('*.txt', {cwd}), ['a.txt'])
})

test('cacache resolves glob 14 and can enumerate and verify its production cache', async t => {
  const cwd = fixture(t)
  const requireCache = createRequire(requireRoot.resolve('cacache/package.json'))
  const glob = requireCache('glob')
  assert.deepEqual(glob.globSync('*.txt', {cwd}), ['a.txt'])
  const cache = requireRoot('cacache')
  const directory = join(cwd, 'cache')
  await cache.put(directory, 'compat', 'cache-value')
  assert.equal((await cache.get(directory, 'compat')).data.toString(), 'cache-value')
  assert.equal((await cache.verify(directory)).verifiedContent, 1)
})

test('website serve-handler serves static paths and minimatch rewrites over HTTP', async t => {
  const cwd = fixture(t)
  const handler = requireWebsite('serve-handler')
  const server = createServer((request, response) => handler(request, response, {public: cwd, rewrites: [{source: '/health', destination: '/a.txt'}]}))
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen))
  try {
    for (const route of ['/a.txt', '/health']) {
      const response = await fetch(`http://127.0.0.1:${server.address().port}${route}`)
      assert.equal(response.status, 200)
      assert.equal(await response.text(), 'a.txt')
    }
  } finally {
    await new Promise(resolveClose => server.close(resolveClose))
  }
})
