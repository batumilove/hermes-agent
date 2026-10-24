// Exercise the imported build manifest with real locked tarballs and an empty
// offline cache. Dropping overrides must fail; the production projection must
// install the security-selected KaTeX despite a conflicting consumer constraint.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [imports, sourceManifest] = process.argv.slice(2);
const imported = JSON.parse(await readFile(join(imports, 'package.json'), 'utf8'));
const locked = JSON.parse(await readFile(join(imports, 'package-lock.json'), 'utf8'));
const original = JSON.parse(await readFile(sourceManifest, 'utf8'));
assert.ok(imported.overrides, 'Offline projection must retain security overrides');
const references = new Set();
function checkOverrides(source, projected) {
  assert.deepEqual(Object.keys(projected).sort(), Object.keys(source).sort());
  for (const [key, value] of Object.entries(source)) {
    const target = projected[key];
    if (typeof value === 'object') {
      checkOverrides(value, target);
    } else if (target !== value) {
      assert.match(target, /^\$[^$]+$/);
      const name = target.slice(1);
      assert.ok(original.dependencies?.[name] || original.devDependencies?.[name]);
      const resolved = imported.dependencies?.[name] || imported.devDependencies?.[name];
      assert.equal(resolved, locked.packages[`node_modules/${name}`].resolved);
      assert.ok(resolved.startsWith('file:'));
      references.add(name);
    }
  }
}
checkOverrides(original.overrides, imported.overrides);
assert.ok(references.size > 0, 'Exercise the direct dependency override repair');
// Keep the conflict independent of workspace upgrades that may align with the pin.
const consumerVersion = '0.0.0-offline-fixture';
assert.notEqual(consumerVersion, locked.packages['node_modules/katex'].version);

const selected = {};
function include(location) {
  if (selected[location]) return;
  const record = locked.packages[location];
  assert.ok(record?.resolved?.startsWith('file:'), `Locked local artifact for ${location}`);
  selected[location] = record;
  for (const name of Object.keys(record.dependencies || {})) {
    const nested = `${location}/node_modules/${name}`;
    include(locked.packages[nested] ? nested : `node_modules/${name}`);
  }
}
include('node_modules/katex');
for (const name of references) include(`node_modules/${name}`);
const work = await mkdtemp(join(tmpdir(), 'hermes-npm-offline-overrides-'));
try {
  for (const retain of [false, true]) {
    const directory = join(work, retain ? 'retained' : 'removed');
    await mkdir(join(directory, 'consumer'), { recursive: true });
    const dependencies = Object.fromEntries([...references].map(name => [name, selected[`node_modules/${name}`].resolved]));
    const root = {
      name: 'hermes-offline-overrides-check', version: '1.0.0',
      workspaces: ['consumer'], dependencies,
      ...(retain ? { overrides: imported.overrides } : {}),
    };
    const member = {
      name: 'hermes-katex-consumer', version: '1.0.0',
      dependencies: { katex: consumerVersion },
    };
    const lock = {
      name: root.name, version: root.version, lockfileVersion: 3, requires: true,
      packages: {
        '': {
          ...root,
          // importNpmLock leaves the lock root's original registry constraints
          // intact while rewriting the build manifest to file: dependencies.
          dependencies: Object.fromEntries([...references].map(name => [
            name, locked.packages[''].dependencies?.[name] || locked.packages[''].devDependencies?.[name],
          ])),
        },
        consumer: member,
        'node_modules/hermes-katex-consumer': { resolved: 'consumer', link: true },
        ...selected,
      },
    };
    for (const [name, data] of [['package.json', root], ['package-lock.json', lock], ['consumer/package.json', member]]) {
      await writeFile(join(directory, name), `${JSON.stringify(data, null, 2)}\n`);
    }
    const result = spawnSync('npm', [
      'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund',
      '--cache', join(directory, 'empty-cache'),
    ], { cwd: directory, encoding: 'utf8', env: process.env, timeout: 60_000 });
    if (!retain) {
      assert.notEqual(result.status, 0, 'Missing override must reproduce the offline failure');
      assert.match(result.stderr, /ENOTCACHED/);
      assert.match(result.stderr, /registry\.npmjs\.org\/katex/);
    } else {
      assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
      const installed = JSON.parse(await readFile(join(directory, 'node_modules/katex/package.json'), 'utf8'));
      assert.equal(installed.version, locked.packages['node_modules/katex'].version);
      const yaml = JSON.parse(await readFile(join(directory, 'node_modules/js-yaml/package.json'), 'utf8'));
      assert.equal(yaml.version, locked.packages['node_modules/js-yaml'].version);
    }
  }
  console.log('Offline security overrides and direct imported references passed');
} finally {
  await rm(work, { recursive: true, force: true });
}
