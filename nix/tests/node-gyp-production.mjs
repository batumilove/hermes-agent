// Run against the production-only node-gyp source/cache after npm ci:
// node nix/tests/node-gyp-production.mjs <node-gyp-source> <local-node-header-root>
import assert from 'node:assert/strict'
import {spawnSync} from 'node:child_process'
import {mkdtempSync, rmSync, writeFileSync} from 'node:fs'
import {createServer} from 'node:http'
import {createRequire} from 'node:module'
import {tmpdir} from 'node:os'
import {join, resolve} from 'node:path'
import test from 'node:test'

const [installation, headers] = process.argv.slice(2)
assert.ok(installation && headers, 'Provide the installed node-gyp source and local Node headers')
const source = resolve(installation)
const requireInstalled = createRequire(join(source, 'package.json'))
const requireGlob = createRequire(requireInstalled.resolve('glob'))

test('glob expands ordinary ranges and bounds adversarial nested/rewrite patterns', () => {
  const expand = requireGlob('brace-expansion')
  assert.deepEqual(expand('src/{a,b}/{1..2}.cc'), ['src/a/1.cc', 'src/a/2.cc', 'src/b/1.cc', 'src/b/2.cc'])
  const nested = expand('{'.repeat(4000) + 'a,b' + '}'.repeat(4000))
  assert.ok(nested.length <= 2)
  assert.ok(nested.every(value => value.length <= 8003))
  assert.ok(expand('{a}' + '}'.repeat(4000) + ',z}').length <= 2)
})

test('stale cached responses remain separated by URL and Vary headers', () => {
  const CachePolicy = requireInstalled('http-cache-semantics')
  const owner = {url: 'https://cache.invalid/account', method: 'GET', headers: {cookie: 'owner'}}
  const policy = new CachePolicy(owner, {status: 200, headers: {'cache-control': 'public, max-age=1', vary: 'cookie'}})
  policy.age = () => 60
  const stale = {...owner, headers: {...owner.headers, 'cache-control': 'max-stale=120'}}
  assert.equal(policy.satisfiesWithoutRevalidation(stale), true)
  assert.equal(policy.satisfiesWithoutRevalidation({...stale, headers: {...stale.headers, cookie: 'other-user'}}), false)
  assert.equal(policy.satisfiesWithoutRevalidation({...stale, url: 'https://cache.invalid/other'}), false)
})

test('IP classifiers cover the complete link-local range and reject cross-family subnets', () => {
  const {Address4, Address6} = requireInstalled('ip-address')
  assert.equal(new Address6('febf::1').isLinkLocal(), true)
  assert.equal(new Address6('fec0::1').isLinkLocal(), false)
  assert.equal(new Address6('64:ff9b:1::1').isGlobal(), false)
  assert.equal(new Address6('a00::1').isInSubnet(new Address4('10.0.0.0/8')), false)
  assert.equal(new Address4('10.0.0.1').isHostInSubnet(new Address6('a00::/8')), false)
})

test('node-gyp fetch consumer performs a loopback HTTP roundtrip', async () => {
  const fetch = requireInstalled('make-fetch-happen')
  const server = createServer((request, response) => response.end('production-cache-consumer'))
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen))
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}`, {timeout: 5000})
    assert.equal(await response.text(), 'production-cache-consumer')
  } finally {
    await new Promise(resolveClose => server.close(resolveClose))
  }
})

test('node-gyp configures, builds and loads a real native addon using local headers', () => {
  const directory = mkdtempSync(join(tmpdir(), 'hermes-node-gyp-production-'))
  try {
    writeFileSync(join(directory, 'binding.gyp'), JSON.stringify({targets: [{target_name: 'smoke', sources: ['smoke.cc']}]}))
    writeFileSync(join(directory, 'smoke.cc'), `#include <node_api.h>
static napi_value Init(napi_env env, napi_value exports) {
  napi_value answer; napi_create_int32(env, 42, &answer);
  napi_set_named_property(env, exports, "answer", answer); return exports;
}
NAPI_MODULE(NODE_GYP_MODULE_NAME, Init)
`)
    const result = spawnSync(process.execPath, [join(source, 'bin/node-gyp.js'), 'rebuild', `--nodedir=${resolve(headers)}`], {cwd: directory, encoding: 'utf8', timeout: 30000})
    assert.ifError(result.error)
    assert.equal(result.status, 0, result.stdout + result.stderr)
    assert.equal(requireInstalled(join(directory, 'build/Release/smoke.node')).answer, 42)
  } finally {
    rmSync(directory, {recursive: true, force: true})
  }
})
