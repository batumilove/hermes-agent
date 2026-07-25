'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const test = require('node:test');

const consumers = [
  '@electron/asar',
  '@electron/universal',
  '@eslint/config-array',
  '@eslint/eslintrc',
  'dir-compare',
  'eslint',
  'eslint-plugin-react',
  'filelist',
  'glob',
];

function requireFrom(packageName) {
  return createRequire(require.resolve(packageName));
}

function packageVersion(req, packageName) {
  return require(req.resolve(`${packageName}/package.json`)).version;
}

test('installed legacy minimatch consumers use patched callable brace expansion', (t) => {
  const repositoryRoot = path.join(__dirname, '..');
  const fullWorkspaceInstall = fs.existsSync(path.join(repositoryRoot, 'apps', 'desktop', 'package.json'));
  const availableConsumers = consumers.filter((consumer) => {
    try {
      require.resolve(consumer);
      return true;
    } catch (error) {
      if (error && error.code === 'MODULE_NOT_FOUND') return false;
      throw error;
    }
  });

  if (fullWorkspaceInstall) {
    assert.deepEqual(
      availableConsumers,
      consumers,
      'a full repository install must include every protected legacy minimatch consumer',
    );
  }

  if (availableConsumers.length === 0) {
    t.skip('partial install contains no legacy minimatch consumers');
    return;
  }

  for (const consumer of availableConsumers) {
    const consumerRequire = requireFrom(consumer);
    const minimatchEntry = consumerRequire.resolve('minimatch');
    const minimatchRequire = createRequire(minimatchEntry);
    const minimatch = consumerRequire('minimatch');
    const expansion = minimatchRequire('brace-expansion');
    const minimatchMajor = Number(packageVersion(consumerRequire, 'minimatch').split('.')[0]);

    assert.ok([3, 5, 9].includes(minimatchMajor), `${consumer} unexpectedly changed minimatch major`);
    assert.equal(typeof expansion, 'function', `${consumer} lost the legacy callable expansion API`);
    assert.equal(expansion.__hermesPatchedVersion, '5.0.8', `${consumer} missed the patched adapter`);
    assert.deepEqual(expansion('artifact-{linux,windows}'), [
      'artifact-linux',
      'artifact-windows',
    ]);

    const match = typeof minimatch === 'function' ? minimatch : minimatch.minimatch;
    assert.equal(typeof match, 'function', `${consumer} lost its minimatch API`);
    assert.equal(match('src/index.ts', 'src/**/*.ts'), true);
  }
});

test('modern implementation and expansion limits are preserved', () => {
  const expansion = require('brace-expansion');
  const expansionRequire = requireFrom('brace-expansion');

  assert.equal(expansion.__hermesPatchedVersion, '5.0.8');
  assert.equal(typeof expansion.expand, 'function');
  assert.equal(typeof expansion.EXPANSION_MAX, 'number');
  assert.equal(packageVersion(expansionRequire, 'brace-expansion-modern'), '5.0.8');
  assert.deepEqual(expansion.expand('release-{a,b}'), ['release-a', 'release-b']);
});

test('vendored tarball is pinned and installs the reviewed adapter source', () => {
  const vendorDirectory = path.join(__dirname, '..', 'vendor', 'brace-expansion-compat');
  const tarball = fs.readFileSync(path.join(vendorDirectory, 'brace-expansion-5.0.8.tgz'));
  const expectedSha256 = fs
    .readFileSync(path.join(vendorDirectory, 'TARBALL.sha256'), 'utf8')
    .trim()
    .split(/\s+/)[0];
  const installedEntry = require.resolve('brace-expansion');

  assert.equal(crypto.createHash('sha256').update(tarball).digest('hex'), expectedSha256);
  assert.equal(
    fs.readFileSync(installedEntry, 'utf8'),
    fs.readFileSync(path.join(vendorDirectory, 'index.cjs'), 'utf8'),
  );
  assert.equal(packageVersion(require, 'brace-expansion'), '5.0.8');
});

test('Docker dependency layer copies local adapter inputs before npm install', (t) => {
  const dockerfilePath = path.join(__dirname, '..', 'Dockerfile');
  if (!fs.existsSync(dockerfilePath)) {
    t.skip('partial Docker dependency context does not include Dockerfile');
    return;
  }

  const dockerfile = fs.readFileSync(dockerfilePath, 'utf8');
  const installIndex = dockerfile.indexOf(
    'RUN npm exec --package=npm@11.18.0 -- npm install --prefer-offline --no-audit',
  );
  assert.notEqual(installIndex, -1, 'Docker npm install step is missing');

  for (const copyCommand of [
    'COPY vendor/brace-expansion-compat/ vendor/brace-expansion-compat/',
    'COPY scripts/brace-expansion-compat.test.cjs scripts/',
  ]) {
    const copyIndex = dockerfile.indexOf(copyCommand);
    assert.notEqual(copyIndex, -1, `Dockerfile is missing: ${copyCommand}`);
    assert.ok(copyIndex < installIndex, `${copyCommand} must run before npm install`);
  }
});
