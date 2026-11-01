const assert = require('node:assert/strict');
const {mkdtempSync, writeFileSync, mkdirSync, rmSync} = require('node:fs');
const {createServer} = require('node:http');
const {tmpdir} = require('node:os');
const path = require('node:path');
const {test} = require('node:test');
const serve = require('serve-handler');

test('site serving retains glob headers, rewrites and excluded directory entries', async () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'hermes-site-matcher-'));
  mkdirSync(path.join(directory, 'assets'));
  writeFileSync(path.join(directory, 'index.html'), 'site-index');
  writeFileSync(path.join(directory, 'assets/app.txt'), 'asset-content');
  writeFileSync(path.join(directory, 'assets/private.txt'), 'private-content');
  const server = createServer((request, response) => serve(request, response, {
    public: directory,
    headers: [{source: '**/*.txt', headers: [{key: 'X-Matcher', value: 'matched'}]}],
    rewrites: [{source: '/route/**', destination: '/index.html'}],
    unlisted: ['**/private.txt'],
  }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const asset = await fetch(`${base}/assets/app.txt`);
    assert.equal(asset.status, 200);
    assert.equal(asset.headers.get('x-matcher'), 'matched');
    assert.equal(await asset.text(), 'asset-content');
    const rewritten = await fetch(`${base}/route/nested`);
    assert.equal(rewritten.status, 200);
    assert.equal(await rewritten.text(), 'site-index');
    const listing = await (await fetch(`${base}/assets/`)).text();
    assert.match(listing, /app\.txt/);
    assert.doesNotMatch(listing, /private\.txt/);
  } finally {
    await new Promise(resolve => server.close(resolve));
    rmSync(directory, {recursive: true, force: true});
  }
});
