const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {createRequire} = require('node:module');
const root = path.resolve(__dirname, '..');
const req = createRequire(path.join(root, 'package.json'));
const provenance = req('./vendor/svgr-plugin-svgo/SOURCE.json');
for (const [name, hash] of Object.entries(provenance.patched_sha256)) {
  const bytes = fs.readFileSync(path.join(root, 'vendor/svgr-plugin-svgo', name));
  assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), hash, name);
  if (name !== 'package.json') assert.equal(hash, provenance.original_sha256[name]);
}
const webpackRequire = createRequire(req.resolve('@svgr/webpack'));
assert.equal(webpackRequire.resolve('@svgr/plugin-svgo'), req.resolve('@svgr/plugin-svgo'));
const plugin = webpackRequire('@svgr/plugin-svgo');
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path id="shape" d="M0 0h24v24H0z"/></svg>';
assert.equal(plugin(svg, {svgo:false}, {}), svg);
assert.match(plugin(svg, {svgo:true, icon:true}, {}), /viewBox/);
assert.match(plugin(svg, {svgo:true, dimensions:false}, {}), /viewBox/);
assert.match(plugin(svg, {svgo:true, native:true}, {}), /<svg/);
assert.match(plugin(svg, {svgo:true, svgoConfig:{plugins:[]}}, {}), /id="shape"/);
assert.throws(() => plugin('<svg><', {svgo:true}, {}));
for (const base of [root, path.resolve(root, '../scripts/whatsapp-bridge')]) {
  const r = createRequire(path.join(base, 'package.json'));
  const qs = r('qs');
  assert.equal(r('qs/package.json').version, '6.16.2');
  for (const encodeValuesOnly of [false, true]) {
    assert.doesNotThrow(() => qs.stringify(JSON.parse('{"a":[{"toString":"x"}]}'), {arrayFormat:'comma', encodeValuesOnly}));
  }
  assert.deepEqual(qs.parse('a=1&b=2'), {a:'1', b:'2'});
  assert.equal(qs.stringify({a:['x','y']}, {arrayFormat:'comma'}), 'a=x%2Cy');
}
assert.throws(() => req.resolve('deepmerge'), {code:'MODULE_NOT_FOUND'});
console.log('PASS: provenance, actual webpack consumer, SVGO behavior, qs exploit and normal cases; deepmerge absent');
