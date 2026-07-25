'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

const serveHandlerEntry = require.resolve('serve-handler');
const minimatchEntry = require.resolve('minimatch', {
  paths: [path.dirname(serveHandlerEntry)],
});
const minimatch = require(minimatchEntry);
const bracePackage = require.resolve('brace-expansion/package.json', {
  paths: [path.dirname(minimatchEntry)],
});
const braceVersion = require(bracePackage).version;

test('serve-handler uses patched brace expansion through a callable minimatch API', () => {
  assert.equal(braceVersion, '5.0.8');
  assert.equal(typeof minimatch, 'function');
  assert.equal(minimatch('docs/index.html', '**/*.html'), true);
  assert.deepEqual(minimatch.braceExpand('docs/{api,guide}.md'), [
    'docs/api.md',
    'docs/guide.md',
  ]);
});
