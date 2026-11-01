import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'

import { expect, test } from 'vitest'

import { prepareWebappWorkspace } from '../apps/desktop/scripts/build-webapp.mjs'
import { sourceHash } from '../scripts/build/freshness.mjs'
import { npmCommand } from '../scripts/build/node-deps.mjs'

test('the private Webapp workspace installs and tracks vendored file dependencies', () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'webapp-local-package-')))
  const source = join(root, 'source')
  const workspace = join(root, 'workspace')
  const put = (name, contents) => {
    const target = join(source, name)
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(target, contents)
  }

  try {
    put('package.json', JSON.stringify({
      name: 'webapp-fixture', private: true, workspaces: ['apps/*'],
      dependencies: { 'fixture-matcher': 'file:vendor/npm/matcher' }
    }))
    put('.npmrc', 'install-links=true\n')
    put('apps/desktop/package.json', '{"name":"desktop-fixture","version":"1.0.0"}')
    put('vendor/npm/matcher/package.json', '{"name":"fixture-matcher","version":"1.0.0","main":"index.cjs"}')
    put('vendor/npm/matcher/index.cjs', 'module.exports = "vendored matcher"')
    const [node, npm] = npmCommand({})
    execFileSync(node, [npm, 'install', '--package-lock-only', '--ignore-scripts', '--no-audit', '--no-fund'], {
      cwd: source, stdio: 'pipe'
    })

    prepareWebappWorkspace({ source, workspace })
    const loaded = execFileSync(node, ['-e', 'process.stdout.write(require("fixture-matcher"))'], {
      cwd: workspace, encoding: 'utf8'
    })
    expect(loaded).toBe('vendored matcher')
    expect(readFileSync(join(workspace, 'vendor/npm/matcher/index.cjs'), 'utf8')).toContain('vendored matcher')
    const before = sourceHash(source, 'webapp')
    put('vendor/npm/matcher/index.cjs', 'module.exports = "updated matcher"')
    expect(sourceHash(source, 'webapp')).not.toBe(before)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}, 30_000)
