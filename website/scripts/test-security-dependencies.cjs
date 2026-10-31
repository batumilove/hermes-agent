const assert = require('node:assert/strict');
const path = require('node:path');
const {createRequire} = require('node:module');
const root = path.resolve(__dirname, '..');
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
const req = createRequire(path.join(root, 'package.json'));
for (const name of ['deepmerge', '@svgr/webpack', '@svgr/plugin-svgo', '@docusaurus/preset-classic']) {
  assert.throws(() => req.resolve(name), {code:'MODULE_NOT_FOUND'});
}
for (const name of ['plugin-content-docs','plugin-content-pages','plugin-debug','plugin-sitemap','theme-classic','theme-search-algolia']) {
  assert.equal(JSON.parse(require('node:fs').readFileSync(path.join(root, 'node_modules/@docusaurus', name, 'package.json'), 'utf8')).version, '3.10.2');
}
console.log('PASS: supported site constituents, unused SVGR/deepmerge absent, qs 6.16.2 exploit and normal cases in both scopes');
